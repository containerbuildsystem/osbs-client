"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from contextlib import contextmanager
from flexmock import flexmock
import argparse
from copy import deepcopy
from osbs.conf import Configuration
from osbs import utils
from osbs.exceptions import OsbsValidationException
from osbs.constants import DEFAULT_ARRANGEMENT_VERSION
import pytest
from tempfile import NamedTemporaryFile


class TestConfiguration(object):
    def tmpfile_with_content(self, value):
        tmpf = NamedTemporaryFile(mode='wt')
        tmpf.write(value)
        tmpf.flush()
        return tmpf

    @contextmanager
    def config_file(self, config):
        with NamedTemporaryFile(mode='wt') as fp:
            tmpfiles = []
            for section, keyvalues in config.items():
                fp.write("\n[{section}]\n".format(section=section))
                for key, value in keyvalues.items():
                    if key == 'token_file':
                        # Create a file with that content
                        tmpf = self.tmpfile_with_content(value)
                        value = tmpf.name

                        # Don't close it (and delete it) until we finish
                        tmpfiles.append(tmpf)

                    fp.write("{key}={value}\n".format(key=key, value=value))

            fp.flush()
            yield fp.name

    @contextmanager
    def build_cli_args(self, args):
        if 'token_file' in args:
            tmpf = self.tmpfile_with_content(args['token_file'])
            args['token_file'] = tmpf.name

        yield argparse.Namespace(**args)

    @pytest.mark.parametrize(('config', 'kwargs', 'cli_args',
                              'login', 'expected'), [
        ({'default': {'token': 'conf'}},
         {},
         {},
         None,
         'conf'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {},
         None,
         'conf_file'),

        ({'default': {'token': 'conf',
                      'token_file': 'conf_file'}},
         {},
         {},
         None,
         'conf'),

        ({'default': {}},
         {'token': 'kw'},
         {},
         None,
         'kw'),

        ({'default': {}},
         {'token_file': 'kw_file'},
         {},
         None,
         'kw_file'),

        ({'default': {}},
         {'token': 'kw',
          'token_file': 'kw_file'},
         {},
         None,
         'kw'),

        ({'default': {'token': 'conf'}},
         {'token': 'kw'},
         {},
         None,
         'kw'),

        ({'default': {'token_file': 'conf_file'}},
         {'token': 'kw'},
         {},
         None,
         'kw'),

        ({'default': {'token': 'conf'}},
         {'token_file': 'kw_file'},
         {},
         None,
         'kw_file'),

        ({'default': {'token_file': 'conf_file'}},
         {'token_file': 'kw_file'},
         {},
         None,
         'kw_file'),

        ({'default': {}},
         {},
         {'token': 'cli'},
         None,
         'cli'),

        ({'default': {}},
         {},
         {'token_file': 'cli_file'},
         None,
         'cli_file'),

        ({'default': {}},
         {},
         {'token': 'cli',
          'token_file': 'cli_file'},
         None,
         'cli'),

        ({'default': {'token': 'conf'}},
         {},
         {'token': 'cli'},
         None,
         'cli'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {'token': 'cli'},
         None,
         'cli'),

        ({'default': {'token': 'conf'}},
         {},
         {'token_file': 'cli_file'},
         None,
         'cli_file'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {'token_file': 'cli_file'},
         None,
         'cli_file'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {},
         'login_file',
         'conf_file'),

        ({'default': {}},
         {},
         {'token_file': 'cli_file'},
         'login_file',
         'cli_file'),

        ({'default': {}},
         {},
         {},
         'login_file',
         'login_file'),
    ])
    def test_oauth2_token(self, config, kwargs, cli_args, login, expected):
        if 'token_file' in kwargs:
            tmpf = self.tmpfile_with_content(kwargs['token_file'])
            kwargs['token_file'] = tmpf.name

        if login:
            login_tmpf = self.tmpfile_with_content(login)

        if 'login_file' == expected:
            (flexmock(utils)
                .should_receive('get_instance_token_file_name')
                .with_args('default')
                .and_return(login_tmpf.name))

        with self.build_cli_args(cli_args) as args:
            with self.config_file(config) as config_file:
                conf = Configuration(conf_file=config_file, cli_args=args,
                                     **kwargs)

                assert conf.get_oauth2_token() == expected

    @pytest.mark.parametrize(('config', 'kwargs', 'cli_args', 'expected'), [
        ({'default': {'client_config_secret': 'client_secret'}},
         {},
         {},
         {'get_client_config_secret': 'client_secret'}),

        ({'default': {}},
         {'client_config_secret': 'client_secret'},
         {},
         {'get_client_config_secret': 'client_secret'}),

        ({'default': {}},
         {},
         {'client_config_secret': 'client_secret'},
         {'get_client_config_secret': 'client_secret'}),
    ])
    def test_param_retrieval(self, config, kwargs, cli_args, expected):
        with self.build_cli_args(cli_args) as args:
            with self.config_file(config) as config_file:
                conf = Configuration(conf_file=config_file, cli_args=args,
                                     **kwargs)

                for fn, value in expected.items():
                    assert getattr(conf, fn)() == value

    @pytest.mark.parametrize(('config', 'expected'), [
        ({'default': {'token_secrets': 'secret'}},
         {'secret': None}),

        ({'default': {'token_secrets': 'secret:'}},
         OsbsValidationException),

        ({'default': {'token_secrets': 'secret:/'}},
         OsbsValidationException),

        ({'default': {'token_secrets': 'secret:path'}},
         {'secret': 'path'}),

        ({'default': {'token_secrets': 'secret:path:with:colons'}},
         {'secret': 'path:with:colons'}),

        ({'default': {'token_secrets': 'secret:path secret2:path2'}},
         {'secret': 'path', 'secret2': 'path2'}),

        ({'default': {'token_secrets': 'secret:path secret2:path2 secret3:path3'}},
         {'secret': 'path', 'secret2': 'path2', 'secret3': 'path3'}),

        ({'default': {'token_secrets': 'secret:path secret2 secret3:path3'}},
         {'secret': 'path', 'secret2': None, 'secret3': 'path3'}),

        ({'default': {'token_secrets': '\n   secret:path     secret2\n\n secret3:path3'}},
         {'secret': 'path', 'secret2': None, 'secret3': 'path3'}),

        ({'default': {'token_secrets': '\t\n   secret:path   \t\t  secret2\n\n '
                      '\tsecret3:path3 \n\t\n'}},
         {'secret': 'path', 'secret2': None, 'secret3': 'path3'}),
    ])
    def test_get_token_secrets(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)

            if expected is not OsbsValidationException:
                assert conf.get_token_secrets() == expected
            else:
                with pytest.raises(OsbsValidationException):
                    conf.get_token_secrets()

    @pytest.mark.parametrize(('config', 'expected'), [
        ({
            'default': {'builder_build_json_dir': 'builder'},
            'general': {'build_json_dir': 'general'},
         }, 'builder'),
        ({
            'default': {},
            'general': {'build_json_dir': 'general'},
         }, 'general'),
    ])
    def test_builder_build_json_dir(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)

            assert conf.get_builder_build_json_store() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({'default': {}}, DEFAULT_ARRANGEMENT_VERSION),

        ({'default': {'arrangement_version': 50}}, 50),
        ({'default': {'arrangement_version': 'one'}}, OsbsValidationException),
    ])
    def test_arrangement_version(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)

        if isinstance(expected, type):
            with pytest.raises(expected):
                conf.get_arrangement_version()
        else:
            assert conf.get_arrangement_version() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({'default': {'smtp_additional_addresses': 'user@example.com'}},
         ['user@example.com']),
        ({'default': {'smtp_additional_addresses': 'user@example.com,user2@example.com'}},
         ['user@example.com', 'user2@example.com']),
        ({'default': {'smtp_additional_addresses': 'user@example.com, user2@example.com'}},
         ['user@example.com', 'user2@example.com']),
        ({'default': {'smtp_additional_addresses': '\tuser@example.com,\tuser2@example.com'}},
         ['user@example.com', 'user2@example.com']),
        ({'default': {'smtp_additional_addresses':
                      '\n  user@example.com,     user2@example.com,\n\n user3@example.com'}},
         ['user@example.com', 'user2@example.com', 'user3@example.com']),
    ])
    def test_get_smtp_additional_addresses(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)
            assert conf.get_smtp_additional_addresses() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({'default': {'smtp_error_addresses': 'user@example.com'}},
         ['user@example.com']),
        ({'default': {'smtp_error_addresses': 'user@example.com,user2@example.com'}},
         ['user@example.com', 'user2@example.com']),
        ({'default': {'smtp_error_addresses': 'user@example.com, user2@example.com'}},
         ['user@example.com', 'user2@example.com']),
        ({'default': {'smtp_error_addresses': '\tuser@example.com,\tuser2@example.com'}},
         ['user@example.com', 'user2@example.com']),
        ({'default': {'smtp_error_addresses':
                      '\n  user@example.com,     user2@example.com,\n\n user3@example.com'}},
         ['user@example.com', 'user2@example.com', 'user3@example.com']),
    ])
    def test_get_smtp_error_addresses(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)
            assert conf.get_smtp_error_addresses() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({'default': {'artifacts_allowed_domains': 'spam.com'}},
         ['spam.com']),

        ({'default': {'artifacts_allowed_domains': 'spam.com,eggs.com'}},
         ['spam.com', 'eggs.com']),

        ({'default': {'artifacts_allowed_domains': 'spam.com, eggs.com'}},
         ['spam.com', 'eggs.com']),

        ({'default': {'artifacts_allowed_domains': '\tspam.com,\teggs.com'}},
         ['spam.com', 'eggs.com']),

        ({'default': {'artifacts_allowed_domains': '\n  spam.com,     eggs.com,\n\n bacon.com'}},
         ['spam.com', 'eggs.com', 'bacon.com']),
    ])
    def test_get_allowed_artifacts_domain(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)
            assert conf.get_artifacts_allowed_domains() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({'default': {'equal_labels':
                      'label1:label2, label3:label4:label5, label6:label7'}},
         [['label1', 'label2'],
          ['label3', 'label4', 'label5'],
          ['label6', 'label7']]),

        ({'default': {'equal_labels':
                      '\t      label1:label2   , label3:label4:label5, label6:label7    '}},
         [['label1', 'label2'],
          ['label3', 'label4', 'label5'],
          ['label6', 'label7']]),

        ({'default': {'equal_labels':
                      '     label1:label2: \t\t, label3:label4:label5, label6:label7    '}},
         OsbsValidationException),

        ({'default': {'equal_labels':
                      'label1:label2,   , label3:label4:label5, label6:label7    '}},
         OsbsValidationException),

        ({'default': {'equal_labels': 'label1:'}},
         OsbsValidationException),

        ({'default': {'equal_labels': 'label1,'}},
         OsbsValidationException),

        ({'default': {'equal_labels': 'label1 '}},
         OsbsValidationException),

        ({'default': {'equal_labels': ',label1 '}},
         OsbsValidationException),

        ({'default': {'equal_labels': ':label1 '}},
         OsbsValidationException),
    ])
    def test_get_equal_labels(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)

            if expected is OsbsValidationException:
                with pytest.raises(OsbsValidationException):
                    conf.get_equal_labels()
            else:
                assert conf.get_equal_labels() == expected

    @pytest.mark.parametrize(('platform', 'config', 'kwargs', 'expected'), [
        ('',
         {},
         {},
         {}),
        ('',
         {'default': {'node_selector.expense': 'ride=taxi.com'}},
         {},
         {}),
        ('',
         {'default': {}},
         {'node_selector.expense': 'ride=taxi.com'},
         {}),
        ('meal',
         {'default': {'node_selector.meal': 'breakfast=eggs.com',
                      'node_selector.expense': 'ride=taxi.com'}},
         {},
         {'breakfast': 'eggs.com'}),
        ('meal',
         {'default': {'node_selector.meal': 'breakfast=eggs.com, lunch=ham.com',
                      'node_selector.expense': 'ride=taxi.com'}},
         {},
         {'breakfast': 'eggs.com', 'lunch': 'ham.com'}),
        ('meal',
         {'default': {}},
         {'node_selector.meal': 'breakfast=eggs.com', 'node_selector.expense': 'ride=taxi.com'},
         {'breakfast': 'eggs.com'}),
        ('meal',
         {'default': {'node_selector.meal': 'breakfast=eggs.com'}},
         {'node_selector.meal': 'breakfast=bacon.com', 'node_selector.expense': 'ride=taxi.com'},
         {'breakfast': 'bacon.com'}),
        ('meal',
         {'default': {}},
         {'node_selector.expense': 'ride=taxi.com'},
         {}),
        ('meal',
         {'default': {'node_selector.meal': 'none'}},
         {'node_selector.expense': 'ride=taxi.com'},
         {}),
    ])
    def test_get_node_selector_platform(self, platform, kwargs, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file, **kwargs)
            assert conf.get_platform_node_selector(platform) == expected

    @pytest.mark.parametrize(('config', 'expected', 'valid'), [
        ({}, {}, True),
        ({'platform:ham': {}}, {'ham': {'architecture': 'ham', 'enable_v1': False}}, True),
        ({'platform:ham': {}, 'platform:eggs': {}},
         {'ham': {'architecture': 'ham', 'enable_v1': False},
          'eggs': {'architecture': 'eggs', 'enable_v1': False}}, True),
        ({'platform:ham': {}, 'platform:eggs': {}, 'fedora': {}},
         {'ham': {'architecture': 'ham', 'enable_v1': False},
          'eggs': {'architecture': 'eggs', 'enable_v1': False}}, True),
        ({'platform:ham': {'architecture': 'bacon', 'enable_v1': 'true'}},
         {'ham': {'architecture': 'bacon', 'enable_v1': True}}, True),
        ({'platform:ham': {'architecture': 'bacon'},
          'platform:eggs': {'enable_v1': 'true'}, 'fedora': {}},
         {'ham': {'architecture': 'bacon', 'enable_v1': False},
          'eggs': {'architecture': 'eggs', 'enable_v1': True}}, True),
        ({'platform:ham': {'enable_v1': 'true'}, 'platform:eggs': {'enable_v1': 'true'}},
         {}, False),
    ])
    def test_get_platform_descriptors(self, config, expected, valid):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)
            if valid:
                assert conf.get_platform_descriptors() == expected
            else:
                with pytest.raises(OsbsValidationException):
                    conf.get_platform_descriptors()

    @pytest.mark.parametrize(('platform', 'config', 'expected', 'valid'), [
        (None, {}, ['v1', 'v2'], True),
        (None, {'default': {'registry_api_versions': 'v1'}}, ['v1'], True),
        (None, {'default': {'registry_api_versions': 'v2'}}, ['v2'], True),
        (None, {'default': {'registry_api_versions': 'v1,v2'}}, ['v1', 'v2'], True),
        ('ham', {'default': {'registry_api_versions': 'v1,v2'}}, ['v2'], True),
        (None, {'platform:ham': {}}, ['v1', 'v2'], True),
        ('ham', {'platform:ham': {}}, ['v2'], True),
        ('ham', {'platform:ham': {'enable_v1': 'true'}}, ['v1', 'v2'], True),
        ('ham', {'default': {'registry_api_versions': 'v1'},
                 'platform:ham': {}}, ['v1', 'v2'], False),
    ])
    def test_get_registry_api_versions(self, platform, config, expected, valid):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file)
            if valid:
                assert conf.get_registry_api_versions(platform) == expected
            else:
                with pytest.raises(OsbsValidationException):
                    conf.get_registry_api_versions(platform)

    @pytest.mark.parametrize('nodeselector_type', [
        'scratch_build_node_selector',
        'explicit_build_node_selector',
        'auto_build_node_selector',
        'isolated_build_node_selector',
    ])
    @pytest.mark.parametrize(('config', 'expected'), [
        ({},
         {}),
        ({'default': {}},
         {}),
        ({'default': {'node_selector': 'breakfast=eggs.com'}},
         {'breakfast': 'eggs.com'}),
        ({'default': {'node_selector': 'breakfast=eggs.com, lunch=ham.com'}},
         {'breakfast': 'eggs.com', 'lunch': 'ham.com'}),
    ])
    def test_get_node_selector_types(self, nodeselector_type, config, expected):
        myconfig = deepcopy(config)
        if 'default' in myconfig:
            if 'node_selector' in myconfig['default']:
                myconfig['default'][nodeselector_type] = myconfig['default'].pop('node_selector')

        with self.config_file(myconfig) as config_file:
            conf = Configuration(conf_file=config_file)
            assert getattr(conf, "get_" + nodeselector_type)() == expected
