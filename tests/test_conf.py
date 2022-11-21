"""
Copyright (c) 2016-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

from contextlib import contextmanager
from flexmock import flexmock
import argparse
from osbs.conf import Configuration
from osbs import utils
import pytest
from tempfile import NamedTemporaryFile
import logging


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
                conf = Configuration(conf_file=config_file, conf_section='default', cli_args=args,
                                     **kwargs)

                assert conf.get_oauth2_token() == expected

    @pytest.mark.parametrize(('config', 'kwargs', 'cli_args', 'expected'), [
        ({'default': {'client_key': 'client_key'}},
         {},
         {},
         {'get_client_key': 'client_key'}),

        ({'default': {}},
         {'client_key': 'client_key'},
         {},
         {'get_client_key': 'client_key'}),

        ({'default': {}},
         {},
         {'client_key': 'client_key'},
         {'get_client_key': 'client_key'}),
    ])
    def test_param_retrieval(self, config, kwargs, cli_args, expected):
        with self.build_cli_args(cli_args) as args:
            with self.config_file(config) as config_file:
                conf = Configuration(conf_file=config_file, conf_section='default', cli_args=args,
                                     **kwargs)

                for fn, value in expected.items():
                    assert getattr(conf, fn)() == value

    @pytest.mark.parametrize(('config', 'expected', 'expected_scratch'), [
        ({
            'default': {'reactor_config_map': 'rcm',
                        'reactor_config_map_scratch': 'rcm_scratch'},
         }, 'rcm', 'rcm_scratch'),
        ({
            'default': {'reactor_config_map': 'rcm'},
         }, 'rcm', None),
        ({
            'default': {'reactor_config_map_scratch': 'rcm_scratch'},
         }, None, 'rcm_scratch'),
        ({
            'default': {},
         }, None, None),
    ])
    def test_reactor_config(self, config, expected, expected_scratch):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file, conf_section='default')

            assert conf.get_reactor_config_map() == expected
            assert conf.get_reactor_config_map_scratch() == expected_scratch

    @pytest.mark.parametrize(('config', 'expected'), [
        ({
            'default': {'pipeline_run_path': 'pipeline_path'},
         }, 'pipeline_path'),
        ({
            'default': {},
         }, None),
    ])
    def test_pipeline_run_path(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file, conf_section='default')

            assert conf.get_pipeline_run_path() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({
            'default': {'cleanup_used_resources': False},
         }, False),
        ({
            'default': {'cleanup_used_resources': True},
         }, True),
        ({
            'default': {},
         }, True),
    ])
    def test_cleanup_used_resources(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file, conf_section='default')

            assert conf.get_cleanup_used_resources() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({
             'default': {'default_buildtime_limit': 1500},
         }, 1500),
        ({
             'default': {'default_buildtime_limit': 2500},
         }, 2500),
        ({
             'default': {},
         }, 10800),
    ])
    def test_default_buildtime_limit(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file, conf_section='default')
        assert conf.get_default_buildtime_limit() == expected

    @pytest.mark.parametrize(('config', 'expected'), [
        ({
             'default': {'max_buildtime_limit': 1500},
         }, 1500),
        ({
             'default': {'max_buildtime_limit': 2500},
         }, 2500),
        ({
             'default': {},
         }, 21600),
    ])
    def test_max_buildtime_limit(self, config, expected):
        with self.config_file(config) as config_file:
            conf = Configuration(conf_file=config_file, conf_section='default')
        assert conf.get_max_buildtime_limit() == expected

    def test_deprecated_warnings(self, caplog):  # noqa:F811
        with caplog.at_level(logging.WARNING):
            assert "it has been deprecated" not in caplog.text
            # kwargs don't get warnings
            self.test_param_retrieval(config={'default': {}},
                                      kwargs={'deprecated_key': 'client_secret'},
                                      cli_args={},
                                      expected={'get_deprecated_key': 'client_secret'})
            assert "it has been deprecated" not in caplog.text
            # cli arguments get warnings
            self.test_param_retrieval(config={'default': {}},
                                      kwargs={},
                                      cli_args={'deprecated_key': 'client_secret'},
                                      expected={'get_deprecated_key': 'client_secret'})
            assert "it has been deprecated" in caplog.text
