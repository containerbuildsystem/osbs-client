"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from collections import namedtuple
from contextlib import contextmanager
import os
from osbs.conf import Configuration
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

        args_tuple = namedtuple('args', args.keys())
        yield args_tuple(**args)

    @pytest.mark.parametrize(('config', 'kwargs', 'cli_args', 'expected'), [
        ({'default': {'token': 'conf'}},
         {},
         {},
         'conf'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {},
         'conf_file'),

        ({'default': {'token': 'conf',
                      'token_file': 'conf_file'}},
         {},
         {},
         'conf'),

        ({'default': {}},
         {'token': 'kw'},
         {},
         'kw'),

        ({'default': {}},
         {'token_file': 'kw_file'},
         {},
         'kw_file'),

        ({'default': {}},
         {'token': 'kw',
          'token_file': 'kw_file'},
         {},
         'kw'),

        ({'default': {'token': 'conf'}},
         {'token': 'kw'},
         {},
         'kw'),

        ({'default': {'token_file': 'conf_file'}},
         {'token': 'kw'},
         {},
         'kw'),

        ({'default': {'token': 'conf'}},
         {'token_file': 'kw_file'},
         {},
         'kw_file'),

        ({'default': {'token_file': 'conf_file'}},
         {'token_file': 'kw_file'},
         {},
         'kw_file'),

        ({'default': {}},
         {},
         {'token': 'cli'},
         'cli'),

        ({'default': {}},
         {},
         {'token_file': 'cli_file'},
         'cli_file'),

        ({'default': {}},
         {},
         {'token': 'cli',
          'token_file': 'cli_file'},
         'cli'),

        ({'default': {'token': 'conf'}},
         {},
         {'token': 'cli'},
         'cli'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {'token': 'cli'},
         'cli'),

        ({'default': {'token': 'conf'}},
         {},
         {'token_file': 'cli_file'},
         'cli_file'),

        ({'default': {'token_file': 'conf_file'}},
         {},
         {'token_file': 'cli_file'},
         'cli_file'),
    ])
    def test_oauth2_token(self, config, kwargs, cli_args, expected):
        if 'token_file' in kwargs:
            tmpf = self.tmpfile_with_content(kwargs['token_file'])
            kwargs['token_file'] = tmpf.name

        with self.build_cli_args(cli_args) as args:
            with self.config_file(config) as config_file:
                conf = Configuration(conf_file=config_file, cli_args=args,
                                     **kwargs)

                assert conf.get_oauth2_token() == expected
