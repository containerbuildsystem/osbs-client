"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import copy
import json

import pytest

from osbs.build.manipulate import DockJsonManipulator

from tests.constants import TEST_BUILD_JSON, TEST_INNER_DOCK_JSON


class TestDockJsonManipulator(object):
    def test_manipulator(self):
        m = DockJsonManipulator(TEST_BUILD_JSON, TEST_INNER_DOCK_JSON)
        assert m is not None

    def test_manipulator_remove_plugin(self):
        inner = copy.deepcopy(TEST_INNER_DOCK_JSON)
        m = DockJsonManipulator(TEST_BUILD_JSON, inner)
        m.remove_plugin("postbuild_plugins", "all_rpm_packages")
        assert len([x for x in inner["postbuild_plugins"] if x.get("all_rpm_packages", None)]) == 0

    def test_manipulator_remove_nonexisting_plugin(self):
        inner = copy.deepcopy(TEST_INNER_DOCK_JSON)
        m = DockJsonManipulator(TEST_BUILD_JSON, inner)
        m.remove_plugin("postbuild_plugins", "this-doesnt-exist")

    def test_manipulator_get_dock_json(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        env_json = build_json['spec']['strategy']['customStrategy']['env']
        p = [env for env in env_json if env["name"] == "ATOMIC_REACTOR_PLUGINS"]
        inner = {
            "a": "b"
        }
        p[0]['value'] = json.dumps(inner)
        m = DockJsonManipulator(build_json, None)
        response = m.get_dock_json()
        assert response["a"] == inner["a"]

    def test_manipulator_get_dock_json_missing_input(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        build_json['spec']['strategy']['customStrategy']['env'] = None
        m = DockJsonManipulator(build_json, None)
        with pytest.raises(RuntimeError):
            m.get_dock_json()

    def test_manipulator_merge(self):
        inner = copy.deepcopy(TEST_INNER_DOCK_JSON)
        plugin = [x for x in inner['prebuild_plugins'] if x["name"] == "a_plugin"][0]
        m = DockJsonManipulator(None, inner)
        m.dock_json_merge_arg("prebuild_plugins", "a_plugin", "key1", {"a": '3', "z": '9'})
        assert plugin['args']['key1']['a'] == '3'
        assert plugin['args']['key1']['b'] == '2'
        assert plugin['args']['key1']['z'] == '9'
