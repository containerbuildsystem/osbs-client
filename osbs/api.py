from __future__ import print_function, unicode_literals, absolute_import
import json
from osbs.build import BuildManager, BuildResponse
from osbs.constants import BUILD_JSON_STORE
from osbs.core import Openshift, OpenshiftException


class OSBS(object):
    """ """
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
                            verify_ssl=self.os_conf.get_verify_ssl())
        self._bm = None

    # some calls might not need build manager so let's make it lazy
    @property
    def bm(self):
        if self._bm is None:
            self._bm = BuildManager(build_json_store=self.os_conf.get_build_json_store())
        return self._bm

    def list_builds(self):
        # FIXME: return list of BuildResponse objects
        builds = self.os.list_builds().json()
        return builds

    def get_build(self, build_id):
        response = self.os.get_build(build_id)
        build_response = BuildResponse(response)
        return build_response

    def create_build(self, git_uri, git_ref, user, component, target):
        build = self.bm.get_build(
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
        )
        response = self.os.create_build(json.dumps(build.build_json))
        build_response = BuildResponse(response)
        return build_response

    def get_build_logs(self, build_id, follow=False):
        if follow:
            return self.os.logs(build_id, follow)
        try:
            build = self.os.get_build(build_id)
        except OpenshiftException as ex:
            if ex.status_code != 404:
                raise
        else:
            if build in ["Complete", "Failed"]:
                return build["metadata"]["labels"]["logs"]
            else:
                return self.os.logs(build_id, follow)

    def wait_for_build_to_finish(self, build_id):
        # FIXME: since OS returns whole build json in watch we could return
        #        instance of BuildResponse here
        response = self.os.wait(build_id)
        return response
