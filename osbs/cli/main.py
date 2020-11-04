"""
Copyright (c) 2015, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals
import collections

import json
import logging
import pkg_resources

from textwrap import dedent
import codecs
import time
import os.path
import sys
import argparse
from osbs import set_logging
from osbs.api import OSBS
from osbs.build.build_response import BuildResponse
from osbs.cli.render import TablePrinter
from osbs.conf import Configuration
from osbs.constants import (DEFAULT_CONFIGURATION_FILE, DEFAULT_CONFIGURATION_SECTION,
                            CLI_LIST_BUILDS_DEFAULT_COLS, PY3, BACKUP_RESOURCES,
                            BUILD_FINISHED_STATES, CLI_WATCH_BUILDS_DEFAULT_COLS)
from osbs.exceptions import (OsbsNetworkException, OsbsException, OsbsAuthException,
                             OsbsResponseException)
from osbs.cli.capture import setup_json_capture
from osbs.utils import (paused_builds, TarReader, TarWriter, get_time_from_rfc3339,
                        graceful_chain_get, ImageName)
from six.moves.urllib.parse import urljoin

logger = logging.getLogger('osbs')


def print_json_nicely(decoded_json):
    print(json.dumps(decoded_json, indent=2))


def cmd_get_all_resource_quota(args, osbs):
    quota_name = args.QUOTA_NAME
    logger.debug("quota name = %s", quota_name)
    if quota_name is None:
        response = osbs.list_resource_quotas()
        for item in response["items"]:
            print(graceful_chain_get(item, "metadata", "name"))
    else:
        print_json_nicely(osbs.get_resource_quota(quota_name))


def cmd_watch_builds(args, osbs):
    field_selector = ",".join(["status!={status}".format(status=status.capitalize())
                               for status in BUILD_FINISHED_STATES])
    cols_to_display = CLI_WATCH_BUILDS_DEFAULT_COLS
    if args.columns:
        cols_to_display = args.columns.split(",")

    data = [{
        "changetype": "CHANGE",
        "status": "STATUS",
        "created": "CREATED",
        "name": "NAME",
    }]
    for changetype, obj in osbs.watch_builds(field_selector=field_selector):
        try:
            name = obj['metadata']['name']
        except KeyError:
            logger.error("'object' doesn't have any name")
            continue
        else:
            try:
                status = obj['status']['phase']
            except KeyError:
                status = '(not reported)'

            try:
                timestamp = obj['metadata']['creationTimestamp']
            except KeyError:
                created = '(not reported)'
            else:
                created = time.ctime(get_time_from_rfc3339(timestamp))

            b = {
                "changetype": changetype or '(none)',
                "name": name or '',
                "status": status,
                "created": created,
            }
            data.append(b)
        if args.output == 'json':
            print(json.dumps(b))
            sys.stdout.flush()
        elif args.output == 'text':
            tp = TablePrinter(data, cols_to_display)
            tp.render()


def cmd_list_builds(args, osbs):
    kwargs = {}
    if args.running:
        kwargs['running'] = args.running

    if args.from_json:
        with open(args.from_json) as fp:
            builds = [BuildResponse(build, osbs) for build in json.load(fp)]
    else:
        builds = osbs.list_builds(**kwargs)

    if args.output == 'json':
        json_output = []
        for build in builds:
            json_output.append(build.json)
        print_json_nicely(json_output)
    elif args.output == 'text':
        if args.columns:
            cols_to_display = args.columns.split(",")
        else:
            cols_to_display = CLI_LIST_BUILDS_DEFAULT_COLS
        data = [{
            "base_image": "BASE IMAGE NAME",
            "base_image_id": "BASE IMAGE ID",
            "commit": "COMMIT",
            "image": "IMAGE NAME",
            "unique_image": "UNIQUE IMAGE NAME",
            "image_id": "IMAGE ID",
            "koji_build_id": "KOJI BUILD ID",
            "name": "BUILD ID",
            "status": "STATUS",
            "time_created": "TIME CREATED",
        }]
        for build in sorted(builds,
                            key=lambda x: x.get_time_created_in_seconds()):
            unique_image = build.get_image_tag()
            try:
                image = \
                    ImageName.parse(build.get_repositories()["primary"][0]).to_str(registry=False)
            except (TypeError, KeyError, IndexError):
                image = ""  # "" or unique_image? failed builds don't have that ^
            if args.FILTER and args.FILTER not in image:
                continue
            if args.running and not build.is_in_progress():
                continue
            b = {
                "base_image": build.get_base_image_name() or '',
                "base_image_id": build.get_base_image_id() or '',
                "commit": build.get_commit_id(),
                "image": image,
                "unique_image": unique_image,
                "image_id": build.get_image_id() or '',
                "koji_build_id": build.get_koji_build_id() or '',
                "name": build.get_build_name(),
                "status": build.status,
                "time_created": build.get_time_created(),
            }
            data.append(b)
        tp = TablePrinter(data, cols_to_display)
        tp.render()


def make_digests_str(digests):
    if digests is not None:
        try:
            digests_str = "\n".join(["{registry}/{repository}:{tag} {digest}".format(**dig)
                                     for dig in digests])
        except (TypeError, KeyError):
            digests_str = "(invalid value)"
    else:
        digests_str = "(unset)"

    if not digests_str:
        digests_str = "(empty)"

    return digests_str


def make_worker_builds_str(worker_builds):
    worker_build_template = dedent("""\
        {platform} WORKER BUILD

        {build_name} on {cluster_url} ({namespace})

        {platform} V2 DIGESTS

        {digests}""")

    worker_builds_formatted = []
    for platform, worker_build in worker_builds.items():

        digests_str = make_digests_str(worker_build.get('digests', []))

        worker_builds_formatted.append(worker_build_template.format(
            platform=platform,
            build_name=worker_build.get('build', {}).get('build-name', '(unset)'),
            cluster_url=worker_build.get('build', {}).get('cluster-url', '(unset)'),
            namespace=worker_build.get('build', {}).get('namespace', '(unset)'),
            digests=digests_str,
        ))

    return '\n\n'.join(worker_builds_formatted)


def cmd_get_build(args, osbs):
    build = osbs.get_build(args.BUILD_ID[0])
    build_json = build.json
    if args.output == 'json':
        print_json_nicely(build_json)
    elif args.output == 'text':
        repositories_dict = build.get_repositories()
        repositories_str = '(unset)'
        if repositories_dict is not None and repositories_dict["primary"]:
            repositories_template = dedent("""\
                Primary

                {primary}

                Unique

                {unique}""")
            repositories_context = {
                "primary": "\n".join(repositories_dict["primary"]),
                "unique": "\n".join(repositories_dict["unique"]),
            }
            repositories_str = repositories_template.format(**repositories_context)

        digests_str = make_digests_str(build.get_digests())

        logs_str = ''
        packages_str = ''

        template = dedent("""\
            BUILD ID: {build_id}
            STATUS: {status}
            IMAGE: {image}
            DATE: {date}

            DOCKERFILE

            {dockerfile}
            {logs}{packages}
            COMMIT ID

            {commit_id}

            BASE IMAGE ID (FROM {base_image})

            {base_image_id}

            IMAGE ID

            {image_id}

            KOJI BUILD ID

            {koji_build_id}

            REPOSITORIES

            {repositories}

            V2 DIGESTS

            {digests}""")

        context = {
            "build_id": build.get_build_name(),
            "status": build.status,
            "image": build.get_image_tag(),
            "date": build.get_time_created(),
            "dockerfile": build.get_dockerfile(),
            "logs": logs_str,
            "packages": packages_str,
            "repositories": repositories_str,
            "commit_id": build.get_commit_id(),
            "base_image": build.get_base_image_name() or '(unset)',
            "base_image_id": build.get_base_image_id() or '(unset)',
            "image_id": build.get_image_id() or '(unset)',
            "koji_build_id": build.get_koji_build_id() or '(unset)',
            "digests": digests_str,
        }

        worker_builds = json.loads(build.get_annotations().get('worker-builds', '{}'))
        if worker_builds:
            worker_builds_str = make_worker_builds_str(worker_builds)

            if worker_builds_str:
                template += dedent("""\


                    WORKER BUILDS

                    {worker_builds}""")
                context['worker_builds'] = worker_builds_str

        print(template.format(**context))


def cmd_cancel_build(args, osbs):
    osbs.cancel_build(args.BUILD_ID[0])


def _print_build_logs(args, osbs, build):
    build_id = build.get_build_name()
    # we need to wait for kubelet to schedule the build, otherwise it's 500
    build = osbs.wait_for_build_to_get_scheduled(build_id)
    if not args.no_logs:
        build_logs = osbs.get_build_logs(build_id, follow=True, decode=True)
        if not isinstance(build_logs, collections.Iterable):
            logger.error("'%s' is not iterable; can't display logs", build_logs)
            return
        print("Build submitted (%s), watching logs (feel free to interrupt)" % build_id)
        try:
            for line in build_logs:
                print('{!r}'.format(line))
        except Exception as ex:
            logger.error("Error during fetching logs for build %s: %s", build_id, repr(ex))

        osbs.wait_for_build_to_finish(build_id)
        return _display_build_summary(osbs.get_build(build_id))
    else:
        if args.output == 'json':
            print_json_nicely(build.json)
        elif args.output == 'text':
            print(build_id)

        if osbs.get_build(build_id).is_succeeded():
            return 0
        else:
            return -1


def cmd_build(args, osbs):
    if args.worker:
        create_func = osbs.create_worker_build
    else:
        create_func = osbs.create_orchestrator_build

    build_kwargs = {
        'git_uri': osbs.build_conf.get_git_uri(),
        'git_ref': osbs.build_conf.get_git_ref(),
        'git_branch': osbs.build_conf.get_git_branch(),
        'user': osbs.build_conf.get_user(),
        'tag': osbs.build_conf.get_tag(),
        'target': osbs.build_conf.get_koji_target(),
        'yum_repourls': osbs.build_conf.get_yum_repourls(),
        'dependency_replacements': osbs.build_conf.get_dependency_replacements(),
        'scratch': args.scratch,
        'platform': args.platform,
        'platforms': args.platforms,
        'release': args.release,
        'koji_parent_build': args.koji_parent_build,
        'isolated': args.isolated,
        'signing_intent': args.signing_intent,
        'compose_ids': args.compose_ids,
        'skip_build': args.skip_build,
    }
    if args.arrangement_version:
        if args.arrangement_version < 6:
            print("Arrangements less than 6 are no longer used.")
            return -1
        build_kwargs['arrangement_version'] = args.arrangement_version

    if args.koji_upload_dir:
        build_kwargs['koji_upload_dir'] = args.koji_upload_dir

    if osbs.build_conf.get_flatpak():
        build_kwargs['flatpak'] = True

    build = create_func(**build_kwargs)
    if build is None:
        print("Build skipped")
        return

    return _print_build_logs(args, osbs, build)


def cmd_build_source_container(args, osbs):
    build_kwargs = {
        'user': osbs.build_conf.get_user(),
        'target': osbs.build_conf.get_koji_target(),
        'scratch': args.scratch,
        'signing_intent': args.signing_intent,
        'sources_for_koji_build_nvr': args.sources_for_koji_build_nvr,
        'sources_for_koji_build_id': args.sources_for_koji_build_id,
        'component': args.component,
    }
    if args.arrangement_version:
        if args.arrangement_version < 6:
            print("Arrangements less than 6 are no longer used.")
            return -1
        build_kwargs['arrangement_version'] = args.arrangement_version

    build = osbs.create_source_container_build(**build_kwargs)

    return _print_build_logs(args, osbs, build)


def _display_build_summary(build):
    output = [
        "",  # Empty line for cleaner display
        "build {} is {}".format(build.get_build_name(), build.status),
    ]
    return_val = -1

    if build.is_succeeded():
        return_val = 0
        all_repositories = build.get_repositories() or {}

        for kind, repositories in all_repositories.items():
            if not repositories:
                continue
            output.append('{} repositories:'.format(kind))
            for repository in repositories:
                output.append('\t{}'.format(repository))

    for line in output:
        print(line)

    return return_val


def cmd_build_logs(args, osbs):
    build_id = args.BUILD_ID[0]
    follow = args.follow

    logs = osbs.get_build_logs(build_id, follow=follow,
                               wait_if_missing=args.wait_if_missing,
                               decode=True)

    if follow:
        for line in logs:
            print(line)
        return
    print(logs, end="")


def cmd_watch_build(args, osbs):
    build_response = osbs.wait_for_build_to_finish(args.BUILD_ID[0])
    if args.output == 'text':
        pass
    elif args.output == 'json':
        print_json_nicely(build_response.json)


def cmd_get_token(args, osbs):  # pylint: disable=W0613
    token = osbs.get_token()
    if args.oc:
        print('oc login --token {} {}'
              .format(token, osbs.os_conf.get_openshift_base_uri()))
    else:
        print(token)


def cmd_login(args, osbs):
    if args.token and (args.username or args.password):
        print("Can't use --token and (--user or --password).")
        return
    osbs.login(args.token, args.username, args.password)


def cmd_get_user(args, osbs):
    args_username = args.USERNAME
    if args_username is None:
        user_json = osbs.get_user()
    else:
        args_username = args_username[0]
        user_json = osbs.get_user(args_username)
    if args.output == 'json':
        print_json_nicely(user_json)
    elif args.output == 'text':
        name = ""
        full_name = ""
        try:
            name = user_json["metadata"]["name"]
        except KeyError:
            logger.error("\"name\" is not in response")
        try:
            full_name = user_json["fullName"]
        except KeyError:
            logger.error("\"full name\" is not in response")
        print("Name: \"%s\"\nFull Name: \"%s\"" % (name, full_name))


def cmd_get_build_image_id(args, osbs):
    pod = osbs.get_pod_for_build(args.BUILD_ID[0])
    if args.output == 'json':
        json_output = pod.get_container_image_ids()
        print_json_nicely(json_output)
    elif args.output == 'text':
        format_str = "{tag:18} {image:64}"
        print(format_str.format(tag='TAG', image='IMAGE ID'), file=sys.stderr)
        image_ids = pod.get_container_image_ids()
        for name, image_id in image_ids.items():
            print(format_str.format(tag=name, image=image_id))


def cmd_backup(args, osbs):
    dirname = time.strftime("osbs-backup-{}-%Y-%m-%d-%H%M%S"
                            .format(args.instance))
    if args.filename == '-':
        outfile = sys.stdout.buffer if PY3 else sys.stdout  # pylint: disable=no-member
    elif args.filename:
        outfile = args.filename
    else:
        outfile = dirname + ".tar.bz2"

    with paused_builds(osbs, quota_name='pause-backup',
                       ignore_quota_errors=args.ignore_quota_errors):
        with TarWriter(outfile, dirname) as t:
            for resource_type in BACKUP_RESOURCES:
                try:
                    logger.info("dumping %s", resource_type)
                    resources = osbs.dump_resource(resource_type)
                    t.write_file(resource_type + ".json", json.dumps(resources).encode('ascii'))
                except Exception as e:
                    if args.continue_on_error:
                        logger.warning(
                            "Error during %s backup", resource_type, exc_info=True
                        )
                    else:
                        raise e

    if not hasattr(outfile, "write"):
        logger.info("backup archive created: %s", outfile)


def cmd_restore(args, osbs):
    if args.BACKUP_ARCHIVE == '-':
        infile = sys.stdin.buffer if PY3 else sys.stdin  # pylint: disable=no-member
    else:
        infile = args.BACKUP_ARCHIVE
    asciireader = codecs.getreader('ascii')

    with paused_builds(osbs, quota_name='pause-backup',
                       ignore_quota_errors=args.ignore_quota_errors):
        for f in TarReader(infile):
            resource_type = os.path.basename(f.filename).split('.')[0]
            if resource_type not in BACKUP_RESOURCES:
                logger.warning("Unknown resource type for %s, skipping", f.filename)
                continue

            logger.info("restoring %s", resource_type)
            osbs.restore_resource(resource_type, json.load(asciireader(f.fileobj)),
                                  continue_on_error=args.continue_on_error)
            f.fileobj.close()

    logger.info("backup recovery complete!")


def cmd_print_token_url(args, osbs):
    uri = urljoin(osbs.os_conf.get_openshift_base_uri(), "oauth/token/request")
    print("To complete authentication please navigate to:\n\n{}\n\n".format(uri) +
          "Set token or token_file in configuration to authenticate requests.")


def cmd_serviceaccount_token(args, osbs):
    output_template = '{token}'
    openshift_uri = None
    if args.oc:
        output_template = 'oc login --token {token} {openshift_uri}'
        openshift_uri = osbs.os_conf.get_openshift_base_uri()

    tokens = osbs.get_serviceaccount_tokens(args.SERVICEACCOUNT)
    for token in tokens.values():
        print(output_template.format(token=token.decode('ascii'),
                                     openshift_uri=openshift_uri))
        break


def str_on_2_unicode_on_3(s):
    """
    argparse is way too awesome when doing repr() on choices when printing usage

    :param s: str or unicode
    :return: str on 2, unicode on 3
    """

    if not PY3:
        return str(s)
    else:  # 3+
        if not isinstance(s, str):
            return str(s, encoding="utf-8")
        return s


def cli():
    try:
        version = pkg_resources.get_distribution("osbs-client").version
    except pkg_resources.DistributionNotFound:
        version = "GIT"

    parser = argparse.ArgumentParser(
        description="OpenShift Build Service client"
    )
    exclusive_group = parser.add_mutually_exclusive_group()
    # FIXME: default=None is needed to indicate for osbs.conf.Configuration
    # that the option was not specified
    exclusive_group.add_argument("--verbose", action="store_true", default=None)
    exclusive_group.add_argument("-q", "--quiet", action="store_true")
    exclusive_group.add_argument("-V", "--version", action="version", version=version)

    subparsers = parser.add_subparsers(help='commands')

    list_builds_parser = subparsers.add_parser(str_on_2_unicode_on_3('list-builds'),
                                               help='list builds in OSBS',
                                               description="list all builds in the namespace")
    list_builds_parser.add_argument("FILTER", help="list only builds which contain provided string",
                                    nargs="?")
    list_builds_parser.add_argument("--columns",
                                    help="comma-separated list of columns to display, possible "
                                    "values: base_image, base_image_id, commit, image, "
                                    "unique_image, image_id, name, status, time_created")
    # this may be a bit confusing, but for users, "running" means not done but
    # for us, "running" means scheduled on kubelet
    list_builds_parser.add_argument("--running", help="list only running builds",
                                    action="store_true")
    list_builds_parser.add_argument("--from-json",
                                    help="fetch builds list from JSON file instead of from server")

    list_builds_parser.set_defaults(func=cmd_list_builds)

    watch_build_parser = subparsers.add_parser(str_on_2_unicode_on_3('watch-build'),
                                               help='wait till build finishes')
    watch_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    watch_build_parser.set_defaults(func=cmd_watch_build)

    watch_builds_parser = subparsers.add_parser(str_on_2_unicode_on_3('watch-builds'),
                                                help='watch running builds')
    watch_builds_parser.add_argument("--columns",
                                     help="comma-separated list of columns to display, possible "
                                     "values: changetype, status, created, name")
    watch_builds_parser.set_defaults(func=cmd_watch_builds)

    get_build_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-build'),
                                             help='get info about build')
    get_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    get_build_parser.set_defaults(func=cmd_get_build)

    cancel_build_parser = subparsers.add_parser(str_on_2_unicode_on_3('cancel-build'),
                                                help='cancel build specified by ID')
    cancel_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    cancel_build_parser.set_defaults(func=cmd_cancel_build)

    get_token_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-token'),
                                             help='get authentication token')
    get_token_parser.add_argument("--oc", help="display oc login command",
                                  action="store_true", default=False)
    get_token_parser.set_defaults(func=cmd_get_token)

    get_login_parser = subparsers.add_parser(str_on_2_unicode_on_3('login'),
                                             help='perform login and store token for later use')
    get_login_parser.add_argument('--token', help='token to be used for login', action="store")
    get_login_parser.add_argument('-u', '--username', help='Username, will prompt if not provided',
                                  action="store")
    get_login_parser.add_argument('-p', '--password', help='Password, will prompt if not provided',
                                  action="store")
    get_login_parser.set_defaults(func=cmd_login)

    get_user_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-user'),
                                            help='get info about user')
    get_user_parser.add_argument("USERNAME", nargs="?", default=None)
    get_user_parser.set_defaults(func=cmd_get_user)

    build_logs_parser = subparsers.add_parser(str_on_2_unicode_on_3('build-logs'),
                                              help='get or follow build logs')
    build_logs_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    build_logs_parser.add_argument("-f", "--follow", help="follow logs as they come",
                                   action="store_true", default=False)
    build_logs_parser.add_argument("--wait-if-missing", help="if build is not created yet, wait",
                                   action="store_true", default=False)
    build_logs_parser.set_defaults(func=cmd_build_logs)

    get_quota_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-quota'),
                                             help='get specific quota or list all quotas '
                                                  'present in OpenShift')
    get_quota_parser.add_argument("QUOTA_NAME", help="name of quota", nargs="?", default=None)
    get_quota_parser.set_defaults(func=cmd_get_all_resource_quota)

    build_parser = subparsers.add_parser(str_on_2_unicode_on_3('build'),
                                         help='build an image in OSBS')
    build_parser.add_argument("--build-json-dir", action="store", metavar="PATH",
                              help="directory with build jsons")
    build_parser.add_argument("-g", "--git-url", action='store', metavar="URL",
                              required=True, help="URL to git repo (fetch)")
    build_parser.add_argument("--git-commit", action='store', default="master",
                              help="checkout this commit")
    build_parser.add_argument("-b", "--git-branch", action='store', required=True,
                              help="name of git branch (for incrementing Release)")
    build_parser.add_argument("-t", "--target", action='store',
                              help="koji target name")
    build_parser.add_argument("--flatpak", action='store_true',
                              help="build a flatpak OCI")
    build_parser.add_argument("-a", "--arch", action='store',
                              help="build architecture")
    build_parser.add_argument("-u", "--user", action='store', required=True,
                              help="prefix for docker image repository")
    build_parser.add_argument("-c", "--component", action='store', required=False,
                              help="not used; use com.redhat.component label in Dockerfile")
    build_parser.add_argument("-A", "--tag", action='store', required=False,
                              help="tag of the built image (simple builds only)")
    build_parser.add_argument("--no-logs", action='store_true', required=False, default=False,
                              help="don't print logs after submitting build")
    build_parser.add_argument("--add-yum-repo", action='append', metavar="URL",
                              dest="yum_repourls", help="URL of yum repo file")
    build_parser.add_argument("--cpu-limit", action='store', required=False,
                              help="CPU limit (KCU)")
    build_parser.add_argument("--memory-limit", action='store', required=False,
                              help="memory limit")
    build_parser.add_argument("--storage-limit", action='store', required=False,
                              help="storage limit")
    build_parser.add_argument("--scratch", action='store_true', required=False,
                              help="perform a scratch build")
    build_parser.add_argument("--yum-proxy", action='store', required=False,
                              help="set yum proxy to repos from koji/add-yum-repo params")
    build_parser.add_argument('--koji-parent-build', action='store', required=False,
                              help='overwrite parent image with image from koji build')
    build_parser.add_argument('--release', action='store', required=False,
                              help='release value to use')
    build_parser.add_argument('--isolated', action='store_true', required=False,
                              help='isolated build')
    build_parser.add_argument('--arrangement-version', action='store', required=False,
                              type=int,
                              help='version of inner template to use; '
                              'versions < 6 are unused')
    build_parser.add_argument('--signing-intent', action='store', required=False,
                              help='override signing intent of ODCS composes')
    build_parser.add_argument("--compose-id", action='append', required=False,
                              dest="compose_ids", type=int, help="ODCS compose"
                              "used, may be used multiple times")
    build_parser.add_argument("--replace-dependency", action='append',
                              metavar="pkg_manager:name:version[:new_name]",
                              dest="dependency_replacements",
                              help="Cachito dependency replacement")

    build_source_container_parser = subparsers.add_parser(
        str_on_2_unicode_on_3('build-source-container'),
        help='build a source container image in OSBS'
    )
    build_source_container_parser.add_argument(
        "--sources-for-koji-build-nvr", action='store',
        metavar='N-V-R', help="koji build NVR"
    )
    build_source_container_parser.add_argument(
        "--sources-for-koji-build-id", action='store',
        type=int, metavar='ID',
        help="koji build ID"
    )
    build_source_container_parser.add_argument(
        "-c", "--component", action='store', required=True,
        help="component for source container"
    )
    build_source_container_parser.add_argument(
        "--build-json-dir", action="store", metavar="PATH",
        help="directory with build jsons"
    )
    build_source_container_parser.add_argument(
        "-t", "--target", action='store',
        help="koji target name"
    )
    build_source_container_parser.add_argument(
        "-u", "--user", action='store', required=True,
        help="prefix for docker image repository"
    )
    build_source_container_parser.add_argument(
        "--no-logs", action='store_true', required=False, default=False,
        help="don't print logs after submitting build"
    )
    build_source_container_parser.add_argument(
        "--cpu-limit", action='store', required=False,
        help="CPU limit (KCU)"
    )
    build_source_container_parser.add_argument(
        "--memory-limit", action='store', required=False,
        help="memory limit"
    )
    build_source_container_parser.add_argument(
        "--storage-limit", action='store', required=False,
        help="storage limit"
    )
    build_source_container_parser.add_argument(
        "--scratch", action='store_true', required=False,
        help="perform a scratch build"
    )
    build_source_container_parser.add_argument(
        '--arrangement-version', action='store', required=False,
        type=int,
        help='version of inner template to use; versions < 6 are unused'
    )
    build_source_container_parser.add_argument(
        '--signing-intent', action='store', required=False,
        help='override signing intent')
    build_source_container_parser.set_defaults(func=cmd_build_source_container)

    worker_group = build_parser.add_argument_group(title='arguments for --worker',
                                                   description='Required arguments for creating a '
                                                   'worker build')
    worker_group.add_argument('--platform', action='store', required=False,
                              help='platform name to use')
    worker_group.add_argument('--koji-upload-dir', action='store', required=False,
                              help='path for uploading to koji')

    orchestrator_group = build_parser.add_argument_group(title='arguments for --orchestrator',
                                                         description='Required arguments for '
                                                         'creating an orchestrator build')
    orchestrator_group.add_argument('--platforms', action='append', metavar='PLATFORM',
                                    help='name of each platform to use (only required for '
                                    'arrangement 5 or earlier; deprecated in arrangement 6+')
    orchestrator_group.add_argument('--skip-build', action='store_true', required=False,
                                    help="don't create build, but just modify settings"
                                         " for autorebuilds")

    build_parser.add_argument('--source-registry-uri', action='store', required=False,
                              help="set source registry for pulling parent image")

    build_type_group = build_parser.add_mutually_exclusive_group()
    build_type_group.add_argument("--worker", action="store_true", required=False,
                                  default=False, help="create worker build")
    build_type_group.add_argument("--orchestrator", action="store_true", required=False,
                                  default=True, help="create orchestrator build")

    group = build_parser.add_mutually_exclusive_group()
    group.add_argument("--build-from", action='store', required=False,
                       help="builder source to use, image:image_spec,"
                            "or imagestream:imagestream_spec")
    group.add_argument("--build-image", action='store', required=False,
                       help="builder image to use, will be replaced by build-from")
    group.add_argument("--build-imagestream", action='store', required=False,
                       help="builder imagestream to use (overrides build-image),"
                            "will be replaced by build-from")
    build_parser.set_defaults(func=cmd_build)

    get_build_image_id = subparsers.add_parser(str_on_2_unicode_on_3('get-build-image-id'),
                                               help='get build container image ID',
                                               description='get build container images for a '
                                               'build in a namespace')
    get_build_image_id.add_argument("BUILD_ID", help="build ID", nargs=1)
    get_build_image_id.set_defaults(func=cmd_get_build_image_id)

    backup_builder = subparsers.add_parser(str_on_2_unicode_on_3('backup-builder'),
                                           help='dump builder data (admin)',
                                           description='create backup of all OSBS data')
    backup_builder.add_argument("-f", "--filename",
                                help="name of the resulting tar.bz2 file (use - for stdout)")
    backup_builder.add_argument("--ignore-quota-errors", action='store_true',
                                help="ignore resourcequota errors")
    backup_builder.add_argument("--continue-on-error", action='store_true',
                                help="don't stop when backing up a resource fails")
    backup_builder.set_defaults(func=cmd_backup)

    restore_builder = subparsers.add_parser(str_on_2_unicode_on_3('restore-builder'),
                                            help='restore builder data (admin)',
                                            description='restore OSBS data from backup')
    restore_builder.add_argument("BACKUP_ARCHIVE",
                                 help="name of the tar.bz2 archive to restore (use - for stdin)")
    restore_builder.add_argument("--continue-on-error", action='store_true',
                                 help="don't stop when restoring a resource fails")
    restore_builder.add_argument("--ignore-quota-errors", action='store_true',
                                 help="ignore resourcequota errors")
    restore_builder.set_defaults(func=cmd_restore)

    token_url_builder = subparsers.add_parser(str_on_2_unicode_on_3('print-token-url'),
                                              description='print a url to oauth authentication '
                                              'page')
    token_url_builder.set_defaults(func=cmd_print_token_url)

    serviceaccount_builder = subparsers.add_parser(
        str_on_2_unicode_on_3('get-serviceaccount-token'),
        description='get auth token for serviceaccount')
    serviceaccount_builder.add_argument("--oc", help="display oc login command",
                                        action="store_true", default=False)
    serviceaccount_builder.add_argument("SERVICEACCOUNT",
                                        help="name of the service account")
    serviceaccount_builder.set_defaults(func=cmd_serviceaccount_token)

    parser.add_argument("--openshift-uri", action='store', metavar="URL",
                        help="openshift URL to remote API")
    parser.add_argument("--registry-uri", action='store', metavar="URL",
                        help="registry where images should be pushed")
    parser.add_argument("--source-registry-uri", action='store', metavar="URL",
                        help="registry with base images")
    parser.add_argument("--config", action='store', metavar="PATH",
                        help="path to configuration file, default %s" % DEFAULT_CONFIGURATION_FILE,
                        default=DEFAULT_CONFIGURATION_FILE)
    parser.add_argument("--instance", "-i", action='store', metavar="SECTION_NAME",
                        help="section within config for requested instance."
                             " If unspecified, osbs will load the section"
                             " named '%s'" % DEFAULT_CONFIGURATION_SECTION,
                        default=DEFAULT_CONFIGURATION_SECTION)
    parser.add_argument("--username", action='store',
                        help="name of user to use for Basic Authentication in OSBS")
    parser.add_argument("--password", action='store',
                        help="password to use for Basic Authentication in OSBS")
    parser.add_argument("--use-kerberos", action='store_true', default=None,
                        help="use kerberos for authentication")
    parser.add_argument("--client-cert", action='store',
                        help="path to client certificate in PEM format to use for authentication")
    parser.add_argument("--client-key", action='store',
                        help="path to key file for the certificate provided with --client-cert")
    parser.add_argument("--kerberos-keytab", action='store',
                        help="path to kerberos keytab to obtain credentials from")
    parser.add_argument("--kerberos-principal", action='store',
                        help="kerberos principal for the provided keytab")
    parser.add_argument("--kerberos-ccache", action='store',
                        help="path to credential cache to use instead of the default one")
    parser.add_argument("--verify-ssl", action='store_true', default=None,
                        help="verify CA on secure connections")
    parser.add_argument("--with-auth", action="store_true", dest="use_auth", default=None,
                        help="get and supply oauth token with every request")
    parser.add_argument("--without-auth", action="store_false", dest="use_auth", default=None,
                        help="don't supply oauth tokens to requests")
    parser.add_argument("--output", choices=["json", "text"], default="text",
                        help="pick output type (default=text)")
    parser.add_argument("--namespace", help="name of namespace to query against",
                        metavar="NAMESPACE", action="store")
    parser.add_argument("--capture-dir", metavar="DIR", action="store",
                        help="capture JSON responses and save them in DIR")
    parser.add_argument("--token", metavar="TOKEN", action="store",
                        help="OAuth 2.0 token")
    parser.add_argument("--token-file", metavar="TOKENFILE", action="store",
                        help="Read oauth 2.0 token from file")
    args = parser.parse_args()

    if getattr(args, 'func', None) is cmd_build_source_container:
        if not (args.sources_for_koji_build_id or args.sources_for_koji_build_nvr):
            parser.error(
                "at least one of --sources-for-koji-build-id and "
                "--sources-for-koji-build-nvr has to be specified"
            )

    return parser, args


def main():
    parser, args = cli()
    try:
        os_conf = Configuration(conf_file=args.config,
                                conf_section=args.instance,
                                cli_args=args)
        build_conf = Configuration(conf_file=args.config,
                                   conf_section=args.instance,
                                   cli_args=args)
    except OsbsException as ex:
        logger.error("Configuration error: %s", ex.message)
        return -1

    is_verbose = os_conf.get_verbosity()

    if args.quiet:
        set_logging(level=logging.WARNING)
    elif is_verbose:
        set_logging(level=logging.DEBUG)
        logger.debug("Logging level set to debug")
    else:
        set_logging(level=logging.INFO)

    osbs = OSBS(os_conf, build_conf)

    if args.capture_dir is not None:
        setup_json_capture(osbs, os_conf, args.capture_dir)

    return_value = -1
    try:
        return_value = args.func(args, osbs)
    except AttributeError:
        if hasattr(args, 'func'):
            raise
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("Quitting on user request.")
        return -1
    except OsbsNetworkException as ex:
        if is_verbose:
            raise
        else:
            logger.error("Network error at %s (%d): %s",
                         ex.url, ex.status_code, ex)
            return -1
    except OsbsAuthException as ex:
        if is_verbose:
            raise
        else:
            logger.error("Authentication failure: %s", ex)
            return -1
    except OsbsResponseException as ex:
        if is_verbose:
            raise
        else:
            if isinstance(ex.json, dict) and 'message' in ex.json:
                msg = ex.json['message']
            else:
                msg = str(ex)
            logger.error("Server returned error %s: %s", ex.status_code, msg)
            return -1
    except Exception as ex:  # pylint: disable=broad-except
        if is_verbose:
            raise
        else:
            logger.error("Exception caught: %s", repr(ex))
            return -1
    return return_value


if __name__ == '__main__':
    sys.exit(main())
