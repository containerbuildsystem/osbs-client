"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals

import pytest
import logging
from osbs.conf import Configuration
from osbs.api import OSBS, osbsapi
from osbs.exceptions import OsbsException
import sys
from tempfile import NamedTemporaryFile
from textwrap import dedent
import yaml


logger = logging.getLogger("osbs.tests")


class TestOsbsException(object):
    def test_str(self):
        """
        The str() representation of the exception should include the str()
        representation of the underlying cause.
        """
        class StrRepr(Exception):
            def __str__(self):
                return "str representation"

            def __repr__(self):
                return "repr representation"

        @osbsapi
        def do_raise():
            raise StrRepr

        try:
            do_raise()
        except OsbsException as exc:
            assert "str representation" in str(exc)

    def test_yaml(self):
        """
        Exceptions caused by yaml parsing should include line and column
        """
        @osbsapi
        def do_raise():
            yaml.load(dedent("""\
                items:
                - foo
                bar
                - baz
            """.rstrip()))

        try:
            do_raise()
        except OsbsException as exc:
            str_rep = str(exc)
            assert 'line' in str_rep and 'column' in str_rep


def test_missing_config():
    os_conf = Configuration(conf_file="/nonexistent/path", conf_section="default")  # noqa


def test_no_config():
    os_conf = Configuration(conf_file=None, openshift_uri='https://example:8443')
    assert os_conf.get_openshift_oauth_api_uri() == 'https://example:8443/oauth/authorize'


def test_missing_section():
    with NamedTemporaryFile() as f:
        os_conf = Configuration(conf_file=f.name, conf_section="missing")  # noqa


def test_no_build_image():
    with NamedTemporaryFile(mode='w+') as f:
        f.write("""
[default]
build_host=localhost
""")
        f.flush()
        f.seek(0)
        os_conf = Configuration(conf_file=f.name,
                                conf_section="default")
        assert os_conf.get_build_image() is None


def test_no_inputs():
    with NamedTemporaryFile(mode='w+') as f:
        f.write("""
[general]
build_json_dir=/nonexistent/path/

[default]
openshift_uri=https://172.0.0.1:8443/
registry_uri=127.0.0.1:5000
""")
        f.flush()
        f.seek(0)
        with pytest.raises(OsbsException):
            os_conf = Configuration(conf_file=f.name,
                                    conf_section="default")
            build_conf = Configuration(conf_file=f.name,
                                       conf_section="default")
            osbs = OSBS(os_conf, build_conf)
            osbs.create_build(git_uri="https://example.com/example.git",
                              git_ref="master",
                              user="user",
                              component="component",
                              target="target",
                              architecture="arch")
