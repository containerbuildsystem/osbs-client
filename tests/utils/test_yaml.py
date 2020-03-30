"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

from flexmock import flexmock
from osbs.utils.yaml import read_yaml, read_yaml_from_file_path

import json
import os
import pkg_resources
import pytest
import yaml


def test_read_yaml_file_ioerrors(tmpdir):
    config_path = os.path.join(str(tmpdir), 'nosuchfile.yaml')
    with pytest.raises(IOError):
        read_yaml_from_file_path(config_path, 'schemas/nosuchfile.json')


@pytest.mark.parametrize('from_file', [True, False])
@pytest.mark.parametrize('config', [
    ("""\
      compose:
          modules:
          - mod_name:mod_stream:mod_version
    """),
])
def test_read_yaml_file_or_yaml(tmpdir, from_file, config):
    expected = yaml.safe_load(config)

    if from_file:
        config_path = os.path.join(str(tmpdir), 'config.yaml')
        with open(config_path, 'w') as fp:
            fp.write(config)
        output = read_yaml_from_file_path(config_path, 'schemas/container.json')
    else:
        output = read_yaml(config, 'schemas/container.json')

    assert output == expected


def test_read_yaml_bad_package(caplog):
    with pytest.raises(ImportError):
        read_yaml("", 'schemas/container.json', package='bad_package')
    assert 'Unable to find package bad_package' in caplog.text


def test_read_yaml_file_bad_extract(tmpdir, caplog):
    class FakeProvider(object):
        def get_resource_stream(self, pkg, rsc):
            raise IOError

    # pkg_resources.resource_stream() cannot be mocked directly
    # Instead mock the module-level function it calls.
    (flexmock(pkg_resources)
        .should_receive('get_provider')
        .and_return(FakeProvider()))

    config_path = os.path.join(str(tmpdir), 'config.yaml')
    with open(config_path, 'w'):
        pass

    with pytest.raises(IOError):
        read_yaml_from_file_path(config_path, 'schemas/container.json')
    assert "unable to extract JSON schema, cannot validate" in caplog.text


def test_read_yaml_file_bad_decode(tmpdir, caplog):
    (flexmock(json)
        .should_receive('load')
        .and_raise(ValueError))

    config_path = os.path.join(str(tmpdir), 'config.yaml')
    with open(config_path, 'w'):
        pass

    with pytest.raises(ValueError):
        read_yaml_from_file_path(config_path, 'schemas/container.json')
    assert "unable to decode JSON schema, cannot validate" in caplog.text
