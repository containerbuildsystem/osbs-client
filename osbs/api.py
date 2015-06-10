"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import

import json
import logging
import sys
from functools import wraps

from .constants import SIMPLE_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE, PROD_WITH_SECRET_BUILD_TYPE
from osbs.build.build_request import BuildManager
from osbs.build.build_response import BuildResponse
from osbs.constants import DEFAULT_NAMESPACE, PROD_BUILD_TYPE
from osbs.core import Openshift
from osbs.exceptions import OsbsResponseException, OsbsException


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
    """ """
    @osbsapi
    def __init__(self, openshift_configuration, build_configuration):
        """ """
        self.os_conf = openshift_configuration
        self.build_conf = build_configuration
        self.os = Openshift(openshift_api_url=self.os_conf.get_openshift_api_uri(),
                            openshift_oauth_url=self.os_conf.get_openshift_oauth_api_uri(),
                            verbose=self.os_conf.get_verbosity(),
                            username=self.os_conf.get_username(),
                            password=self.os_conf.get_password(),
                            use_kerberos=self.os_conf.get_use_kerberos(),
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
            build_list.append(BuildResponse(response, build))
        return build_list

    @osbsapi
    def get_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.os.get_build(build_id, namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def get_build_request(self, build_type=None):
        """
        return instance of BuildRequest according to specified build type

        :param build_type: str, name of build type
        :return: instance of BuildRequest
        """
        build_type = build_type or self.build_conf.get_build_type()
        return self.bm.get_build_request_by_type(build_type=build_type)

    @osbsapi
    def create_build_from_buildrequest(self, build_request, namespace=DEFAULT_NAMESPACE):
        """
        render provided build_request and submit build from it

        :param build_request: instance of build.build_request.BuildRequest
        :param namespace: str, place/context where the build should be executed
        :return: instance of build.build_response.BuildResponse
        """
        build = build_request.render()
        response = self.os.create_build(json.dumps(build.build_json), namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def create_prod_build(self, git_uri, git_ref, user, component, target, architecture, yum_repourls=None,
                          namespace=DEFAULT_NAMESPACE, **kwargs):
        build_request = self.get_build_request(PROD_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            user=user,
            component=component,
            registry_uri=self.build_conf.get_registry_uri(),
            openshift_uri=self.os_conf.get_openshift_api_uri(),
            kojiroot=self.build_conf.get_kojiroot(),
            kojihub=self.build_conf.get_kojihub(),
            sources_command=self.build_conf.get_sources_command(),
            koji_target=target,
            architecture=architecture,
            vendor=self.build_conf.get_vendor(),
            build_host=self.build_conf.get_build_host(),
            authoritative_registry=self.build_conf.get_authoritative_registry(),
            yum_repourls=yum_repourls,
            metadata_plugin_use_auth=self.build_conf.get_metadata_plugin_use_auth(),
        )
        build_json = build_request.render()
        response = self.os.create_build(json.dumps(build_json), namespace=namespace)
        build_response = BuildResponse(response)
        logger.debug(build_response.json)
        return build_response

    @osbsapi
    def create_prod_with_secret_build(self, git_uri, git_ref, user, component, target, architecture, source_secret,
                                      yum_repourls=None, namespace=DEFAULT_NAMESPACE, **kwargs):
        build_request = self.get_build_request(PROD_WITH_SECRET_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            user=user,
            component=component,
            registry_uri=self.build_conf.get_registry_uri(),
            openshift_uri=self.os_conf.get_openshift_api_uri(),
            kojiroot=self.build_conf.get_kojiroot(),
            kojihub=self.build_conf.get_kojihub(),
            sources_command=self.build_conf.get_sources_command(),
            koji_target=target,
            architecture=architecture,
            vendor=self.build_conf.get_vendor(),
            build_host=self.build_conf.get_build_host(),
            authoritative_registry=self.build_conf.get_authoritative_registry(),
            yum_repourls=yum_repourls,
            source_secret=source_secret,
            metadata_plugin_use_auth=self.build_conf.get_metadata_plugin_use_auth(),
            pulp_registry=self.os_conf.get_pulp_registry(),
            nfs_server_path=self.os_conf.get_nfs_server_path(),
            nfs_dest_dir=self.build_conf.get_nfs_destination_dir(),
        )
        build_json = build_request.render()
        response = self.os.create_build(json.dumps(build_json), namespace=namespace)
        build_response = BuildResponse(response)
        logger.debug(build_response.json)
        return build_response

    @osbsapi
    def create_prod_without_koji_build(self, git_uri, git_ref, user, component, architecture, yum_repourls=None,
                                       namespace=DEFAULT_NAMESPACE, **kwargs):
        build_request = self.get_build_request(PROD_WITHOUT_KOJI_BUILD_TYPE)
        build_request.set_params(
            git_uri=git_uri,
            git_ref=git_ref,
            user=user,
            component=component,
            registry_uri=self.build_conf.get_registry_uri(),
            openshift_uri=self.os_conf.get_openshift_api_uri(),
            sources_command=self.build_conf.get_sources_command(),
            architecture=architecture,
            vendor=self.build_conf.get_vendor(),
            build_host=self.build_conf.get_build_host(),
            authoritative_registry=self.build_conf.get_authoritative_registry(),
            yum_repourls=yum_repourls,
            metadata_plugin_use_auth=self.build_conf.get_metadata_plugin_use_auth(),
        )
        build_json = build_request.render()
        response = self.os.create_build(json.dumps(build_json), namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

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
            openshift_uri=self.os_conf.get_openshift_api_uri(),
            yum_repourls=yum_repourls,
        )
        build_json = build_request.render()
        response = self.os.create_build(json.dumps(build_json), namespace=namespace)
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
        if build_type == PROD_BUILD_TYPE:
            return self.create_prod_build(namespace=namespace, **kwargs)
        elif build_type == SIMPLE_BUILD_TYPE:
            return self.create_simple_build(namespace=namespace, **kwargs)
        elif build_type == PROD_WITHOUT_KOJI_BUILD_TYPE:
            return self.create_prod_without_koji_build(namespace=namespace, **kwargs)
        elif build_type == PROD_WITH_SECRET_BUILD_TYPE:
            return self.create_prod_with_secret_build(namespace=namespace, **kwargs)
        else:
            raise OsbsException("Unknown build type: '%s'" % build_type)

    @osbsapi
    def get_build_logs(self, build_id, follow=False, build_json=None, namespace=DEFAULT_NAMESPACE):
        if follow:
            return self.os.logs(build_id, follow=follow, build_json=build_json,
                                namespace=namespace)
        try:
            build = self.os.get_build(build_id, namespace=namespace)
        except OsbsResponseException as ex:
            if ex.status_code != 404:
                raise
        else:
            build_response = BuildResponse(build)
            logs = None
            if build_response.is_finished():
                metadata = build_response.json.get("metadata", {})
                md = metadata.get("annotations", metadata.get("labels", {}))
                logs = md.get("logs", None)

            if logs:
                return logs

            return self.os.logs(build_id, follow=False, build_json=build_json, namespace=namespace)

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
    def set_labels_on_build(self, build_id, labels, namespace=DEFAULT_NAMESPACE):
        response = self.os.set_labels_on_build(build_id, labels, namespace=namespace)
        return response

    @osbsapi
    def get_token(self):
        return self.os.get_oauth_token()

    @osbsapi
    def get_user(self, username="~"):
        return self.os.get_user(username).json()
