"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from collections import namedtuple
from osbs.constants import REPO_CONFIG_FILE, ADDITIONAL_TAGS_FILE
from six import StringIO
from six.moves.configparser import ConfigParser
from textwrap import dedent

import logging
import os


logger = logging.getLogger(__name__)


class RepoInfo(object):
    """
    Aggregator for different aspects of the repository.
    """

    def __init__(self, dockerfile_parser=None, configuration=None, additional_tags=None):
        self.dockerfile_parser = dockerfile_parser
        self.configuration = configuration or RepoConfiguration()
        self.additional_tags = additional_tags or AdditionalTagsConfig()


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

        # Set default options
        self._config_parser.readfp(StringIO(self.DEFAULT_CONFIG))

        config_path = os.path.join(dir_path, file_name)
        if os.path.exists(config_path):
            self._config_parser.read(config_path)

    def is_autorebuild_enabled(self):
        return self._config_parser.getboolean('autorebuild', 'enabled')


class AdditionalTagsConfig(object):
    """
    Read specified additional tags from repository.
    """

    INVALID_CHARS = ('-', '{', '}')

    def __init__(self, dir_path='', file_name=ADDITIONAL_TAGS_FILE):
        self._tags = set()
        self._file_path = os.path.join(dir_path, file_name)

        self._populate_tags()

    def _populate_tags(self):
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

        for char in self.INVALID_CHARS:
            if char in tag:
                logger.warning('Invalid character, "%s", in additional tag "%s"', char, tag)
                return False

        return True

    @property
    def tags(self):
        return list(self._tags)
