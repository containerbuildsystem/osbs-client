"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from __future__ import absolute_import

from osbs.exceptions import OsbsException
from osbs.constants import REPO_CONFIG_FILE, ADDITIONAL_TAGS_FILE, REPO_CONTAINER_CONFIG
from six import StringIO
from six.moves.configparser import ConfigParser
from textwrap import dedent

import logging
import os
import re
import yaml


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


class RepoConfiguration(object):
    """
    Read configuration from repository.
    """

    DEFAULT_CONFIG = dedent("""\
        [autorebuild]
        enabled = false
        """)

    def __init__(self, dir_path='', file_name=REPO_CONFIG_FILE, depth=None):

        self._config_parser = ConfigParser()
        self.container = {}
        self.depth = depth or 0

        # Set default options
        self._config_parser.readfp(StringIO(self.DEFAULT_CONFIG))   # pylint: disable=W1505; py2

        config_path = os.path.join(dir_path, file_name)
        if os.path.exists(config_path):
            self._config_parser.read(config_path)

        file_path = os.path.join(dir_path, REPO_CONTAINER_CONFIG)
        if os.path.exists(file_path):
            with open(file_path) as f:
                try:
                    self.container = yaml.load(f) or {}
                except yaml.scanner.ScannerError as e:
                    msg = ('Failed to parse YAML file "{file}": {reason}'
                           .format(file=REPO_CONTAINER_CONFIG, reason=e))
                    raise OsbsException(msg)

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

    def is_autorebuild_enabled(self):
        return self._config_parser.getboolean('autorebuild', 'enabled')


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
