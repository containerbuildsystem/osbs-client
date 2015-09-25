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
from osbs.api import OSBS
from osbs.exceptions import OsbsException
from tempfile import NamedTemporaryFile


logger = logging.getLogger("osbs.tests")


def test_missing_config():
    os_conf = Configuration(conf_file="/nonexistent/path",
                            conf_section="default")

def test_no_config():
    os_conf = Configuration(conf_file=None,
                            openshift_uri='https://example:8443')
    assert os_conf.get_openshift_oauth_api_uri() == \
            'https://example:8443/oauth/authorize'

def test_missing_section():
    with NamedTemporaryFile() as f:
        os_conf = Configuration(conf_file=f.name,
                                conf_section="missing")

def test_no_build_type():
    with NamedTemporaryFile(mode='w+') as f:
        f.write("""
[default]
build_host=localhost
""")
        f.flush()
        f.seek(0)
        os_conf = Configuration(conf_file=f.name,
                                conf_section="default")
        assert os_conf.get_build_type() is None

def test_no_inputs():
    with NamedTemporaryFile(mode='w+') as f:
        f.write("""
[general]
build_json_dir=/nonexistent/path/

[default]
build_type=simple
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
