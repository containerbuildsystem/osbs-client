"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals, print_function

import pytest
from osbs.conf import Configuration
from osbs.api import OSBS
from tests.constants import (TEST_PIPELINE_RUN_TEMPLATE, TEST_OCP_NAMESPACE, TEST_OCP_URL,
                             INPUTS_PATH)
from tempfile import NamedTemporaryFile


@pytest.fixture
def osbs_source():
    with NamedTemporaryFile(mode="wt") as fp:
        fp.write("""
[general]
build_json_dir = {build_json_dir}
[default_source]
openshift_url = {url}
namespace = {namespace}
use_auth = false
pipeline_run_path = {pipeline_run_path}
reactor_config_map = rcm
""".format(build_json_dir=INPUTS_PATH, url=TEST_OCP_URL, namespace=TEST_OCP_NAMESPACE, pipeline_run_path=TEST_PIPELINE_RUN_TEMPLATE)) # noqa E501
        fp.flush()
        dummy_config = Configuration(fp.name, conf_section='default_source')
        osbs = OSBS(dummy_config)

    return osbs


@pytest.fixture
def osbs_binary():
    with NamedTemporaryFile(mode="wt") as fp:
        fp.write("""
[general]
build_json_dir = {build_json_dir}
[default_binary]
openshift_url = /
namespace = {namespace}
use_auth = false
pipeline_run_path = {pipeline_run_path}
reactor_config_map = rcm
""".format(build_json_dir=INPUTS_PATH, namespace=TEST_OCP_NAMESPACE, pipeline_run_path=TEST_PIPELINE_RUN_TEMPLATE)) # noqa E501
        fp.flush()
        dummy_config = Configuration(fp.name, conf_section='default_binary')
        osbs = OSBS(dummy_config)

    return osbs
