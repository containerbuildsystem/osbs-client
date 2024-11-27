"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

from flexmock import flexmock
from textwrap import dedent
from osbs.utils.yaml import (read_yaml,
                             read_yaml_from_file_path,
                             load_schema,
                             validate_with_schema)


from osbs.exceptions import OsbsValidationException

import json
import jsonschema
import os
import pkg_resources
import pytest
import yaml
import re


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


@pytest.mark.parametrize(('config', 'expected'), [
    ("""\
        operator_manifests:
            enable_digest_pinning: true
            repo_replacements: [] """,
     ("at top level: validating 'anyOf' has failed "
      "(%r is a required property)") % u'manifests_dir'),
    ("""\
        compose:
            packages: []
            pulp_repos: true
        mage_build_method: "imagebuilder" """,
     ("at top level: validating 'anyOf' has failed "
      "(Additional properties are not allowed ('mage_build_method' was unexpected))")),
])
def test_read_yaml_validation_error(config, expected, caplog):
    with pytest.raises(OsbsValidationException) as exc_info:
        read_yaml(config, 'schemas/container.json')

    assert "schema validation error" in caplog.text
    assert expected == str(exc_info.value)


@pytest.mark.parametrize(('package', 'package_pass'), [
    ('osbs', True),
    ('FOO', False)
])
def test_load_schema_package(package, package_pass, caplog):
    schema = 'schemas/container.json'
    if not package_pass:
        with pytest.raises(ImportError):
            load_schema(package, schema)
        assert "Unable to find package FOO" in caplog.text
    else:
        assert isinstance(load_schema(package, schema), dict)


@pytest.mark.parametrize(('schema', 'schema_pass'), [
    ('schemas/container.json', True),
    ('schemas/container.json', False)
])
def test_load_schema_schema(schema, schema_pass, caplog):
    package = 'osbs'
    if not schema_pass:
        (flexmock(json)
            .should_receive('load')
            .and_raise(ValueError))
        with pytest.raises(ValueError):
            load_schema(package, schema)
        assert "unable to decode JSON schema, cannot validate" in caplog.text
    else:
        assert isinstance(load_schema(package, schema), dict)


@pytest.mark.parametrize(('config', 'validation_pass', 'expected'), [
    ({
        'name': 1
    }, False,
     ".name: validating 'type' has failed (1 is not of type 'string')"
     ),
    (
        {
            'name': 'foo',
            'module': 'bar'
        },
        False,
        ("at top level: validating 'additionalProperties' has failed "
         "(Additional properties are not allowed ('module' was unexpected))"),
    ), ({
        'name': 'foo'
    }, True, '')
])
def test_validate_with_schema_validation(config, validation_pass, expected, caplog):
    schema = {
        'type': 'object',
        'required': ['name'],
        'properties': {
            'name': {
                'type': 'string'
            }
        },
        'additionalProperties': False
    }
    if not validation_pass:
        with pytest.raises(OsbsValidationException) as exc_info:
            validate_with_schema(config, schema)
        assert 'schema validation error' in caplog.text
        assert expected == str(exc_info.value)
    else:
        validate_with_schema(config, schema)
        assert expected == ''


def test_validate_with_schema_bad_schema(caplog):
    config = {
        'name': 'foo'
    }
    schema = {
        'type': 'bakagaki',  # Nonexistent type
        'properties': {
            'name': {
                'type': 'string'
            }
        }
    }
    with pytest.raises(jsonschema.SchemaError):
        validate_with_schema(config, schema)
    assert 'invalid schema, cannot validate' in caplog.text


@pytest.mark.parametrize(
    "config, err_message",
    [
        (
            (
             """
             remote_sources:
              - name: invalid/name
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
             """
            ),
            r"'invalid/name' does not match u?'\^\[a-zA-Z0-9_-\]\*\$'",
        ),
        (
            ("""
            remote_sources:
              - name: valid
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
              - name: invalid/name
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
             """),
            r"'invalid/name' does not match u?'\^\[a-zA-Z0-9_-\]\*\$'",
        ),
        (
            ("""
            remote_sources:
              - name: ""
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
            """),
            r"'' is too short",
        ),
        (
            ("""
            remote_sources:
              - name: valid
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: bad
             """),
            r"'bad' does not match u?'\^\[0-9a-z]\{40}\$",
        ),
        (
            ("""
            remote_sources:
              - "not an object"
            """),
            r"'not an object' is not of type u?'object'",
        ),
        (
            (
             """
             remote_sources: "not an array"
             """
            ),
            r"'not an array' is not of type u?'array'",
        ),
        (
            (
             """
             remote_sources: []
             """
            ),
            r"\[\] is too short",
        ),
        (
            (
             """
             remote_sources:
              - name: valid
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
                  additional_property: this_should_fail
             """
            ),
            r"Additional properties are not allowed \('additional_property' was unexpected\)",
        ),
        (
            (
             """
             remote_sources:
              - name: valid
                additional_property: this_should_fail
                remote_source:
                  repo: https://git.example.com/team/repo.git
                  ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
             """
            ),
            r"Additional properties are not allowed \('additional_property' was unexpected\)",
        ),
        (
            (
             """
             remote_sources_version: 40
             """
            ),
            r"40 is greater than the maximum of 2",
        ),
    ],
)
def test_invalid_remote_sources_schema(config, err_message, caplog):
    with pytest.raises(OsbsValidationException) as exc_info:
        read_yaml(dedent(config), "schemas/container.json")

    assert "schema validation error" in caplog.text
    assert re.search(err_message, str(exc_info.value))


@pytest.mark.parametrize(
    "config, expected_data",
    [
        (
         """
          remote_sources:
            - name: valid-name
              remote_source:
                repo: https://git.example.com/team/repo.git
                ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
            - name: also_valid
              remote_source:
                repo: https://git.example.com/team/repo.git
                ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
         """,
         {
             "remote_sources": [
                 {
                     "name": "valid-name",
                     "remote_source": {
                         "repo": "https://git.example.com/team/repo.git",
                         "ref": "b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a",
                     },
                 },
                 {
                     "name": "also_valid",
                     "remote_source": {
                         "repo": "https://git.example.com/team/repo.git",
                         "ref": "b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a",
                     },
                 },
             ]
         }
        )
    ],
)
def test_valid_remote_sources_schema(config, expected_data):
    data = read_yaml(dedent(config), "schemas/container.json")
    assert expected_data == data


@pytest.mark.parametrize("pkg_manager,valid", [
    ("npm", True),
    ("pip", True),
    ("gomod", True),
    ("git-submodule", True),
    ("yarn", True),
    ("rubygems", True),
    ("generic", False),
    ("rpm", False),
])
def test_valid_pkg_managers(pkg_manager, valid):
    remote_sources = f"""
        remote_sources:
        - name: valid-name
          remote_source:
            repo: https://git.example.com/team/repo.git
            ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
            pkg_managers:
                - {pkg_manager}
        """
    if valid:
        assert read_yaml(dedent(remote_sources), "schemas/container.json")
    else:
        with pytest.raises(OsbsValidationException):
            read_yaml(dedent(remote_sources), "schemas/container.json")

    remote_source = f"""
        remote_source:
          repo: https://git.example.com/team/repo.git
          ref: b55c00f45ec3dfee0c766cea3d395d6e21cc2e5a
          pkg_managers:
            - {pkg_manager}
    """
    if valid:
        read_yaml(dedent(remote_source), "schemas/container.json")
    else:
        with pytest.raises(OsbsValidationException):
            read_yaml(dedent(remote_source), "schemas/container.json")
