"""
Copyright (c) 2015-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals
import collections.abc

import json
import logging
import pkg_resources

import sys
import argparse
from osbs import set_logging
from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.constants import (DEFAULT_CONFIGURATION_FILE, DEFAULT_CONF_BINARY_SECTION,
                            DEFAULT_CONF_SOURCE_SECTION)
from osbs.exceptions import (OsbsNetworkException, OsbsException, OsbsAuthException,
                             OsbsResponseException)
from osbs.utils import UserWarningsStore

logger = logging.getLogger('osbs')


def _print_pipeline_run_logs(pipeline_run, user_warnings_store):
    """
    prints pipeline run logs

    :return: bool, True if logs are correctly returned,
                   False when reading logs fails or is not iterable
    """
    pipeline_run_name = pipeline_run.pipeline_run_name

    pipeline_run_logs = pipeline_run.get_logs(follow=True, wait=True)
    if not isinstance(pipeline_run_logs, collections.abc.Iterable):
        logger.error("'%s' is not iterable; can't display logs", pipeline_run_name)
        return False
    print(f"Pipeline run created ({pipeline_run_name}), watching logs (feel free to interrupt)")
    try:
        for _, line in pipeline_run_logs:
            if user_warnings_store.is_user_warning(line):
                user_warnings_store.store(line)
                continue

            print('{!r}'.format(line))
        return True
    except Exception as ex:
        logger.error("Error during fetching logs for pipeline run %s: %s",
                     pipeline_run_name, repr(ex))
        return False


def _get_build_metadata(pipeline_run, user_warnings_store):
    output = {
        "pipeline_run": {
            "name": pipeline_run.pipeline_run_name,
            "status": pipeline_run.status_reason,
            "info": {}
        },
        "results": {
            "user_warnings": [],
            "repositories": {},
            "error_msg": "",
        },
    }

    if pipeline_run.has_succeeded():
        info = pipeline_run.get_info()
        output['pipeline_run']['info'] = info
        annotations = info['metadata']['annotations']
        str_repositories = annotations.get('repositories', '{}')
        all_repositories = json.loads(str_repositories)
        output['results']['repositories'] = all_repositories
    else:
        output['results']['error_msg'] = pipeline_run.get_error_message()

    if user_warnings_store:
        output['results']['user_warnings'] = list(user_warnings_store)

    return output


def print_output(pipeline_run, export_metadata_file=None):
    user_warnings_store = UserWarningsStore()
    get_logs_passed = _print_pipeline_run_logs(pipeline_run, user_warnings_store)
    pipeline_run.wait_for_finish()
    build_metadata = _get_build_metadata(pipeline_run, user_warnings_store)
    _display_pipeline_run_summary(build_metadata)

    if export_metadata_file:
        with open(export_metadata_file, "w") as f:
            json.dump(build_metadata, f)

    if not get_logs_passed and pipeline_run.has_not_finished():
        pipeline_run_name = pipeline_run.pipeline_run_name
        try:
            logger.debug("Will try to cancel pipeline run: %s", pipeline_run_name)
            pipeline_run.cancel_pipeline_run()
        except Exception as ex:
            logger.error("Error during canceling pipeline run %s: %s", pipeline_run_name, repr(ex))


def cmd_build(args):
    if args.instance is None:
        conf_section = DEFAULT_CONF_BINARY_SECTION
    else:
        conf_section = args.instance
    os_conf = Configuration(conf_file=args.config,
                            conf_section=conf_section,
                            cli_args=args)
    osbs = OSBS(os_conf)

    build_kwargs = {
        'git_uri': osbs.os_conf.get_git_uri(),
        'git_ref': osbs.os_conf.get_git_ref(),
        'git_branch': osbs.os_conf.get_git_branch(),
        'user': osbs.os_conf.get_user(),
        'tag': osbs.os_conf.get_tag(),
        'target': osbs.os_conf.get_koji_target(),
        'yum_repourls': osbs.os_conf.get_yum_repourls(),
        'dependency_replacements': osbs.os_conf.get_dependency_replacements(),
        'scratch': args.scratch,
        'platforms': args.platforms,
        'release': args.release,
        'koji_parent_build': args.koji_parent_build,
        'isolated': args.isolated,
        'signing_intent': args.signing_intent,
        'compose_ids': args.compose_ids,
        'operator_csv_modifications_url': args.operator_csv_modifications_url,
    }
    if args.userdata:
        build_kwargs['userdata'] = json.loads(args.userdata)
    if osbs.os_conf.get_flatpak():
        build_kwargs['flatpak'] = True

    pipeline_run = osbs.create_binary_container_pipeline_run(**build_kwargs)

    print_output(pipeline_run, export_metadata_file=args.export_metadata_file)

    return_val = -1

    if pipeline_run.has_succeeded():
        return_val = 0
    cleanup_used_resources = osbs.os_conf.get_cleanup_used_resources()
    if cleanup_used_resources:
        try:
            logger.info("pipeline run removed: %s", pipeline_run.remove_pipeline_run())
        except OsbsResponseException:
            logger.error("failed to remove pipeline run %s", pipeline_run.pipeline_run_name)
            raise
    return return_val


def cmd_build_source_container(args):
    if args.instance is None:
        conf_section = DEFAULT_CONF_SOURCE_SECTION
    else:
        conf_section = args.instance
    os_conf = Configuration(conf_file=args.config,
                            conf_section=conf_section,
                            cli_args=args)
    osbs = OSBS(os_conf)

    build_kwargs = {
        'user': osbs.os_conf.get_user(),
        'target': osbs.os_conf.get_koji_target(),
        'scratch': args.scratch,
        'signing_intent': args.signing_intent,
        'sources_for_koji_build_nvr': args.sources_for_koji_build_nvr,
        'sources_for_koji_build_id': args.sources_for_koji_build_id,
        'component': args.component,
    }
    if args.userdata:
        build_kwargs['userdata'] = json.loads(args.userdata)

    pipeline_run = osbs.create_source_container_pipeline_run(**build_kwargs)

    print_output(pipeline_run, export_metadata_file=args.export_metadata_file)

    return_val = -1

    if pipeline_run.has_succeeded():
        return_val = 0
    cleanup_used_resources = osbs.os_conf.get_cleanup_used_resources()
    if cleanup_used_resources:
        try:
            logger.info("pipeline run removed: %s", pipeline_run.remove_pipeline_run())
        except OsbsResponseException:
            logger.error("failed to remove pipeline run %s", pipeline_run.pipeline_run_name)
            raise
    return return_val


def _display_pipeline_run_summary(build_metadata):
    output = [
        "",  # Empty line for cleaner display
        "pipeline run {} is {}".format(
            build_metadata['pipeline_run']['name'],
            build_metadata['pipeline_run']['status']
        )
    ]
    results = build_metadata['results']

    for kind, repositories in results['repositories'].items():
        if not repositories:
            continue
        output.append('{} repositories:'.format(kind))
        for repository in repositories:
            output.append('\t{}'.format(repository))

    user_warnings = results['user_warnings']
    if user_warnings:
        output.append("")
        output.append('user warnings:')
        for user_warning in user_warnings:
            output.append('\t{}'.format(user_warning))

    error_msg = results['error_msg']
    if error_msg:
        output.append("")
        output.append(error_msg)

    for line in output:
        print(line)


def cli():
    try:
        version = pkg_resources.get_distribution("osbs-client").version
    except pkg_resources.DistributionNotFound:
        version = "GIT"

    parser = argparse.ArgumentParser(
        description="OpenShift Build Service client"
    )
    exclusive_group = parser.add_mutually_exclusive_group()
    exclusive_group.add_argument("--verbose", action="store_true", default=None)
    exclusive_group.add_argument("-q", "--quiet", action="store_true")
    exclusive_group.add_argument("-V", "--version", action="version", version=version)

    subparsers = parser.add_subparsers(help='commands')

    build_parser = subparsers.add_parser('build', help='build an image in OSBS')
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
    build_parser.add_argument("--add-yum-repo", action='append', metavar="URL",
                              dest="yum_repourls", help="URL of yum repo file")
    build_parser.add_argument("--scratch", action='store_true', required=False,
                              help="perform a scratch build")
    build_parser.add_argument('--koji-parent-build', action='store', required=False,
                              help='overwrite parent image with image from koji build')
    build_parser.add_argument('--release', action='store', required=False,
                              help='release value to use')
    build_parser.add_argument('--isolated', action='store_true', required=False,
                              help='isolated build')
    build_parser.add_argument('--signing-intent', action='store', required=False,
                              help='override signing intent of ODCS composes')
    build_parser.add_argument("--compose-id", action='append', required=False,
                              dest="compose_ids", type=int, help="ODCS compose"
                              "used, may be used multiple times")
    build_parser.add_argument("--replace-dependency", action='append',
                              metavar="pkg_manager:name:version[:new_name]",
                              dest="dependency_replacements",
                              help="Cachito dependency replacement")
    build_parser.add_argument("--operator-csv-modifications-url", action='store', required=False,
                              dest='operator_csv_modifications_url', metavar='URL',
                              help="URL to JSON file with operator CSV modification")
    build_parser.add_argument('--platforms', action='append', metavar='PLATFORM',
                              help='name of each platform to use (deprecated)')
    build_parser.add_argument('--source-registry-uri', action='store', required=False,
                              help="set source registry for pulling parent image")
    build_parser.add_argument("--userdata", required=False,
                              help="JSON dictionary of user defined custom metadata")
    build_parser.set_defaults(func=cmd_build)

    build_source_container_parser = subparsers.add_parser(
        'build-source-container', help='build a source container image in OSBS'
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
    # most likely can be removed, source build should get component name from binary build OSBS2 TBD
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
        "--scratch", action='store_true', required=False,
        help="perform a scratch build"
    )
    build_source_container_parser.add_argument(
        '--signing-intent', action='store', required=False,
        help='override signing intent')
    build_source_container_parser.add_argument(
        '--userdata', required=False,
        help='JSON dictionary of user defined custom metadata')
    build_source_container_parser.set_defaults(func=cmd_build_source_container)

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
                             " If unspecified, osbs will load the section based on the build type"
                             " named '%s' or '%s'" % (DEFAULT_CONF_BINARY_SECTION,
                                                      DEFAULT_CONF_SOURCE_SECTION))
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
    parser.add_argument("--namespace", help="name of namespace to query against",
                        metavar="NAMESPACE", action="store")
    parser.add_argument("--capture-dir", metavar="DIR", action="store",
                        help="capture JSON responses and save them in DIR")
    parser.add_argument("--token", metavar="TOKEN", action="store",
                        help="OAuth 2.0 token")
    parser.add_argument("--token-file", metavar="TOKENFILE", action="store",
                        help="Read oauth 2.0 token from file")
    parser.add_argument("--export-metadata-file", metavar="FILE", action="store",
                        help="Export build metadata as JSON file")
    args = parser.parse_args()

    if getattr(args, 'func', None) is cmd_build_source_container:
        if not (args.sources_for_koji_build_id or args.sources_for_koji_build_nvr):
            parser.error(
                "at least one of --sources-for-koji-build-id and "
                "--sources-for-koji-build-nvr has to be specified"
            )

    if getattr(args, 'func', None) is cmd_build:
        if args.operator_csv_modifications_url and not args.isolated:
            parser.error("Only --isolated builds support option --operator-csv-modifications-url")

    return parser, args


def main():
    parser, args = cli()

    try:
        os_conf = Configuration(conf_file=args.config,
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

    return_value = -1
    try:
        return_value = args.func(args)
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
