"""
Copyright (c) 2017-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from __future__ import print_function, absolute_import, unicode_literals

from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.constants import (ADDITIONAL_TAGS_FILE,
                            REPO_CONTAINER_CONFIG,
                            REPO_CONTAINER_CONFIG_POSSIBLE_TYPOS,
                            REPO_CONTENT_SETS_FILE,
                            REPO_CONTENT_SETS_FILE_POSSIBLE_TYPOS)
from osbs.utils.labels import Labels
from osbs.utils.yaml import read_yaml_from_file_path

import logging
import os
import re


logger = logging.getLogger(__name__)


class RepoInfo(object):
    """
    Aggregator for different aspects of the repository.
    """

    def __init__(self, dockerfile_parser=None, configuration=None, additional_tags=None):
        self.dockerfile_parser = dockerfile_parser
        self.configuration = configuration or RepoConfiguration()
        self.additional_tags = additional_tags or AdditionalTagsConfig(
            tags=self.configuration.container.get('tags', set()))
        self._parsed = False
        self._base_image = None
        self._labels = None

    @property
    def git_branch(self):
        return self.configuration.git_branch

    @property
    def git_ref(self):
        return self.configuration.git_ref

    @property
    def git_uri(self):
        return self.configuration.git_uri

    @property
    def git_commit_depth(self):
        return self.configuration.depth

    def _ensure_parsed(self):
        """Parse the Dockerfile and set self._labels and self._base_image."""

        if self._parsed:
            return

        self._parsed = True

        if self.configuration.is_flatpak:
            modules = self.configuration.container_module_specs

            if modules:
                module = modules[0]
            else:
                raise OsbsValidationException('"compose" config is missing "modules",'
                                              ' required for Flatpak')

            # modules is always required for a Flatpak build, but is only used
            # for the name and component labels if they aren't explicitly set
            # in container.yaml
            name = self.configuration.flatpak_name or module.name
            component = self.configuration.flatpak_component or module.name

            self._labels = Labels({
                Labels.LABEL_TYPE_NAME: name,
                Labels.LABEL_TYPE_COMPONENT: component,
                Labels.LABEL_TYPE_VERSION: module.stream,
            })

            self._base_image = self.configuration.flatpak_base_image
        else:
            df_parser = self.dockerfile_parser

            # DockerfileParse does not ensure a Dockerfile exists during initialization
            try:
                self._labels = Labels(df_parser.labels)
                self._base_image = df_parser.baseimage
            except IOError as e:
                raise RuntimeError('Could not parse Dockerfile in {}: {}'
                                   .format(df_parser.dockerfile_path, e))

    @property
    def labels(self):
        self._ensure_parsed()

        return self._labels

    @property
    def base_image(self):
        self._ensure_parsed()

        return self._base_image


class RepoConfiguration(object):
    """
    Read configuration from repository.
    """

    def __init__(self, dir_path='', depth=None, git_uri=None, git_branch=None, git_ref=None):
        self.container = {}
        self.depth = depth or 0
        # Keep track of the repo metadata in the repo configuration
        self.git_uri = git_uri
        self.git_branch = git_branch
        self.git_ref = git_ref
        self.dir_path = dir_path

        if self._check_repo_file_exists_with_expected_filename(
                expected_filename=REPO_CONTAINER_CONFIG,
                possible_filename_typos=REPO_CONTAINER_CONFIG_POSSIBLE_TYPOS
        ):
            self._validate_container_config_file()
        self._check_repo_file_exists_with_expected_filename(
            expected_filename=REPO_CONTENT_SETS_FILE,
            possible_filename_typos=REPO_CONTENT_SETS_FILE_POSSIBLE_TYPOS
        )

        if 'autorebuild' in self.container:
            logger.user_warning("'autorebuild' config is deprecated in OSBS 2.0, this config will "
                                'be ignored')
            del self.container['autorebuild']

        if 'image_build_method' in self.container:
            logger.user_warning("'image_build_method' config is deprecated in OSBS 2.0, this config"
                                " will be ignored")
            del self.container['image_build_method']

        self.buildtime_limit = self.container.get('buildtime_limit', 0)
        # container values may be set to None
        container_compose = self.container.get('compose') or {}
        modules = container_compose.get('modules') or []

        self.container_module_specs = []
        value_errors = []
        for module in modules:
            try:
                self.container_module_specs.append(ModuleSpec.from_str(module))
            except ValueError as e:
                value_errors.append(e)
        if value_errors:
            raise ValueError(value_errors)

        flatpak = self.container.get('flatpak') or {}
        self.is_flatpak = bool(flatpak)
        self.flatpak_base_image = flatpak.get('base_image')
        self.flatpak_component = flatpak.get('component')
        self.flatpak_name = flatpak.get('name')

    def _check_repo_file_exists_with_expected_filename(self, expected_filename,
                                                       possible_filename_typos):
        """
        Checks if a file with given filename exists in repo

        :param str expected_filename: Expected filename to lookup in the repo
        :param set possible_filename_typos: Set of possible typos for expected_filename
        :return: boolean stating wheter expected_filename exists in repo
        :rtype bool
        :raises OsbsException: if any filename from possible_filename_typos exists in repo
        """
        expected_file_path = os.path.join(self.dir_path, expected_filename)

        wrong_filename = ''
        for possible_filename_typo in possible_filename_typos:
            path = os.path.join(self.dir_path, possible_filename_typo)
            if os.path.exists(path):
                wrong_filename = possible_filename_typo
                break

        if os.path.exists(expected_file_path) and wrong_filename:
            msg = ('This repo contains both {expected_filename} and {wrong_filename} '
                   'Please remove {wrong_filename}'
                   .format(expected_filename=expected_filename,
                           wrong_filename=wrong_filename))
            raise OsbsException(msg)
        elif wrong_filename:
            msg = ('Repo contains wrong filename: {wrong_filename}, expected: {expected_filename}'
                   .format(expected_filename=expected_filename, wrong_filename=wrong_filename))
            raise OsbsException(msg)
        elif os.path.exists(expected_file_path):
            return True

        return False

    def _validate_container_config_file(self):
        try:
            container_config_path = os.path.join(self.dir_path, REPO_CONTAINER_CONFIG)
            self.container = read_yaml_from_file_path(container_config_path,
                                                      'schemas/container.json') or {}
        except Exception as e:
            msg = ('Failed to load or validate container file "{file}": {reason}'
                   .format(file=container_config_path, reason=e))
            raise OsbsException(msg)


class ModuleSpec(object):
    """
    Specification for a to-be-requested module.

    This module representation is simplified from the possible
    NAME:STREAM:VERSION:CONTEXT:ARCH/PROFILE by not supporting ARCH, which
    should be determined by the architecture of the build, and by not
    supporting partal specifications such as NAME:::CONTEXT.
    """

    def __init__(self, name, stream, version=None, context=None, profile=None):
        self.name = name
        self.stream = stream
        self.version = version
        self.context = context
        self.profile = profile

    def to_str(self, include_profile=True):
        result = self.name + ':' + self.stream
        if self.version:
            result += ':' + self.version
        if self.context:
            result += ':' + self.context
        if include_profile and self.profile:
            result += '/' + self.profile

        return result

    def __repr__(self):
        return "ModuleSpec({})".format(self.to_str())

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    __hash__ = None     # py2 compatibility

    @classmethod
    def from_str(cls, text):
        profile = None
        if '/' in text:
            module, profile = text.rsplit('/', 1)
        else:
            module = text

        pieces = module.split(':')
        if not 1 < len(pieces) < 5:
            raise ValueError('Module specification {} should be in '
                             'NAME:STREAM[:VERSION[:CONTEXT]][/PROFILE] format'.format(module))
        if not all(pieces) or profile == '':
            raise ValueError('Module specification {} contains empty fields'.format(module))
        return cls(*pieces, profile=profile)


class AdditionalTagsConfig(object):
    """
    Container for additional image tags.
    Tags are passed to constructor or are read from repository.
    """

    VALID_TAG_REGEX = re.compile(r'^[\w.]{0,127}$')

    def __init__(self, dir_path='', file_name=ADDITIONAL_TAGS_FILE, tags=None):
        tags = tags or set()
        self._tags = set([x for x in tags if self._is_tag_valid(x)])
        self._from_container_yaml = True if tags else False
        self._file_path = os.path.join(dir_path, file_name)

        if os.path.exists(self._file_path):
            logger.warning('%s file is deprecated and will no longer be '
                           'supported in a future version. Please consider '
                           'using tags list in container.yaml instead', file_name)
        self._populate_tags()

    def _populate_tags(self):
        if self._from_container_yaml:
            logger.warning('Tags were read from container.yaml file. Additional tags'
                           ' are being ignored!')
            return

        if not os.path.exists(self._file_path):
            return

        with open(self._file_path) as f:
            for tag in f:
                tag = tag.strip()
                if not self._is_tag_valid(tag):
                    continue
                self._tags.add(tag)

    def _is_tag_valid(self, tag):
        if not tag:
            return False

        if not self.VALID_TAG_REGEX.match(tag):
            logger.warning('Invalid additional tag "%s", must match pattern %s',
                           tag, self.VALID_TAG_REGEX.pattern)
            return False

        return True

    @property
    def tags(self):
        return list(self._tags)

    @property
    def from_container_yaml(self):
        return self._from_container_yaml
