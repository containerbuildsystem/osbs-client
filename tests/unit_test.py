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
import shutil
import sys
from types import GeneratorType

from flexmock import flexmock
import pytest
import logging
import six
from .fake_api import openshift, osbs
from osbs.build.manipulate import DockJsonManipulator
from osbs.build.build_response import BuildResponse
from osbs.build.build_request import BuildManager, BuildRequest
from osbs.build.build_request import SimpleBuild, ProductionBuild
from osbs.build.spec import BuildIDParam
from osbs.cli.main import str_on_2_unicode_on_3
from osbs.constants import BUILD_FINISHED_STATES
from osbs.constants import SIMPLE_BUILD_TYPE, PROD_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE
from osbs.constants import PROD_WITH_SECRET_BUILD_TYPE
from osbs.exceptions import OsbsValidationException
from osbs.http import Response
from osbs import utils
from tests.constants import TEST_BUILD, TEST_LABEL, TEST_LABEL_VALUE
from tests.constants import TEST_GIT_URI, TEST_GIT_REF, TEST_USER
from tests.constants import TEST_COMPONENT, TEST_TARGET, TEST_ARCH
from tests.fake_api import ResponseMapping, get_definition_for


logger = logging.getLogger("osbs.tests")


class NoSuchPluginException(Exception):
    pass


def get_plugin(plugins, plugin_type, plugin_name):
    plugins = plugins[plugin_type]
    for plugin in plugins:
        if plugin["name"] == plugin_name:
            return plugin
    else:
        raise NoSuchPluginException()


def plugin_value_get(plugins, plugin_type, plugin_name, *args):
    result = get_plugin(plugins, plugin_type, plugin_name)
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
    "kind": "BuildConfig",
    "apiVersion": "v1beta3",
    "spec": {
        "triggers": [
            {
                "type": "ImageChange",
                "imageChange": {
                "from": {
                    "kind": "ImageStreamTag",
                    "name": "{{BASE_IMAGE_STREAM}}"
                }
                }
            }
        ],
        "source": {
            "type": "Git",
            "git": {
                "uri": "{{GIT_URI}}"
            }
        },
        "strategy": {
            "type": "Custom",
            "customStrategy": {
                "from": {
                    "kind": "ImageStreamTag",
                    "name": "buildroot:latest"
                },
                "exposeDockerSocket": True,
                "env": [{
                    "name": "DOCK_PLUGINS",
                    "value": "TBD"
                }]
            }
        },
        "output": {
            "to": {
                "kind": "DockerImage",
                "name": "{{REGISTRY_URI}}/{{OUTPUT_IMAGE_TAG}}"
            }
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


def test_manipulator_remove_plugin():
    inner = copy.deepcopy(INNER_DOCK_JSON)
    m = DockJsonManipulator(BUILD_JSON, inner)
    m.remove_plugin("postbuild_plugins", "all_rpm_packages")
    assert len([x for x in inner["postbuild_plugins"] if x.get("all_rpm_packages", None)]) == 0


def test_manipulator_remove_nonexisting_plugin():
    inner = copy.deepcopy(INNER_DOCK_JSON)
    m = DockJsonManipulator(BUILD_JSON, inner)
    m.remove_plugin("postbuild_plugins", "this-doesnt-exist")


def test_manipulator_get_dock_json():
    build_json = copy.deepcopy(BUILD_JSON)
    env_json = build_json['spec']['strategy']['customStrategy']['env']
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
    build_json['spec']['strategy']['customStrategy']['env'] = None
    m = DockJsonManipulator(build_json, None)
    with pytest.raises(RuntimeError):
        m.get_dock_json()


def test_build_request_is_auto_instantiated():
    build_json = copy.deepcopy(BUILD_JSON)
    br = BuildRequest('something')
    flexmock(br).should_receive('template').and_return(build_json)
    assert br.is_auto_instantiated() == True


def test_build_request_isnt_auto_instantiated():
    build_json = copy.deepcopy(BUILD_JSON)
    build_json['spec']['triggers'] = []
    br = BuildRequest('something')
    flexmock(br).should_receive('template').and_return(build_json)
    assert br.is_auto_instantiated() == False


def test_manipulator_merge():
    inner = copy.deepcopy(INNER_DOCK_JSON)
    plugin = [x for x in inner['prebuild_plugins'] if x["name"] == "a_plugin"][0]
    m = DockJsonManipulator(None, inner)
    m.dock_json_merge_arg("prebuild_plugins", "a_plugin", "key1", {"a": '3', "z": '9'})
    assert plugin['args']['key1']['a'] == '3'
    assert plugin['args']['key1']['b'] == '2'
    assert plugin['args']['key1']['z'] == '9'


def test_render_simple_request_incorrect_postbuild(tmpdir):
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")

    # Make temporary copies of the JSON files
    for basename in ['simple.json', 'simple_inner.json']:
        shutil.copy(os.path.join(inputs_path, basename),
                    os.path.join(str(tmpdir), basename))

    # Create an inner JSON description which incorrectly runs the exit
    # plugins as postbuild plugins.
    with open(os.path.join(str(tmpdir), 'simple_inner.json'), 'r+') as inner:
        inner_json = json.load(inner)

        # Re-write all the exit plugins as postbuild plugins
        exit_plugins = inner_json['exit_plugins']
        inner_json['postbuild_plugins'].extend(exit_plugins)
        del inner_json['exit_plugins']

        inner.seek(0)
        json.dump(inner_json, inner)
        inner.truncate()

    bm = BuildManager(str(tmpdir))
    build_request = bm.get_build_request_by_type("simple")
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': "master",
        'user': "john-foo",
        'component': "component",
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'registry_uri': "registry.example.com",
        'openshift_uri': "http://openshift/",
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    env_vars = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    # Check the store_metadata_in_osv3's uri parameter was set
    # correctly, even though it was listed as a postbuild plugin.
    assert plugin_value_get(plugins, "postbuild_plugins", "store_metadata_in_osv3", "args", "url") == \
           "http://openshift/"


def test_render_simple_request():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type("simple")
    name_label = "fedora/resultingimage"
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': name_label,
        'registry_uri': "http://registry.example.com:5000",
        'openshift_uri': "http://openshift/",
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    assert build_json["metadata"]["name"] == name_label.replace('/', '-')
    assert "triggers" not in build_json["spec"]
    assert build_json["spec"]["source"]["git"]["uri"] == "http://git/"
    assert build_json["spec"]["source"]["git"]["ref"] == "master"
    assert build_json["spec"]["output"]["to"]["name"].startswith(
        "registry.example.com:5000/john-foo/component:"
    )

    env_vars = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)
    assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args", "url") == \
           "http://openshift/"


def test_render_prod_request_with_repo():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
    name_label = "fedora/resultingimage"
    assert isinstance(build_request, ProductionBuild)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': name_label,
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
        'yum_repourls': ["http://example.com/my.repo"],
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    assert build_json["metadata"]["name"] == name_label.replace('/', '-')
    assert "triggers" not in build_json["spec"]
    assert build_json["spec"]["source"]["git"]["uri"] == "http://git/"
    assert build_json["spec"]["source"]["git"]["ref"] == "master"
    assert build_json["spec"]["output"]["to"]["name"].startswith(
        "registry.example.com/john-foo/component:"
    )

    env_vars = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
    assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts", "args", "command") == "make"
    assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args", "parent_registry") == \
           "registry.example.com"
    assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args", "url") == \
           "http://openshift/"
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "koji")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
    assert 'sourceSecret' not in build_json["spec"]["source"]
    assert 'sourceSecretName' not in build_json["spec"]["source"]
    plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url", "args", "repourls") == \
        ["http://example.com/my.repo"]

    labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile", "args", "labels")

    assert labels is not None
    assert labels['Architecture'] is not None
    assert labels['Authoritative_Registry'] is not None
    assert labels['Build_Host'] is not None
    assert labels['Vendor'] is not None


def test_render_prod_request():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
    name_label = "fedora/resultingimage"
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': name_label,
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

    assert build_json["metadata"]["name"] == name_label.replace('/', '-')
    assert "triggers" not in build_json["spec"]
    assert build_json["spec"]["source"]["git"]["uri"] == "http://git/"
    assert build_json["spec"]["source"]["git"]["ref"] == "master"
    assert build_json["spec"]["output"]["to"]["name"].startswith(
        "registry.example.com/john-foo/component:"
    )

    env_vars = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
    assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts", "args", "command") == "make"
    assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args", "parent_registry") == \
        "registry.example.com"
    assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args", "url") == \
        "http://openshift/"
    assert plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "root") == "http://root/"
    assert plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "target") == "koji-target"
    assert plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "hub") == "http://hub/"
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
    assert 'sourceSecret' not in build_json["spec"]["source"]
    assert 'sourceSecretName' not in build_json["spec"]["source"]

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
    name_label = "fedora/resultingimage"
    assert isinstance(build_request, ProductionBuild)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': name_label,
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

    assert build_json["metadata"]["name"] == name_label.replace('/', '-')
    assert "triggers" not in build_json["spec"]
    assert build_json["spec"]["source"]["git"]["uri"] == "http://git/"
    assert build_json["spec"]["source"]["git"]["ref"] == "master"
    assert build_json["spec"]["output"]["to"]["name"].startswith(
        "registry.example.com/john-foo/component:"
    )

    env_vars = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in env_vars:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
    assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts", "args", "command") == "make"
    assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args", "parent_registry") == \
        "registry.example.com"
    assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args", "url") == \
        "http://openshift/"

    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "koji")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
    assert 'sourceSecret' not in build_json["spec"]["source"]
    assert 'sourceSecretName' not in build_json["spec"]["source"]

    labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile", "args", "labels")

    assert labels is not None
    assert labels['Architecture'] is not None
    assert labels['Authoritative_Registry'] is not None
    assert labels['Build_Host'] is not None
    assert labels['Vendor'] is not None


def test_render_prod_with_secret_request():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type(PROD_WITH_SECRET_BUILD_TYPE)
    assert isinstance(build_request, ProductionBuild)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'registry_uri': "",
        'pulp_registry': "registry.example.com",
        'nfs_server_path': "server:path",
        'openshift_uri': "http://openshift/",
        'koji_target': "koji-target",
        'kojiroot': "http://root/",
        'kojihub': "http://hub/",
        'sources_command': "make",
        'architecture': "x86_64",
        'vendor': "Foo Vendor",
        'build_host': "our.build.host.example.com",
        'authoritative_registry': "registry.example.com",
        'source_secret': 'mysecret',
    }
    build_request.set_params(**kwargs)
    build_json = build_request.render()

    assert build_json["spec"]["source"]["sourceSecret"]["name"] == "mysecret"
    assert build_json["spec"]["source"]["sourceSecretName"] == "mysecret"

    strategy = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in strategy:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    assert plugins_json is not None
    plugins = json.loads(plugins_json)

    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
    assert get_plugin(plugins, "prebuild_plugins", "koji")
    assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
    assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "import_image")


def test_render_with_yum_repourls():
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
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
    strategy = build_json['spec']['strategy']['customStrategy']['env']
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

    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "prebuild_plugins", "koji")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
    with pytest.raises(NoSuchPluginException):
        assert get_plugin(plugins, "postbuild_plugins", "import_image")


def test_render_prod_with_pulp_no_auth():
    """
    Rendering should fail if pulp is specified but auth config isn't
    """
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")
    bm = BuildManager(inputs_path)
    build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'registry_uri': "registry.example.com",
        'openshift_uri': "http://openshift/",
        'sources_command': "make",
        'architecture': "x86_64",
        'vendor': "Foo Vendor",
        'build_host': "our.build.host.example.com",
        'authoritative_registry': "registry.example.com",
        'pulp_registry': "foo",
    }
    build_request.set_params(**kwargs)
    with pytest.raises(OsbsValidationException):
        build_json = build_request.render()


def test_render_prod_request_with_trigger(tmpdir):
    this_file = inspect.getfile(test_render_prod_request)
    this_dir = os.path.dirname(this_file)
    parent_dir = os.path.dirname(this_dir)
    inputs_path = os.path.join(parent_dir, "inputs")

    # Make temporary copies of the JSON files
    for basename in ['prod.json', 'prod_inner.json']:
        shutil.copy(os.path.join(inputs_path, basename),
                    os.path.join(str(tmpdir), basename))

    # Create a build JSON description with an image change trigger
    with open(os.path.join(str(tmpdir), 'prod.json'), 'r+') as prod_json:
        build_json = json.load(prod_json)

        # Add the image change trigger
        build_json['spec']['triggers'] = [
            {
                "type": "ImageChange",
                "imageChange": {
                    "from": {
                        "kind": "ImageStreamTag",
                        "name": "{{BASE_IMAGE_STREAM}}"
                    }
                }
            }
        ]

        prod_json.seek(0)
        json.dump(build_json, prod_json)
        prod_json.truncate()

    bm = BuildManager(str(tmpdir))
    build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
    name_label = "fedora/resultingimage"
    kwargs = {
        'git_uri': "http://git/",
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_REF,
        'user': "john-foo",
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': name_label,
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

    assert "triggers" in build_json["spec"]
    assert build_json["spec"]["triggers"][0]\
        ["imageChange"]["from"]["name"] == 'fedora'

    strategy = build_json['spec']['strategy']['customStrategy']['env']
    plugins_json = None
    for d in strategy:
        if d['name'] == 'DOCK_PLUGINS':
            plugins_json = d['value']
            break

    plugins = json.loads(plugins_json)
    assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
    assert plugin_value_get(plugins, "prebuild_plugins",
                            "check_and_set_rebuild", "args",
                            "url") == kwargs["openshift_uri"]
    assert get_plugin(plugins, "postbuild_plugins", "import_image")
    assert plugin_value_get(plugins,
                            "postbuild_plugins", "import_image", "args",
                            "imagestream") == name_label.replace('/', '-')
    expected_repo = os.path.join(kwargs["registry_uri"], name_label)
    assert plugin_value_get(plugins,
                            "postbuild_plugins", "import_image", "args",
                            "docker_image_repo") == expected_repo
    assert plugin_value_get(plugins,
                            "postbuild_plugins", "import_image", "args",
                            "url") == kwargs["openshift_uri"]


def test_get_user(openshift):
    l = openshift.get_user()
    assert l.json() is not None


def test_watch_build(openshift):
    response = openshift.wait_for_build_to_finish(TEST_BUILD)
    status_lower = response["status"]["phase"].lower()
    assert response["metadata"]["name"] == TEST_BUILD
    assert status_lower in BUILD_FINISHED_STATES
    assert isinstance(TEST_BUILD, six.text_type)
    assert isinstance(status_lower, six.text_type)


def test_create_build(openshift):
    response = openshift.create_build({})
    assert response is not None
    assert response.json()["metadata"]["name"] == TEST_BUILD
    assert response.json()["status"]["phase"].lower() in BUILD_FINISHED_STATES


## API tests (osbs.api.OSBS)

def test_list_builds_api(osbs):
    response_list = osbs.list_builds()
    # We should get a response
    assert response_list is not None
    assert len(response_list) > 0
    # response_list is a list of BuildResponse objects
    assert isinstance(response_list[0], BuildResponse)


def test_create_prod_build(osbs):
    # TODO: test situation when a buildconfig already exists
    class MockParser(object):
        labels = {'Name': 'fedora23/something'}
        baseimage = 'fedora23/python'
    (flexmock(utils)
     .should_receive('get_df_parser')
     .with_args(TEST_GIT_URI, TEST_GIT_REF)
     .and_return(MockParser()))
    response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF, TEST_GIT_REF, TEST_USER,
                                      TEST_COMPONENT, TEST_TARGET, TEST_ARCH)
    assert isinstance(response, BuildResponse)


def test_create_prod_with_secret_build(osbs):
    # TODO: test situation when a buildconfig already exists
    class MockParser(object):
        labels = {'Name': 'fedora23/something'}
        baseimage = 'fedora23/python'
    (flexmock(utils)
     .should_receive('get_df_parser')
     .with_args(TEST_GIT_URI, TEST_GIT_REF)
     .and_return(MockParser()))
    response = osbs.create_prod_with_secret_build(TEST_GIT_URI, TEST_GIT_REF,
                                                  TEST_GIT_REF, TEST_USER,
                                                  TEST_COMPONENT, TEST_TARGET,
                                                  TEST_ARCH)
    assert isinstance(response, BuildResponse)


def test_create_prod_without_koji_build(osbs):
    # TODO: test situation when a buildconfig already exists
    class MockParser(object):
        labels = {'Name': 'fedora23/something'}
        baseimage = 'fedora23/python'
    (flexmock(utils)
     .should_receive('get_df_parser')
     .with_args(TEST_GIT_URI, TEST_GIT_REF)
     .and_return(MockParser()))
    response = osbs.create_prod_without_koji_build(TEST_GIT_URI, TEST_GIT_REF,
                                                   TEST_GIT_REF, TEST_USER,
                                                   TEST_COMPONENT, TEST_ARCH)
    assert isinstance(response, BuildResponse)


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
    assert isinstance(prodwithoutkoji, ProductionBuild)


def test_set_labels_on_build_api(osbs):
    labels = {'label1': 'value1', 'label2': 'value2'}
    response = osbs.set_labels_on_build(TEST_BUILD, labels)
    assert isinstance(response, Response)


def test_set_annotations_on_build_api(osbs):
    annotations = {'ann1': 'value1', 'ann2': 'value2'}
    response = osbs.set_annotations_on_build(TEST_BUILD, annotations)
    assert isinstance(response, Response)


def test_get_token_api(osbs):
    assert isinstance(osbs.get_token(), bytes)


def test_get_user_api(osbs):
    assert 'name' in osbs.get_user()['metadata']


def test_build_logs_api(osbs):
    logs = osbs.get_build_logs(TEST_BUILD)
    assert isinstance(logs, tuple(list(six.string_types) + [bytes]))
    assert logs == b"line 1"


def test_build_logs_api_follow(osbs):
    logs = osbs.get_build_logs(TEST_BUILD, follow=True)
    assert isinstance(logs, GeneratorType)
    assert next(logs) == "line 1"
    with pytest.raises(StopIteration):
        assert next(logs)


@pytest.mark.parametrize('decode_docker_logs', [True, False])
def test_build_logs_api_from_docker(osbs, decode_docker_logs):
    logs = osbs.get_docker_build_logs(TEST_BUILD, decode_logs=decode_docker_logs)
    assert isinstance(logs, tuple(list(six.string_types) + [bytes]))
    assert logs.split('\n')[0].find("Step ") != -1


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_parse_headers():
    rm = ResponseMapping("0.5.4")

    file_name = get_definition_for("/oauth/authorize")["get"]["file"]
    raw_headers = rm.get_response_content(file_name)

    r = Response(raw_headers=raw_headers)

    assert r.headers is not None
    assert len(r.headers.items()) > 0
    assert r.headers["location"]


def test_build_id_param_shorten_id():
    p = BuildIDParam()
    p.value = "x" * 63

    val = p.value

    assert len(val) == 63


def test_build_id_param_raise_exc():
    p = BuildIDParam()
    with pytest.raises(OsbsValidationException):
        p.value = r"\\\\@@@@||||"


def test_force_str():
    b = b"s"
    if sys.version_info[0] == 3:
        s = "s"
        assert str_on_2_unicode_on_3(s) == s
        assert str_on_2_unicode_on_3(b) == s
    else:
        s = u"s"
        assert str_on_2_unicode_on_3(s) == b
        assert str_on_2_unicode_on_3(b) == b
