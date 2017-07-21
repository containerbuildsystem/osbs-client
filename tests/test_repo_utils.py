"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from osbs.constants import REPO_CONFIG_FILE
from osbs.repo_utils import RepoConfiguration
from textwrap import dedent

import os
import pytest


class TestRepoConfiguration(object):

    def test_default_values(self):
        conf = RepoConfiguration()
        assert conf.is_autorebuild_enabled() is False

    @pytest.mark.parametrize(('config_value', 'expected_value'), (
        (None, False),
        ('false', False),
        ('true', True),
    ))
    def test_is_autorebuild_enabled(self, tmpdir, config_value, expected_value):

        with open(os.path.join(str(tmpdir), REPO_CONFIG_FILE), 'w') as f:
            if config_value is not None:
                f.write(dedent("""\
                    [autorebuild]
                    enabled={0}
                    """.format(config_value)))

        conf = RepoConfiguration(dir_path=str(tmpdir))
        assert conf.is_autorebuild_enabled() is expected_value
