#!/usr/bin/python -tt
from __future__ import print_function, absolute_import, unicode_literals
import copy
import logging

import sys
import argparse
from osbs import set_logging
from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.constants import BUILD_JSON_STORE, DEFAULT_CONFIGURATION_FILE


logger = logging.getLogger('osbs')


def gracful_chain_get(d, *args):
    t = copy.deepcopy(d)
    for arg in args:
        try:
            t = t[arg]
        except (AttributeError, KeyError):
            return None
    return t


def cmd_list_builds(args, osbs):
    builds = osbs.list_builds()
    format_str = "{name:48} {status:16} {image:64}"
    print(format_str.format(**{"name": "BUILD NAME", "status": "STATUS", "image": "IMAGE NAME"}), file=sys.stderr)
    for build in builds['items']:
        image = gracful_chain_get(build, 'parameters', 'output', 'imageTag')
        if args.USER:
            if not image.startswith(args.USER + "/"):
                continue
        b = {
            "name": build['metadata']['name'],
            "status": build['status'],
            "image": image,
        }
        print(format_str.format(**b))


def cmd_get_build(args, osbs):
    build_json = osbs.get_build(args.BUILD_ID[0])
    # FIXME: pretty printing json could be a better idea
    template = """\
BUILD ID: {build_id}
STATUS: {status}
IMAGE: {image}
DATE: {date}

DOCKERFILE

{dockerfile}

BUILD LOGS

{logs}

PACKAGES

{packages}"""
    context = {
        "build_id": build_json['metadata']['name'],
        "status": build_json['status'],
        "image": gracful_chain_get(build_json, 'parameters', 'output', 'imageTag'),
        "date": build_json['metadata']['creationTimestamp'],
        "dockerfile": gracful_chain_get(build_json, 'metadata', 'labels', 'dockerfile'),
        "logs": gracful_chain_get(build_json, 'metadata', 'labels', 'logs'),
        "packages": gracful_chain_get(build_json, 'metadata', 'labels', 'rpm-packages'),
    }
    print(template.format(**context))


def cmd_prod_build(args, osbs):
    build_id = osbs.create_prod_build(
        git_uri=args.git_url,
        git_ref=args.git_commit,
        user=args.user,
        component=args.component,
        registry=args.registry,
        target=args.target,
        build_json_dir=args.build_json_dir
    )
    print("Build submitted (%s), watching logs (feel free to interrupt)" % build_id)
    for line in osbs.get_build_logs(build_id, follow=True):
        print(line)


def cli():
    parser = argparse.ArgumentParser(
        description="OpenShift Build Service client"
    )
    exclusive_group = parser.add_mutually_exclusive_group()
    exclusive_group.add_argument("--verbose", action="store_true")
    exclusive_group.add_argument("-q", "--quiet", action="store_true")

    subparsers = parser.add_subparsers(help='commands')

    list_builds_parser = subparsers.add_parser('list-builds', help='list builds in OSBS')
    list_builds_parser.add_argument("USER", help="list builds only for specified username",
                                    nargs="?")
    list_builds_parser.set_defaults(func=cmd_list_builds)

    get_build_parser = subparsers.add_parser('get-build', help='get info about build')
    get_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    get_build_parser.set_defaults(func=cmd_get_build)

    build_parser = subparsers.add_parser('build', help='build an image in OSBS')
    build_parser.add_argument("--build-json-dir", help="directory with build jsons",
                              default=BUILD_JSON_STORE, metavar="PATH", action="store")
    build_subparsers = build_parser.add_subparsers(help="build subcommands")

    prod_build_parser = build_subparsers.add_parser('prod', help='build an image in OSBS')

    prod_build_parser.add_argument("-g", "--git-url", action='store', metavar="URL",
                                   required=True, help="URL to git repo")
    prod_build_parser.add_argument("--git-commit", action='store', default="master",
                                   help="checkout this commit")
    prod_build_parser.add_argument("-c", "--component", action='store', required=True,
                                   help="name of component")
    prod_build_parser.add_argument("-t", "--target", action='store', required=True,
                                   help="koji target name")
    prod_build_parser.add_argument("-u", "--user", action='store', required=True,
                                   help="username (will be image prefix)")
    prod_build_parser.add_argument("-r", "--registry", action='store', required=True,
                                   help="registry where image should be pushed")
    prod_build_parser.set_defaults(func=cmd_prod_build)

    parser.add_argument("--openshift-uri", action='store', metavar="URL",
                        help="openshift URL to remote API")
    parser.add_argument("--kubelet-uri", action='store', metavar="URL",
                        help="kubelet URL to remote API")
    parser.add_argument("--config", action='store', metavar="PATH",
                        help="path to configuration file", default=DEFAULT_CONFIGURATION_FILE)
    args = parser.parse_args()
    return parser, args


def main():
    parser, args = cli()
    if args.verbose:
        set_logging(level=logging.DEBUG)
    elif args.quiet:
        set_logging(level=logging.WARNING)
    else:
        set_logging(level=logging.INFO)

    configuration = Configuration(conf_file=args.config, cli_args=args)
    osbs = OSBS(configuration)

    try:
        args.func(args, osbs)
    except AttributeError as ex:
        if hasattr(args, 'func'):
            raise
        else:
            parser.print_help()
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        if args.verbose:
            raise
        else:
            logger.error("Exception caught: %s", repr(ex))

if __name__ == '__main__':
    sys.exit(main())
