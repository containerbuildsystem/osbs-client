"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


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

    def __init__(self, dir_path='', file_name=REPO_CONFIG_FILE):

        self._config_parser = ConfigParser()
        self.container = {}

        # Set default options
        self._config_parser.readfp(StringIO(self.DEFAULT_CONFIG))

        config_path = os.path.join(dir_path, file_name)
        if os.path.exists(config_path):
            self._config_parser.read(config_path)

        file_path = os.path.join(dir_path, REPO_CONTAINER_CONFIG)
        if os.path.exists(file_path):
            with open(file_path) as f:
                self.container = (yaml.load(f) or {})

    def is_autorebuild_enabled(self):
        return self._config_parser.getboolean('autorebuild', 'enabled')


class AdditionalTagsConfig(object):
    """
    Container for additional image tags.
    Tags are passed to constructor or are read from repository.
    """

    VALID_TAG_REGEX = re.compile(r'^[\w.]{0,127}$')

    def __init__(self, dir_path='', file_name=ADDITIONAL_TAGS_FILE, tags=set()):
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
