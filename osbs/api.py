"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import
import json
from osbs.build import BuildManager, BuildResponse
from osbs.constants import DEFAULT_NAMESPACE
from osbs.core import Openshift
from osbs.exceptions import OsbsException, OsbsResponseException


# Decorator for API methods.
def osbsapi(func):
    def catch_exceptions(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except OsbsException:
            # Re-raise OsbsExceptions
            raise
        except Exception as ex:
            # Convert anything else to OsbsException
            raise OsbsException(repr(ex))

    return catch_exceptions


class OSBS(object):
    """ """
    @osbsapi
    def __init__(self, openshift_configuration, build_configuration):
        """ """
        self.os_conf = openshift_configuration
        self.build_conf = build_configuration
        self.os = Openshift(openshift_api_url=self.os_conf.get_openshift_api_uri(),
                            openshift_oauth_url=self.os_conf.get_openshift_oauth_api_uri(),
                            kubelet_base=self.os_conf.get_kubelet_uri(),
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
        # FIXME: return list of BuildResponse objects
        builds = self.os.list_builds(namespace=namespace).json()
        return builds

    @osbsapi
    def get_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.os.get_build(build_id, namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def create_build(self, git_uri, git_ref, user, component, target, architecture, namespace=DEFAULT_NAMESPACE):
        build = self.bm.get_build(
            build_type=self.build_conf.get_build_type(),
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
            metadata_plugin_use_auth=self.build_conf.get_metadata_plugin_use_auth(),
        )
        response = self.os.create_build(json.dumps(build.build_json), namespace=namespace)
        build_response = BuildResponse(response)
        return build_response

    @osbsapi
    def get_build_logs(self, build_id, follow=False, namespace=DEFAULT_NAMESPACE):
        if follow:
            return self.os.logs(build_id, follow, namespace=namespace)
        try:
            build = self.os.get_build(build_id, namespace=namespace)
        except OsbsResponseException as ex:
            if ex.status_code != 404:
                raise
        else:
            if build in ["Complete", "Failed"]:
                return build["metadata"]["labels"]["logs"]
            else:
                return self.os.logs(build_id, follow, namespace=namespace)

    @osbsapi
    def wait_for_build_to_finish(self, build_id, namespace=DEFAULT_NAMESPACE):
        # FIXME: since OS returns whole build json in watch we could return
        #        instance of BuildResponse here
        response = self.os.wait_for_build_to_finish(build_id, namespace=namespace)
        return response

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
