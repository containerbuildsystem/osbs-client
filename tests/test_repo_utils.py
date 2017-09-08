"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from flexmock import flexmock
from osbs.constants import REPO_CONFIG_FILE, ADDITIONAL_TAGS_FILE
from osbs.repo_utils import RepoInfo, RepoConfiguration, AdditionalTagsConfig
from textwrap import dedent

import os
import pytest


class TestRepoInfo(object):

    def test_default_params(self):
        repo_info = RepoInfo()
        assert repo_info.dockerfile_parser is None
        assert isinstance(repo_info.configuration, RepoConfiguration)
        assert isinstance(repo_info.additional_tags, AdditionalTagsConfig)

    def test_explicit_params(self):
        df_parser = flexmock()
        configuration = RepoConfiguration()
        tags_config = AdditionalTagsConfig()

        repo_info = RepoInfo(df_parser, configuration, tags_config)
        assert repo_info.dockerfile_parser is df_parser
        assert repo_info.configuration is configuration
        assert repo_info.additional_tags is tags_config


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


class TestAdditionalTagsConfig(object):

    def test_default_values(self):
        conf = AdditionalTagsConfig()
        assert conf.tags == []

    def test_tags_parsed(self, tmpdir):
        tags = ['spam', 'bacon', 'eggs', 'saus.age']
        self.mock_additional_tags(str(tmpdir), tags)
        conf = AdditionalTagsConfig(dir_path=str(tmpdir))
        # Compare as a "set" because order is not guaranteed
        assert set(conf.tags) == set(tags)

    @pytest.mark.parametrize('bad_tag', [
        '{bad', 'bad}', '{bad}', 'ba-d', '-bad', 'bad-', 'b@d',
    ])
    def test_invalid_tags(self, tmpdir, bad_tag):
        tags = [bad_tag, 'good']
        self.mock_additional_tags(str(tmpdir), tags)
        conf = AdditionalTagsConfig(dir_path=str(tmpdir))
        assert conf.tags == ['good']

    def mock_additional_tags(self, dir_path, tags=None):
        contents = ''

        if tags:
            contents = '\n'.join(tags)
        with open(os.path.join(dir_path, ADDITIONAL_TAGS_FILE), 'w') as f:
            f.write(contents)
