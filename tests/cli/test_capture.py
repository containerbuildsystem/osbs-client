"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import json
import os
import pytest

from osbs.constants import DEFAULT_NAMESPACE
from osbs.cli.capture import setup_json_capture
from tests.fake_api import openshift, osbs
from tests.constants import TEST_BUILD
from osbs.conf import Configuration


API_VER = Configuration.get_openshift_api_version()
API_PREFIX = "/oapi/{v}/".format(v=API_VER)
PREFIX = API_PREFIX.replace('/', '_')


@pytest.fixture
def osbs_with_capture(osbs, tmpdir):
    setup_json_capture(osbs, osbs.os_conf, str(tmpdir))
    return osbs


def test_json_capture_no_watch(osbs_with_capture, tmpdir):
    for visit in ["000", "001"]:
        osbs_with_capture.list_builds()
        filename = "get-{p}namespaces_{n}_builds_-{v}.json"
        path = os.path.join(str(tmpdir), filename.format(p=PREFIX,
                                                         n=DEFAULT_NAMESPACE,
                                                         v=visit))
        assert os.access(path, os.R_OK)
        with open(path) as fp:
            obj = json.load(fp)

        assert obj

def test_json_capture_watch(osbs_with_capture, tmpdir):
    osbs_with_capture.wait_for_build_to_finish(TEST_BUILD)
    filename = "get-{p}watch_namespaces_{n}_builds_{b}_-000-000.json"
    path = os.path.join(str(tmpdir), filename.format(p=PREFIX,
                                                     n=DEFAULT_NAMESPACE,
                                                     b=TEST_BUILD))
    assert os.access(path, os.R_OK)
    with open(path) as fp:
        obj = json.load(fp)

    assert obj
