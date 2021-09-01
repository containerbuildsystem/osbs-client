"""
Copyright (c) 2017, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals, absolute_import
import shutil
import os
import json
from osbs.api import OSBS
from osbs.constants import (DEFAULT_ARRANGEMENT_VERSION,
                            ORCHESTRATOR_INNER_TEMPLATE,
                            ORCHESTRATOR_SOURCES_INNER_TEMPLATE,
                            WORKER_INNER_TEMPLATE,
                            ORCHESTRATOR_OUTER_TEMPLATE)
from osbs import utils
from osbs.conf import Configuration
from osbs.constants import USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS
from osbs.repo_utils import RepoInfo, ModuleSpec
from osbs.build.build_requestv2 import BuildRequestV2
from osbs.build.plugins_configuration import (PluginsConfiguration,
                                              SourceContainerPluginsConfiguration,)
from osbs.build.user_params import BuildUserParams, SourceContainerUserParams
from tests.constants import (TEST_GIT_URI,
                             TEST_GIT_REF,
                             TEST_GIT_BRANCH,
                             TEST_COMPONENT,
                             TEST_VERSION,
                             INPUTS_PATH,
                             TEST_USER,
                             TEST_KOJI_BUILD_NVR,)
from tests.test_api import request_as_response
from flexmock import flexmock
import pytest


# Copied from atomic_reactor.constants
# Can't import directly, because atomic_reactor depends on osbs-client and therefore
# osbs-client can't dpeend on atomic_reactor.
# Don't want to put these in osbs.constants and then have atomic_reactor import them,
# because then atomic_reactor could break in weird ways if run with the wrong version
# of osbs-client
# But we need to verify the input json against the actual keys, so keeping this list
# up to date is the best solution.
PLUGIN_KOJI_IMPORT_PLUGIN_KEY = 'koji_import'
PLUGIN_KOJI_IMPORT_SOURCE_CONTAINER_PLUGIN_KEY = 'koji_import_source_container'
PLUGIN_KOJI_UPLOAD_PLUGIN_KEY = 'koji_upload'
PLUGIN_KOJI_TAG_BUILD_KEY = 'koji_tag_build'
PLUGIN_ADD_FILESYSTEM_KEY = 'add_filesystem'
PLUGIN_FETCH_WORKER_METADATA_KEY = 'fetch_worker_metadata'
PLUGIN_GROUP_MANIFESTS_KEY = 'group_manifests'
PLUGIN_BUILD_ORCHESTRATE_KEY = 'orchestrate_build'
PLUGIN_KOJI_PARENT_KEY = 'koji_parent'
PLUGIN_COMPARE_COMPONENTS_KEY = 'compare_components'
PLUGIN_CHECK_AND_SET_PLATFORMS_KEY = 'check_and_set_platforms'
PLUGIN_REMOVE_WORKER_METADATA_KEY = 'remove_worker_metadata'
PLUGIN_RESOLVE_COMPOSES_KEY = 'resolve_composes'
PLUGIN_VERIFY_MEDIA_KEY = 'verify_media'
PLUGIN_EXPORT_OPERATOR_MANIFESTS_KEY = 'export_operator_manifests'
PLUGIN_PUSH_OPERATOR_MANIFESTS_KEY = 'push_operator_manifests'
PLUGIN_GENERATE_MAVEN_METADATA_KEY = 'generate_maven_metadata'


class NoSuchPluginException(Exception):
    pass


def get_plugin(plugins, plugin_type, plugin_name):
    plugins_type = plugins[plugin_type]
    for plugin in plugins_type:
        if plugin["name"] == plugin_name:
            return plugin
    else:
        raise NoSuchPluginException()


def plugin_value_get(plugins, plugin_type, plugin_name, *args):
    result = get_plugin(plugins, plugin_type, plugin_name)
    for arg in args:
        result = result[arg]
    return result


def unsupported_arrangement_version(version_test_class):
    """
    Mark a test class as unsupported to disable version validation.
    Does not disable validation for classes that inherit from said class.
    """
    from osbs.api import validate_arrangement_version

    def setup_class(cls):
        import osbs.api
        osbs.api.validate_arrangement_version = lambda version: None

    def teardown_class(cls):
        import osbs.api
        # restore original validation logic
        osbs.api.validate_arrangement_version = validate_arrangement_version
        # prevent setup and teardown of child classes
        del cls.setup_class, cls.teardown_class

    version_test_class.setup_class = classmethod(setup_class)
    version_test_class.teardown_class = classmethod(teardown_class)

    return version_test_class


class ArrangementBase(object):
    ARRANGEMENT_VERSION = None
    COMMON_PARAMS = {}
    DEFAULT_PLUGINS = {}
    ORCHESTRATOR_ADD_PARAMS = {}
    WORKER_ADD_PARAMS = {}

    def mock_env(self, base_image='fedora23/python', additional_tags=None,
                 flatpak=False):
        class MockParser(object):
            labels = {
                'name': 'fedora23/something',
                'com.redhat.component': TEST_COMPONENT,
                'version': TEST_VERSION,
            }
            baseimage = base_image

        class MockConfiguration(object):
            def __init__(self, git_uri, git_ref, git_branch, depth):
                self.container = {
                    'tags': additional_tags or [],
                    'compose': {
                        'modules': ['mod_name:mod_stream:mod_version']
                    }
                }

                self.module = self.container['compose']['modules'][0]
                self.container_module_specs = [ModuleSpec.from_str(self.module)]
                self.depth = int(depth) if depth else 0
                self.is_flatpak = flatpak
                self.flatpak_base_image = None
                self.flatpak_component = None
                self.flatpak_name = None
                self.git_uri = git_uri
                self.git_ref = git_ref
                self.git_branch = git_branch

            def is_autorebuild_enabled(self):
                return False

        mock_conf = MockConfiguration(TEST_GIT_URI, TEST_GIT_REF, TEST_GIT_BRANCH, None)
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(RepoInfo(MockParser(), mock_conf)))

        # Trick create_orchestrator_build into return the *request* JSON
        flexmock(OSBS, _create_build_config_and_build=request_as_response)
        flexmock(OSBS, _create_scratch_build=request_as_response)

    def get_plugins_from_buildrequest(self, build_request, template=None):
        return build_request.inner_template

    @pytest.mark.parametrize('template', [  # noqa:F811
        ORCHESTRATOR_INNER_TEMPLATE,
        WORKER_INNER_TEMPLATE,
    ])
    def test_running_order(self, osbs, template):
        """
        Verify the plugin running order.

        This is to catch tests missing from these test classes when a
        plugin is added.
        """

        inner_template = template.format(
            arrangement_version=self.ARRANGEMENT_VERSION,
        )
        build_request = osbs.get_build_request(inner_template=inner_template,
                                               arrangement_version=self.ARRANGEMENT_VERSION)
        plugins = self.get_plugins_from_buildrequest(build_request, template)
        phases = ('prebuild_plugins',
                  'buildstep_plugins',
                  'prepublish_plugins',
                  'postbuild_plugins',
                  'exit_plugins')
        actual = {}
        for phase in phases:
            actual[phase] = [plugin['name']
                             for plugin in plugins.get(phase, {})]

        assert actual == self.DEFAULT_PLUGINS[template]

    def get_build_request(self, build_type, osbs,  # noqa:F811
                          additional_params=None):
        base_image = additional_params.pop('base_image', None)
        self.mock_env(base_image=base_image,
                      additional_tags=additional_params.get('additional_tags'),
                      flatpak=additional_params.get('flatpak'))
        params = self.COMMON_PARAMS.copy()
        assert build_type in ('orchestrator', 'worker',
                              'source_container')
        if build_type == 'orchestrator':
            params.update(self.ORCHESTRATOR_ADD_PARAMS)
            fn = osbs.create_orchestrator_build
        elif build_type == 'worker':
            params.update(self.WORKER_ADD_PARAMS)
            fn = osbs.create_worker_build
        elif build_type == 'source_container':
            params.update(self.ORCHESTRATOR_ADD_PARAMS)
            fn = osbs.create_source_container_build

        params.update(additional_params or {})
        params['arrangement_version'] = self.ARRANGEMENT_VERSION
        osbs.build_conf = osbs.build_conf or Configuration(params)

        return params, fn(**params).json

    def get_orchestrator_build_request(self, osbs,  # noqa:F811
                                       additional_params=None):
        return self.get_build_request('orchestrator', osbs, additional_params)

    def get_worker_build_request(self, osbs,  # noqa:F811
                                 additional_params=None):
        return self.get_build_request('worker', osbs, additional_params)

    def get_source_container_build_request(self, osbs,
                                           additional_params=None):
        return self.get_build_request('source_container', osbs, additional_params)


class TestArrangementV6(ArrangementBase):
    """
    This class tests support for the oldest supported arrangement
    version, 6.

    NOTE! When removing this test class, *make sure* that any methods
    it provides for the test class for the next oldest supported
    arrangement version are copied across to that test class.

    No change to parameters, but use UserParams, BuildRequestV2, and PluginsConfiguration
    instead of Spec and BuildRequest. Most plugin arguments are not populated by
    osbs-client but are pulled from the REACTOR_CONFIG environment variable in
    atomic-reactor at runtime.

    Inherit from ArrangementBase, not the previous arrangements, because argument handling is
    different now and all previous tests break.

    No orchestrator build differences from arrangement version 5

    No worker build differences from arrangement version 5
    """

    ARRANGEMENT_VERSION = 6

    # Override common params
    COMMON_PARAMS = {
        'git_uri': TEST_GIT_URI,
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_BRANCH,
        'user': 'john-foo',
        'build_from': 'image:test',
        'reactor_config_map': 'special-config',
    }

    ORCHESTRATOR_ADD_PARAMS = {
        'build_type': 'orchestrator',
        'platforms': ['x86_64'],
    }

    WORKER_ADD_PARAMS = {
        'build_type': 'worker',
        'platform': 'x86_64',
        'release': 1,
    }

    DEFAULT_PLUGINS = {
        # Changing this? Add test methods
        ORCHESTRATOR_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'reactor_config',
                'check_user_settings',
                'check_and_set_rebuild',
                PLUGIN_CHECK_AND_SET_PLATFORMS_KEY,
                'flatpak_create_dockerfile',
                'inject_parent_image',
                'pull_base_image',
                PLUGIN_KOJI_PARENT_KEY,
                'koji_delegate',
                PLUGIN_RESOLVE_COMPOSES_KEY,
                PLUGIN_ADD_FILESYSTEM_KEY,
                'flatpak_update_dockerfile',
                'bump_release',
                'add_flatpak_labels',
                'add_labels_in_dockerfile',
                'resolve_remote_source',
                'pin_operator_digest',
            ],

            'buildstep_plugins': [
                PLUGIN_BUILD_ORCHESTRATE_KEY,
            ],

            'prepublish_plugins': [
            ],

            'postbuild_plugins': [
                PLUGIN_FETCH_WORKER_METADATA_KEY,
                PLUGIN_COMPARE_COMPONENTS_KEY,
                'tag_from_config',
                PLUGIN_GROUP_MANIFESTS_KEY,
                PLUGIN_PUSH_OPERATOR_MANIFESTS_KEY,
                PLUGIN_GENERATE_MAVEN_METADATA_KEY,
            ],

            'exit_plugins': [
                PLUGIN_VERIFY_MEDIA_KEY,
                PLUGIN_KOJI_IMPORT_PLUGIN_KEY,
                'push_floating_tags',
                'import_image',
                'koji_tag_build',
                'store_metadata_in_osv3',
                'sendmail',
                'remove_built_image',
                PLUGIN_REMOVE_WORKER_METADATA_KEY,
            ],
        },

        # Changing this? Add test methods
        WORKER_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'reactor_config',
                'flatpak_create_dockerfile',
                'flatpak_update_dockerfile',
                PLUGIN_ADD_FILESYSTEM_KEY,
                'inject_parent_image',
                'pull_base_image',
                'add_flatpak_labels',
                'add_labels_in_dockerfile',
                'change_from_in_dockerfile',
                'add_help',
                'fetch_maven_artifacts',
                'add_image_content_manifest',
                'add_dockerfile',
                'distgit_fetch_artefacts',
                'koji',
                'add_yum_repo_by_url',
                'inject_yum_repo',
                'hide_files',
                'distribution_scope',
                'download_remote_source',
                'add_buildargs_in_dockerfile',
                'pin_operator_digest',
            ],

            'buildstep_plugins': [
            ],

            'prepublish_plugins': [
                'squash',
                'flatpak_create_oci',
            ],

            'postbuild_plugins': [
                'all_rpm_packages',
                'tag_from_config',
                'tag_and_push',
                PLUGIN_EXPORT_OPERATOR_MANIFESTS_KEY,
                'compress',
                PLUGIN_KOJI_UPLOAD_PLUGIN_KEY,
            ],

            'exit_plugins': [
                'store_metadata_in_osv3',
                'remove_built_image',
            ],
        },
    }

    # override
    def get_plugins_from_buildrequest(self, build_request, template):
        conf_kwargs = {
           'build_from': 'image:test',
           'reactor_config_map': 'reactor-config-map',
        }
        kwargs = {
            'build_json_dir': INPUTS_PATH,
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': 'john-foo',
            'build_type': template.split('_')[0],
            'build_conf': Configuration(conf_file=None, **conf_kwargs),
            'base_image': 'test',
            'name_label': 'test',
        }
        user_params = BuildUserParams.make_params(**kwargs)
        build_request.set_params(user_params)
        return PluginsConfiguration(build_request.user_params).pt.template

    def get_build_request(self, build_type, osbs,  # noqa:F811
                          additional_params=None):
        if not additional_params.get('reactor_config_override'):
            (flexmock(BuildRequestV2)
                .should_receive('get_reactor_config_data')
                .and_return({}))

        params, build_json = super(TestArrangementV6, self).get_build_request(build_type, osbs,
                                                                              additional_params)
        # Make the REACTOR_CONFIG return look like previous returns
        env = build_json['spec']['strategy']['customStrategy']['env']
        for entry in env:
            if entry['name'] == 'USER_PARAMS':
                user_params = entry['value']
                break
        else:
            raise KeyError('USER_PARAMS not set in env')

        plugins_json = json.loads(osbs.render_plugins_configuration(user_params))

        return params, plugins_json

    def test_is_default(self):
        """
        Test this is the default arrangement
        """

        # Note! If this test fails it probably means you need to
        # derive a new TestArrangementV[n] class from this class and
        # move the method to the new class.
        assert DEFAULT_ARRANGEMENT_VERSION == self.ARRANGEMENT_VERSION

    @pytest.mark.parametrize('build_type', [  # noqa:F811
        'orchestrator',
        'worker',
    ])
    @pytest.mark.parametrize('scratch', [False, True])
    @pytest.mark.parametrize('base_image', ['koji/image-build', 'foo'])
    def test_pull_base_image(self, osbs, build_type, scratch, base_image):
        phase = 'prebuild_plugins'
        plugin = 'pull_base_image'
        additional_params = {
            'base_image': base_image,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        if scratch:
            additional_params['scratch'] = True

        _, plugins = self.get_build_request(build_type, osbs, additional_params)

        assert get_plugin(plugins, phase, plugin)

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    @pytest.mark.parametrize('base_image', ['koji/image-build', 'foo'])
    def test_add_filesystem_in_worker(self, osbs, base_image, scratch):
        additional_params = {
            'base_image': base_image,
            'yum_repourls': ['https://example.com/my.repo'],
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        if scratch:
            additional_params['scratch'] = True
        params, plugins = self.get_worker_build_request(osbs, additional_params)

        args = plugin_value_get(plugins, 'prebuild_plugins', PLUGIN_ADD_FILESYSTEM_KEY, 'args')

        assert 'repos' in args.keys()
        assert args['repos'] == params['yum_repourls']

    def test_resolve_composes(self, osbs):  # noqa:F811
        koji_target = 'koji-target'

        additional_params = {
            'base_image': 'fedora:latest',
            'target': koji_target,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        assert get_plugin(plugins, 'prebuild_plugins', 'reactor_config')
        assert get_plugin(plugins, 'prebuild_plugins', PLUGIN_RESOLVE_COMPOSES_KEY)

    def test_import_image_renders(self, osbs):
        additional_params = {
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        args = plugin_value_get(plugins, 'exit_plugins', 'import_image', 'args')

        match_args = {
            "imagestream": "source_registry-fedora23-something",
        }
        assert match_args == args

    def test_orchestrate_render_no_platforms(self, osbs):  # noqa:F811
        additional_params = {
            'platforms': None,
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        args = plugin_value_get(plugins, 'buildstep_plugins',
                                PLUGIN_BUILD_ORCHESTRATE_KEY, 'args')

        assert 'platforms' not in args

    @pytest.mark.parametrize('extract_platform', ['x86_64', None])  # noqa:F811
    def test_export_operator_manifests(self, osbs, extract_platform):
        additional_params = {'base_image': 'fedora:latest',
                             'reactor_config_override':
                                 {'source_registry': {'url': 'source_registry'}}}
        match_args = {'platform': 'x86_64'}
        if extract_platform:
            additional_params['operator_manifests_extract_platform'] = extract_platform
            match_args['operator_manifests_extract_platform'] = extract_platform

        _, plugins = self.get_worker_build_request(osbs, additional_params)
        args = plugin_value_get(plugins, 'postbuild_plugins', 'export_operator_manifests', 'args')
        assert match_args == args

    @pytest.mark.parametrize('base_image', ['koji/image-build', 'foo'])
    def test_koji_parent_in_orchestrator(self, osbs, base_image):
        additional_params = {
            'base_image': base_image,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        get_plugin(plugins, 'prebuild_plugins', PLUGIN_KOJI_PARENT_KEY)
        with pytest.raises(KeyError):
            plugin_value_get(plugins, 'prebuild_plugins', PLUGIN_KOJI_PARENT_KEY, 'args')

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_koji_upload(self, scratch, osbs):
        additional_params = {
            'base_image': 'fedora:latest',
            'koji_upload_dir': 'upload',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        if scratch:
            additional_params['scratch'] = True
        _, plugins = self.get_worker_build_request(osbs, additional_params)

        args = plugin_value_get(plugins, 'postbuild_plugins', PLUGIN_KOJI_UPLOAD_PLUGIN_KEY, 'args')
        expected_args = {
            'blocksize': 10485760,
            'koji_upload_dir': 'upload',
            'platform': 'x86_64',
            'report_multiple_digests': True
        }
        assert args == expected_args

    def test_koji_import(self, osbs):  # noqa:F811
        additional_params = {
            'base_image': 'fedora:latest',
            'koji_upload_dir': 'upload',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'postbuild_plugins', PLUGIN_KOJI_UPLOAD_PLUGIN_KEY)

        get_plugin(plugins, 'exit_plugins', PLUGIN_KOJI_IMPORT_PLUGIN_KEY)
        with pytest.raises(KeyError):
            plugin_value_get(plugins, 'exit_plugins', PLUGIN_KOJI_IMPORT_PLUGIN_KEY, 'args')

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_fetch_worker_metadata(self, osbs, scratch):
        additional_params = {
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        if scratch:
            additional_params['scratch'] = True
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        get_plugin(plugins, 'postbuild_plugins', PLUGIN_FETCH_WORKER_METADATA_KEY)
        with pytest.raises(KeyError):
            plugin_value_get(plugins, 'postbuild_plugins', PLUGIN_FETCH_WORKER_METADATA_KEY, 'args')

    @pytest.mark.parametrize('triggers', [False, True])  # noqa:F811
    def test_check_and_set_rebuild(self, tmpdir, osbs, triggers):
        imagechange = [
            {
                "type": "ImageChange",
                "imageChange": {
                    "from": {
                        "kind": "ImageStreamTag",
                        "name": "{{BASE_IMAGE_STREAM}}",
                    }
                }
            }
        ]

        if triggers:
            orch_outer_temp = ORCHESTRATOR_INNER_TEMPLATE.format(
                arrangement_version=self.ARRANGEMENT_VERSION
            )
            for basename in [ORCHESTRATOR_OUTER_TEMPLATE, orch_outer_temp]:
                shutil.copy(os.path.join(INPUTS_PATH, basename),
                            os.path.join(str(tmpdir), basename))

            with open(os.path.join(str(tmpdir), ORCHESTRATOR_OUTER_TEMPLATE), 'r+') as orch_json:
                build_json = json.load(orch_json)
                build_json['spec']['triggers'] = imagechange

                orch_json.seek(0)
                json.dump(build_json, orch_json)
                orch_json.truncate()

            flexmock(osbs.os_conf, get_build_json_store=lambda: str(tmpdir))
            (flexmock(BuildRequestV2)
                .should_receive('adjust_for_repo_info')
                .and_return(True))

        additional_params = {
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        args = plugin_value_get(plugins, 'prebuild_plugins', 'check_and_set_rebuild', 'args')

        match_args = {
            "label_key": "is_autorebuild",
            "label_value": "true",
        }
        assert match_args == args

    @pytest.mark.parametrize(('params', 'build_type', 'has_plat_tag',  # noqa:F811
                              'has_primary_tag'), (
        ({}, 'orchestrator', False, True),
        ({'scratch': True}, 'orchestrator', False, False),
        ({'platform': 'x86_64'}, 'worker', True, False),
        ({'platform': 'x86_64', 'scratch': True}, 'worker', True, False),
    ))
    def test_tag_from_config(self, osbs, params, build_type, has_plat_tag, has_primary_tag):
        additional_params = {
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        additional_params.update(params)
        _, plugins = self.get_build_request(build_type, osbs, additional_params)

        args = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config', 'args')

        assert set(args.keys()) == set(['tag_suffixes'])
        assert set(args['tag_suffixes'].keys()) == set(['unique', 'primary', 'floating'])

        unique_tags = args['tag_suffixes']['unique']
        assert len(unique_tags) == 1
        unique_tag_suffix = ''
        if has_plat_tag:
            unique_tag_suffix = '-' + additional_params.get('platform')
        assert unique_tags[0].endswith(unique_tag_suffix)

        primary_tags = args['tag_suffixes']['primary']
        if has_primary_tag:
            assert set(primary_tags) == set(['{version}-{release}'])
            floating_tags = args['tag_suffixes']['floating']
            assert set(floating_tags) == set(['latest', '{version}'])

    def test_group_manifests(self, osbs):  # noqa:F811
        additional_params = {
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)
        with pytest.raises(KeyError):
            plugin_value_get(plugins, 'postbuild_plugins', PLUGIN_GROUP_MANIFESTS_KEY, 'args')

    @pytest.mark.parametrize('build_type', ['orchestrator', 'worker'])  # noqa:F811
    def test_inject_parent_image(self, osbs, build_type):
        additional_params = {
            'base_image': 'foo',
            'koji_parent_build': 'fedora-26-9',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_build_request(build_type, osbs, additional_params)

        args = plugin_value_get(plugins, 'prebuild_plugins', 'inject_parent_image', 'args')
        expected_args = {
            'koji_parent_build': 'fedora-26-9',
        }
        assert args == expected_args

    @pytest.mark.parametrize('worker', [False, True])  # noqa:F811
    @pytest.mark.parametrize('scratch', [False, True])
    def test_flatpak(self, osbs, worker, scratch):
        additional_params = {
            'flatpak': True,
            'reactor_config_override': {'flatpak': {'base_image': 'koji-target'},
                                        'source_registry': {'url': 'source_registry'}},
            'target': 'koji-target',
        }
        if scratch:
            additional_params['scratch'] = True
        if worker:
            additional_params['compose_ids'] = [42]
            _, plugins = self.get_worker_build_request(osbs, additional_params)
        else:
            _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        if worker:
            plugin = get_plugin(plugins, "prebuild_plugins", "koji")
            assert plugin

            args = plugin['args']
            assert args['target'] == "koji-target"

            plugin = get_plugin(plugins, "prebuild_plugins", "flatpak_update_dockerfile")
            assert plugin

            args = plugin['args']
            assert args['compose_ids'] == [42]
        else:
            with pytest.raises(KeyError):
                plugin_value_get(plugins, 'prebuild_plugins', 'flatpak_update_dockerfile', 'args')

            args = plugin_value_get(plugins, 'buildstep_plugins', PLUGIN_BUILD_ORCHESTRATE_KEY,
                                    'args')
            build_kwargs = args['build_kwargs']
            assert build_kwargs['flatpak'] is True

        with pytest.raises(KeyError):
            plugin_value_get(plugins, 'prebuild_plugins', 'flatpak_create_dockerfile', 'args')
        with pytest.raises(KeyError):
            plugin_value_get(plugins, 'prebuild_plugins', 'add_flatpak_labels', 'args')

    def test_render_tag_from_container_yaml(self, osbs):  # noqa:F811
        expected_primary = set(['{version}-{release}'])
        tags = set(['spam', 'bacon', 'eggs'])

        additional_params = {
            'platforms': ['x86_64', 'ppc64le'],
            'base_image': 'fedora:latest_is_the_best',
            'additional_tags': tags,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_build_request('orchestrator', osbs, additional_params)

        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary
        assert len(tag_suffixes['floating']) == len(tags)
        assert set(tag_suffixes['floating']) == tags

    def test_render_tag_from_container_yaml_contains_bad_tag(self, osbs):  # noqa:F811
        expected_floating = set(['bacon', 'eggs'])
        expected_primary = set(['{version}-{release}'])
        tags = set(['!!not a tag spam', 'bacon', 'eggs'])
        additional_params = {
            'platforms': ['x86_64', 'ppc64le'],
            'additional_tags': tags,
            'base_image': 'fedora:latest',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}},
        }
        _, plugins = self.get_orchestrator_build_request(osbs, additional_params)

        tag_suffixes = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config',
                                        'args', 'tag_suffixes')
        assert len(tag_suffixes['primary']) == len(expected_primary)
        assert set(tag_suffixes['primary']) == expected_primary
        assert len(tag_suffixes['floating']) == len(expected_floating)
        assert set(tag_suffixes['floating']) == expected_floating


class TestArrangementSourceV6(ArrangementBase):
    """
    Really, this is a subclass of TestArrangementV6 but for source container
    builds. Actually *making* this a subclass of TestArrangementV6 seems like a
    really bad idea, however.
    """

    ARRANGEMENT_VERSION = 6

    DEFAULT_PLUGINS = {
        # This looks just like the real source containers JSON. Please add
        #     useful tests if any come to mind.
        ORCHESTRATOR_SOURCES_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'reactor_config',
                'fetch_sources',
                'bump_release',
            ],

            'buildstep_plugins': [
                'source_container',
            ],

            'postbuild_plugins': [
                'compress',
                'tag_and_push',
            ],

            'prepublish_plugins': [
            ],

            'exit_plugins': [
                PLUGIN_VERIFY_MEDIA_KEY,
                PLUGIN_KOJI_IMPORT_SOURCE_CONTAINER_PLUGIN_KEY,
                'koji_tag_build',
                'store_metadata_in_osv3',
            ],
        }
    }

    def get_plugins_from_buildrequest(self, build_request, template):
        conf_args = {
            "build_from": "image:buildroot:latest",
            'orchestrator_max_run_hours': 5,
            'reactor_config_map': 'reactor-config-map-scratch',
            'scratch': True,
            'worker_max_run_hours': 3,
        }
        param_kwargs = {
            'build_json_dir': INPUTS_PATH,
            'build_conf': Configuration(**conf_args),
            'user': TEST_USER,
            'component': TEST_COMPONENT,
            "koji_target": "tothepoint",
            "platform": "x86_64",
            "signing_intent": "test-signing-intent",
            'sources_for_koji_build_nvr': TEST_KOJI_BUILD_NVR,
            'kind': USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS,
        }
        user_params = SourceContainerUserParams.make_params(**param_kwargs)
        build_request.set_params(user_params)
        return SourceContainerPluginsConfiguration(build_request.user_params).pt.template

    def test_running_order(self, osbs):
        """
        Verify the plugin running order.
        """
        orch_inner = self.DEFAULT_PLUGINS[ORCHESTRATOR_SOURCES_INNER_TEMPLATE]
        build_request = osbs.get_source_container_build_request(
                            arrangement_version=self.ARRANGEMENT_VERSION
                        )
        plugins = self.get_plugins_from_buildrequest(build_request, orch_inner)
        phases = ('prebuild_plugins',
                  'buildstep_plugins',
                  'prepublish_plugins',
                  'postbuild_plugins',
                  'exit_plugins')
        actual = {}
        for phase in phases:
            actual[phase] = [plugin['name']
                             for plugin in plugins.get(phase, {})]

        assert actual == orch_inner
