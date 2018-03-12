"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
# import copy
# import glob
import json
import os
# import shutil

from osbs.build.user_params import BuildUserParams
from osbs.build.plugins_configuration import PluginsConfiguration
from osbs.constants import (BUILD_TYPE_WORKER, BUILD_TYPE_ORCHESTRATOR,
                            ADDITIONAL_TAGS_FILE, REACTOR_CONFIG_ARRANGEMENT_VERSION)
from osbs.exceptions import OsbsValidationException
from osbs.conf import Configuration

import pytest

from tests.constants import (INPUTS_PATH, TEST_BUILD_CONFIG,
                             TEST_COMPONENT, TEST_FLATPAK_BASE_IMAGE,
                             TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI,
                             TEST_FILESYSTEM_KOJI_TASK_ID, TEST_SCRATCH_BUILD_NAME,
                             TEST_ISOLATED_BUILD_NAME, TEST_USER)
# These are used as fixtures
from tests.fake_api import openshift, osbs  # noqa


USE_DEFAULT_TRIGGERS = object()


class NoSuchPluginException(Exception):
    pass


def get_sample_prod_params(build_type=BUILD_TYPE_ORCHESTRATOR):
    return {
        'git_uri': TEST_GIT_URI,
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_BRANCH,
        'user': TEST_USER,
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'koji_target': 'koji-target',
        'platforms': ['x86_64'],
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
        'build_from': 'image:buildroot:latest',
        'build_type': build_type,
    }


def get_sample_user_params(extra_args=None, build_type=BUILD_TYPE_ORCHESTRATOR):
    sample_params = get_sample_prod_params(build_type)
    if extra_args:
        sample_params.update(extra_args)
    user_params = BuildUserParams(INPUTS_PATH)
    user_params.set_params(**sample_params)
    return user_params


def get_plugins_from_build_json(build_json):
    return json.loads(build_json)


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


class TestPluginsConfiguration(object):

    def assert_import_image_plugin(self, plugins, name_label):
        phase = 'postbuild_plugins'
        plugin = 'import_image'

        assert get_plugin(plugins, phase, plugin)
        plugin_args = plugin_value_get(plugins, phase, plugin, 'args')

        assert plugin_args['imagestream'] == name_label.replace('/', '-')

    def assert_koji_upload_plugin(self, plugins):
        phase = 'postbuild_plugins'
        name = 'koji_upload'

        assert get_plugin(plugins, phase, name)
        plugin_args = plugin_value_get(plugins, phase, name, 'args')
        assert plugin_args.get('build_json_dir')

    @pytest.mark.parametrize('build_type', (BUILD_TYPE_ORCHESTRATOR, BUILD_TYPE_WORKER))
    def test_render_koji_upload(self, build_type):
        user_params = get_sample_user_params(build_type=build_type)
        build_json = PluginsConfiguration(user_params).render()
        plugins = get_plugins_from_build_json(build_json)
        if build_type == BUILD_TYPE_WORKER:
            self.assert_koji_upload_plugin(plugins)
        else:
            with pytest.raises(NoSuchPluginException):
                assert get_plugin(plugins, 'postbuild_plugins', 'koji_upload')

    @pytest.mark.parametrize(('base_image', 'scratch', 'enabled'), (
        ('fedora:latest', True, False),
        ('fedora:latest', False, True),
        ('fedora:latest', False, False),
        ('koji/image-build', False, False),
        ('koji/image-build', True, False),
    ))
    def test_render_koji_import(self, base_image, scratch, enabled):
        plugin_type = 'exit_plugins'
        plugin_name = 'koji_import'

        extra_args = None
        if not enabled:
            extra_args = {'koji_target': None}

        user_params = get_sample_user_params(extra_args)
        build_json = PluginsConfiguration(user_params).render()
        plugins = get_plugins_from_build_json(build_json)

        if not enabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)
            return

        assert get_plugin(plugins, plugin_type, plugin_name)

        actual_plugin_args = plugin_value_get(plugins, plugin_type, plugin_name, 'args')

        expected_plugin_args = {'koji_keytab': False,
                                'target': 'koji-target',
                                'verify_ssl': False}

        assert actual_plugin_args == expected_plugin_args

    def test_render_simple_request(self):
        user_params = get_sample_user_params()
        build_json = PluginsConfiguration(user_params).render()
        plugins = get_plugins_from_build_json(build_json)

        pull_base_image = get_plugin(plugins, "prebuild_plugins", "pull_base_image")
        assert pull_base_image is not None
        assert ('args' not in pull_base_image or
                'parent_registry' not in pull_base_image['args'] or
                not pull_base_image['args']['parent_registry'])

    @pytest.mark.parametrize('build_type', (BUILD_TYPE_ORCHESTRATOR, BUILD_TYPE_WORKER))
    @pytest.mark.parametrize(('build_image', 'imagestream_name', 'valid'), (
        (None, None, False),
        ('ultimate-buildroot:v1.0', None, True),
        (None, 'buildroot-stream:v1.0', True),
        ('ultimate-buildroot:v1.0', 'buildroot-stream:v1.0', False)
    ))
    def test_render_request_with_yum(self, build_image, imagestream_name, valid, build_type):
        extra_args = {
            'build_image': build_image,
            'build_imagestream': imagestream_name,
            'name_label': "fedora/resultingimage",
            'build_from': None,
            'yum_repourls': ["http://example.com/my.repo"],
            'build_type': build_type,
        }

        if valid:
            user_params = get_sample_user_params(extra_args)
            build_json = PluginsConfiguration(user_params).render()
        else:
            with pytest.raises(OsbsValidationException):
                user_params = get_sample_user_params(extra_args)
                build_json = PluginsConfiguration(user_params).render()
            return

        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins",
                       "stop_autorebuild_if_disabled")

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")

        if imagestream_name and build_type == BUILD_TYPE_ORCHESTRATOR:
            assert get_plugin(plugins, "exit_plugins", "import_image")
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "postbuild_plugins", "import_image")

        if build_type == BUILD_TYPE_WORKER:
            assert plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url",
                                    "args", "repourls") == ["http://example.com/my.repo"]
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "prebuild_plugins", "add_yum_repo_by_url")

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert 'release' not in labels

    # May be incomplete
    @pytest.mark.parametrize(('extra_args', 'expected_name'), (
        ({'isolated': True, 'release': '1.1'}, TEST_ISOLATED_BUILD_NAME),
        ({'scratch': True}, TEST_SCRATCH_BUILD_NAME),
        ({}, TEST_BUILD_CONFIG),
    ))
    def test_render_build_name(self, tmpdir, extra_args, expected_name):
        user_params = get_sample_user_params(extra_args)
        build_json = PluginsConfiguration(user_params).render()

        assert get_plugins_from_build_json(build_json)

    @pytest.mark.parametrize('build_type', (BUILD_TYPE_ORCHESTRATOR, BUILD_TYPE_WORKER))
    @pytest.mark.parametrize('compose_ids', (None, [], [42], [42, 2]))
    def test_render_flatpak(self, compose_ids, build_type):
        extra_args = {
            'flatpak': True,
            'compose_ids': compose_ids,
            'flatpak_base_image': TEST_FLATPAK_BASE_IMAGE,
            'base_image': TEST_FLATPAK_BASE_IMAGE,
            'build_type': build_type,
        }

        user_params = get_sample_user_params(extra_args)
        build_json = PluginsConfiguration(user_params).render()

        plugins = get_plugins_from_build_json(build_json)

        plugin = get_plugin(plugins, "prebuild_plugins", "resolve_module_compose")
        assert plugin

        args = plugin['args']
        # compose_ids will always have a value of at least []
        if compose_ids is None:
            assert args['compose_ids'] == []
        else:
            assert args['compose_ids'] == compose_ids

        plugin = get_plugin(plugins, "prebuild_plugins", "flatpak_create_dockerfile")
        assert plugin

        args = plugin['args']
        assert args['base_image'] == TEST_FLATPAK_BASE_IMAGE

        if build_type == BUILD_TYPE_ORCHESTRATOR:
            plugin = get_plugin(plugins, "prebuild_plugins", "bump_release")
            assert plugin

            args = plugin['args']
            assert args['append'] is True
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "prepublish_plugins", "flatpak_create_oci")
        else:
            assert get_plugin(plugins, "prepublish_plugins", "flatpak_create_oci")
            with pytest.raises(NoSuchPluginException):
                plugin = get_plugin(plugins, "prebuild_plugins", "bump_release")

        with pytest.raises(NoSuchPluginException):
            assert get_plugin(plugins, "prepublish_plugins", "squash")
        with pytest.raises(NoSuchPluginException):
            assert get_plugin(plugins, "postbuild_plugins", "import_image")

    @pytest.mark.parametrize('build_type', (BUILD_TYPE_ORCHESTRATOR, BUILD_TYPE_WORKER))
    def test_render_prod_not_flatpak(self, build_type):
        extra_args = {
            'flatpak': False,
            'build_type': build_type,
        }
        user_params = get_sample_user_params(extra_args)
        build_json = PluginsConfiguration(user_params).render()

        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "resolve_module_compose")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "flatpak_create_dockerfile")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prepublish_plugins", "flatpak_create_oci")
        if build_type == BUILD_TYPE_ORCHESTRATOR:
            with pytest.raises(NoSuchPluginException):
                assert get_plugin(plugins, "prepublish_plugins", "squash")
            assert get_plugin(plugins, "exit_plugins", "import_image")
        else:
            assert get_plugin(plugins, "prepublish_plugins", "squash")

    @pytest.mark.parametrize(('disabled', 'release'), (
        (False, None),
        (True, '1.2.1'),
        (False, None),
    ))
    @pytest.mark.parametrize('flatpak', (True, False))
    def test_render_bump_release(self, disabled, release, flatpak):
        extra_args = {
            'release': release,
            'flatpak': flatpak,
            'build_type': BUILD_TYPE_ORCHESTRATOR,
        }

        user_params = get_sample_user_params(extra_args)
        build_json = PluginsConfiguration(user_params).render()

        plugins = get_plugins_from_build_json(build_json)

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")
        assert labels.get('release') == release

        if disabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, "prebuild_plugins", "bump_release")
            return

        plugin = get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin
        if plugin.get('args'):
            assert plugin['args'].get('append', False) == flatpak

    @pytest.mark.parametrize(('extra_args', 'has_platform_tag', 'extra_tags', 'primary_tags'), (
        # Worker build cases
        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64'}, True, (), ()),
        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64'}, True, ('tag1', 'tag2'), ()),
        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64', 'scratch': True}, True, (), ()),
        ({'build_type': BUILD_TYPE_WORKER, 'platform': 'x86_64',
          'isolated': True, 'release': '1.1'}, True, (), ()),
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
    def test_render_tag_from_config(self, tmpdir, extra_args, has_platform_tag, extra_tags,
                                    primary_tags):
        kwargs = get_sample_prod_params(BUILD_TYPE_WORKER)
        kwargs.pop('platforms', None)
        kwargs.pop('platform', None)
        expected_primary = set(primary_tags)

        if extra_tags:
            with open(os.path.join(str(tmpdir), ADDITIONAL_TAGS_FILE), 'w') as f:
                f.write('\n'.join(extra_tags))
            extra_args['additional_tag_data'] = {
                'dir_path': str(tmpdir),
                'file_name': ADDITIONAL_TAGS_FILE,
                'tags': set(),
            }
        kwargs.update(extra_args)
        user_params = BuildUserParams(INPUTS_PATH)
        user_params.set_params(**kwargs)
        build_json = PluginsConfiguration(user_params).render()

        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, 'postbuild_plugins', 'tag_from_config')
        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['unique']) == 1
        if has_platform_tag:
            unique_tag_suffix = tag_suffixes['unique'][0]
            assert unique_tag_suffix.endswith('-x86_64') == has_platform_tag
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary

    @pytest.mark.parametrize(('platforms', 'disabled'), (
        (['x86_64', 'ppc64le'], False),
        (None, True),
    ))
    @pytest.mark.parametrize('koji_parent_build', ['fedora-26-9', None])
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
            'flatpak': True,
            'flatpak_base_image': "fedora:latest",
        },
        {},
    ))
    def test_render_orchestrate_build(self, tmpdir, platforms, disabled,
                                      build_from, build_image,
                                      build_imagestream, worker_build_image,
                                      additional_kwargs, koji_parent_build, valid):
        phase = 'buildstep_plugins'
        plugin = 'orchestrate_build'

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'platforms': platforms,
            'build_type': BUILD_TYPE_ORCHESTRATOR,
        }
        if build_image:
            kwargs['build_image'] = build_image
        if build_imagestream:
            kwargs['build_imagestream'] = build_imagestream
        if build_from:
            kwargs['build_from'] = build_from
        if koji_parent_build:
            kwargs['koji_parent_build'] = koji_parent_build
        kwargs.update(additional_kwargs)

        user_params = BuildUserParams(INPUTS_PATH)

        if valid:
            user_params.set_params(**kwargs)
            build_json = PluginsConfiguration(user_params).render()
        else:
            with pytest.raises(OsbsValidationException):
                user_params.set_params(**kwargs)
                build_json = PluginsConfiguration(user_params).render()
            return

        plugins = get_plugins_from_build_json(build_json)

        if disabled:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, phase, plugin)

        else:
            assert plugin_value_get(plugins, phase, plugin, 'args', 'platforms') == platforms
            build_kwargs = plugin_value_get(plugins, phase, plugin, 'args', 'build_kwargs')
            assert build_kwargs['arrangement_version'] == REACTOR_CONFIG_ARRANGEMENT_VERSION
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

            if kwargs.get('flatpak', False):
                assert kwargs.get('flatpak') is True
                assert kwargs.get('flatpak_base_image') == worker_config.get_flatpak_base_image()

        """
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
        # Create JSON templates with an image change trigger added.

        # :param outdir: str, path to store modified templates
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


    @pytest.mark.parametrize(('base_image', 'is_custom'), [
        ('fedora', False),
        ('fedora:latest', False),
        ('koji/image-build', True),
        ('koji/image-build:spam.conf', True),
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
        # Test to make sure that when we attempt to enable a plugin, it is
        # actually enabled in the JSON for the build_request after running
        # build_request.render()
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
        # Test to make sure that when we attempt to disable a plugin, it is
        # actually disabled in the JSON for the build_request after running
        # build_request.render()
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
        # Test to make sure that when we attempt to override a plugin's args,
        # they are actually overridden in the JSON for the build_request
        # after running build_request.render()
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
        """
    @pytest.mark.parametrize(('koji_parent_build'), (
        ('fedora-26-9'),
        (None),
    ))
    def test_render_inject_parent_image(self, koji_parent_build):
        plugin_type = "prebuild_plugins"
        plugin_name = "inject_parent_image"

        extra_args = {
            'koji_parent_build': koji_parent_build,
        }

        user_params = get_sample_user_params(extra_args)
        build_json = PluginsConfiguration(user_params).render()
        plugins = get_plugins_from_build_json(build_json)

        if koji_parent_build:
            assert get_plugin(plugins, plugin_type, plugin_name)
            assert plugin_value_get(plugins, plugin_type, plugin_name, 'args',
                                    'koji_parent_build') == koji_parent_build
        else:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)

    @pytest.mark.parametrize('additional_params', (
        {'signing_intent': 'release'},
        {'compose_ids': [1, ]},
        {'compose_ids': [1, 2]},
    ))
    def test_render_resolve_composes(self, additional_params):
        plugin_type = 'prebuild_plugins'
        plugin_name = 'resolve_composes'

        user_params = get_sample_user_params(additional_params)
        build_json = PluginsConfiguration(user_params).render()
        plugins = get_plugins_from_build_json(build_json)

        assert get_plugin(plugins, plugin_type, plugin_name)
        assert plugin_value_get(plugins, plugin_type, plugin_name, 'args')

    @pytest.mark.parametrize('yum_repos', [
        None,
        [],
        ["http://example.com/my.repo"],
    ])
    def test_remove_resolve_composes(self, yum_repos):
        plugin_type = 'prebuild_plugins'
        plugin_name = 'resolve_composes'

        additional_params = {
            'yum_repourls': yum_repos
        }

        user_params = get_sample_user_params(additional_params)
        build_json = PluginsConfiguration(user_params).render()
        plugins = get_plugins_from_build_json(build_json)

        if yum_repos:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, plugin_type, plugin_name)
        else:
            assert get_plugin(plugins, plugin_type, plugin_name)
