"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from collections import namedtuple
from osbs.constants import REPO_CONFIG_FILE
from six import StringIO
from six.moves.configparser import ConfigParser
from textwrap import dedent

import os


RepoInfo = namedtuple('RepoInfo', 'dockerfile_parser, configuration')


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
