"""
Copyright (c) 2015, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

import json
import os
import pytest

from osbs.constants import DEFAULT_NAMESPACE
from osbs.cli.capture import setup_json_capture
from tests.constants import TEST_BUILD


@pytest.fixture  # noqa
def osbs_with_capture(osbs, tmpdir):
    setup_json_capture(osbs, osbs.os_conf, str(tmpdir))
    return osbs


def test_json_capture_no_watch(osbs_with_capture, tmpdir):
    for visit in ["000", "001"]:
        osbs_with_capture.list_builds()
        filename = "get-build.openshift.io_v1_namespaces_{n}_builds_-{v}.json"
        path = os.path.join(str(tmpdir), filename.format(n=DEFAULT_NAMESPACE,
                                                         v=visit))
        assert os.access(path, os.R_OK)
        with open(path) as fp:
            obj = json.load(fp)

        assert obj


def test_json_capture_watch(osbs_with_capture, tmpdir):
    # Take the first two yielded values (fresh object, update)
    # PyCQA/pylint#2731 fixed in 2.4.4, so noqa
    for _ in zip(range(2),  # pylint: disable=W1638
                 osbs_with_capture.os.watch_resource('builds', TEST_BUILD)):
        pass

    filename = "get-build.openshift.io_v1_watch_namespaces_{n}_builds_{b}_-000-000.json"
    path = os.path.join(str(tmpdir), filename.format(n=DEFAULT_NAMESPACE,
                                                     b=TEST_BUILD))
    assert os.access(path, os.R_OK)
    with open(path) as fp:
        obj = json.load(fp)

    assert obj
