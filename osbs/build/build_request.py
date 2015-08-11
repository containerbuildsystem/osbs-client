"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import json
import logging
import os

from osbs.build.manipulate import DockJsonManipulator
from osbs.build.spec import CommonSpec, ProdSpec, SimpleSpec
from osbs.constants import PROD_BUILD_TYPE, SIMPLE_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE
from osbs.constants import PROD_WITH_SECRET_BUILD_TYPE
from osbs.exceptions import OsbsValidationException


build_classes = {}
logger = logging.getLogger(__name__)


def register_build_class(cls):
    build_classes[cls.key] = cls
    return cls


class BuildRequest(object):
    """
    Wraps logic for creating build inputs
    """

    key = None

    def __init__(self, build_json_store):
        """
        :param build_json_store: str, path to directory with JSON build files
        """
        self.spec = None
        self.build_json_store = build_json_store
        self.build_json = None       # rendered template
        self._template = None        # template loaded from filesystem
        self._inner_template = None  # dock json
        self._dj = None
        self._resource_limits = None

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        :param kwargs:
        :return:
        """
        raise NotImplementedError()

    def set_resource_limits(self, cpu=None, memory=None, storage=None):
        if self._resource_limits is None:
            self._resource_limits = {}

        if cpu is not None:
            self._resource_limits['cpu'] = cpu

        if memory is not None:
            self._resource_limits['memory'] = memory

        if storage is not None:
            self._resource_limits['storage'] = storage

    @staticmethod
    def new_by_type(build_name, *args, **kwargs):
        """Find BuildRequest with the given name."""

        # Compatibility
        if build_name in (PROD_WITHOUT_KOJI_BUILD_TYPE,
                          PROD_WITH_SECRET_BUILD_TYPE):
            build_name = PROD_BUILD_TYPE

        try:
            build_class = build_classes[build_name]
            logger.debug("Instantiating: %s(%s, %s)", build_class.__name__, args, kwargs)
            return build_class(*args, **kwargs)
        except KeyError:
            raise RuntimeError("Unknown build type '{0}'".format(build_name))

    def render(self):
        """
        render input parameters into template

        :return: dict, build json
        """
        raise NotImplementedError()

    @property
    def build_id(self):
        return self.build_json['metadata']['name']

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.build_json_store, "%s.json" % self.key)
            logger.debug("loading template from path %s", path)
            try:
                with open(path, "r") as fp:
                    self._template = json.load(fp)
            except (IOError, OSError) as ex:
                raise OsbsException("Can't open template '%s': %s" %
                                    (path, repr(ex)))
        return self._template

    @property
    def inner_template(self):
        if self._inner_template is None:
            path = os.path.join(self.build_json_store, "%s_inner.json" % self.key)
            logger.debug("loading inner template from path %s", path)
            with open(path, "r") as fp:
                self._inner_template = json.load(fp)
        return self._inner_template

    @property
    def dj(self):
        if self._dj is None:
            self._dj = DockJsonManipulator(self.template, self.inner_template)
        return self._dj


class CommonBuild(BuildRequest):
    def __init__(self, build_json_store):
        """
        :param build_json_store: str, path to directory with JSON build files
        """
        super(CommonBuild, self).__init__(build_json_store)
        self.spec = CommonSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param git_uri: str, URL of source git repository
        :param git_ref: str, what git tree to build (default: master)
        :param registry_uri: str, URL of docker registry where built image is pushed
        :param user: str, user part of resulting image name
        :param component: str, component part of the image name
        :param openshift_uri: str, URL of openshift instance for the build
        :param yum_repourls: list of str, URLs to yum repo files to include
        :param use_auth: bool, use auth from atomic-reactor?
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self):
        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = self.spec.name.value

        if self._resource_limits is not None:
            resources = self.template['spec'].get('resources', {})
            limits = resources.get('limits', {})
            limits.update(self._resource_limits)
            resources['limits'] = limits
            self.template['spec']['resources'] = resources

        self.template['spec']['source']['git']['uri'] = self.spec.git_uri.value
        self.template['spec']['source']['git']['ref'] = self.spec.git_ref.value

        tag_with_registry = self.spec.registry_uri.value + "/" + self.spec.image_tag.value
        self.template['spec']['output']['to']['name'] = tag_with_registry
        if 'triggers' in self.template['spec']:
            self.template['spec']['triggers']\
                [0]['imageChange']['from']['name'] = self.spec.trigger_imagestream_name.value

        if (self.spec.yum_repourls.value is not None and
                self.dj.dock_json_has_plugin_conf('prebuild_plugins', "add_yum_repo_by_url")):
            self.dj.dock_json_set_arg('prebuild_plugins', "add_yum_repo_by_url", "repourls",
                                      self.spec.yum_repourls.value)

        if self.spec.use_auth.value is not None:
            try:
                self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3",
                                          "use_auth", self.spec.use_auth.value)
            except RuntimeError:
                # For compatibility with older osbs.conf files
                self.dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3",
                                          "use_auth", self.spec.use_auth.value)

        if self.dj.dock_json_has_plugin_conf('postbuild_plugins', 'import_image'):
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image', 'imagestream',
                                      self.spec.imagestream_name.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image', 'docker_image_repo',
                                      self.spec.imagestream_url.value)
            self.dj.dock_json_set_arg('postbuild_plugins', 'import_image', 'url',
                                      self.spec.openshift_uri.value)
            if self.spec.use_auth.value is not None:
                self.dj.dock_json_set_arg('postbuild_plugins', 'import_image', 'use_auth',
                                          self.spec.use_auth.value)

    def validate_input(self):
        self.spec.validate()


@register_build_class
class ProductionBuild(CommonBuild):
    key = PROD_BUILD_TYPE

    def __init__(self, build_json_store, **kwargs):
        super(ProductionBuild, self).__init__(build_json_store, **kwargs)
        self.spec = ProdSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param source_secret: str, resource name of source secret
        :param koji_target: str, koji tag with packages used to build the image
        :param kojiroot: str, URL from which koji packages are fetched
        :param kojihub: str, URL of the koji hub
        :param pulp_registry: str, name of pulp registry in dockpulp.conf
        :param nfs_server_path: str, NFS server and path
        :param nfs_dest_dir: str, directory to create on NFS server
        :param sources_command: str, command used to fetch dist-git sources
        :param architecture: str, architecture we are building for
        :param vendor: str, vendor name
        :param build_host: str, host the build will run on
        :param authoritative_registry: str, the docker registry authoritative for this image
        :param use_auth: bool, use auth from atomic-reactor?
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(ProductionBuild, self).render()

        self.dj.dock_json_set_arg('prebuild_plugins', "distgit_fetch_artefacts",
                                  "command", self.spec.sources_command.value)
        self.dj.dock_json_set_arg('prebuild_plugins', "pull_base_image",
                                  "parent_registry", self.spec.registry_uri.value)

        implicit_labels = {
            'Architecture': self.spec.architecture.value,
            'Vendor': self.spec.vendor.value,
            'Build_Host': self.spec.build_host.value,
            'Authoritative_Registry': self.spec.authoritative_registry.value,
        }

        self.dj.dock_json_merge_arg('prebuild_plugins', "add_labels_in_dockerfile",
                                    "labels", implicit_labels)

        try:
            self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3",
                                      "url", self.spec.openshift_uri.value)
        except RuntimeError:
            # For compatibility with older osbs.conf files
            self.dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3",
                                      "url", self.spec.openshift_uri.value)

        # if there is yum repo specified, don't pick stuff from koji
        if self.spec.yum_repourls.value:
            logger.info("removing koji from request, because there is yum repo specified")
            self.dj.remove_plugin("prebuild_plugins", "koji")
        elif not (self.spec.koji_target.value and
                  self.spec.kojiroot.value and
                  self.spec.kojihub.value):
            logger.info("removing koji from request as not specified")
            self.dj.remove_plugin("prebuild_plugins", "koji")
        else:
            self.dj.dock_json_set_arg('prebuild_plugins', "koji",
                                      "target", self.spec.koji_target.value)
            self.dj.dock_json_set_arg('prebuild_plugins', "koji", "root", self.spec.kojiroot.value)
            self.dj.dock_json_set_arg('prebuild_plugins', "koji", "hub", self.spec.kojihub.value)

        # If there is a pulp secret, use it
        if self.spec.source_secret.value:
            name = self.spec.source_secret.value
            self.template['spec']['source']['sourceSecret']['name'] = name
            # OpenShift 0.5.2 compatibility, remove once we migrate to v1 api
            self.template['spec']['source']['sourceSecretName'] = name

            # Don't push to docker registry, we're using pulp here
            # but still construct the unique tag
            self.template['spec']['output']['to']['name'] = self.spec.image_tag.value
        else:
            # Otherwise remove references to the secret
            del self.template['spec']['source']['sourceSecret']
            # Openshift 0.5.2 compatibility, remove once we migrate to v1 api
            del self.template['spec']['source']['sourceSecretName']

        # If NFS destination set, use it
        nfs_server_path = self.spec.nfs_server_path.value
        if nfs_server_path:
            self.dj.dock_json_set_arg('postbuild_plugins', 'cp_built_image_to_nfs',
                                      'nfs_server_path', nfs_server_path)
            self.dj.dock_json_set_arg('postbuild_plugins', 'cp_built_image_to_nfs',
                                      'nfs_dest_dir', self.spec.nfs_dest_dir.value)
        else:
            # Otherwise, don't run the NFS plugin
            self.dj.remove_plugin("postbuild_plugins", "cp_built_image_to_nfs")

        # If a pulp registry is specified, use the pulp plugin
        pulp_registry = self.spec.pulp_registry.value
        if pulp_registry:
            self.dj.dock_json_set_arg('postbuild_plugins', 'pulp_push',
                                      'pulp_registry_name', pulp_registry)

            # Verify we have either a sourceSecret or username/password
            if 'sourceSecret' not in self.template['spec']['source']:
                conf = self.dj.dock_json_get_plugin_conf('postbuild_plugins',
                                                         'pulp_push')
                args = conf.get('args', {})
                if 'username' not in args:
                    raise OsbsValidationException("Pulp registry specified "
                                                  "but no auth config")
        else:
            # If no pulp registry is specified, don't run the pulp plugin
            self.dj.remove_plugin("postbuild_plugins", "pulp_push")

        self.dj.write_dock_json()
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json


@register_build_class
class SimpleBuild(CommonBuild):
    """
    Simple build type for scratch builds - gets sources from git, builds image
    according to Dockerfile, pushes it to a registry.
    """

    key = SIMPLE_BUILD_TYPE

    def __init__(self, build_json_store, **kwargs):
        super(SimpleBuild, self).__init__(build_json_store, **kwargs)
        self.spec = SimpleSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(SimpleBuild, self).render()
        self.dj.dock_json_set_arg('prebuild_plugins', "pull_base_image", "parent_registry",
                                  self.spec.registry_uri.value)
        try:
            self.dj.dock_json_set_arg('exit_plugins', "store_metadata_in_osv3", "url",
                                      self.spec.openshift_uri.value)
        except RuntimeError:
            # For compatibility with older osbs.conf files
            self.dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url",
                                      self.spec.openshift_uri.value)

        self.dj.write_dock_json()
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json


class BuildManager(object):

    def __init__(self, build_json_store):
        self.build_json_store = build_json_store

    def get_build_request_by_type(self, build_type):
        """
        return instance of BuildRequest according to specified build type

        :param build_type: str, name of build type
        :return: instance of BuildRequest
        """
        b = BuildRequest.new_by_type(build_type, build_json_store=self.build_json_store)
        return b
