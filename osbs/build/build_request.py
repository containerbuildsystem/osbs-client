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
from osbs.build.spec import CommonSpec, ProdSpec, SimpleSpec, ProdWithoutKojiSpec, CommonProdSpec
from osbs.constants import PROD_BUILD_TYPE, SIMPLE_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE
from osbs.exceptions import OsbsException


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

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        :param kwargs:
        :return:
        """
        raise NotImplementedError()

    @staticmethod
    def new_by_type(build_name, *args, **kwargs):
        """Find BuildRequest with the given name."""
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
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self):
        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = self.spec.name.value
        self.template['parameters']['source']['git']['uri'] = self.spec.git_uri.value
        self.template['parameters']['source']['git']['ref'] = self.spec.git_ref.value
        self.template['parameters']['output']['registry'] = self.spec.registry_uri.value
        if (self.spec.yum_repourls.value is not None and
                self.dj.dock_json_has_plugin_conf('prebuild_plugins', "add_yum_repo_by_url")):
            self.dj.dock_json_set_arg('prebuild_plugins', "add_yum_repo_by_url", "repourls",
                                      self.spec.yum_repourls.value)

    def validate_input(self):
        self.spec.validate()


class CommonProductionBuild(CommonBuild):
    def __init__(self, build_json_store, **kwargs):
        super(CommonProductionBuild, self).__init__(build_json_store, **kwargs)
        self.spec = CommonProdSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param sources_command: str, command used to fetch dist-git sources
        :param architecture: str, architecture we are building for
        :param vendor: str, vendor name
        :param build_host: str, host the build will run on
        :param authoritative_registry: str, the docker registry authoritative for this image
        :param metadata_plugin_use_auth: bool, use auth when posting metadata from dock?
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(CommonProductionBuild, self).render()
        dj = DockJsonManipulator(self.template, self.inner_template)

        dj.dock_json_set_arg('prebuild_plugins', "distgit_fetch_artefacts", "command",
                             self.spec.sources_command.value)
        dj.dock_json_set_arg('prebuild_plugins', "change_source_registry", "registry_uri",
                             self.spec.registry_uri.value)
        dj.dock_json_set_arg('postbuild_plugins', "tag_by_labels", "registry_uri",
                             self.spec.registry_uri.value)
        if self.spec.metadata_plugin_use_auth.value is not None:
            dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3",
                                 "use_auth", self.spec.metadata_plugin_use_auth.value)

        implicit_labels = {
            'Architecture': self.spec.architecture.value,
            'Vendor': self.spec.vendor.value,
            'Build_Host': self.spec.build_host.value,
            'Authoritative_Registry': self.spec.authoritative_registry.value,
        }

        dj.dock_json_merge_arg('prebuild_plugins', "add_labels_in_dockerfile", "labels",
                               implicit_labels)

        dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url",
                             self.spec.openshift_uri.value)


@register_build_class
class ProductionBuild(CommonProductionBuild):
    key = PROD_BUILD_TYPE

    def __init__(self, build_json_store, **kwargs):
        super(ProductionBuild, self).__init__(build_json_store, **kwargs)
        self.spec = ProdSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param koji_target: str, koji tag with packages used to build the image
        :param kojiroot: str, URL from which koji packages are fetched
        :param kojihub: str, URL of the koji hub
        :param sources_command: str, command used to fetch dist-git sources
        :param architecture: str, architecture we are building for
        :param vendor: str, vendor name
        :param build_host: str, host the build will run on
        :param authoritative_registry: str, the docker registry authoritative for this image
        :param metadata_plugin_use_auth: bool, use auth when posting metadata from dock?
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(ProductionBuild, self).render()
        dj = DockJsonManipulator(self.template, self.inner_template)

        self.template['parameters']['output']['imageTag'] = self.spec.image_tag.value

        dj.dock_json_set_arg('prebuild_plugins', "koji", "target", self.spec.koji_target.value)
        dj.dock_json_set_arg('prebuild_plugins', "koji", "root", self.spec.kojiroot.value)
        dj.dock_json_set_arg('prebuild_plugins', "koji", "hub", self.spec.kojihub.value)

        dj.write_dock_json()
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json


@register_build_class
class ProductionWithoutKojiBuild(CommonProductionBuild):
    key = PROD_WITHOUT_KOJI_BUILD_TYPE

    def __init__(self, build_json_store, **kwargs):
        super(ProductionWithoutKojiBuild, self).__init__(build_json_store, **kwargs)
        self.spec = ProdWithoutKojiSpec()

    def set_params(self, **kwargs):
        """
        set parameters according to specification

        these parameters are accepted:

        :param sources_command: str, command used to fetch dist-git sources
        :param architecture: str, architecture we are building for
        :param vendor: str, vendor name
        :param build_host: str, host the build will run on
        :param authoritative_registry: str, the docker registry authoritative for this image
        :param metadata_plugin_use_auth: bool, use auth when posting metadata from dock?
        """
        logger.debug("setting params '%s' for %s", kwargs, self.spec)
        self.spec.set_params(**kwargs)

    def render(self, validate=True):
        if validate:
            self.spec.validate()
        super(ProductionWithoutKojiBuild, self).render()
        dj = DockJsonManipulator(self.template, self.inner_template)

        self.template['parameters']['output']['imageTag'] = self.spec.image_tag.value

        dj.write_dock_json()
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
        dj = DockJsonManipulator(self.template, self.inner_template)
        self.template['parameters']['output']['imageTag'] = self.spec.image_tag.value
        dj.dock_json_set_arg('prebuild_plugins', "change_source_registry", "registry_uri",
                             self.spec.registry_uri.value)
        dj.dock_json_set_arg('postbuild_plugins', "store_metadata_in_osv3", "url",
                             self.spec.openshift_uri.value)
        dj.write_dock_json()
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
