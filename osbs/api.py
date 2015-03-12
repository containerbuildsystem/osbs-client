from __future__ import print_function, unicode_literals, absolute_import
import json
from osbs.build import BuildManager
from osbs.constants import BUILD_JSON_STORE
from osbs.core import Openshift, OpenshiftException


class OSBS(object):
    """ """
    def __init__(self, configuration):
        """ """
        self.conf = configuration
        self.os = Openshift(openshift_api_url=self.conf.get_openshift_api_uri(),
                            openshift_oauth_url=self.conf.get_openshift_oauth_api_uri(),
                            kubelet_base=self.conf.get_kubelet_uri(),
                            verbose=self.conf.get_verbosity())

    def list_builds(self):
        builds = self.os.list_builds().json()
        return builds

    def get_build(self, build_id):
        build = self.os.get_build(build_id).json()
        return build

    def create_prod_build(self, git_uri, git_ref, user, component, registry,
                          target, build_json_dir=BUILD_JSON_STORE):
        bm = BuildManager(build_json_store=build_json_dir)
        build = bm.get_prod_build(
            git_uri=git_uri,
            git_ref=git_ref,
            user=user,
            component=component,
            registry_uri=registry,
            openshift_uri=self.conf.get_openshift_api_uri(),
            kojiroot=self.conf.get_kojiroot(),
            kojihub=self.conf.get_kojihub(),
            rpkg_bin=self.conf.get_rpkg_binary(),
            koji_target=target,
        )
        response = self.os.create_build(json.dumps(build.build_json))
        return build.build_id

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
