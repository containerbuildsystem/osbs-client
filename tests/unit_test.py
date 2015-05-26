"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals
import copy
import inspect
import json
import os
import sys

import pytest
import logging
import six
from .fake_api import openshift, osbs
from osbs.build.manipulate import DockJsonManipulator
from osbs.build.build_response import BuildResponse
from osbs.build.build_request import BuildManager, BuildRequest
from osbs.build.build_request import SimpleBuild, ProductionBuild, ProductionWithoutKojiBuild
from osbs.constants import BUILD_FINISHED_STATES
from osbs.constants import SIMPLE_BUILD_TYPE, PROD_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE
from osbs.exceptions import OsbsValidationException
from osbs.http import Response
from tests.constants import TEST_BUILD, TEST_LABEL, TEST_LABEL_VALUE
from tests.fake_api import ResponseMapping, DEFINITION


logger = logging.getLogger("osbs.tests")


def plugin_value_get(plugins, plugin_type, plugin_name, *args):
    plugins = plugins[plugin_type]
    for plugin in plugins:
        if plugin["name"] == plugin_name:
            break
    else:
        raise RuntimeError("no such plugin")
    result = plugin
    for arg in args:
        result = result[arg]
    return result


def test_set_labels_on_build(openshift):
    l = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
    assert l.json() is not None


def test_get_oauth_token(openshift):
    token = openshift.get_oauth_token()
    assert token is not None


def test_list_builds(openshift):
    l = openshift.list_builds()
    assert l is not None
    assert bool(l.json())  # is there at least something



#####
#
# build/DockJsonManipulator
#
#####

BUILD_JSON = {
    "metadata": {
        "name": "{{NAME}}"
    },
    "kind": "Build",
    "apiVersion": "v1beta1",
    "parameters": {
        "source": {
            "type": "Git",
            "git": {
                "uri": "{{GIT_URI}}"
            }
        },
        "strategy": {
            "type": "Custom",
            "customStrategy": {
                "image": "buildroot",
                "exposeDockerSocket": True,
                "env": [{
                    "name": "DOCK_PLUGINS",
                    "value": "TBD"
                }]
            }
        },
        "output": {
            "imageTag": "{{OUTPUT_IMAGE_TAG}}",
            "registry": "{{REGISTRY_URI}}"
        }
    }
}

INNER_DOCK_JSON = {
    "prebuild_plugins": [
        {
            "name": "change_from_in_dockerfile"
        },
        {
            "args": {
                "key1": {
                    "a": "1",
                    "b": "2"
                },
                "key2": "b"
            },
            "name": "a_plugin"
        },
    ],
    "postbuild_plugins": [
        {
            "args": {
                "image_id": "BUILT_IMAGE_ID"
            },
            "name": "all_rpm_packages"
        },
    ]
}


def test_manipulator():
    m = DockJsonManipulator(BUILD_JSON, INNER_DOCK_JSON)
    assert m is not None

    
def test_manipulator_get_dock_json():
    build_json = copy.deepcopy(BUILD_JSON)
    env_json = build_json['parameters']['strategy']['customStrategy']['env']
    p = [env for env in env_json if env["name"] == "DOCK_PLUGINS"]
    inner = {
        "a": "b"
    }
    p[0]['value'] = json.dumps(inner)
    m = DockJsonManipulator(build_json, None)
    response = m.get_dock_json()
    assert response["a"] == inner["a"]


def test_manipulator_get_dock_json_missing_input():
    build_json = copy.deepcopy(BUILD_JSON)
    build_json['parameters']['strategy']['customStrategy']['env'] = None
    m = DockJsonManipulator(build_json, None)
    with pytest.raises(RuntimeError):
        m.get_dock_json()


def test_manipulator_merge():
    inner = copy.deepcopy(INNER_DOCK_JSON)
    plugin = [x for x in inner['prebuild_plugins'] if x["name"] == "a_plugin"][0]
    m = DockJsonManipulator(None, inner)
    m.dock_json_merge_arg("prebuild_plugins", "a_plugin", "key1", {"a": '3', "z": '9'})
    assert plugin['args']['key1']['a'] == '3'
    assert plugin['args']['key1']['b'] == '2'
    assert plugin['args']['key1']['z'] == '9'


def test_render_simple_request():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type("simple")
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': "master",
        'user': "john-foo",
        'component': "component",
        'registry_uri': "registry.example.com",
        'openshift_uri': "http://openshift/",
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    assert build_json["metadata"]["name"].startswith("component-")
    assert build_json["parameters"]["source"]['git']['uri'] == "http://git/"
    assert build_json["parameters"]["source"]['git']['ref'] == "master"
    assert build_json["parameters"]["output"]['registry'] == "registry.example.com"
    assert build_json["parameters"]["output"]['imageTag'].startswith(
        "john-foo/component:"
    )

    env_vars = build_json['parameters']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)
    assert plugin_value_get(plugins, "postbuild_plugins", "store_metadata_in_osv3", "args", "url") == \
           "http://openshift/"


def test_render_prod_request():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': "master",
        'user': "john-foo",
        'component': "component",
        'registry_uri': "registry.example.com",
        'openshift_uri': "http://openshift/",
        'koji_target': "koji-target",
        'kojiroot': "http://root/",
        'kojihub': "http://hub/",
        'sources_command': "make",
        'architecture': "x86_64",
        'vendor': "Foo Vendor",
        'build_host': "our.build.host.example.com",
        'authoritative_registry': "registry.example.com",
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    assert build_json["metadata"]["name"].startswith("component-")
    assert build_json["parameters"]["source"]['git']['uri'] == "http://git/"
    assert build_json["parameters"]["source"]['git']['ref'] == "master"
    assert build_json["parameters"]["output"]['registry'] == "registry.example.com"
    assert build_json["parameters"]["output"]['imageTag'].startswith(
        "john-foo/component:"
    )

    env_vars = build_json['parameters']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts", "args", "command") == "make"
    assert plugin_value_get(plugins, "prebuild_plugins", "change_source_registry", "args", "registry_uri") == \
        "registry.example.com"
    assert plugin_value_get(plugins, "postbuild_plugins", "tag_by_labels", "args", "registry_uri") == \
        "registry.example.com"
    assert plugin_value_get(plugins, "postbuild_plugins", "store_metadata_in_osv3", "args", "url") == \
        "http://openshift/"
    assert plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "root") == "http://root/"
    assert plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "target") == "koji-target"
    assert plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "hub") == "http://hub/"

    labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile", "args", "labels")

    assert labels is not None
    assert labels['Architecture'] is not None
    assert labels['Authoritative_Registry'] is not None
    assert labels['Build_Host'] is not None
    assert labels['Vendor'] is not None


def test_render_prod_without_koji_request():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type(PROD_WITHOUT_KOJI_BUILD_TYPE)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': "master",
        'user': "john-foo",
        'component': "component",
        'registry_uri': "registry.example.com",
        'openshift_uri': "http://openshift/",
        'sources_command': "make",
        'architecture': "x86_64",
        'vendor': "Foo Vendor",
        'build_host': "our.build.host.example.com",
        'authoritative_registry': "registry.example.com",
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    assert build_json["metadata"]["name"].startswith("component-")
    assert build_json["parameters"]["source"]['git']['uri'] == "http://git/"
    assert build_json["parameters"]["source"]['git']['ref'] == "master"
    assert build_json["parameters"]["output"]['registry'] == "registry.example.com"
    assert build_json["parameters"]["output"]['imageTag'].startswith(
        "john-foo/component:"
    )

    env_vars = build_json['parameters']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts", "args", "command") == "make"
    assert plugin_value_get(plugins, "prebuild_plugins", "change_source_registry", "args", "registry_uri") == \
        "registry.example.com"
    assert plugin_value_get(plugins, "postbuild_plugins", "tag_by_labels", "args", "registry_uri") == \
        "registry.example.com"
    assert plugin_value_get(plugins, "postbuild_plugins", "store_metadata_in_osv3", "args", "url") == \
        "http://openshift/"

    labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile", "args", "labels")

    assert labels is not None
    assert labels['Architecture'] is not None
    assert labels['Authoritative_Registry'] is not None
    assert labels['Build_Host'] is not None
    assert labels['Vendor'] is not None


def test_render_with_yum_repourls():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': "master",
        'user': "john-foo",
        'component': "component",
        'registry_uri': "registry.example.com",
        'openshift_uri': "http://openshift/",
        'koji_target': "koji-target",
        'kojiroot': "http://root/",
        'kojihub': "http://hub/",
        'sources_command': "make",
        'architecture': "x86_64",
        'vendor': "Foo Vendor",
        'build_host': "our.build.host.example.com",
        'authoritative_registry': "registry.example.com",
    }
    build_request = bm.get_build_request_by_type("prod")

    # Test validation for yum_repourls parameter
    kwargs['yum_repourls'] = 'should be a list'
    with pytest.raises(OsbsValidationException):
        build_request.set_params(**kwargs)

    # Use a valid yum_repourls parameter and check the result
    kwargs['yum_repourls'] = ['http://example.com/repo1.repo',
                              'http://example.com/repo2.repo']
    build_request.set_params(**kwargs)
    build_json = build_request.render()
    strategy = build_json['parameters']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in strategy:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    repourls = None
    for d in plugins['prebuild_plugins']:
        if d['name'] == 'add_yum_repo_by_url':
            repourls = d['args']['repourls']

    assert repourls is not None
    assert len(repourls) == 2
    assert 'http://example.com/repo1.repo' in repourls
    assert 'http://example.com/repo2.repo' in repourls

def test_get_user(openshift):
    l = openshift.get_user()
    assert l.json() is not None


def test_watch_build(openshift):
    response = openshift.wait_for_build_to_finish(TEST_BUILD)
    status_lower = response["status"].lower()
    assert response["metadata"]["name"] == TEST_BUILD
    assert status_lower in BUILD_FINISHED_STATES
    assert isinstance(TEST_BUILD, six.text_type)
    assert isinstance(status_lower, six.text_type)


def test_create_build(openshift):
    response = openshift.create_build({})
    assert response is not None
    assert response.json()["metadata"]["name"] == TEST_BUILD
    assert response.json()["status"].lower() in BUILD_FINISHED_STATES


## API tests (osbs.api.OSBS)

def test_list_builds_api(osbs):
    response_list = osbs.list_builds()
    # We should get a response
    assert response_list is not None
    assert len(response_list) > 0
    # response_list is a list of BuildResponse objects
    assert isinstance(response_list[0], BuildResponse)


def test_wait_for_build_to_finish(osbs):
    build_response = osbs.wait_for_build_to_finish(TEST_BUILD)
    assert isinstance(build_response, BuildResponse)


def test_get_build_api(osbs):
    response = osbs.get_build(TEST_BUILD)
    # We should get a BuildResponse
    assert isinstance(response, BuildResponse)


def test_get_build_request_api(osbs):
    build = osbs.get_build_request()
    assert isinstance(build, BuildRequest)
    simple = osbs.get_build_request(SIMPLE_BUILD_TYPE)
    assert isinstance(simple, SimpleBuild)
    prod = osbs.get_build_request(PROD_BUILD_TYPE)
    assert isinstance(prod, ProductionBuild)
    prodwithoutkoji = osbs.get_build_request(PROD_WITHOUT_KOJI_BUILD_TYPE)
    assert isinstance(prodwithoutkoji, ProductionWithoutKojiBuild)


def test_set_labels_on_build_api(osbs):
    labels = {'label1': 'value1', 'label2': 'value2'}
    response = osbs.set_labels_on_build(TEST_BUILD, labels)
    assert isinstance(response, Response)


def test_get_token_api(osbs):
    assert isinstance(osbs.get_token(), bytes)


def test_get_user_api(osbs):
    assert 'fullName' in osbs.get_user()


def test_build_logs_api(osbs):
    response = osbs.get_build_logs(TEST_BUILD)
    # We should get a response.
    assert response is not None
    # The first line of the logs should be 'Step 0 : FROM ...'
    assert response.split('\n')[0].find("Step ") != -1


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_parse_headers():
    rm = ResponseMapping("0.4.1")

    file_name = DEFINITION["/oauth/authorize"]["get"]["file"]
    raw_headers = rm.get_response_content(file_name)

    r = Response(raw_headers=raw_headers)

    assert r.headers is not None
    assert len(r.headers.items()) > 0
    assert r.headers["location"]
