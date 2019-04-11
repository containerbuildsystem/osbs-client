"""
Copyright (c) 2015, 2016, 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, print_function

import copy
import glob
import json
import os
import fnmatch
from pkg_resources import parse_version
import shutil
import six

from osbs.build.build_request import BuildRequest
from osbs.api import OSBS
from osbs.constants import (DEFAULT_OUTER_TEMPLATE,
                            DEFAULT_INNER_TEMPLATE, SECRETS_PATH,
                            ORCHESTRATOR_INNER_TEMPLATE, WORKER_INNER_TEMPLATE,
                            DEFAULT_ARRANGEMENT_VERSION,
                            REACTOR_CONFIG_ARRANGEMENT_VERSION,
                            BUILD_TYPE_WORKER, BUILD_TYPE_ORCHESTRATOR,
                            ADDITIONAL_TAGS_FILE)
from osbs.exceptions import OsbsValidationException
from osbs import __version__ as expected_version
from osbs.conf import Configuration
from osbs.repo_utils import RepoInfo, RepoConfiguration, AdditionalTagsConfig

from flexmock import flexmock
import pytest

from tests.constants import (INPUTS_PATH, TEST_BUILD_CONFIG, TEST_BUILD_JSON,
                             TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_GIT_URI_HUMAN_NAME,
                             TEST_FILESYSTEM_KOJI_TASK_ID, TEST_SCRATCH_BUILD_NAME,
                             TEST_ISOLATED_BUILD_NAME, TEST_FLATPAK_BASE_IMAGE)

USE_DEFAULT_TRIGGERS = object()


# Don't use REACTOR_CONFIG_ARRANGEMENT templates
TEST_ARRANGEMENT_VERSION = min(DEFAULT_ARRANGEMENT_VERSION,
                               REACTOR_CONFIG_ARRANGEMENT_VERSION - 1)


class NoSuchPluginException(Exception):
    pass


def get_sample_prod_params():
    return {
        'git_uri': TEST_GIT_URI,
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_BRANCH,
        'user': 'john-foo',
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'registry_uri': 'registry.example.com',
        'source_registry_uri': 'registry.example.com',
        'openshift_uri': 'http://openshift/',
        'builder_openshift_url': 'http://openshift/',
        'koji_target': 'koji-target',
        'kojiroot': 'http://root/',
        'kojihub': 'http://hub/',
        'sources_command': 'make',
        'vendor': 'Foo Vendor',
        'authoritative_registry': 'registry.example.com',
        'distribution_scope': 'authoritative-source-only',
        'registry_api_versions': ['v2'],
        'smtp_host': 'smtp.example.com',
        'smtp_from': 'user@example.com',
        'proxy': 'http://proxy.example.com',
        'platforms': ['x86_64'],
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
        'build_from': 'image:buildroot:latest',
        'osbs_api': MockOSBSApi()
    }


def MockOSBSApi(config_map_data=None):
    class MockConfigMap(object):
        def __init__(self, data):
            self.data = data or {}

        def get_data_by_key(self, key=None):
            return self.data

    mock_osbs = flexmock(OSBS)
    flexmock(mock_osbs).should_receive('get_config_map').and_return(MockConfigMap(config_map_data))
    return mock_osbs


def get_plugins_from_build_json(build_json):
    env_vars = build_json['spec']['strategy']['customStrategy']['env']
    plugins = None

    for d in env_vars:
        if d['name'] == 'ATOMIC_REACTOR_PLUGINS':
            plugins = json.loads(d['value'])
            break

    assert plugins is not None
    return plugins


def get_plugin(plugins, plugin_type, plugin_name):
    plugins = plugins[plugin_type]
    for plugin in plugins:
        if plugin["name"] == plugin_name:
            return plugin
    else:
        raise NoSuchPluginException()


def has_plugin(plugins, plugin_type, plugin_name):
    try:
        get_plugin(plugins, plugin_type, plugin_name)
    except NoSuchPluginException:
        return False
    return True


def plugin_value_get(plugins, plugin_type, plugin_name, *args):
    result = get_plugin(plugins, plugin_type, plugin_name)
    for arg in args:
        result = result[arg]
    return result


def get_secret_mountpath_by_name(build_json, name):
    secrets = build_json['spec']['strategy']['customStrategy']['secrets']
    named_secrets = [secret for secret in secrets
                     if secret['secretSource']['name'] == name]
    assert len(named_secrets) == 1
    secret = named_secrets[0]
    assert 'mountPath' in secret
    return secret['mountPath']


class TestBuildRequest(object):

    def assert_import_image_plugin(self, plugins, name_label, registry_uri,
                                   openshift_uri, use_auth, insecure_registry):
        phase = 'postbuild_plugins'
        plugin = 'import_image'

        assert get_plugin(plugins, phase, plugin)
        plugin_args = plugin_value_get(plugins, phase, plugin, 'args')

        assert plugin_args['imagestream'] == name_label.replace('/', '-')

        expected_repo = os.path.join(registry_uri, name_label)
        expected_repo = expected_repo.replace('https://', '')
        expected_repo = expected_repo.replace('http://', '')
        assert plugin_args['docker_image_repo'] == expected_repo

        assert plugin_args['url'] == openshift_uri
        assert plugin_args.get('use_auth') == use_auth
        assert plugin_args.get('insecure_registry', False) == insecure_registry

    def assert_koji_upload_plugin(self, plugins, use_auth, prefer_schema1_digest, valid=True):
        phase = 'postbuild_plugins'
        name = 'koji_upload'
        if not valid:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, phase, name)
        else:
            assert get_plugin(plugins, phase, name)
            plugin_args = plugin_value_get(plugins, phase, name, 'args')
            assert plugin_args.get('koji_upload_dir')

            if use_auth is not None:
                assert plugin_args['use_auth'] == use_auth
            else:
                assert 'use_auth' not in plugin_args

            if prefer_schema1_digest is not None:
                assert plugin_args['prefer_schema1_digest'] == prefer_schema1_digest
            else:
                assert 'prefer_schema1_digest' not in plugin_args

    @pytest.mark.parametrize('kojihub', ("http://hub/", None))
    @pytest.mark.parametrize('use_auth', (True, False, None))
    @pytest.mark.parametrize('prefer_schema1_digest', (True, False, None))
    def test_render_koji_upload(self, use_auth, kojihub, prefer_schema1_digest):
        inner_template = WORKER_INNER_TEMPLATE.format(
            arrangement_version=TEST_ARRANGEMENT_VERSION)
        build_request = BuildRequest(INPUTS_PATH, inner_template=inner_template,
                                     outer_template=None, customize_conf=None)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uris': [],
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v2'],
            'kojihub': kojihub,
            'koji_upload_dir': 'upload',
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        if use_auth is not None:
            kwargs['use_auth'] = use_auth
        if prefer_schema1_digest is not None:
            kwargs['prefer_schema1_digest'] = prefer_schema1_digest
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)
        self.assert_koji_upload_plugin(plugins, use_auth, prefer_schema1_digest, kojihub)

    @pytest.mark.parametrize(('koji_hub', 'base_image', 'scratch', 'enabled'), (
        ("http://hub/", 'fedora:latest', False, True),
        (None, 'fedora:latest', False, False),
        ("http://hub/", 'fedora:latest', True, False),
        (None, 'fedora:latest', True, False),
        ("http://hub/", 'koji/image-build', False, False),
        ("http://hub/", 'koji/image-build', True, False),
    ))
    @pytest.mark.parametrize(('certs_dir', 'certs_dir_set'), (
        ('/my/super/secret/dir', True),
        (None, False),
    ))
    def test_render_koji_parent(self, koji_hub, base_image, scratch, enabled, certs_dir,
                                certs_dir_set):
        plugin_type = 'prebuild_plugins'
        plugin_name = 'koji_parent'

        build_request = BuildRequest(INPUTS_PATH)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'base_image': base_image,
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v1', 'v2'],
            'kojihub': koji_hub,
            'koji_certs_secret': certs_dir,
            'scratch': scratch,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        if not enabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)
            return

        assert get_plugin(plugins, plugin_type, plugin_name)

        actual_plugin_args = plugin_value_get(plugins, plugin_type, plugin_name, 'args')

        expected_plugin_args = {'koji_hub': koji_hub}
        if certs_dir_set:
            expected_plugin_args['koji_ssl_certs_dir'] = certs_dir

        assert actual_plugin_args == expected_plugin_args

    @pytest.mark.parametrize(('koji_hub', 'base_image', 'scratch', 'enabled'), (
        ("http://hub/", 'fedora:latest', False, True),
        (None, 'fedora:latest', False, False),
        ("http://hub/", 'fedora:latest', True, False),
        (None, 'fedora:latest', True, False),
        ("http://hub/", 'koji/image-build', False, False),
        ("http://hub/", 'koji/image-build', True, False),
    ))
    @pytest.mark.parametrize(('certs_dir', 'certs_dir_set'), (
        ('/my/super/secret/dir', True),
        (None, False),
    ))
    def test_render_koji_import(self, koji_hub, base_image, scratch, enabled, certs_dir,
                                certs_dir_set):
        plugin_type = 'exit_plugins'
        plugin_name = 'koji_import'

        if enabled:
            inner_template = ORCHESTRATOR_INNER_TEMPLATE.format(
                arrangement_version=TEST_ARRANGEMENT_VERSION)
        else:
            inner_template = None
        build_request = BuildRequest(INPUTS_PATH, inner_template=inner_template)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'base_image': base_image,
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v1', 'v2'],
            'kojihub': koji_hub,
            'koji_certs_secret': certs_dir,
            'scratch': scratch,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        if not enabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)
            return

        assert get_plugin(plugins, plugin_type, plugin_name)

        actual_plugin_args = plugin_value_get(plugins, plugin_type, plugin_name, 'args')

        expected_plugin_args = {'kojihub': koji_hub,
                                'koji_keytab': False,
                                'url': 'http://openshift/',
                                'verify_ssl': False}
        if certs_dir_set:
            expected_plugin_args['koji_ssl_certs'] = certs_dir

        assert actual_plugin_args == expected_plugin_args

    def test_build_request_has_ist_trigger(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.has_ist_trigger() is True

    def test_build_request_isnt_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        build_json['spec']['triggers'] = []
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.has_ist_trigger() is False

    def test_set_label(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.template['metadata'].get('labels') is None

        br.set_label('label-1', 'value-1')
        br.set_label('label-2', 'value-2')
        br.set_label('label-3', 'value-3')
        assert br.template['metadata']['labels'] == {
            'label-1': 'value-1',
            'label-2': 'value-2',
            'label-3': 'value-3',
        }

    @pytest.mark.parametrize(('extra_kwargs', 'valid'), (
        ({'scratch': True}, True),
        ({'is_auto': True}, True),
        ({'isolated': True, 'release': '1.0'}, True),
        ({'scratch': True, 'isolated': True, 'release': '1.0'}, False),
        ({'scratch': True, 'is_auto': True}, False),
        ({'is_auto': True, 'isolated': True, 'release': '1.0'}, False),
    ))
    def test_mutually_exclusive_build_variation(self, extra_kwargs, valid):
        kwargs = get_sample_prod_params()
        kwargs.update(extra_kwargs)
        build_request = BuildRequest(INPUTS_PATH)

        if valid:
            build_request.set_params(**kwargs)
            build_request.render()
        else:
            with pytest.raises(OsbsValidationException) as exc_info:
                build_request.set_params(**kwargs)
            assert 'mutually exclusive' in str(exc_info.value)

    @pytest.mark.parametrize('registry_uris', [
        [],
        ["registry.example.com:5000"],
        ["registry.example.com:5000", "localhost:6000"],
    ])
    def test_render_simple_request(self, registry_uris):
        build_request = BuildRequest(INPUTS_PATH)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uris': registry_uris,
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'build_image': 'fancy_buildroot:latestest',
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v1', 'v2'],
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] is not None
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        expected_output = "john-foo/component:none-"
        if registry_uris:
            expected_output = registry_uris[0] + "/" + expected_output
        assert build_json["spec"]["output"]["to"]["name"].startswith(expected_output)

        plugins = get_plugins_from_build_json(build_json)
        pull_base_image = get_plugin(plugins, "prebuild_plugins",
                                     "pull_base_image")
        assert pull_base_image is not None
        assert ('args' not in pull_base_image or
                'parent_registry' not in pull_base_image['args'] or
                not pull_base_image['args']['parent_registry'])

        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args",
                                "url") == "http://openshift/"

        for r in registry_uris:
            assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                    "registries", r) == {"insecure": True}

        rendered_build_image = build_json["spec"]["strategy"]["customStrategy"]["from"]["name"]
        assert rendered_build_image == 'fancy_buildroot:latestest'

    @pytest.mark.parametrize('proxy', [
        None,
        'http://proxy.example.com',
    ])
    @pytest.mark.parametrize(('build_image', 'build_imagestream', 'valid'), (
        (None, None, False),
        ('ultimate-buildroot:v1.0', None, True),
        (None, 'buildroot-stream:v1.0', True),
        ('ultimate-buildroot:v1.0', 'buildroot-stream:v1.0', False)
    ))
    def test_render_prod_request_with_repo(self, build_image, build_imagestream, proxy, valid):
        build_request = BuildRequest(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        vendor = "Foo Vendor"
        authoritative_registry = "registry.example.com"
        distribution_scope = "authoritative-source-only"
        koji_task_id = 4756
        assert isinstance(build_request, BuildRequest)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'koji_task_id': koji_task_id,
            'sources_command': "make",
            'vendor': vendor,
            'authoritative_registry': authoritative_registry,
            'distribution_scope': distribution_scope,
            'yum_repourls': ["http://example.com/my.repo"],
            'registry_api_versions': ['v1', 'v2'],
            'build_image': build_image,
            'build_imagestream': build_imagestream,
            'proxy': proxy,
            'osbs_api': MockOSBSApi(),
        }

        if valid:
            build_request.set_params(**kwargs)
        else:
            with pytest.raises(OsbsValidationException):
                build_request.set_params(**kwargs)
            return

        build_json = build_request.render()

        assert fnmatch.fnmatch(build_json["metadata"]["name"], TEST_BUILD_CONFIG)
        assert build_json["metadata"]["labels"]["koji-task-id"] == str(koji_task_id)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )

        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")

        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release",
                                "args", "hub") == "http://hub/"

        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image",
                                "args", "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_pull")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'exit_plugins', 'delete_from_registry')

        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url",
                                "args", "repourls") == ["http://example.com/my.repo"]
        if proxy:
            assert plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url",
                                    "args", "inject_proxy") == proxy
        else:
            with pytest.raises(KeyError):
                plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url",
                                 "args", "inject_proxy")

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['authoritative-source-url'] == authoritative_registry
        assert labels['vendor'] == vendor
        assert labels['distribution-scope'] == distribution_scope
        assert 'release' not in labels

        rendered_build_image = build_json["spec"]["strategy"]["customStrategy"]["from"]["name"]
        if not build_imagestream:
            assert rendered_build_image == build_image
        else:
            assert rendered_build_image == build_imagestream
            assert build_json["spec"]["strategy"]["customStrategy"]["from"]["kind"] == \
                "ImageStreamTag"

    @pytest.mark.parametrize('proxy', [
        None,
        'http://proxy.example.com',
    ])
    def test_render_prod_request(self, proxy):
        build_request = BuildRequest(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        koji_target = "koji-target"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': koji_target,
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'smtp_host': 'smtp.example.com',
            'smtp_from': 'user@example.com',
            'proxy': proxy,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert fnmatch.fnmatch(build_json["metadata"]["name"], TEST_BUILD_CONFIG)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )
        assert build_json["metadata"]["labels"]["git-repo-name"] == TEST_GIT_URI_HUMAN_NAME
        assert build_json["metadata"]["labels"]["git-branch"] == TEST_GIT_BRANCH

        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")

        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release",
                                "args", "hub") == "http://hub/"

        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args",
                                "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "root") == "http://root/"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "target") == koji_target
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "hub") == "http://hub/"
        if proxy:
            assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                    "args", "proxy") == proxy
        else:
            with pytest.raises(KeyError):
                plugin_value_get(plugins, "prebuild_plugins", "koji", "args", "proxy")

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "flatpak_create_dockerfile")
        assert get_plugin(plugins, "prepublish_plugins", "squash")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prepublish_plugins", "flatpak_create_oci")

        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_pull")

        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote", "args",
                                "target") == koji_target

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'exit_plugins', 'delete_from_registry')
        assert get_plugin(plugins, "exit_plugins", "koji_tag_build")
        assert plugin_value_get(plugins, "exit_plugins", "koji_tag_build", "args",
                                "target") == koji_target

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['authoritative-source-url'] is not None
        assert labels['vendor'] is not None
        assert labels['distribution-scope'] is not None
        assert 'release' not in labels

    def test_render_prod_without_koji_request(self):
        build_request = BuildRequest(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        assert isinstance(build_request, BuildRequest)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert fnmatch.fnmatch(build_json["metadata"]["name"], TEST_BUILD_CONFIG)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:none-"
        )

        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args",
                                "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_pull")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "exit_plugins", "koji_tag_build")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'exit_plugins', 'delete_from_registry')

        assert get_plugin(plugins, "postbuild_plugins", "import_image")

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['authoritative-source-url'] is not None
        assert labels['vendor'] is not None
        assert labels['distribution-scope'] is not None
        assert 'release' not in labels

    def test_render_prod_with_secret_request(self):
        build_request = BuildRequest(INPUTS_PATH)
        assert isinstance(build_request, BuildRequest)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "",
            'pulp_registry': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'pulp_secret': 'mysecret',
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        # Check that the secret's mountPath matches the plugin's
        # configured path for the secret
        mount_path = get_secret_mountpath_by_name(build_json, 'mysecret')
        plugins = get_plugins_from_build_json(build_json)
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
        assert plugin_value_get(plugins, 'postbuild_plugins', 'pulp_push',
                                'args', 'pulp_secret_path') == mount_path
        assert get_plugin(plugins, "postbuild_plugins", "pulp_pull")

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")

        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release",
                                "args", "hub") == "http://hub/"

        assert get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'exit_plugins', 'delete_from_registry')

        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries") == {}

    @pytest.mark.parametrize('registry_secrets', [None, ['registry-secret']])
    @pytest.mark.parametrize('source_registry', [None, 'registry.example.com', 'localhost'])
    def test_render_pulp_sync(self, registry_secrets, source_registry):
        build_request = BuildRequest(INPUTS_PATH)
        pulp_env = 'env'
        pulp_secret = 'pulp-secret'
        registry_uri = 'https://registry.example.com'
        registry_ver = '/v2'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': registry_uri + registry_ver,
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'registry_secrets': registry_secrets,
            'pulp_registry': pulp_env,
            'pulp_secret': pulp_secret,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        if source_registry:
            kwargs['source_registry_uri'] = source_registry

        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, 'postbuild_plugins', 'pulp_sync')
        assert plugin_value_get(plugins, 'postbuild_plugins',
                                'pulp_sync', 'args',
                                'pulp_registry_name') == pulp_env
        assert plugin_value_get(plugins, 'postbuild_plugins',
                                'pulp_sync', 'args',
                                'docker_registry') == registry_uri

        if source_registry and source_registry in kwargs['registry_uri']:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'exit_plugins', 'delete_from_registry')
        else:
            assert get_plugin(plugins, 'exit_plugins', 'delete_from_registry')
            assert 'https://registry.example.com' in plugin_value_get(plugins, 'exit_plugins',
                                                                      'delete_from_registry',
                                                                      'args', 'registries')

            if registry_secrets:
                assert plugin_value_get(plugins, 'exit_plugins',
                                        'delete_from_registry', 'args',
                                        'registries', 'https://registry.example.com', 'secret')
            else:
                assert plugin_value_get(plugins, 'exit_plugins',
                                        'delete_from_registry', 'args',
                                        'registries', 'https://registry.example.com') == {}

        if registry_secrets:
            mount_path = get_secret_mountpath_by_name(build_json,
                                                      registry_secrets[0])
            assert plugin_value_get(plugins, 'postbuild_plugins',
                                    'pulp_sync', 'args',
                                    'registry_secret_path') == mount_path

        mount_path = get_secret_mountpath_by_name(build_json, pulp_secret)
        assert plugin_value_get(plugins, 'postbuild_plugins',
                                'pulp_sync', 'args',
                                'pulp_secret_path') == mount_path

    def test_render_prod_with_registry_secrets(self):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'pulp_secret': 'mysecret',
            'registry_secrets': ['registry_secret'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        mount_path = get_secret_mountpath_by_name(build_json, 'registry_secret')
        plugins = get_plugins_from_build_json(build_json)
        assert get_plugin(plugins, "postbuild_plugins", "tag_and_push")
        assert plugin_value_get(
            plugins, "postbuild_plugins", "tag_and_push", "args", "registries",
            "registry.example.com", "secret") == mount_path

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert get_plugin(plugins, "prebuild_plugins", "koji")
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_pull")

    def test_render_prod_request_requires_newer(self):
        """
        We should get an OsbsValidationException when trying to use the
        sendmail plugin without requiring OpenShift 1.0.6, as
        configuring the plugin requires the new-style secrets.
        """
        build_request = BuildRequest(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uris': ["registry1.example.com",  # first is primary
                              "registry2.example.com/v2"],
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'smtp_host': 'smtp.example.com',
            'smtp_from': 'user@example.com',
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        with pytest.raises(OsbsValidationException):
            build_request.render()

    @pytest.mark.parametrize('registry_api_versions', [
        ['v1'],
        ['v1', 'v2'],
        ['v2'],
    ])
    @pytest.mark.parametrize('platform', [None, 'x86_64'])
    @pytest.mark.parametrize('arrangement_version',
                             list(range(3, TEST_ARRANGEMENT_VERSION + 1)))
    @pytest.mark.parametrize('scratch', [False, True])
    def test_render_prod_request_v1_v2(self, registry_api_versions, platform, arrangement_version,
                                       scratch):
        build_request = BuildRequest(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        pulp_env = 'v1pulp'
        pulp_secret = pulp_env + 'secret'
        registry_secret = 'registry_secret'
        kwargs = {
            'pulp_registry': pulp_env,
            'pulp_secret': pulp_secret,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }

        kwargs.update({
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uris': [
                # first is primary
                "http://registry1.example.com:5000",

                "http://registry2.example.com:5000/v2"
            ],
            'registry_secrets': [
                registry_secret,
                "",
            ],
            'source_registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': registry_api_versions,
            'scratch': scratch,
            'platform': platform,
            'arrangement_version': arrangement_version,
        })
        build_request.set_params(**kwargs)
        if registry_api_versions == ['v1']:
            with pytest.raises(OsbsValidationException):
                build_json = build_request.render()
            return
        build_json = build_request.render()

        expected_name = TEST_SCRATCH_BUILD_NAME if scratch else TEST_BUILD_CONFIG
        assert fnmatch.fnmatch(build_json["metadata"]["name"], expected_name)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        # Pulp used, so no direct registry output
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "john-foo/component:"
        )

        plugins = get_plugins_from_build_json(build_json)

        # tag_and_push configuration. Must not have the scheme part.
        expected_registries = {
            'registry1.example.com:5000': {
                'insecure': True,
                'secret': '/var/run/secrets/atomic-reactor/registry_secret'},
            'registry2.example.com:5000': {
                'insecure': True}
            }

        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push",
                                "args", "registries") == expected_registries

        for version, plugin in [('v1', 'pulp_push'), ('v2', 'pulp_sync')]:
            if version not in registry_api_versions:
                continue

            path = plugin_value_get(plugins, "postbuild_plugins", plugin, "args",
                                    "pulp_secret_path")
            mount_path = get_secret_mountpath_by_name(build_json, pulp_secret)
            assert mount_path == path

            if plugin == 'pulp_sync':
                path = plugin_value_get(plugins, "postbuild_plugins", plugin,
                                        "args", "registry_secret_path")
                mount_path = get_secret_mountpath_by_name(build_json,
                                                          registry_secret)
                assert mount_path == path

        if 'v1' in registry_api_versions:
            assert get_plugin(plugins, "postbuild_plugins",
                              "pulp_push")
            assert plugin_value_get(plugins, "postbuild_plugins", "pulp_push",
                                    "args", "pulp_registry_name") == pulp_env
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins",
                           "pulp_push")

        assert get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        env = plugin_value_get(plugins, "postbuild_plugins", "pulp_sync",
                               "args", "pulp_registry_name")
        assert env == pulp_env

        pulp_secret = plugin_value_get(plugins, "postbuild_plugins",
                                       "pulp_sync", "args",
                                       "pulp_secret_path")
        docker_registry = plugin_value_get(plugins, "postbuild_plugins",
                                           "pulp_sync", "args",
                                           "docker_registry")

        # pulp_sync config must have the scheme part to satisfy pulp.
        assert docker_registry == 'http://registry1.example.com:5000'

        if scratch:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins", "compress")

            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins", "tag_from_config")

            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins", "import_image")

            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins", "pulp_pull")

            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "exit_plugins", "koji_promote")

            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "exit_plugins", "koji_tag_build")

        else:
            assert get_plugin(plugins, "postbuild_plugins", "compress")
            assert get_plugin(plugins, "postbuild_plugins", "tag_from_config")
            assert get_plugin(plugins, "postbuild_plugins", "pulp_pull")
            assert get_plugin(plugins, "exit_plugins", "koji_promote")
            assert get_plugin(plugins, "exit_plugins", "koji_tag_build")

        assert (get_plugin(plugins, "postbuild_plugins", "tag_by_labels")
                .get('args', {}).get('unique_tag_only', False) == scratch)

    @pytest.mark.parametrize(('extra_kwargs', 'expected_name'), (
        ({'isolated': True, 'release': '1.1'}, TEST_ISOLATED_BUILD_NAME),
        ({'scratch': True}, TEST_SCRATCH_BUILD_NAME),
        ({}, TEST_BUILD_CONFIG),
    ))
    def test_render_build_name(self, tmpdir, extra_kwargs, expected_name):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs.update(extra_kwargs)
        build_request.set_params(**kwargs)

        build_json = build_request.render()
        assert fnmatch.fnmatch(build_json['metadata']['name'], expected_name)

    def test_render_with_yum_repourls(self):
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request = BuildRequest(INPUTS_PATH)

        # Test validation for yum_repourls parameter
        kwargs['yum_repourls'] = 'should be a list'
        with pytest.raises(OsbsValidationException):
            build_request.set_params(**kwargs)

        # Use a valid yum_repourls parameter and check the result
        kwargs['yum_repourls'] = ['http://example.com/repo1.repo', 'http://example.com/repo2.repo']
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        repourls = None
        for d in plugins['prebuild_plugins']:
            if d['name'] == 'add_yum_repo_by_url':
                repourls = d['args']['repourls']

        assert repourls is not None
        assert len(repourls) == 2
        assert 'http://example.com/repo1.repo' in repourls
        assert 'http://example.com/repo2.repo' in repourls

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")

        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release",
                                "args", "hub") == "http://hub/"

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_sync")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_pull")

        assert get_plugin(plugins, "postbuild_plugins", "import_image")

    @pytest.mark.parametrize('odcs_insecure', [False, True, None])
    @pytest.mark.parametrize('pdc_insecure', [False, True, None])
    @pytest.mark.parametrize('odcs_openidc_secret', [None, "odcs-openidc"])
    @pytest.mark.parametrize('compose_ids', (None, [], [42], [42, 2]))
    def test_render_prod_flatpak(self, odcs_insecure, pdc_insecure,
                                 odcs_openidc_secret, compose_ids):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'flatpak': True,
            'compose_ids': compose_ids,
            'odcs_url': "https://odcs.fedoraproject.org/odcs/1",
            'pdc_url': "https://pdc.fedoraproject.org/rest_api/v1",
            'user': "john-foo",
            'base_image': TEST_FLATPAK_BASE_IMAGE,
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        if odcs_insecure is not None:
            kwargs['odcs_insecure'] = odcs_insecure
        if pdc_insecure is not None:
            kwargs['pdc_insecure'] = pdc_insecure
        if odcs_openidc_secret is not None:
            kwargs['odcs_openidc_secret'] = odcs_openidc_secret

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        plugins = get_plugins_from_build_json(build_json)

        plugin = get_plugin(plugins, "prebuild_plugins", "resolve_module_compose")
        assert plugin

        args = plugin['args']
        if compose_ids:
            assert args['compose_ids'] == compose_ids
        else:
            assert 'compose_ids' not in args
        assert args['odcs_url'] == kwargs['odcs_url']
        assert args['odcs_insecure'] == (False if odcs_insecure is None else odcs_insecure)
        assert args['pdc_url'] == kwargs['pdc_url']
        assert args['pdc_insecure'] == (False if pdc_insecure is None else pdc_insecure)

        if odcs_openidc_secret:
            mount_path = get_secret_mountpath_by_name(build_json, odcs_openidc_secret)
            assert args['odcs_openidc_secret_path'] == mount_path
        else:
            assert 'odcs_openid_secret_path' not in args

        plugin = get_plugin(plugins, "prebuild_plugins", "flatpak_create_dockerfile")
        assert plugin

        args = plugin['args']
        assert args['base_image'] == '{{BASE_IMAGE}}'

        plugin = get_plugin(plugins, "prebuild_plugins", "koji")
        assert plugin

        args = plugin['args']
        assert args['target'] == "koji-target"

        plugin = get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin

        args = plugin['args']
        assert args['append'] is True

        assert get_plugin(plugins, "prepublish_plugins", "flatpak_create_oci")
        with pytest.raises(NoSuchPluginException):
            assert get_plugin(plugins, "prepublish_plugins", "squash")
        with pytest.raises(NoSuchPluginException):
            assert get_plugin(plugins, "postbuild_plugins", "import_image")

    def test_render_prod_not_flatpak(self):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'flatpak': False,
            'user': "john-foo",
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "resolve_module_compose")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "flatpak_create_dockerfile")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prepublish_plugins", "flatpak_create_oci")
        assert get_plugin(plugins, "prepublish_plugins", "squash")
        assert get_plugin(plugins, "postbuild_plugins", "import_image")

    @pytest.mark.parametrize(('hub', 'disabled', 'release'), (
        ('http://hub/', False, None),
        ('http://hub/', True, '1.2.1'),
        (None, True, None),
        (None, True, '1.2.1'),
    ))
    @pytest.mark.parametrize('flatpak', (True, False))
    def test_render_bump_release(self, hub, disabled, release, flatpak):
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }

        if hub:
            kwargs['kojihub'] = hub

        if release:
            kwargs['release'] = release

        if flatpak:
            kwargs['flatpak'] = flatpak

        build_request = BuildRequest(INPUTS_PATH)
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")
        assert labels.get('release') == release

        if disabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "prebuild_plugins", "bump_release")
            return

        plugin_args = plugin_value_get(plugins, "prebuild_plugins", "bump_release", "args")
        assert plugin_args['hub'] == hub

        assert plugin_args.get('append', False) == flatpak

    @pytest.mark.parametrize(('hub', 'root', 'disabled'), [
        ('http://hub/', 'http://root/', False),
        (None, None, True),
    ])
    @pytest.mark.parametrize(('allowed_domains'), [
        [],
        ['spam.com'],
        ['spam', 'bacon.com'],
    ])
    def test_render_fetch_maven_artifacts(self, hub, root, disabled, allowed_domains):
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }

        if hub:
            kwargs['kojihub'] = hub
        if root:
            kwargs['kojiroot'] = root
        if allowed_domains:
            kwargs['artifacts_allowed_domains'] = allowed_domains

        build_request = BuildRequest(INPUTS_PATH)
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        if disabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "prebuild_plugins", "fetch_maven_artifacts")

        else:
            assert plugin_value_get(plugins, "prebuild_plugins", "fetch_maven_artifacts",
                                    "args", "koji_hub") == hub
            assert plugin_value_get(plugins, "prebuild_plugins", "fetch_maven_artifacts",
                                    "args", "koji_root") == root

            if allowed_domains:
                assert plugin_value_get(plugins, "prebuild_plugins", "fetch_maven_artifacts",
                                        "args", "allowed_domains") == allowed_domains
            else:
                with pytest.raises(KeyError):
                    plugin_value_get(plugins, "prebuild_plugins", "fetch_maven_artifacts",
                                     "args", "allowed_domains")

    @pytest.mark.parametrize(('extra_kwargs', 'has_platform_tag', 'extra_tags', 'primary_tags'), (
        # Worker build cases
        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64'},
         True, (), ()),

        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64'},
         True, ('tag1', 'tag2'), ()),

        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64', 'scratch': True},
         True, (), ()),

        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64', 'isolated': True,
          'release': '1.1'},
         True, (), ()),


        # Orchestrator build cases
        ({'build_type': BUILD_TYPE_ORCHESTRATOR, 'platforms': ['x86_64']},
         False, ('tag1', 'tag2'), ('latest', '{version}', '{version}-{release}', 'tag1', 'tag2')),

        ({'build_type': BUILD_TYPE_ORCHESTRATOR, 'platforms': ['x86_64']},
         False, (), ('latest', '{version}', '{version}-{release}')),

        ({'build_type': BUILD_TYPE_ORCHESTRATOR, 'platforms': ['x86_64'], 'scratch': True},
         False, ('tag1', 'tag2'), ()),

        ({'build_type': BUILD_TYPE_ORCHESTRATOR, 'platforms': ['x86_64'], 'isolated': True,
          'release': '1.1'},
         False, ('tag1', 'tag2'), ('{version}-{release}',)),

        # When build_type is not specified, no primary tags are set
        ({}, False, (), ()),
        ({}, False, ('tag1', 'tag2'), ()),
        ({'scratch': True}, False, (), ()),
        ({'isolated': True, 'release': '1.1'}, False, (), ()),
    ))
    def test_render_tag_from_config(self, tmpdir, extra_kwargs, has_platform_tag, extra_tags,
                                    primary_tags):
        kwargs = get_sample_prod_params()
        kwargs.pop('platforms', None)
        kwargs.pop('platform', None)
        kwargs.update(extra_kwargs)
        kwargs['arrangement_version'] = 4

        expected_primary = set(primary_tags)

        if extra_tags:
            self._mock_addional_tags_config(str(tmpdir), extra_tags)

        repo_info = RepoInfo(additional_tags=AdditionalTagsConfig(dir_path=str(tmpdir)))
        build_json = self._render_tag_from_config_build_request(kwargs, repo_info)
        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, 'postbuild_plugins', 'tag_from_config')
        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['unique']) == 1
        if has_platform_tag:
            unique_tag_suffix = tag_suffixes['unique'][0]
            assert unique_tag_suffix.endswith('-' + kwargs.get('platform', '')) == has_platform_tag
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary

    def test_render_tag_from_container_yaml(self):
        kwargs = get_sample_prod_params()
        kwargs.pop('platform', None)

        kwargs['platforms'] = ['x86_64', 'ppc64le']
        kwargs['build_type'] = BUILD_TYPE_ORCHESTRATOR
        kwargs['arrangement_version'] = 3

        tags = set(['spam', 'bacon', 'eggs'])
        expected_primary = set(['{version}-{release}', 'spam', 'bacon', 'eggs'])

        repo_info = RepoInfo(additional_tags=AdditionalTagsConfig(tags=tags))

        build_json = self._render_tag_from_config_build_request(kwargs, repo_info=repo_info)
        plugins = get_plugins_from_build_json(build_json)

        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary

    def test_render_tag_from_container_yaml_contains_bad_tag(self):
        kwargs = get_sample_prod_params()
        kwargs.pop('platform', None)

        kwargs['platforms'] = ['x86_64', 'ppc64le']
        kwargs['build_type'] = BUILD_TYPE_ORCHESTRATOR
        kwargs['arrangement_version'] = 3

        expected_primary = set(['{version}-{release}', 'bacon', 'eggs'])

        repo_info = RepoInfo(additional_tags=AdditionalTagsConfig(tags=expected_primary))

        build_json = self._render_tag_from_config_build_request(kwargs, repo_info=repo_info)
        plugins = get_plugins_from_build_json(build_json)

        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary

    def test_render_tag_from_config_unmodified(self):
        kwargs = get_sample_prod_params()
        kwargs.pop('platform', None)

        kwargs['platforms'] = ['x86_64', 'ppc64le']
        kwargs['build_type'] = BUILD_TYPE_ORCHESTRATOR
        kwargs['arrangement_version'] = 3

        expected_primary = set(['spam', 'bacon', 'eggs'])

        tag_suffixes = {'primary': ['spam', 'bacon', 'eggs']}
        build_json = self._render_tag_from_config_build_request(kwargs, tag_suffixes=tag_suffixes)
        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, 'postbuild_plugins', 'tag_from_config')
        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary

    def _render_tag_from_config_build_request(self, kwargs, repo_info=None,
                                              tag_suffixes='{{TAG_SUFFIXES}}'):
        build_request = BuildRequest(INPUTS_PATH)
        build_request.set_params(**kwargs)
        repo_info = repo_info or RepoInfo()
        build_request.set_repo_info(repo_info)
        build_request.customize_conf['enable_plugins'].append(
            {
                "plugin_type": 'postbuild_plugins',
                "plugin_name": 'tag_from_config',
                "plugin_args": {
                    'tag_suffixes': tag_suffixes,
                },
            }
        )

        return build_request.render()

    def _mock_addional_tags_config(self, dir_path, tags):
        with open(os.path.join(dir_path, ADDITIONAL_TAGS_FILE), 'w') as f:
            f.write('\n'.join(tags))

    @staticmethod
    def create_no_plugins_json(outdir):
        """
        Create JSON templates with no plugins added.

        :param outdir: str, path to store modified templates
        """

        # Make temporary copies of the JSON files
        for basename in [DEFAULT_OUTER_TEMPLATE, DEFAULT_OUTER_TEMPLATE]:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(outdir, basename))

        # Create a build JSON description with an image change trigger
        with open(os.path.join(outdir, DEFAULT_INNER_TEMPLATE), 'w') as prod_inner_json:
            prod_inner_json.write(json.dumps({
                'prebuild_plugins': [],
                'prepublish_plugins': [],
                'postbuild_plugins': [],
                'exit_plugins': []
            }))
            prod_inner_json.flush()

    def test_render_optional_plugins(self, tmpdir):
        kwargs = get_sample_prod_params()

        self.create_no_plugins_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        assert plugins['prebuild_plugins'] == []
        assert plugins['prepublish_plugins'] == []
        assert plugins['postbuild_plugins'] == []
        assert plugins['exit_plugins'] == []

    @pytest.mark.parametrize(('platforms', 'secret', 'disabled'), (
        (['x86_64', 'ppc64le'], 'client_config_secret', False),
        (None, 'client_config_secret', True),
        (['x86_64', 'ppc64le'], None, False),
        (None, None, True),
    ))
    @pytest.mark.parametrize('arrangement_version',
                             list(range(3, TEST_ARRANGEMENT_VERSION + 1)))
    @pytest.mark.parametrize(('build_from', 'build_image', 'build_imagestream',
                              'worker_build_image', 'valid'), (

        ('image:fedora:latest', 'fedora:latest', None, 'fedora:latest', False),
        ('image:fedora:latest', 'fedora:latest', 'buildroot-stream:v1.0', 'fedora:latest', False),
        ('image:fedora:latest', None, 'buildroot-stream:v1.0', 'fedora:latest', False),
        (None, 'fedora:latest', None, 'fedora:latest', True),
        ('image:fedora:latest', None, None, 'fedora:latest', True),
        ('wrong:fedora:latest', None, None, KeyError, False),
        (None, None, 'buildroot-stream:v1.0', KeyError, True),
        ('imagestream:buildroot-stream:v1.0', None, None, KeyError, True),
        ('wrong:buildroot-stream:v1.0', None, None, KeyError, False),
        (None, 'fedora:latest', 'buildroot-stream:v1.0', KeyError, False),
    ))
    @pytest.mark.parametrize('additional_kwargs', (
        {
            'authoritative_registry': 'authoritative_registry',
            'distribution_scope': 'distribution_scope',
            'info_url_format': 'info_url_format',
            'kojihub': 'kojihub',
            'kojiroot': 'kojiroot',
            'pulp_registry': 'pulp_registry',
            'pulp_secret': 'pulp_secret',
            'registry_api_versions': ['v1', 'v2'],
            'smtp_additional_addresses': ['spam@food.bz', 'bacon@food.bz'],
            'smtp_email_domain': 'smtp_email_domain',
            'smtp_error_addresses': ['error1@foo.com', 'error2@foo.com'],
            'smtp_from': 'smtp_from',
            'smtp_host': 'smtp_host',
            'smtp_to_pkgowner': True,
            'smtp_to_submitter': False,
            'source_registry_uri': 'source_registry_uri',
            'sources_command': 'sources_command',
            'vendor': 'vendor',
            'equal_labels': [['label1', 'label2'], ['label3', 'label4']],
            'artifacts_allowed_domains': ['foo.domain.com/bar', 'bar.domain.com/foo'],
            'yum_proxy': 'http://proxy:3128',
        },
        {
            'flatpak': True,
            'odcs_url': "https://odcs.fedoraproject.org/rest_api/v1",
            'odcs_insecure': True,
            'pdc_url': "https://pdc.fedoraproject.org/rest_api/v1",
            'pdc_insecure': True,
        },
        {}
    ))
    @pytest.mark.parametrize(('koji_parent_build', 'prefer_schema1_digest',
                              'platform_descriptors', 'goarch'), (
            ('fedora-26-9', True, {}, {}),
            ('fedora-26-9', False, {'ham': {'architecture': 'ham'}}, {'ham': 'ham'}),
            ('fedora-26-9', None, {'ham': {'architecture': 'bacon'},
                                   'eggs': {'architecture': 'eggs'}},
             {'ham': 'bacon', 'eggs': 'eggs'}),
            (None, None, {}, {}),
    ))
    @pytest.mark.parametrize(('openshift_req_version', 'worker_openshift_req_version'), (
        (None, '1.0.6'),
        ('1.3.4', '1.3.4'),
    ))
    def test_render_orchestrate_build(self, tmpdir, platforms, secret, disabled,
                                      arrangement_version, build_from, build_image,
                                      build_imagestream, worker_build_image,
                                      additional_kwargs, koji_parent_build,
                                      openshift_req_version, worker_openshift_req_version,
                                      prefer_schema1_digest, valid, platform_descriptors,
                                      goarch):
        phase = 'buildstep_plugins'
        plugin = 'orchestrate_build'

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'client_config_secret': secret,
            'platforms': platforms,
            'arrangement_version': arrangement_version,
            'osbs_api': MockOSBSApi(),
            'platform_descriptors': platform_descriptors,
            'build_type': BUILD_TYPE_ORCHESTRATOR if platforms else BUILD_TYPE_WORKER,
        }
        if build_image:
            kwargs['build_image'] = build_image
        if build_imagestream:
            kwargs['build_imagestream'] = build_imagestream
        if build_from:
            kwargs['build_from'] = build_from
        if koji_parent_build:
            kwargs['koji_parent_build'] = koji_parent_build
        if prefer_schema1_digest is not None:
            kwargs['prefer_schema1_digest'] = prefer_schema1_digest
        kwargs.update(additional_kwargs)

        inner_template = ORCHESTRATOR_INNER_TEMPLATE.format(
            arrangement_version=arrangement_version)
        build_request = BuildRequest(INPUTS_PATH, inner_template=inner_template)
        if valid:
            build_request.set_params(**kwargs)
        else:
            with pytest.raises(OsbsValidationException):
                build_request.set_params(**kwargs)
            return
        if openshift_req_version:
            build_request.set_openshift_required_version(parse_version(openshift_req_version))
        repo_info = RepoInfo()
        build_request.set_repo_info(repo_info)
        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        if disabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, phase, plugin)

        else:
            assert plugin_value_get(plugins, phase, plugin, 'args', 'platforms') == platforms
            assert plugin_value_get(plugins, phase, plugin, 'args', 'goarch') == goarch
            build_kwargs = plugin_value_get(plugins, phase, plugin, 'args', 'build_kwargs')
            assert build_kwargs['arrangement_version'] == arrangement_version
            assert build_kwargs.get('koji_parent_build') == koji_parent_build

            worker_config_kwargs = plugin_value_get(plugins, phase, plugin, 'args',
                                                    'config_kwargs')

            worker_config = Configuration(conf_file=None, **worker_config_kwargs)

            if isinstance(worker_build_image, type):
                with pytest.raises(worker_build_image):
                    worker_config_kwargs['build_image']
                assert not worker_config.get_build_image()
            else:
                assert worker_config_kwargs['build_image'] == worker_build_image
                assert worker_config.get_build_image() == worker_build_image

            assert (kwargs.get('authoritative_registry') ==
                    worker_config.get_authoritative_registry())
            assert kwargs.get('distribution_scope') == worker_config.get_distribution_scope()
            assert kwargs.get('info_url_format') == worker_config.get_info_url_format()
            assert kwargs.get('kojihub') == worker_config.get_kojihub()
            assert kwargs.get('kojiroot') == worker_config.get_kojiroot()
            assert kwargs.get('pulp_registry') == worker_config.get_pulp_registry()
            assert ['v1', 'v2'] == worker_config.get_registry_api_versions()
            assert (kwargs.get('smtp_additional_addresses', []) ==
                    worker_config.get_smtp_additional_addresses())
            assert kwargs.get('smtp_email_domain') == worker_config.get_smtp_email_domain()
            assert (kwargs.get('smtp_error_addresses', []) ==
                    worker_config.get_smtp_error_addresses())
            assert kwargs.get('smtp_from') == worker_config.get_smtp_from()
            assert kwargs.get('smtp_host') == worker_config.get_smtp_host()
            assert kwargs.get('smtp_to_pkgowner') == worker_config.get_smtp_to_pkgowner()
            assert kwargs.get('smtp_to_submitter') == worker_config.get_smtp_to_submitter()
            assert kwargs.get('source_registry_uri') == worker_config.get_source_registry_uri()
            assert kwargs.get('sources_command') == worker_config.get_sources_command()
            assert kwargs.get('vendor') == worker_config.get_vendor()
            assert (kwargs.get('equal_labels', []) ==
                    worker_config.get_equal_labels())
            assert (kwargs.get('artifacts_allowed_domains', []) ==
                    worker_config.get_artifacts_allowed_domains())
            assert (kwargs.get('yum_proxy') ==
                    worker_config.get_proxy())
            assert (parse_version(worker_openshift_req_version) ==
                    worker_config.get_openshift_required_version())
            assert (kwargs.get('prefer_schema1_digest') ==
                    worker_config.get_prefer_schema1_digest())

            if kwargs.get('flatpak', False):
                assert kwargs.get('flatpak') is True
                assert kwargs.get('odcs_url') == worker_config.get_odcs_url()
                assert kwargs.get('odcs_insecure') == worker_config.get_odcs_insecure()
                assert kwargs.get('pdc_url') == worker_config.get_pdc_url()
                assert kwargs.get('pdc_insecure') == worker_config.get_pdc_insecure()

    def test_render_prod_with_pulp_no_auth(self):
        """
        Rendering should fail if pulp is specified but auth config isn't
        """
        build_request = BuildRequest(INPUTS_PATH)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'pulp_registry': "foo",
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        with pytest.raises(OsbsValidationException):
            build_request.render()

    @pytest.mark.parametrize('triggers', [
        None,
        [],
        [{
            "type": "Generic",
            "generic": {
                "secret": "secret101",
                "allowEnv": True
            }
        }]
    ])
    def test_render_prod_with_falsey_triggers(self, tmpdir, triggers):

        self.create_image_change_trigger_json(str(tmpdir), custom_triggers=triggers)
        build_request = BuildRequest(str(tmpdir))
        kwargs = get_sample_prod_params()
        build_request.set_params(**kwargs)
        build_request.render()

    @staticmethod
    def create_image_change_trigger_json(outdir, custom_triggers=USE_DEFAULT_TRIGGERS):
        """
        Create JSON templates with an image change trigger added.

        :param outdir: str, path to store modified templates
        """

        triggers = custom_triggers if custom_triggers is not USE_DEFAULT_TRIGGERS else [
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

        # Make temporary copies of all the JSON files
        for json_file_path in glob.glob(os.path.join(INPUTS_PATH, '*.json')):
            basename = os.path.basename(json_file_path)
            shutil.copy(json_file_path,
                        os.path.join(outdir, basename))

        # Create a build JSON description with an image change trigger
        with open(os.path.join(outdir, DEFAULT_OUTER_TEMPLATE), 'r+') as prod_json:
            build_json = json.load(prod_json)

            # Add the image change trigger
            build_json['spec']['triggers'] = triggers

            prod_json.seek(0)
            json.dump(build_json, prod_json)
            prod_json.truncate()

    @pytest.mark.parametrize(('registry_uri', 'insecure_registry'), [
        ("https://registry.example.com", False),
        ("http://registry.example.com", True),
    ])
    @pytest.mark.parametrize('use_auth', (True, False, None))
    @pytest.mark.parametrize(('scratch', 'isolated'), (
        (True, False),
        (False, True),
        (False, False),
    ))
    def test_render_prod_request_with_trigger(self, tmpdir, registry_uri,
                                              insecure_registry, use_auth, scratch, isolated):
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))
        name_label = "fedora/resultingimage"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': registry_uri,
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'smtp_host': 'smtp.example.com',
            'smtp_from': 'user@example.com',
            'smtp_error_addresses': ['errors@example.com'],
            'smtp_additional_addresses': 'user2@example.com, user3@example.com',
            'smtp_email_domain': 'example.com',
            'smtp_to_submitter': True,
            'smtp_to_pkgowner': True,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        if use_auth is not None:
            kwargs['use_auth'] = use_auth
        if scratch:
            kwargs['scratch'] = scratch
        if isolated:
            kwargs['isolated'] = isolated
            kwargs['release'] = '1.1'

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        if scratch or isolated:
            assert "triggers" not in build_json["spec"]
        else:
            assert "triggers" in build_json["spec"]
            assert (build_json["spec"]["triggers"][0]["imageChange"]["from"]["name"] ==
                    'fedora:latest')

        plugins = get_plugins_from_build_json(build_json)

        if not scratch and not isolated:
            assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
            assert get_plugin(plugins, "prebuild_plugins",
                              "stop_autorebuild_if_disabled")
            assert plugin_value_get(plugins, "prebuild_plugins",
                                    "check_and_set_rebuild", "args",
                                    "url") == kwargs["openshift_uri"]

            self.assert_import_image_plugin(
                plugins=plugins,
                name_label=name_label,
                registry_uri=kwargs['registry_uri'],
                openshift_uri=kwargs['openshift_uri'],
                use_auth=use_auth,
                insecure_registry=insecure_registry)

        assert plugin_value_get(plugins, "postbuild_plugins", "tag_and_push", "args",
                                "registries", "registry.example.com") == {"insecure": True}

        if not scratch and not isolated:
            assert get_plugin(plugins, "exit_plugins", "koji_promote")
            assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                    "args", "kojihub") == kwargs["kojihub"]
            assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                    "args", "url") == kwargs["openshift_uri"]
            with pytest.raises(KeyError):
                plugin_value_get(plugins, 'exit_plugins', "koji_promote",
                                 'args', 'metadata_only')  # v1 enabled by default

            assert get_plugin(plugins, "exit_plugins", "koji_tag_build")
            assert plugin_value_get(plugins, "exit_plugins", "koji_tag_build",
                                    "args", "kojihub") == kwargs["kojihub"]

        expected = {'args': {'additional_addresses': 'user2@example.com, user3@example.com',
                             'email_domain': 'example.com',
                             'error_addresses': ['errors@example.com'],
                             'from_address': 'user@example.com',
                             'koji_hub': 'http://hub/',
                             'koji_root': 'http://root/',
                             'send_on': [
                                 'auto_canceled',
                                 'auto_fail',
                                 'manual_success',
                                 'manual_fail'],
                             'smtp_host': 'smtp.example.com',
                             'to_koji_pkgowner': True,
                             'to_koji_submitter': True,
                             'url': 'http://openshift/'},
                    'name': 'sendmail'}
        assert get_plugin(plugins, 'exit_plugins', 'sendmail') == expected

    @pytest.mark.parametrize(('registry_uri', 'insecure_registry'), [
        ("https://registry.example.com", False),
        ("http://registry.example.com", True),
    ])
    @pytest.mark.parametrize('use_auth', (True, False, None))
    @pytest.mark.parametrize('koji_parent_build', ('fedora-26-9', None))
    def test_render_custom_base_image_with_trigger(self, tmpdir, registry_uri,
                                                   insecure_registry, use_auth,
                                                   koji_parent_build):
        name_label = "fedora/resultingimage"
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))

        build_request.customize_conf['enable_plugins'].append(
            {
                "plugin_type": "prebuild_plugins",
                "plugin_name": "inject_parent_image",
                "plugin_args": {
                    "koji_parent_build": "{{KOJI_PARENT_BUILD}}",
                    "koji_hub": "{{KOJI_HUB}}"
                },
            }
        )

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = 'koji/image-build'
        kwargs['yum_repourls'] = ["http://example.com/my.repo"]
        kwargs['smtp_host'] = 'smtp.example.com'
        kwargs['smtp_from'] = 'user@example.com',
        kwargs['registry_uri'] = registry_uri
        kwargs['source_registry_uri'] = registry_uri
        kwargs['openshift_uri'] = 'http://openshift/'
        if use_auth is not None:
            kwargs['use_auth'] = use_auth
        if koji_parent_build:
            kwargs['koji_parent_build'] = koji_parent_build

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_request.is_custom_base_image() is True

        # Verify the triggers are now disabled
        assert "triggers" not in build_json["spec"]

        # Verify the rebuild plugins are all disabled
        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "inject_parent_image")

        self.assert_import_image_plugin(
            plugins=plugins,
            name_label=name_label,
            registry_uri=kwargs['registry_uri'],
            openshift_uri=kwargs['openshift_uri'],
            use_auth=use_auth,
            insecure_registry=insecure_registry)

    @pytest.mark.parametrize(('extra_kwargs', 'expected_error'), (
        ({'isolated': True}, 'release parameter is required'),
        ({'isolated': True, 'release': '1'}, 'must be in the format'),
        ({'isolated': True, 'release': '1.1'}, None),
    ))
    def test_adjust_for_isolated(self, tmpdir, extra_kwargs, expected_error):
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))

        kwargs = get_sample_prod_params()
        kwargs.update(extra_kwargs)
        build_request.set_params(**kwargs)

        if expected_error:
            with pytest.raises(OsbsValidationException) as exc_info:
                build_request.render()
            assert expected_error in str(exc_info.value)

        else:
            build_json = build_request.render()

            assert 'triggers' not in build_json['spec']
            assert build_json['metadata']['labels']['isolated'] == 'true'
            assert build_json['metadata']['labels']['isolated-release'] == extra_kwargs['release']

    @pytest.mark.parametrize(('autorebuild_enabled', 'release_label', 'expected'), (
        (True, None, True),
        (True, 'release', RuntimeError),
        (True, 'Release', RuntimeError),
        (False, 'release', False),
        (False, 'Release', False),
    ))
    def test_render_prod_request_with_repo_info(self, tmpdir, autorebuild_enabled, release_label,
                                                expected):
        self.create_image_change_trigger_json(str(tmpdir))

        class MockDfParser(object):
            labels = {release_label: '13'} if release_label else {}

        (flexmock(RepoConfiguration)
            .should_receive('is_autorebuild_enabled')
            .and_return(autorebuild_enabled))

        repo_info = RepoInfo(MockDfParser())

        build_request_kwargs = get_sample_prod_params()
        base_image = build_request_kwargs['base_image']
        build_request = BuildRequest(str(tmpdir))
        build_request.set_params(**build_request_kwargs)
        build_request.set_repo_info(repo_info)
        if isinstance(expected, type):
            with pytest.raises(expected):
                build_json = build_request.render()
            return

        build_json = build_request.render()

        plugins = get_plugins_from_build_json(build_json)
        autorebuild_plugins = (
            ('prebuild_plugins', 'check_and_set_rebuild'),
            ('prebuild_plugins', 'stop_autorebuild_if_disabled'),
        )

        if expected:
            assert build_json["spec"]["triggers"][0]["imageChange"]["from"]["name"] == base_image

            for phase, plugin in autorebuild_plugins:
                assert get_plugin(plugins, phase, plugin)

        else:
            assert 'triggers' not in build_json['spec']
            for phase, plugin in autorebuild_plugins:
                with pytest.raises(NoSuchPluginException):
                    get_plugin(plugins, phase, plugin)

        assert get_plugin(plugins, 'postbuild_plugins', 'import_image')

    def test_render_prod_request_new_secrets(self, tmpdir):
        secret_name = 'mysecret'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': "fedora/resultingimage",
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'sources_command': "make",
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v1', 'v2'],
            'pulp_registry': 'foo',
            'pulp_secret': secret_name,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }

        # Default required version (1.0.6), implicitly and explicitly
        for required in (None, parse_version('1.0.6')):
            build_request = BuildRequest(INPUTS_PATH)
            if required is not None:
                build_request.set_openshift_required_version(required)

            build_request.set_params(**kwargs)
            build_json = build_request.render()

            # Not using the sourceSecret scheme
            assert 'sourceSecret' not in build_json['spec']['source']

            # Check that the secret's mountPath matches the plugin's
            # configured path for the secret
            mount_path = get_secret_mountpath_by_name(build_json, secret_name)
            plugins = get_plugins_from_build_json(build_json)
            assert plugin_value_get(plugins, 'postbuild_plugins', 'pulp_push',
                                    'args', 'pulp_secret_path') == mount_path

    def test_render_prod_request_with_koji_secret(self, tmpdir):
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))
        name_label = "fedora/resultingimage"
        koji_certs_secret_name = 'foobar'
        koji_task_id = 1234
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'koji_task_id': koji_task_id,
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'koji_certs_secret': koji_certs_secret_name,
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["labels"]["koji-task-id"] == str(koji_task_id)

        plugins = get_plugins_from_build_json(build_json)
        assert get_plugin(plugins, "exit_plugins", "koji_tag_build")
        assert plugin_value_get(plugins, "exit_plugins", "koji_tag_build",
                                "args", "kojihub") == kwargs["kojihub"]

        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                "args", "kojihub") == kwargs["kojihub"]
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                "args", "url") == kwargs["openshift_uri"]

        mount_path = get_secret_mountpath_by_name(build_json,
                                                  koji_certs_secret_name)
        assert get_plugin(plugins, 'exit_plugins',
                          "koji_promote")['args']['koji_ssl_certs'] == mount_path
        assert get_plugin(plugins, 'exit_plugins',
                          'koji_tag_build')['args']['koji_ssl_certs'] == mount_path

    def test_render_prod_request_with_koji_kerberos(self, tmpdir):
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))
        name_label = "fedora/resultingimage"
        koji_task_id = 1234
        koji_use_kerberos = True
        koji_kerberos_keytab = "FILE:/tmp/fakekeytab"
        koji_kerberos_principal = "myprincipal@OSBSDOMAIN.COM"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'koji_task_id': koji_task_id,
            'koji_use_kerberos': koji_use_kerberos,
            'koji_kerberos_keytab': koji_kerberos_keytab,
            'koji_kerberos_principal': koji_kerberos_principal,
            'vendor': "Foo Vendor",
            'authoritative_registry': "registry.example.com",
            'distribution_scope': "authoritative-source-only",
            'registry_api_versions': ['v2'],
            'build_from': 'image:buildroot:latest',
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["labels"]["koji-task-id"] == str(koji_task_id)

        plugins = get_plugins_from_build_json(build_json)
        assert get_plugin(plugins, "exit_plugins", "koji_promote")
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                "args", "kojihub") == kwargs["kojihub"]
        assert plugin_value_get(plugins, "exit_plugins", "koji_promote",
                                "args", "url") == kwargs["openshift_uri"]

        assert get_plugin(plugins, "exit_plugins", "koji_tag_build")
        assert plugin_value_get(plugins, "exit_plugins", "koji_tag_build",
                                "args", "kojihub") == kwargs["kojihub"]

        assert get_plugin(plugins, 'exit_plugins',
                          'koji_tag_build')['args']['koji_principal'] == koji_kerberos_principal
        assert get_plugin(plugins, 'exit_plugins',
                          'koji_tag_build')['args']['koji_keytab'] == koji_kerberos_keytab

    @pytest.mark.parametrize(('base_image', 'is_custom'), [
        ('fedora', False),
        ('fedora:latest', False),
        ('koji/image-build', True),
        ('koji/image-build:spam.conf', True),
        ('scratch', False),
    ])
    def test_prod_is_custom_base_image(self, tmpdir, base_image, is_custom):
        build_request = BuildRequest(INPUTS_PATH)
        # Safe to call prior to build image being set
        assert build_request.is_custom_base_image() is False

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = base_image
        build_request.set_params(**kwargs)
        build_json = build_request.render()  # noqa

        assert build_request.is_custom_base_image() == is_custom

    @pytest.mark.parametrize(('base_image', 'is_from_scratch'), [
        ('fedora', False),
        ('fedora:latest', False),
        ('koji/image-build', False),
        ('koji/image-build:spam.conf', False),
        ('scratch', True),
    ])
    def test_prod_is_from_scratch_image(self, base_image, is_from_scratch):
        build_request = BuildRequest(INPUTS_PATH)
        # Safe to call prior to build image being set
        assert build_request.is_from_scratch_image() is False

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = base_image
        build_request.set_params(**kwargs)
        build_json = build_request.render()  # noqa

        assert build_request.is_from_scratch_image() == is_from_scratch

    @pytest.mark.parametrize('base_image, msg, keep_triggers', (
        ('fedora', None, True),
        ('scratch', 'from request because FROM scratch image', False),
        ('koji/image-build', 'from request because custom base image', False),
    ))
    def test_adjust_for_triggers_base_builds(self, tmpdir, caplog, base_image, msg, keep_triggers):
        """Test if triggers are properly adjusted for base and FROM scratch builds"""
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequest(str(tmpdir))

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = base_image
        build_request.set_params(**kwargs)
        build_request.render()  # triggers are adjusted in render method

        assert bool(build_request.template['spec'].get('triggers', [])) == keep_triggers

        if msg is not None:
            assert msg in caplog.text

    def test_prod_missing_kojihub__custom_base_image(self, tmpdir):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = 'koji/image-build'
        del kwargs['kojihub']
        build_request.set_params(**kwargs)

        with pytest.raises(OsbsValidationException) as exc:
            build_request.render()

        assert str(exc.value).startswith(
            'Custom base image builds require kojihub')

    def test_prod_custom_base_image(self, tmpdir):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = 'koji/image-build'
        kwargs['yum_repourls'] = ["http://example.com/my.repo"]
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_request.is_custom_base_image() is True
        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'prebuild_plugins', 'pull_base_image')

        add_filesystem_args = plugin_value_get(
            plugins, 'prebuild_plugins', 'add_filesystem', 'args')
        assert add_filesystem_args['koji_hub'] == kwargs['kojihub']
        assert add_filesystem_args['repos'] == kwargs['yum_repourls']
        assert add_filesystem_args['architectures'] == kwargs['platforms']
        assert add_filesystem_args['from_task_id'] == kwargs['filesystem_koji_task_id']

    def test_worker_custom_base_image(self, tmpdir):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = 'koji/image-build'
        kwargs['yum_repourls'] = ["http://example.com/my.repo"]
        kwargs.pop('platforms', None)
        kwargs['platform'] = 'ppc64le'
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_request.is_custom_base_image() is True
        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'prebuild_plugins', 'pull_base_image')

        add_filesystem_args = plugin_value_get(
            plugins, 'prebuild_plugins', 'add_filesystem', 'args')
        assert add_filesystem_args['koji_hub'] == kwargs['kojihub']
        assert add_filesystem_args['repos'] == kwargs['yum_repourls']
        assert add_filesystem_args['architecture'] == kwargs['platform']
        assert add_filesystem_args['from_task_id'] == kwargs['filesystem_koji_task_id']

    def test_prod_non_custom_base_image(self, tmpdir):
        build_request = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_request.is_custom_base_image() is False
        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'prebuild_plugins', 'add_filesystem')

        pull_base_image_plugin = get_plugin(
            plugins, 'prebuild_plugins', 'pull_base_image')
        assert pull_base_image_plugin is not None

    def test_render_prod_custom_site_plugin_enable(self):
        """
        Test to make sure that when we attempt to enable a plugin, it is
        actually enabled in the JSON for the build_request after running
        build_request.render()
        """

        plugin_type = "exit_plugins"
        plugin_name = "testing_exit_plugin"
        plugin_args = {"foo": "bar"}

        build_request = BuildRequest(INPUTS_PATH)
        build_request.customize_conf['enable_plugins'].append(
            {
                "plugin_type": plugin_type,
                "plugin_name": plugin_name,
                "plugin_args": plugin_args
            }
        )
        kwargs = get_sample_prod_params()
        build_request.set_params(**kwargs)
        build_request.render()

        assert {
                "name": plugin_name,
                "args": plugin_args
        } in build_request.dj.dock_json[plugin_type]

    def test_render_prod_custom_site_plugin_disable(self):
        """
        Test to make sure that when we attempt to disable a plugin, it is
        actually disabled in the JSON for the build_request after running
        build_request.render()
        """

        plugin_type = "postbuild_plugins"
        plugin_name = "compress"

        build_request = BuildRequest(INPUTS_PATH)
        build_request.customize_conf['disable_plugins'].append(
            {
                "plugin_type": plugin_type,
                "plugin_name": plugin_name
            }
        )
        kwargs = get_sample_prod_params()
        build_request.set_params(**kwargs)
        build_request.render()

        for plugin in build_request.dj.dock_json[plugin_type]:
            if plugin['name'] == plugin_name:
                assert False

    def test_render_prod_custom_site_plugin_override(self):
        """
        Test to make sure that when we attempt to override a plugin's args,
        they are actually overridden in the JSON for the build_request
        after running build_request.render()
        """

        plugin_type = "postbuild_plugins"
        plugin_name = "compress"
        plugin_args = {"foo": "bar"}

        kwargs = get_sample_prod_params()

        unmodified_build_request = BuildRequest(INPUTS_PATH)
        unmodified_build_request.set_params(**kwargs)
        unmodified_build_request.render()

        for plugin_dict in unmodified_build_request.dj.dock_json[plugin_type]:
            if plugin_dict['name'] == plugin_name:
                plugin_index = unmodified_build_request.dj.dock_json[plugin_type].index(plugin_dict)

        build_request = BuildRequest(INPUTS_PATH)
        build_request.customize_conf['enable_plugins'].append(
            {
                "plugin_type": plugin_type,
                "plugin_name": plugin_name,
                "plugin_args": plugin_args
            }
        )
        build_request.set_params(**kwargs)
        build_request.render()

        assert {
                "name": plugin_name,
                "args": plugin_args
        } in build_request.dj.dock_json[plugin_type]

        assert unmodified_build_request.dj.dock_json[plugin_type][plugin_index]['name'] == \
            plugin_name
        assert build_request.dj.dock_json[plugin_type][plugin_index]['name'] == plugin_name

    def test_has_version(self):
        br = BuildRequest(INPUTS_PATH)
        br.set_params(**get_sample_prod_params())
        br.render()
        assert 'client_version' in br.dj.dock_json

        actual_version = br.dj.dock_json['client_version']
        assert isinstance(actual_version, six.string_types)
        assert expected_version == actual_version

    @pytest.mark.parametrize('secret', [None, 'osbsconf'])
    def test_reactor_config(self, secret):
        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        kwargs['reactor_config_secret'] = secret
        br.set_params(**kwargs)
        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if secret is None:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'prebuild_plugins', 'reactor_config')
        else:
            assert get_plugin(plugins, 'prebuild_plugins', 'reactor_config')
            assert plugin_value_get(plugins, 'prebuild_plugins',
                                    'reactor_config', 'args',
                                    'config_path').startswith('/')

    @pytest.mark.parametrize('secret', [None, 'osbsconf'])
    def test_client_config_secret(self, secret):
        br = BuildRequest(INPUTS_PATH)
        plugin_type = "buildstep_plugins"
        plugin_name = "orchestrate_build"

        kwargs = get_sample_prod_params()
        kwargs['client_config_secret'] = secret
        kwargs['platforms'] = ['x86_64', 'ppc64le']
        kwargs['arrangement_version'] = TEST_ARRANGEMENT_VERSION
        br.set_params(**kwargs)

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, {})
        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if secret is not None:
            assert get_secret_mountpath_by_name(build_json, secret) == os.path.join(SECRETS_PATH,
                                                                                    secret)
            assert get_plugin(plugins, plugin_type, plugin_name)
            assert plugin_value_get(plugins, plugin_type, plugin_name,
                                    'args', 'osbs_client_config') == os.path.join(SECRETS_PATH,
                                                                                  secret)
        else:
            with pytest.raises(AssertionError):
                get_secret_mountpath_by_name(build_json, secret)

    @pytest.mark.parametrize('secret', [
        {'secret': None},
        {'secret': 'path'},
        {'secret1': 'path1',
         'secret2': 'path2'
         },
        {'secret1': 'path1',
         'secret2': 'path2',
         'secret3': 'path3'
         },
    ])
    def test_token_secrets(self, secret):
        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        kwargs['token_secrets'] = secret
        br.set_params(**kwargs)
        build_json = br.render()

        for (sec, path) in secret.items():
            if path:
                assert get_secret_mountpath_by_name(build_json, sec) == path
            else:
                assert get_secret_mountpath_by_name(build_json, sec) == os.path.join(SECRETS_PATH,
                                                                                     sec)

    def test_info_url_format(self):
        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        info_url_format = "info_url"
        kwargs['info_url_format'] = info_url_format
        br.set_params(**kwargs)
        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, 'prebuild_plugins', 'add_labels_in_dockerfile')
        assert plugin_value_get(plugins, 'prebuild_plugins',
                                'add_labels_in_dockerfile', 'args',
                                'info_url_format') == info_url_format

    @pytest.mark.parametrize(('platform_descriptors', 'goarch'), [
        ({}, {}),
        ({'ham': {'architecture': 'ham'}}, {'ham': 'ham'}),
        ({'ham': {'architecture': 'bacon'}, 'eggs': {'architecture': 'eggs'}},
         {'ham': 'bacon', 'eggs': 'eggs'}),
    ])
    def test_render_group_manifest(self, platform_descriptors, goarch):
        plugin_type = "postbuild_plugins"
        plugin_name = "group_manifests"

        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        kwargs['platform_descriptors'] = platform_descriptors
        br.set_params(**kwargs)
        args = {
            "registries": {
                "registry.example.com": {"insecure": True}
            }
        }

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, args)

        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, plugin_type, plugin_name)
        assert plugin_value_get(plugins, plugin_type, plugin_name, 'args',
                                'registries')
        assert plugin_value_get(plugins, plugin_type, plugin_name, 'args',
                                'goarch') == goarch

    @pytest.mark.parametrize(('koji_parent_build', 'koji_hub', 'plugin_enabled'), (
        ('fedora-26-9', 'http://hub/', True),
        (None, 'http://hub/', False),
        ('fedora-26-9', None, False),
        (None, None, False),
    ))
    def test_render_inject_parent_image(self, koji_parent_build, koji_hub, plugin_enabled):
        plugin_type = "prebuild_plugins"
        plugin_name = "inject_parent_image"

        build_request = BuildRequest(INPUTS_PATH)
        build_request.customize_conf['enable_plugins'].append(
            {
                "plugin_type": plugin_type,
                "plugin_name": plugin_name,
                "plugin_args": {
                    "koji_parent_build": "{{KOJI_PARENT_BUILD}}",
                    "koji_hub": "{{KOJI_HUB}}"
                },
            }
        )

        kwargs = get_sample_prod_params()
        kwargs.pop('kojihub', None)
        if koji_hub:
            kwargs['kojihub'] = koji_hub
        if koji_parent_build:
            kwargs['koji_parent_build'] = koji_parent_build
        build_request.set_params(**kwargs)

        build_json = build_request.render()
        plugins = get_plugins_from_build_json(build_json)

        if plugin_enabled:
            assert get_plugin(plugins, plugin_type, plugin_name)
            assert plugin_value_get(plugins, plugin_type, plugin_name, 'args',
                                    'koji_parent_build') == koji_parent_build
            assert plugin_value_get(plugins, plugin_type, plugin_name, 'args',
                                    'koji_hub') == kwargs['kojihub']
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)

    @pytest.mark.parametrize(('platform', 'platforms', 'is_auto', 'scratch',
                              'isolated', 'expected'), [
        (None, None, False, False, False, {'explicit1': 'yes',
                                           'explicit2': 'yes'}),
        (None, None, False, True, False, {'scratch1': 'yes',
                                          'scratch2': 'yes'}),
        (None, None, True, False, False, {'auto1': 'yes',
                                          'auto2': 'yes'}),
        (None, None, False, False, True, {'isolated1': 'yes',
                                          'isolated2': 'yes'}),
        (None, ["x86"], False, False, False, {}),
        (None, ["ppc"], False, False, False, {}),
        (None, ["x86"], True, False, False, {}),
        (None, ["ppc"], False, True, False, {}),
        (None, ["ppc"], False, False, True, {}),
        ("x86", None, False, False, False, {'explicit1': 'yes',
                                            'explicit2': 'yes',
                                            'plx86a': 'yes',
                                            'plx86b': 'yes'}),
        ("x86", None, False, True, False, {'scratch1': 'yes',
                                           'scratch2': 'yes',
                                           'plx86a': 'yes',
                                           'plx86b': 'yes'}),
        ("x86", None, True, False, False, {'auto1': 'yes',
                                           'auto2': 'yes',
                                           'plx86a': 'yes',
                                           'plx86b': 'yes'}),
        ("x86", None, False, False, True, {'isolated1': 'yes',
                                           'isolated2': 'yes',
                                           'plx86a': 'yes',
                                           'plx86b': 'yes'}),
        ("ppc", None, False, False, False, {'explicit1': 'yes',
                                            'explicit2': 'yes',
                                            'plppc1': 'yes',
                                            'plppc2': 'yes'}),
        ("ppc", None, False, True, False, {'scratch1': 'yes',
                                           'scratch2': 'yes',
                                           'plppc1': 'yes',
                                           'plppc2': 'yes'}),
        ("ppc", None, True, False, False, {'auto1': 'yes',
                                           'auto2': 'yes',
                                           'plppc1': 'yes',
                                           'plppc2': 'yes'}),
        ("ppc", None, False, False, True, {'isolated1': 'yes',
                                           'isolated2': 'yes',
                                           'plppc1': 'yes',
                                           'plppc2': 'yes'}),
    ])
    def test_check_set_nodeselectors(self, platform, platforms, is_auto, scratch,
                                     isolated, expected):
        platform_nodeselectors = {
            'x86': {
                'plx86a': 'yes',
                'plx86b': 'yes'
            },
            'ppc': {
                'plppc1': 'yes',
                'plppc2': 'yes'
            }
        }
        built_type_nodeselectors = {
            'auto': {
                'auto1': 'yes',
                'auto2': 'yes'
            },
            'explicit': {
                'explicit1': 'yes',
                'explicit2': 'yes'
            },
            'scratch': {
                'scratch1': 'yes',
                'scratch2': 'yes'
            },
            'isolated': {
                'isolated1': 'yes',
                'isolated2': 'yes'
            }
        }

        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        if platforms:
            kwargs['platforms'] = [platforms]
            kwargs['build_type'] = BUILD_TYPE_ORCHESTRATOR
        else:
            kwargs['platforms'] = None
            kwargs['build_type'] = BUILD_TYPE_WORKER

        if platform:
            kwargs['platform_node_selector'] = platform_nodeselectors[platform]

        kwargs['is_auto'] = is_auto
        kwargs['scratch'] = scratch
        kwargs['isolated'] = isolated
        if isolated:
            kwargs['release'] = '1.0'
        kwargs['scratch_build_node_selector'] = built_type_nodeselectors['scratch']
        kwargs['explicit_build_node_selector'] = built_type_nodeselectors['explicit']
        kwargs['auto_build_node_selector'] = built_type_nodeselectors['auto']
        kwargs['isolated_build_node_selector'] = built_type_nodeselectors['isolated']
        br.set_params(**kwargs)
        build_json = br.render()

        if expected:
            print(build_json['spec']['nodeSelector'])
            assert build_json['spec']['nodeSelector'] == expected
        else:
            assert 'nodeSelector' not in build_json['spec']

    @pytest.mark.parametrize(('pulp_registry', 'pulp_secret'), [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ])
    def test_render_pulp_tag(self, pulp_registry, pulp_secret):
        plugin_type = "postbuild_plugins"
        plugin_name = "pulp_tag"

        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        if pulp_registry:
            kwargs['pulp_registry'] = "registry.example.com"
        if pulp_secret:
            kwargs['pulp_secret'] = "pulp_secret"
        br.set_params(**kwargs)

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, {})

        if pulp_registry and not pulp_secret:
            with pytest.raises(OsbsValidationException):
                br.render()
            return

        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if pulp_registry:
            assert get_plugin(plugins, plugin_type, plugin_name)
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)

    @pytest.mark.parametrize(('pulp_registry', 'pulp_secret'), [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ])
    def test_render_pulp_publish(self, pulp_registry, pulp_secret):
        plugin_type = "exit_plugins"
        plugin_name = "pulp_publish"

        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        if pulp_registry:
            kwargs['pulp_registry'] = "registry.example.com"
        if pulp_secret:
            kwargs['pulp_secret'] = "pulp_secret"
        br.set_params(**kwargs)

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, {})

        if pulp_registry and not pulp_secret:
            with pytest.raises(OsbsValidationException):
                br.render()
            return

        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if pulp_registry:
            assert get_plugin(plugins, plugin_type, plugin_name)
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)

    @pytest.mark.parametrize('prefer_schema1_digest', (True, False, None))
    @pytest.mark.parametrize('plugin_type', ('exit_plugins', 'postbuild_plugins'))
    @pytest.mark.parametrize(('kojihub', 'pulp_registry', 'pulp_secret', 'enabled'), [
        (True, True, True, True),
        (True, False, True, False),
        (True, False, False, False),
        (False, True, True, False),
        (False, False, True, False),
        (False, False, False, False),
    ])
    def test_render_pulp_pull(self, plugin_type, kojihub, pulp_registry, pulp_secret, enabled,
                              prefer_schema1_digest):
        plugin_name = 'pulp_pull'

        br = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        kwargs.pop('pulp_registry', None)
        kwargs.pop('pulp_secret', None)
        kwargs.pop('kojihub', None)

        if pulp_registry:
            kwargs['pulp_registry'] = 'registry.example.com'
        if pulp_secret:
            kwargs['pulp_secret'] = 'pulp_secret'
        if kojihub:
            kwargs['kojihub'] = 'http://hub/'
        if prefer_schema1_digest is not None:
            kwargs['prefer_schema1_digest'] = prefer_schema1_digest
        br.set_params(**kwargs)

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, {})

        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if enabled:
            assert get_plugin(plugins, plugin_type, plugin_name)
            plugin_args = plugin_value_get(plugins, plugin_type, plugin_name, 'args')
            if prefer_schema1_digest is not None:
                assert plugin_args['expect_v2schema2'] is not prefer_schema1_digest
            else:
                assert 'expect_v2schema2' not in plugin_args
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)

    @pytest.mark.parametrize('odcs_insecure', (True, False, None))
    @pytest.mark.parametrize('additional_params', (
        {'signing_intent': 'release'},
        {'compose_ids': [1, ]},
        {'compose_ids': [1, 2]},
    ))
    @pytest.mark.parametrize('odcs_openidc_secret', (None, 'odcs-openidc'))
    @pytest.mark.parametrize('odcs_ssl_secret', (None, 'odcs-ssl'))
    def test_render_resolve_composes(self, odcs_insecure, additional_params, odcs_openidc_secret,
                                     odcs_ssl_secret):
        plugin_type = 'prebuild_plugins'
        plugin_name = 'resolve_composes'

        odcs_url = 'https://odcs.example.com/odcs/1'

        br = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs.pop('odcs_url', None)
        kwargs['odcs_url'] = odcs_url
        if odcs_insecure is not None:
            kwargs['odcs_insecure'] = odcs_insecure
        if odcs_openidc_secret is not None:
            kwargs['odcs_openidc_secret'] = odcs_openidc_secret
        if odcs_ssl_secret is not None:
            kwargs['odcs_ssl_secret'] = odcs_ssl_secret
        kwargs.update(additional_params)

        expected_plugin_args = {
            'odcs_url': odcs_url,
            'odcs_insecure': bool(odcs_insecure),
            'koji_hub': kwargs['kojihub'],
            'koji_target': kwargs['koji_target'],
        }
        expected_plugin_args.update(additional_params)

        br.set_params(**kwargs)

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, {})

        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if odcs_openidc_secret:
            openidc_mount_path = get_secret_mountpath_by_name(build_json, odcs_openidc_secret)
            expected_plugin_args['odcs_openidc_secret_path'] = openidc_mount_path

        if odcs_ssl_secret:
            ssl_mount_path = get_secret_mountpath_by_name(build_json, odcs_ssl_secret)
            expected_plugin_args['odcs_ssl_secret_path'] = ssl_mount_path

        assert get_plugin(plugins, plugin_type, plugin_name)
        plugin_args = plugin_value_get(plugins, plugin_type, plugin_name, 'args')
        assert plugin_args == expected_plugin_args

    @pytest.mark.parametrize(('odcs_url', 'yum_repos', 'raised'), [
        ('https://odcs.example.com/odcs/1', None, False),
        ('https://odcs.example.com/odcs/1', [], False),
        ('https://odcs.example.com/odcs/1', ["http://example.com/my.repo"], True),
        (None, ["http://example.com/my.repo"], True),
        (None, [], True),
        (None, None, True),
    ])
    def test_remove_resolve_composes(self, odcs_url, yum_repos, raised):
        plugin_type = 'prebuild_plugins'
        plugin_name = 'resolve_composes'

        br = BuildRequest(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs.pop('odcs_url', None)
        if odcs_url:
            kwargs['odcs_url'] = odcs_url
        if yum_repos:
            kwargs['yum_repourls'] = yum_repos

        br.set_params(**kwargs)

        br.dj.dock_json_set_param(plugin_type, [])
        br.dj.add_plugin(plugin_type, plugin_name, {})

        build_json = br.render()
        plugins = get_plugins_from_build_json(build_json)

        if raised:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)
        else:
            assert get_plugin(plugins, plugin_type, plugin_name)

    @pytest.mark.parametrize('reactor_config_secret', [
        None,
        'reactor_config_secret',
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        None,
        {'version': 1},
    ])
    @pytest.mark.parametrize('reactor_config_map', [
        None,
        'reactor-config-map',
    ])
    def test_render_reactor_config(self, reactor_config_secret,
                                   reactor_config_override, reactor_config_map):
        plugin_type = 'prebuild_plugins'
        plugin_name = 'reactor_config'

        build_request = BuildRequest(INPUTS_PATH)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uri': "example.com",
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'build_image': 'fedora:latest',
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v2'],
            'reactor_config_map': reactor_config_map,
            'reactor_config_override': reactor_config_override,
            'reactor_config_secret': reactor_config_secret,
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        plugins = get_plugins_from_build_json(build_json)

        if reactor_config_secret is None:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)
        else:
            assert get_plugin(plugins, plugin_type, plugin_name)
