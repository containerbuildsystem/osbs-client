"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import

import json
import logging
import os
import sys
import time
import warnings
from functools import wraps

from .constants import SIMPLE_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE, PROD_WITH_SECRET_BUILD_TYPE
from osbs.build.build_request import BuildManager
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.constants import BUILD_RUNNING_STATES, PROD_BUILD_TYPE
from osbs.core import Openshift
from osbs.exceptions import OsbsException, OsbsValidationException
# import utils in this way, so that we can mock standalone functions with flexmock
from osbs import utils


# Decorator for API methods.
def osbsapi(func):
    @wraps(func)
    def catch_exceptions(*args, **kwargs):
        # XXX: remove this in the future
        if kwargs.pop("namespace", None):
            warnings.warn("OSBS.%s: the 'namespace' argument is no longer supported" % func.__name__)
        try:
            return func(*args, **kwargs)
        except OsbsException:
            # Re-raise OsbsExceptions
            raise
        except Exception as ex:
            # Convert anything else to OsbsException

            # Python 3 has implicit exception chaining and enhanced
            # reporting, so you get the original traceback as well as
            # the one originating here.
            # For Python 2, let's do that explicitly.
            raise OsbsException(cause=ex, traceback=sys.exc_info()[2])

    return catch_exceptions


logger = logging.getLogger(__name__)


class OSBS(object):
    """
    Note: all API methods return osbs.http.Response object. This is, due to historical
    reasons, untrue for list_builds and get_user, which return list of BuildResponse objects
    and dict respectively.
    """
    @osbsapi
    def __init__(self, openshift_configuration, build_configuration):
        """ """
        self.os_conf = openshift_configuration
        self.build_conf = build_configuration
        self.os = Openshift(openshift_api_url=self.os_conf.get_openshift_api_uri(),
                            openshift_api_version=self.os_conf.get_openshift_api_version(),
                            openshift_oauth_url=self.os_conf.get_openshift_oauth_api_uri(),
                            k8s_api_url=self.os_conf.get_k8s_api_uri(),
                            verbose=self.os_conf.get_verbosity(),
                            username=self.os_conf.get_username(),
                            password=self.os_conf.get_password(),
                            use_kerberos=self.os_conf.get_use_kerberos(),
                            client_cert=self.os_conf.get_client_cert(),
                            client_key=self.os_conf.get_client_key(),
                            kerberos_keytab=self.os_conf.get_kerberos_keytab(),
                            kerberos_principal=self.os_conf.get_kerberos_principal(),
                            kerberos_ccache=self.os_conf.get_kerberos_ccache(),
                            use_auth=self.os_conf.get_use_auth(),
                            verify_ssl=self.os_conf.get_verify_ssl(),
                            token=self.os_conf.get_oauth2_token(),
                            namespace=self.os_conf.get_namespace())
        self._bm = None

    # some calls might not need build manager so let's make it lazy
    @property
    def bm(self):
        if self._bm is None:
            self._bm = BuildManager(build_json_store=self.os_conf.get_build_json_store())
        return self._bm

    @osbsapi
    def list_builds(self, field_selector=None):
        """
        List builds with matching fields

        :param field_selector: str, field selector for Builds
        :return: BuildResponse list
        """

        response = self.os.list_builds(field_selector=field_selector)
        serialized_response = response.json()
        build_list = []
        for build in serialized_response["items"]:
            build_list.append(BuildResponse(build))
        return build_list

    @osbsapi
    def get_build(self, build_id):
        response = self.os.get_build(build_id)
        build_response = BuildResponse(response.json())
        return build_response

    @osbsapi
    def cancel_build(self, build_id):
        response = self.os.cancel_build(build_id)
        build_response = BuildResponse(response.json())
        return build_response

    @osbsapi
    def get_pod_for_build(self, build_id):
        """
        :return: PodResponse object for pod relating to the build
        """
        pods = self.os.list_pods(label='openshift.io/build.name=%s' % build_id)
        serialized_response = pods.json()
        pod_list = [PodResponse(pod) for pod in serialized_response["items"]]
        if not pod_list:
            raise OsbsException("No pod for build")
        elif len(pod_list) != 1:
            raise OsbsException("Only one pod expected but %d returned",
                                len(pod_list))
        return pod_list[0]

    @osbsapi
    def get_build_request(self, build_type=None):
        """
        return instance of BuildRequest according to specified build type

        :param build_type: str, name of build type
        :return: instance of BuildRequest
        """
        build_type = build_type or self.build_conf.get_build_type()
        build_request = self.bm.get_build_request_by_type(build_type=build_type)

        # Apply configured resource limits.
        cpu_limit = self.build_conf.get_cpu_limit()
        memory_limit = self.build_conf.get_memory_limit()
        storage_limit = self.build_conf.get_storage_limit()
        if (cpu_limit is not None or
                memory_limit is not None or
                storage_limit is not None):
            build_request.set_resource_limits(cpu=cpu_limit,
                                              memory=memory_limit,
                                              storage=storage_limit)

        return build_request

    @osbsapi
    def create_build_from_buildrequest(self, build_request):
        """
        render provided build_request and submit build from it

        :param build_request: instance of build.build_request.BuildRequest
        :return: instance of build.build_response.BuildResponse
        """
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())
        build = build_request.render()
        response = self.os.create_build(json.dumps(build))
        build_response = BuildResponse(response.json())
        return build_response

    def _get_running_builds_for_build_config(self, build_config_id):
        all_builds_for_bc = self.os.list_builds(build_config_id=build_config_id).json()['items']
        running = []
        for b in all_builds_for_bc:
            br = BuildResponse(b)
            if br.is_pending() or br.is_running():
                running.append(br)
        return running

    @staticmethod
    def _panic_msg_for_more_running_builds(self, build_config_name, builds):
        # this should never happen, but if it does, we want to know all the builds
        #  that were running at the time
        builds = ', '.join(['%s: %s' % (b.get_build_name(), b.status) for b in builds])
        msg = 'Multiple builds for %s running, can\'t proceed: %s' % \
            (build_config_name, builds)
        return msg

    def _create_build_config_and_build(self, build_request):
        # TODO: test this method more thoroughly
        build_json = build_request.render()
        api_version = build_json['apiVersion']
        if api_version != self.os_conf.get_openshift_api_version():
            raise OsbsValidationException("BuildConfig template has incorrect apiVersion (%s)" %
                                          api_version)

        build_config_name = build_json['metadata']['name']

        # check if a build already exists for this config; if so then raise
        running_builds = self._get_running_builds_for_build_config(build_config_name)
        rb_len = len(running_builds)
        if rb_len > 0:
            if rb_len == 1:
                rb = running_builds[0]
                msg = 'Build %s for %s in state %s, can\'t proceed.' % \
                    (rb.get_build_name(), build_config_name, rb.status)
            else:
                msg = self._panic_msg_for_more_running_builds(build_config_name, running_builds)
            raise OsbsException(msg)

        try:
            # see if there's already a build config
            existing_bc = self.os.get_build_config(build_config_name)
        except OsbsException:
            # doesn't exist
            existing_bc = None

        build = None
        if existing_bc is not None:
            utils.buildconfig_update(existing_bc, build_json)
            logger.debug('build config for %s already exists, updating...', build_config_name)
            self.os.update_build_config(build_config_name, json.dumps(existing_bc))
        else:
            # if it doesn't exist, then create it
            logger.debug('build config for %s doesn\'t exist, creating...', build_config_name)
            bc = self.os.create_build_config(json.dumps(build_json)).json()
            # if there's an "ImageChangeTrigger" on the BuildConfig and "From" is of type
            #  "ImageStreamTag", the build will be scheduled automatically
            #  see https://github.com/projectatomic/osbs-client/issues/205
            if build_request.is_auto_instantiated():
                prev_version = bc['status']['lastVersion']
                build_id = self.os.wait_for_new_build_config_instance(build_config_name,
                                                                      prev_version)
                build = BuildResponse(self.os.get_build(build_id).json())

        if build is None:
            response = self.os.start_build(build_config_name)
            build = BuildResponse(response.json())
        return build

    @osbsapi
    def create_prod_build(self, git_uri, git_ref,
                          git_branch,  # may be None
                          user, component,
                          target,      # may be None
                          architecture=None, yum_repourls=None,
                          **kwargs):
        """
        Create a production build

        :param git_uri: str, URI of git repository
        :param git_ref: str, reference to commit
        :param git_branch: str, branch name (may be None)
        :param user: str, user name
        :param component: str, component name
        :param target: str, koji target (may be None)
        :param architecture: str, build architecture
        :param yum_repourls: list, URLs for yum repos
        :return: BuildResponse instance
        """
        df_parser = utils.get_df_parser(git_uri, git_ref, git_branch=git_branch)
        build_request = self.get_build_request(PROD_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            git_branch=git_branch,
            user=user,
            component=component,
            build_image=self.build_conf.get_build_image(),
            build_imagestream=self.build_conf.get_build_imagestream(),
            base_image=df_parser.baseimage,
            name_label=df_parser.labels['Name'],
            registry_uris=self.build_conf.get_registry_uris(),
            source_registry_uri=self.build_conf.get_source_registry_uri(),
            registry_api_versions=self.build_conf.get_registry_api_versions(),
            openshift_uri=self.os_conf.get_openshift_base_uri(),
            builder_openshift_url=self.os_conf.get_builder_openshift_url(),
            kojiroot=self.build_conf.get_kojiroot(),
            kojihub=self.build_conf.get_kojihub(),
            sources_command=self.build_conf.get_sources_command(),
            koji_target=target,
            koji_certs_secret=self.build_conf.get_koji_certs_secret(),
            architecture=architecture,
            vendor=self.build_conf.get_vendor(),
            build_host=self.build_conf.get_build_host(),
            authoritative_registry=self.build_conf.get_authoritative_registry(),
            distribution_scope=self.build_conf.get_distribution_scope(),
            yum_repourls=yum_repourls,
            proxy=self.build_conf.get_proxy(),
            pulp_secret=self.build_conf.get_pulp_secret(),
            pdc_secret=self.build_conf.get_pdc_secret(),
            pdc_url=self.build_conf.get_pdc_url(),
            smtp_uri=self.build_conf.get_smtp_uri(),
            use_auth=self.build_conf.get_builder_use_auth(),
            pulp_registry=self.os_conf.get_pulp_registry(),
            nfs_server_path=self.os_conf.get_nfs_server_path(),
            nfs_dest_dir=self.build_conf.get_nfs_destination_dir(),
            git_push_url=self.build_conf.get_git_push_url(),
            git_push_username=self.build_conf.get_git_push_username(),
            builder_build_json_dir=self.build_conf.get_builder_build_json_store(),
            scratch=kwargs.get("scratch", False)
        )
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())
        response = self._create_build_config_and_build(build_request)
        logger.debug(response.json)
        return response

    @osbsapi
    def create_prod_with_secret_build(self, git_uri, git_ref, git_branch, user, component,
                                      target, architecture=None, yum_repourls=None, **kwargs):
        return self.create_prod_build(git_uri, git_ref, git_branch, user, component, target,
                                      architecture, yum_repourls=yum_repourls, **kwargs)

    @osbsapi
    def create_prod_without_koji_build(self, git_uri, git_ref, git_branch, user, component,
                                       architecture=None, yum_repourls=None, **kwargs):
        return self.create_prod_build(git_uri, git_ref, git_branch, user, component, None,
                                      architecture, yum_repourls=yum_repourls, **kwargs)

    @osbsapi
    def create_simple_build(self, git_uri, git_ref, user, component, tag,
                            yum_repourls=None, **kwargs):
        build_request = self.get_build_request(SIMPLE_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            user=user,
            component=component,
            tag=tag,
            build_image=self.build_conf.get_build_image(),
            registry_uris=self.build_conf.get_registry_uris(),
            source_registry_uri=self.build_conf.get_source_registry_uri(),
            openshift_uri=self.os_conf.get_openshift_base_uri(),
            builder_openshift_url=self.os_conf.get_builder_openshift_url(),
            yum_repourls=yum_repourls,
            proxy=self.build_conf.get_proxy(),
            use_auth=self.build_conf.get_builder_use_auth(),
        )
        build_request.set_openshift_required_version(self.os_conf.get_openshift_required_version())
        response = self._create_build_config_and_build(build_request)
        logger.debug(response.json)
        return response

    @osbsapi
    def create_build(self, **kwargs):
        """
        take input args, create build request from provided build type and submit the build

        :param kwargs: keyword args for build
        :return: instance of BuildRequest
        """
        build_type = self.build_conf.get_build_type()
        if build_type in (PROD_BUILD_TYPE,
                          PROD_WITHOUT_KOJI_BUILD_TYPE,
                          PROD_WITH_SECRET_BUILD_TYPE):
            kwargs.setdefault('git_branch', None)
            kwargs.setdefault('target', None)
            return self.create_prod_build(**kwargs)
        elif build_type == SIMPLE_BUILD_TYPE:
            # Only Prod Build type cares about potential koji scratch builds
            try:
                kwargs.pop("scratch")
            except KeyError:
                pass
            return self.create_simple_build(**kwargs)
        elif build_type == PROD_WITH_SECRET_BUILD_TYPE:
            return self.create_prod_with_secret_build(**kwargs)
        else:
            raise OsbsException("Unknown build type: '%s'" % build_type)

    @osbsapi
    def get_build_logs(self, build_id, follow=False, build_json=None, wait_if_missing=False):
        """
        provide logs from build

        :param build_id: str
        :param follow: bool, fetch logs as they come?
        :param build_json: dict, to save one get-build query
        :param wait_if_missing: bool, if build doesn't exist, wait
        :return: None, str or iterator
        """
        return self.os.logs(build_id, follow=follow, build_json=build_json,
                            wait_if_missing=wait_if_missing)

    @osbsapi
    def get_docker_build_logs(self, build_id, decode_logs=True, build_json=None):
        """
        get logs provided by "docker build"

        :param build_id: str
        :param decode_logs: bool, docker by default output logs in simple json structure:
            { "stream": "line" }
            if this arg is set to True, it decodes logs to human readable form
        :param build_json: dict, to save one get-build query
        :return: str
        """
        if not build_json:
            build = self.os.get_build(build_id)
            build_response = BuildResponse(build.json())
        else:
            build_response = BuildResponse(build_json)

        if build_response.is_finished():
            logs = build_response.get_logs(decode_logs=decode_logs)
            return logs
        logger.warning("build haven't finished yet")

    @osbsapi
    def wait_for_build_to_finish(self, build_id):
        response = self.os.wait_for_build_to_finish(build_id)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def wait_for_build_to_get_scheduled(self, build_id):
        response = self.os.wait_for_build_to_get_scheduled(build_id)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def update_labels_on_build(self, build_id, labels):
        response = self.os.update_labels_on_build(build_id, labels)
        return response

    @osbsapi
    def set_labels_on_build(self, build_id, labels):
        response = self.os.set_labels_on_build(build_id, labels)
        return response

    @osbsapi
    def update_labels_on_build_config(self, build_config_id, labels):
        response = self.os.update_labels_on_build_config(build_config_id, labels)
        return response

    @osbsapi
    def set_labels_on_build_config(self, build_config_id, labels):
        response = self.os.set_labels_on_build_config(build_config_id, labels)
        return response

    @osbsapi
    def update_annotations_on_build(self, build_id, annotations):
        return self.os.update_annotations_on_build(build_id, annotations)

    @osbsapi
    def set_annotations_on_build(self, build_id, annotations):
        return self.os.set_annotations_on_build(build_id, annotations)

    @osbsapi
    def import_image(self, name):
        """
        Import image tags from a Docker registry into an ImageStream

        :return: bool, whether new tags were imported
        """

        return self.os.import_image(name)

    @osbsapi
    def get_token(self):
        return self.os.get_oauth_token()

    @osbsapi
    def get_user(self, username="~"):
        return self.os.get_user(username).json()

    @osbsapi
    def get_serviceaccount_tokens(self, username="~"):
        return self.os.get_serviceaccount_tokens(username)

    @osbsapi
    def get_image_stream(self, stream_id):
        return self.os.get_image_stream(stream_id)

    @osbsapi
    def create_image_stream(self, name, docker_image_repository,
                            insecure_registry=False):
        """
        Create an ImageStream object

        Raises exception on error

        :param name: str, name of ImageStream
        :param docker_image_repository: str, pull spec for docker image
               repository
        :param insecure_registry: bool, whether plain HTTP should be used
        :return: response
        """
        img_stream_file = os.path.join(self.os_conf.get_build_json_store(), 'image_stream.json')
        stream = json.load(open(img_stream_file))
        stream['metadata']['name'] = name
        stream['spec']['dockerImageRepository'] = docker_image_repository
        if insecure_registry:
            stream['metadata'].setdefault('annotations', {})
            insecure_annotation = 'openshift.io/image.insecureRepository'
            stream['metadata']['annotations'][insecure_annotation] = 'true'

        return self.os.create_image_stream(json.dumps(stream))

    def _load_quota_json(self, quota_name=None):
        quota_file = os.path.join(self.os_conf.get_build_json_store(),
                                  'pause_quota.json')
        with open(quota_file) as fp:
            quota_json = json.load(fp)

        if quota_name:
            quota_json['metadata']['name'] = quota_name

        return quota_json['metadata']['name'], quota_json

    @osbsapi
    def pause_builds(self, quota_name=None):
        # First, set quota so 0 pods are allowed to be running
        quota_name, quota_json = self._load_quota_json(quota_name)
        self.os.create_resource_quota(quota_name, quota_json)

        # Now wait for running builds to finish
        while True:
            field_selector = ','.join(['status=%s' % status.capitalize()
                                       for status in BUILD_RUNNING_STATES])
            builds = self.list_builds(field_selector)

            # Double check builds are actually in running state.
            running_builds = [build for build in builds if build.is_running()]

            if not running_builds:
                break

            name = running_builds[0].get_build_name()
            logger.info("waiting for build to finish: %s", name)
            self.wait_for_build_to_finish(name)

    @osbsapi
    def resume_builds(self, quota_name=None):
        quota_name, _ = self._load_quota_json(quota_name)
        self.os.delete_resource_quota(quota_name)

    # implements subset of OpenShift's export logic in pkg/cmd/cli/cmd/exporter.go
    @staticmethod
    def _prepare_resource(resource_type, resource):
        utils.graceful_chain_del(resource, 'metadata', 'resourceVersion')

        if resource_type == 'buildconfigs':
            utils.graceful_chain_del(resource, 'status', 'lastVersion')

            triggers = utils.graceful_chain_get(resource, 'spec', 'triggers') or ()
            for t in triggers:
                utils.graceful_chain_del(t, 'imageChange', 'lastTrigerredImageID')

    @osbsapi
    def dump_resource(self, resource_type):
        return self.os.dump_resource(resource_type).json()

    @osbsapi
    def restore_resource(self, resource_type, resources, continue_on_error=False):
        nfailed = 0
        for r in resources["items"]:
            name = utils.graceful_chain_get(r, 'metadata', 'name') or '(no name)'
            logger.debug("restoring %s/%s", resource_type, name)
            try:
                self._prepare_resource(resource_type, r)
                self.os.restore_resource(resource_type, r)
            except Exception:
                if continue_on_error:
                    logger.exception("failed to restore %s/%s", resource_type, name)
                    nfailed += 1
                else:
                    raise

        if continue_on_error:
            ntotal = len(resources["items"])
            logger.info("restored %s/%s %s", ntotal - nfailed, ntotal, resource_type)

    @osbsapi
    def get_compression_extension(self):
        """
        Find the filename extension for the 'docker save' output, which
        may or may not be compressed.

        Raises OsbsValidationException if the extension cannot be
        determined due to a configuration error.

        :returns: str including leading dot, or else None if no compression
        """

        build_type = self.build_conf.get_build_type()
        build_request = self.bm.get_build_request_by_type(build_type=build_type)
        inner = build_request.inner_template
        postbuild_plugins = inner.get('postbuild_plugins', [])
        for plugin in postbuild_plugins:
            if plugin.get('name') == 'compress':
                args = plugin.get('args', {})
                method = args.get('method', 'gzip')
                if method == 'gzip':
                    return '.gz'
                elif method == 'lzma':
                    return '.xz'
                raise OsbsValidationException("unknown compression method '%s'"
                                              % method)

        return None

    @osbsapi
    def list_resource_quotas(self):
        return self.os.list_resource_quotas().json()

    @osbsapi
    def get_resource_quota(self, quota_name):
        return self.os.get_resource_quota(quota_name).json()
