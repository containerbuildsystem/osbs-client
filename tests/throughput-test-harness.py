#!/usr/bin/python

import re
import sys
import time
import logging
import argparse
import subprocess

from dockerfile_parse import DockerfileParser

DEFAULT_BRANCH_PREFIX = "branch"
DEFAULT_BRANCH_COUNT = 100
DEFAULT_STAGGER_NUMBER = 10
DEFAULT_STAGGER_WAIT = 60
DEFAULT_KOJI_BIN = "koji"
DEFAULT_KOJI_TARGET = "extras-rhel-7.2-candidate"
DEFAULT_GIT_REMOTE = "origin"


class SubprocessError(Exception):
    pass


def run(*args):
    logging.info("running: %s", " ".join(args))

    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (out, err) = p.communicate()
    if out:
        logging.info("stdout:\n%s", out.rstrip())
    if err:
        logging.info("stderr:\n%s", err.rstrip())
    if p.returncode != 0:
        raise SubprocessError("Subprocess failed w/ return code {}".format(p.returncode))

    return out


def bump_release(df_path, branch):
    parser = DockerfileParser(df_path)
    oldrelease = parser.labels["Release"]
    if not oldrelease:
        raise RuntimeError("Dockerfile has no Release label")

    m = re.match(r"(.*\D)?(\d+)", oldrelease)
    if not m:
        raise RuntimeError("Release does not end with number")

    num = int(m.group(2))
    newrelease = "{}{:03d}".format(m.group(1), num+1)

    parser.labels["Release"] = newrelease
    return newrelease


def set_initial_release(df_path, branch):
    parser = DockerfileParser(df_path)
    oldrelease = parser.labels.get("Release", "1")
    newrelease = "{}.{}.iteration001".format(oldrelease, branch)
    parser.labels["Release"] = newrelease
    return newrelease


def get_branches(branch_prefix):
    branches = run("git", "branch", "--list")
    branches = [b[2:] for b in branches.splitlines()]
    branches = [b for b in branches if b.startswith(branch_prefix)]
    if not branches:
        raise RuntimeError("No branches starting with %s found" % branch_prefix)
    return branches


def cmd_create_branches(args):
    branches = ["{}{:03d}".format(args.branch_prefix, n+1) for n in range(args.number)]

    logging.info("Creating branches from current branch")
    for b in branches:
        run("git", "branch", b)

    logging.info("Setting initial Release")
    for b in branches:
        run("git", "checkout", b)
        release = set_initial_release("Dockerfile", b)
        run("git", "add", "Dockerfile")
        run("git", "commit", "--message", release)

    logging.info("Pusing ALL branches to %s", args.git_remote)
    run("git", "push", "--force", args.git_remote, *branches)


def cmd_delete_branches(args):
    branches = get_branches(args.branch_prefix)

    # otherwise we get Cannot delete the branch 'branch005' which you are currently on.
    run("git", "checkout", "master")

    logging.info("Deleting %d branches", len(branches))
    run("git", "branch", "--delete", "--force", *branches)

    logging.info("Deleting remote branches in %s", args.git_remote)
    run("git", "push", "--force", args.git_remote, *[":"+b for b in branches])


def cmd_bump_release(args):
    branches = get_branches(args.branch_prefix)

    for b in branches:
        run("git", "checkout", b)
        release = bump_release("Dockerfile", b)
        run("git", "add", "Dockerfile")
        run("git", "commit", "--message", release)

    logging.info("Pusing ALL branches to %s", args.git_remote)
    run("git", "push", "--force", args.git_remote, *branches)


def cmd_start_builds(args):
    branches = get_branches(args.branch_prefix)

    if args.git_url:
        remote_url = args.git_url
    else:
        for line in run("git", "remote", "-v").splitlines():
            parts = line.split()
            if parts[0] == args.git_remote and parts[2] == "(fetch)":
                remote_url = parts[1]
                break
        else:
            raise RuntimeError("Remote URL for repository %s not found" % args.git_remote)

    stagger_remaining = args.stagger_number
    failed_builds = {}
    repo_url = []
    if args.repo_url:
        if args.use_koji:
            repo_url = ['--repo-url', args.repo_url]
        else:
            repo_url = ['--add-yum-repo', args.repo_url]

    for (i, b) in enumerate(branches):
        if i >= DEFAULT_BRANCH_COUNT:
            break
        commit = run("git", "rev-parse", b).strip()
        branch_url = "{}#{}".format(remote_url, commit)

        try:
            if args.use_koji:
                run(args.koji_bin,
                    "container-build",
                    args.koji_target,
                    "--nowait",
                    "--git-branch", b,
                    branch_url,
                    *repo_url)
            else:
                run("osbs",
                    "build",
                    "-g", remote_url,
                    "-b", b,
                    "--target", args.koji_target,
                    "-c", "fake-component",
                    "-u", "vrutkovs",
                    "--no-logs",
                    *repo_url)
        except SubprocessError as ex:
            logging.exception("Failed to start build for branch %s", b)
            failed_builds[b] = ex

        if stagger_remaining > 0:
            logging.info("Waiting %d seconds before starting another build", args.stagger_wait)
            time.sleep(args.stagger_wait)
            stagger_remaining -= 1

    if failed_builds:
        logging.error("Failed to start builds: %d", len(failed_builds))
        for b, ex in failed_builds.items():
            logging.error("Branch %s:", b, exc_info=ex)


def main():
    parser = argparse.ArgumentParser(description="OSBS throughput test harness",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--branch-prefix", default=DEFAULT_BRANCH_PREFIX,
                        help="work on branches with this prefix")
    parser.add_argument("--git-remote", default=DEFAULT_GIT_REMOTE,
                        help="git remote to use")

    subparsers = parser.add_subparsers(help="subcommand")

    create_branches = subparsers.add_parser("create-branches",
                                            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    create_branches.add_argument("--number", metavar="N", type=int, default=DEFAULT_BRANCH_COUNT,
                                 help="number of branches to create")
    create_branches.set_defaults(func=cmd_create_branches)

    delete_branches = subparsers.add_parser("delete-branches",
                                            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    delete_branches.set_defaults(func=cmd_delete_branches)

    bump_release = subparsers.add_parser("bump-release",
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    bump_release.set_defaults(func=cmd_bump_release)

    start_builds = subparsers.add_parser("start-builds",
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    start_builds.add_argument("--stagger-number", metavar="N", type=int,
                              default=DEFAULT_STAGGER_NUMBER,
                              help="wait between starting N first builds")
    start_builds.add_argument("--stagger-wait", metavar="SECONDS", type=int,
                              default=DEFAULT_STAGGER_WAIT,
                              help="amount of time to wait between initial builds")
    start_builds.add_argument("--use-koji", default=False, action="store_true",
                              help="use koji to submit builds (default: use osbs")
    start_builds.add_argument("--koji-bin", default=DEFAULT_KOJI_BIN, help="koji executable")
    start_builds.add_argument("--koji-target", default=DEFAULT_KOJI_TARGET,
                              help="koji target to build in")
    start_builds.add_argument("--git-url",
                              help="url of git repo to pass to koji "
                              "(autodetected if not specified)")
    start_builds.add_argument("--repo-url", help="url of rpm repo to install for builds")
    start_builds.set_defaults(func=cmd_start_builds)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
