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
from functools import wraps

from .constants import SIMPLE_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE, PROD_WITH_SECRET_BUILD_TYPE
from osbs.build.build_request import BuildManager
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.constants import DEFAULT_NAMESPACE, PROD_BUILD_TYPE
from osbs.core import Openshift
from osbs.exceptions import OsbsException, OsbsValidationException
# import utils in this way, so that we can mock standalone functions with flexmock
from osbs import utils


# Decorator for API methods.
def osbsapi(func):
    @wraps(func)
    def catch_exceptions(*args, **kwargs):
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
                            verify_ssl=self.os_conf.get_verify_ssl())
        self._bm = None

    # some calls might not need build manager so let's make it lazy
    @property
    def bm(self):
        if self._bm is None:
            self._bm = BuildManager(build_json_store=self.os_conf.get_build_json_store())
        return self._bm

    @osbsapi
    def list_builds(self, namespace=DEFAULT_NAMESPACE):
        response = self.os.list_builds(namespace=namespace)
        serialized_response = response.json()
        build_list = []
        for build in serialized_response["items"]:
            build_list.append(BuildResponse(None, build))
        return build_list

    @osbsapi
    def get_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.os.get_build(build_id, namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def cancel_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.os.cancel_build(build_id, namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def get_pod_for_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        """
        :return: PodResponse object for pod relating to the build
        """
        pods = self.os.list_pods(label='openshift.io/build.name=%s' % build_id,
                                 namespace=namespace)
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
    def create_build_from_buildrequest(self, build_request, namespace=DEFAULT_NAMESPACE):
        """
        render provided build_request and submit build from it

        :param build_request: instance of build.build_request.BuildRequest
        :param namespace: str, place/context where the build should be executed
        :return: instance of build.build_response.BuildResponse
        """
        build = build_request.render()
        response = self.os.create_build(json.dumps(build), namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    def _get_running_builds_for_build_config(self, build_config_id, namespace=DEFAULT_NAMESPACE):
        all_builds_for_bc = self.os.list_builds(
            build_config_id=build_config_id,
            namespace=namespace).json()['items']
        running = []
        for b in all_builds_for_bc:
            br = BuildResponse(request=None, build_json=b)
            if br.is_pending() or br.is_running():
                running.append(br)
        return running

    def _poll_for_builds_from_buildconfig(self, build_config_id, namespace=DEFAULT_NAMESPACE):
        # try polling for 60 seconds and then fail if build doesn't appear
        deadline = int(time.time()) + 60
        while int(time.time()) < deadline:
            logger.debug('polling for build from BuildConfig "%s"' % build_config_id)
            builds = self._get_running_builds_for_build_config(build_config_id, namespace)
            if len(builds) > 0:
                return builds
            # wait for 5 seconds before trying again
            time.sleep(5)

        raise OsbsException('Waited for new build from "%s", but none was automatically created' %
                            build_config_id)

    def _panic_msg_for_more_running_builds(self, build_config_name, builds):
        # this should never happen, but if it does, we want to know all the builds
        #  that were running at the time
        builds = ', '.join(['%s: %s' % (b.get_build_name(), b.status) for b in builds])
        msg = 'Multiple builds for %s running, can\'t proceed: %s' % \
            (build_config_name, builds)
        return msg

    def _create_build_config_and_build(self, build_request, namespace):
        # TODO: test this method more thoroughly
        build_json = build_request.render()
        apiVersion = build_json['apiVersion']
        if apiVersion != self.os_conf.get_openshift_api_version():
            raise OsbsValidationException("BuildConfig template has incorrect apiVersion (%s)" %
                                          apiVersion)

        build_config_name = build_json['metadata']['name']

        # check if a build already exists for this config; if so then raise
        running_builds = self._get_running_builds_for_build_config(build_config_name, namespace)
        rb_len = len(running_builds)
        if rb_len > 0:
            if rb_len == 1:
                rb = running_builds[0]
                msg = 'Build %s for %s in state %s, can\'t proceed.' % \
                    (rb.get_build_name(), build_config_name, rb.status)
            else:
                msg = self._panic_msg_for_more_running_builds(build_config_name, running_builds)
            raise OsbsException(msg)

        existing_bc = None
        try:
            # see if there's already a build config
            existing_bc = self.os.get_build_config(build_config_name)
        except OsbsException:
            pass  # doesn't exist => do nothing

        build = None
        if existing_bc is not None:
            utils.deep_update(existing_bc, build_json)
            logger.debug('build config for %s already exists, updating...', build_config_name)
            self.os.update_build_config(build_config_name, json.dumps(existing_bc), namespace)
        else:
            # if it doesn't exist, then create it
            logger.debug('build config for %s doesn\'t exist, creating...', build_config_name)
            self.os.create_build_config(json.dumps(build_json), namespace=namespace)
            # if there's an "ImageChangeTrigger" on the BuildConfig and "From" is of type
            #  "ImageStreamTag", the build will be scheduled automatically
            #  see https://github.com/projectatomic/osbs-client/issues/205
            if build_request.is_auto_instantiated():
                builds = self._poll_for_builds_from_buildconfig(build_config_name, namespace)
                if len(builds) > 0:
                    if len(builds) > 1:
                        raise OsbsException(
                            self._panic_msg_for_more_running_builds(build_config_name, builds))
                    else:
                        build = builds[0].request
        if build is None:
            build = self.os.start_build(build_config_name, namespace=namespace)
        return build

    @osbsapi
    def create_prod_build(self, git_uri, git_ref, git_branch, user, component, target,
                          architecture, yum_repourls=None, git_push_url=None,
                          namespace=DEFAULT_NAMESPACE, **kwargs):
        df_parser = utils.get_df_parser(git_uri, git_ref, git_branch)
        build_request = self.get_build_request(PROD_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            git_branch=git_branch,
            user=user,
            component=component,
            base_image=df_parser.baseimage,
            name_label=df_parser.labels['Name'],
            registry_uri=self.build_conf.get_registry_uri(),
            openshift_uri=self.os_conf.get_openshift_base_uri(),
            kojiroot=self.build_conf.get_kojiroot(),
            kojihub=self.build_conf.get_kojihub(),
            sources_command=self.build_conf.get_sources_command(),
            koji_target=target,
            architecture=architecture,
            vendor=self.build_conf.get_vendor(),
            build_host=self.build_conf.get_build_host(),
            authoritative_registry=self.build_conf.get_authoritative_registry(),
            yum_repourls=yum_repourls,
            source_secret=self.build_conf.get_source_secret(),
            use_auth=self.build_conf.get_use_auth(),
            pulp_registry=self.os_conf.get_pulp_registry(),
            nfs_server_path=self.os_conf.get_nfs_server_path(),
            nfs_dest_dir=self.build_conf.get_nfs_destination_dir(),
            git_push_url=self.build_conf.get_git_push_url(),
            git_push_username=self.build_conf.get_git_push_username(),
        )
        response = self._create_build_config_and_build(build_request, namespace)
        build_response = BuildResponse(response)
        logger.debug(build_response.json)
        return build_response

    @osbsapi
    def create_prod_with_secret_build(self, git_uri, git_ref, git_branch, user, component,
                                      target, architecture, yum_repourls=None,
                                      namespace=DEFAULT_NAMESPACE, **kwargs):
        return self.create_prod_build(git_uri, git_ref, git_branch, user, component, target,
                                      architecture, yum_repourls=yum_repourls,
                                      namespace=namespace, **kwargs)

    @osbsapi
    def create_prod_without_koji_build(self, git_uri, git_ref, git_branch, user, component,
                                       architecture, yum_repourls=None,
                                       namespace=DEFAULT_NAMESPACE, **kwargs):
        return self.create_prod_build(git_uri, git_ref, git_branch, user, component, None,
                                      architecture, yum_repourls=yum_repourls,
                                      namespace=namespace, **kwargs)

    @osbsapi
    def create_simple_build(self, git_uri, git_ref, user, component, yum_repourls=None,
                            namespace=DEFAULT_NAMESPACE, **kwargs):
        build_request = self.get_build_request(SIMPLE_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            user=user,
            component=component,
            registry_uri=self.build_conf.get_registry_uri(),
            openshift_uri=self.os_conf.get_openshift_base_uri(),
            yum_repourls=yum_repourls,
            use_auth=self.build_conf.get_use_auth(),
        )
        response = self._create_build_config_and_build(build_request, namespace)
        build_response = BuildResponse(response)
        logger.debug(build_response.json)
        return build_response

    @osbsapi
    def create_build(self, namespace=DEFAULT_NAMESPACE, **kwargs):
        """
        take input args, create build request from provided build type and submit the build

        :param namespace: str, place/context where the build should be executed
        :param kwargs: keyword args for build
        :return: instance of BuildRequest
        """
        build_type = self.build_conf.get_build_type()
        if build_type in (PROD_BUILD_TYPE,
                          PROD_WITHOUT_KOJI_BUILD_TYPE,
                          PROD_WITH_SECRET_BUILD_TYPE):
            return self.create_prod_build(namespace=namespace, **kwargs)
        elif build_type == SIMPLE_BUILD_TYPE:
            return self.create_simple_build(namespace=namespace, **kwargs)
        elif build_type == PROD_WITH_SECRET_BUILD_TYPE:
            return self.create_prod_with_secret_build(namespace=namespace, **kwargs)
        else:
            raise OsbsException("Unknown build type: '%s'" % build_type)

    @osbsapi
    def get_build_logs(self, build_id, follow=False, build_json=None, wait_if_missing=False,
                       namespace=DEFAULT_NAMESPACE):
        """
        provide logs from build

        :param build_id: str
        :param follow: bool, fetch logs as they come?
        :param build_json: dict, to save one get-build query
        :param wait_if_missing: bool, if build doesn't exist, wait
        :param namespace: str
        :return: None, str or iterator
        """
        return self.os.logs(build_id, follow=follow, build_json=build_json,
                            wait_if_missing=wait_if_missing, namespace=namespace)

    @osbsapi
    def get_docker_build_logs(self, build_id, decode_logs=True, build_json=None,
                              namespace=DEFAULT_NAMESPACE):
        """
        get logs provided by "docker build"

        :param build_id: str
        :param decode_logs: bool, docker by default output logs in simple json structure:
            { "stream": "line" }
            if this arg is set to True, it decodes logs to human readable form
        :param build_json: dict, to save one get-build query
        :param namespace: str
        :return: str
        """
        if not build_json:
            build = self.os.get_build(build_id, namespace=namespace)
            build_response = BuildResponse(build)
        else:
            build_response = BuildResponse(None, build_json)

        if build_response.is_finished():
            logs = build_response.get_logs(decode_logs=decode_logs)
            return logs
        logger.warning("build haven't finished yet")

    @osbsapi
    def wait_for_build_to_finish(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.os.wait_for_build_to_finish(build_id, namespace=namespace)
        build_response = BuildResponse(None, response)
        return build_response

    @osbsapi
    def wait_for_build_to_get_scheduled(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.os.wait_for_build_to_get_scheduled(build_id, namespace=namespace)
        build_response = BuildResponse(None, response)
        return build_response

    @osbsapi
    def update_labels_on_build(self, build_id, labels,
                               namespace=DEFAULT_NAMESPACE):
        response = self.os.update_labels_on_build(build_id, labels,
                                                  namespace=namespace)
    @osbsapi
    def set_labels_on_build(self, build_id, labels, namespace=DEFAULT_NAMESPACE):
        response = self.os.set_labels_on_build(build_id, labels, namespace=namespace)
        return response

    @osbsapi
    def update_labels_on_build_config(self, build_config_id, labels,
                                      namespace=DEFAULT_NAMESPACE):
        response = self.os.update_labels_on_build_config(build_config_id,
                                                         labels,
                                                         namespace=namespace)
        return response

    @osbsapi
    def set_labels_on_build_config(self, build_config_id, labels,
                                   namespace=DEFAULT_NAMESPACE):
        response = self.os.set_labels_on_build_config(build_config_id,
                                                      labels,
                                                      namespace=namespace)
        return response

    @osbsapi
    def update_annotations_on_build(self, build_id, annotations,
                                    namespace=DEFAULT_NAMESPACE):
        return self.os.update_annotations_on_build(build_id, annotations,
                                                   namespace=namespace)

    @osbsapi
    def set_annotations_on_build(self, build_id, annotations, namespace=DEFAULT_NAMESPACE):
        return self.os.set_annotations_on_build(build_id, annotations, namespace=namespace)

    @osbsapi
    def import_image(self, name, namespace=DEFAULT_NAMESPACE):
        return self.os.import_image(name, namespace=namespace)

    @osbsapi
    def get_token(self):
        return self.os.get_oauth_token()

    @osbsapi
    def get_user(self, username="~"):
        return self.os.get_user(username).json()

    @osbsapi
    def get_image_stream(self, stream_id, namespace=DEFAULT_NAMESPACE):
        return self.os.get_image_stream(stream_id, namespace)

    @osbsapi
    def create_image_stream(self, name, docker_image_repository, namespace=DEFAULT_NAMESPACE):
        img_stream_file = os.path.join(self.os_conf.get_build_json_store(), 'image_stream.json')
        stream = json.load(open(img_stream_file))
        stream['metadata']['name'] = name
        stream['spec']['dockerImageRepository'] = docker_image_repository
        return self.os.create_image_stream(json.dumps(stream), namespace=DEFAULT_NAMESPACE)
