"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals
from osbs.api import OSBS
from osbs.constants import (DEFAULT_ARRANGEMENT_VERSION,
                            ORCHESTRATOR_INNER_TEMPLATE,
                            WORKER_INNER_TEMPLATE)
from osbs import utils
from tests.constants import (TEST_GIT_URI,
                             TEST_GIT_REF,
                             TEST_GIT_BRANCH,
                             TEST_COMPONENT,
                             TEST_VERSION,
                             TEST_FILESYSTEM_KOJI_TASK_ID)
from tests.fake_api import openshift, osbs, osbs_with_pulp  # noqa:F401
from tests.test_api import request_as_response
from tests.build_.test_build_request import (get_plugins_from_build_json,
                                             get_plugin,
                                             plugin_value_get,
                                             NoSuchPluginException)
from flexmock import flexmock
import pytest


class ArrangementBase(object):
    COMMON_PARAMS = {}
    ORCHESTRATOR_ADD_PARAMS = {}
    WORKER_ADD_PARAMS = {}

    def mock_env(self, base_image='fedora23/python'):
        class MockParser(object):
            labels = {
                'name': 'fedora23/something',
                'com.redhat.component': TEST_COMPONENT,
                'version': TEST_VERSION,
            }
            baseimage = base_image

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

        # Trick create_orchestrator_build into return the *request* JSON
        flexmock(OSBS, _create_build_config_and_build=request_as_response)
        flexmock(OSBS, _create_scratch_build=request_as_response)

    @pytest.mark.parametrize('template', [  # noqa:F811
        ORCHESTRATOR_INNER_TEMPLATE,
        WORKER_INNER_TEMPLATE,
    ])
    def test_orchestrator_running_order(self, osbs, template):
        """
        Verify the plugin running order.

        This is to catch tests missing from these test classes when a
        plugin is added.
        """

        inner_template = template.format(
            arrangement_version=self.ARRANGEMENT_VERSION,
        )
        build_request = osbs.get_build_request(inner_template=inner_template)
        inner = build_request.inner_template
        phases = ('prebuild_plugins',
                  'buildstep_plugins',
                  'postbuild_plugins',
                  'prepublish_plugins',
                  'exit_plugins')
        actual = {}
        for phase in phases:
            actual[phase] = [plugin['name']
                             for plugin in inner.get(phase, {})]

        assert actual == self.DEFAULT_PLUGINS[template]

    def get_build_request(self, build_type, osbs,  # noqa:F811
                          additional_params=None):
        self.mock_env(base_image=additional_params.get('base_image'))
        params = self.COMMON_PARAMS.copy()
        assert build_type in ('orchestrator', 'worker')
        if build_type == 'orchestrator':
            params.update(self.ORCHESTRATOR_ADD_PARAMS)
            fn = osbs.create_orchestrator_build
        elif build_type == 'worker':
            params.update(self.WORKER_ADD_PARAMS)
            fn = osbs.create_worker_build

        params.update(additional_params or {})
        params['arrangement_version'] = self.ARRANGEMENT_VERSION
        return params, fn(**params).json

    def get_orchestrator_build_request(self, osbs,  # noqa:F811
                                       additional_params=None):
        return self.get_build_request('orchestrator', osbs, additional_params)

    def get_worker_build_request(self, osbs,  # noqa:F811
                                 additional_params=None):
        return self.get_build_request('worker', osbs, additional_params)

    def assert_plugin_not_present(self, build_json, phase, name):
        plugins = get_plugins_from_build_json(build_json)
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, phase, name)


class TestArrangementV1(ArrangementBase):
    """
    This class tests support for the oldest supported arrangement
    version, 1.

    NOTE! When removing this test class, *make sure* that any methods
    it provides for the test class for the next oldest supported
    arrangement version are copied across to that test class.
    """

    ARRANGEMENT_VERSION = 1

    COMMON_PARAMS = {
        'git_uri': TEST_GIT_URI,
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_BRANCH,
        'user': 'john-foo',
        'component': TEST_COMPONENT,
    }

    ORCHESTRATOR_ADD_PARAMS = {
        'platforms': ['x86_64'],
    }

    WORKER_ADD_PARAMS = {
        'platform': 'x86_64',
        'release': 1,
    }

    DEFAULT_PLUGINS = {
        # Changing this? Add test methods
        ORCHESTRATOR_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'pull_base_image',
                'bump_release',
                'add_labels_in_dockerfile',
                'reactor_config',
            ],

            'buildstep_plugins': [
                'orchestrate_build',
            ],

            'postbuild_plugins': [
            ],

            'prepublish_plugins': [
            ],

            'exit_plugins': [
                'store_metadata_in_osv3',
                'remove_built_image',
            ],
        },

        # Changing this? Add test methods
        WORKER_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'add_filesystem',
                'pull_base_image',
                'add_labels_in_dockerfile',
                'change_from_in_dockerfile',
                'add_help',
                'add_dockerfile',
                'distgit_fetch_artefacts',
                'fetch_maven_artifacts',
                'koji',
                'add_yum_repo_by_url',
                'inject_yum_repo',
                'distribution_scope',
            ],

            'buildstep_plugins': [
            ],

            'postbuild_plugins': [
                'all_rpm_packages',
                'tag_by_labels',
                'tag_from_config',
                'tag_and_push',
                'pulp_push',
                'pulp_sync',
                'compress',
                'pulp_pull',
            ],

            'prepublish_plugins': [
                'squash',
            ],

            'exit_plugins': [
                'delete_from_registry',  # not tested
                'koji_promote',  # not tested
                'store_metadata_in_osv3',  # not tested
                'koji_tag_build',  # not tested
                'sendmail',  # not tested
                'remove_built_image',  # not tested
            ],
        },
    }

    @pytest.mark.parametrize('build_type', [  # noqa:F811
        'orchestrator',
        'worker',
    ])
    @pytest.mark.parametrize('scratch', [False, True])
    @pytest.mark.parametrize('base_image, expect_plugin', [
        ('koji/image-build', False),
        ('foo', True),
    ])
    def test_pull_base_image(self, osbs, build_type, scratch,
                             base_image, expect_plugin):
        phase = 'prebuild_plugins'
        plugin = 'pull_base_image'
        additional_params = {
            'base_image': base_image,
        }
        if scratch:
            additional_params['scratch'] = True

        (params, build_json) = self.get_build_request(build_type,
                                                      osbs,
                                                      additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if not expect_plugin:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, phase, plugin)
        else:
            args = plugin_value_get(plugins, phase, plugin, 'args')

            allowed_args = set([
                'parent_registry',
                'parent_registry_insecure',
            ])
            assert set(args.keys()) <= allowed_args

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    @pytest.mark.parametrize('base_image', ['koji/image-build', 'foo'])
    def test_delete_from_registry(self, osbs_with_pulp, base_image, scratch):
        phase = 'exit_plugins'
        plugin = 'delete_from_registry'
        additional_params = {
            'base_image': base_image,
        }
        if scratch:
            additional_params['scratch'] = True

        (params, build_json) = self.get_build_request('worker',
                                                      osbs_with_pulp,
                                                      additional_params)
        plugins = get_plugins_from_build_json(build_json)
        args = plugin_value_get(plugins, phase, plugin, 'args')
        allowed_args = set([
            'registries',
        ])
        assert set(args.keys()) <= allowed_args

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    @pytest.mark.parametrize('base_image, expect_plugin', [
        ('koji/image-build', True),
        ('foo', False)
    ])
    def test_add_filesystem_in_worker(self, osbs, base_image, scratch,
                                      expect_plugin):
        additional_params = {
            'base_image': base_image,
            'yum_repourls': ['https://example.com/my.repo'],
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_worker_build_request(osbs,
                                                           additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if not expect_plugin:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'prebuild_plugins', 'add_filesystem')
        else:
            args = plugin_value_get(plugins, 'prebuild_plugins',
                                    'add_filesystem', 'args')

            allowed_args = set([
                'koji_hub',
                'repos',
            ])
            assert set(args.keys()) <= allowed_args
            assert 'koji_hub' in args
            assert args['repos'] == params['yum_repourls']


class TestArrangementV2(TestArrangementV1):
    """
    Differences from arrangement version 1:
    - add_filesystem runs with different parameters
    - add_filesystem also runs in orchestrator build
    - koji_parent runs in orchestrator build
    """

    ARRANGEMENT_VERSION = 2

    WORKER_ADD_PARAMS = {
        'platform': 'x86_64',
        'release': 1,
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
    }

    DEFAULT_PLUGINS = {
        # Changing this? Add test methods
        ORCHESTRATOR_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'add_filesystem',
                'pull_base_image',
                'bump_release',
                'add_labels_in_dockerfile',
                'koji_parent',
                'reactor_config',
            ],

            'buildstep_plugins': [
                'orchestrate_build',
            ],

            'postbuild_plugins': [
            ],

            'prepublish_plugins': [
            ],

            'exit_plugins': [
                'store_metadata_in_osv3',
                'remove_built_image',
            ],
        },

        # Changing this? Add test methods
        WORKER_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'add_filesystem',
                'pull_base_image',
                'add_labels_in_dockerfile',
                'change_from_in_dockerfile',
                'add_help',
                'add_dockerfile',
                'distgit_fetch_artefacts',
                'fetch_maven_artifacts',
                'koji',
                'add_yum_repo_by_url',
                'inject_yum_repo',
                'distribution_scope',
            ],

            'buildstep_plugins': [
            ],

            'postbuild_plugins': [
                'all_rpm_packages',
                'tag_by_labels',
                'tag_from_config',
                'tag_and_push',
                'pulp_push',
                'pulp_sync',
                'compress',
                'pulp_pull',
            ],

            'prepublish_plugins': [
                'squash',
            ],

            'exit_plugins': [
                'delete_from_registry',
                'koji_promote',
                'store_metadata_in_osv3',
                'koji_tag_build',
                'sendmail',
                'remove_built_image',
            ],
        },
    }

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    @pytest.mark.parametrize('base_image, expect_plugin', [
        ('koji/image-build', True),
        ('foo', False)
    ])
    def test_add_filesystem_in_orchestrator(self, osbs, base_image, scratch,
                                            expect_plugin):
        additional_params = {
            'base_image': base_image,
            'yum_repourls': ['https://example.com/my.repo'],
        }
        if scratch:
            additional_params['scratch'] = True

        (params,
         build_json) = self.get_orchestrator_build_request(osbs,
                                                           additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if not expect_plugin:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'prebuild_plugins', 'add_filesystem')
        else:
            args = plugin_value_get(plugins, 'prebuild_plugins',
                                    'add_filesystem', 'args')
            allowed_args = set([
                'koji_hub',
                'repos',
                'architectures',
            ])
            assert set(args.keys()) <= allowed_args
            assert 'koji_hub' in args
            assert args['repos'] == params['yum_repourls']
            assert args['architectures'] == params['platforms']

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    @pytest.mark.parametrize('base_image, expect_plugin', [
        ('koji/image-build', True),
        ('foo', False)
    ])
    def test_add_filesystem_in_worker(self, osbs, base_image, scratch,
                                      expect_plugin):
        additional_params = {
            'base_image': base_image,
            'yum_repourls': ['https://example.com/my.repo'],
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_worker_build_request(osbs,
                                                           additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if not expect_plugin:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'prebuild_plugins', 'add_filesystem')
        else:
            args = plugin_value_get(plugins, 'prebuild_plugins',
                                    'add_filesystem', 'args')
            allowed_args = set([
                'koji_hub',
                'repos',
                'from_task_id',
            ])
            assert set(args.keys()) <= allowed_args
            assert 'koji_hub' in args
            assert args['repos'] == params['yum_repourls']
            assert args['from_task_id'] == params['filesystem_koji_task_id']

    @pytest.mark.parametrize(('scratch', 'base_image', 'expect_plugin'), [  # noqa:F811
        (True, 'koji/image-build', False),
        (True, 'foo', False),
        (False, 'koji/image-build', False),
        (False, 'foo', True),
    ])
    def test_koji_parent_in_orchestrator(self, osbs, base_image, scratch,
                                         expect_plugin):
        additional_params = {
            'base_image': base_image,
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_orchestrator_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if not expect_plugin:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'prebuild_plugins', 'koji_parent')
        else:
            args = plugin_value_get(plugins, 'prebuild_plugins',
                                    'koji_parent', 'args')
            allowed_args = set([
                'koji_hub',
            ])
            assert set(args.keys()) <= allowed_args
            assert 'koji_hub' in args


class TestArrangementV3(TestArrangementV2):
    """
    Differences from arrangement version 2:
    - fetch_worker_metadata, koji_import, koji_tag_build, sendmail, run in the orcestrator build
    - koji_upload runs in the worker build
    - koji_promote does not run
    """

    ARRANGEMENT_VERSION = 3

    WORKER_ADD_PARAMS = {
        'platform': 'x86_64',
        'release': 1,
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
    }

    DEFAULT_PLUGINS = {
        # Changing this? Add test methods
        ORCHESTRATOR_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'add_filesystem',
                'pull_base_image',
                'bump_release',
                'add_labels_in_dockerfile',
                'koji_parent',
                'reactor_config',
            ],

            'buildstep_plugins': [
                'orchestrate_build',
            ],

            'postbuild_plugins': [
                'fetch_worker_metadata',
            ],

            'prepublish_plugins': [
            ],

            'exit_plugins': [
                'delete_from_registry',
                'koji_import',
                'koji_tag_build',
                'store_metadata_in_osv3',
                'sendmail',
                'remove_built_image',
            ],
        },

        # Changing this? Add test methods
        WORKER_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'add_filesystem',
                'pull_base_image',
                'add_labels_in_dockerfile',
                'change_from_in_dockerfile',
                'add_help',
                'add_dockerfile',
                'distgit_fetch_artefacts',
                'fetch_maven_artifacts',
                'koji',
                'add_yum_repo_by_url',
                'inject_yum_repo',
                'distribution_scope',
            ],

            'buildstep_plugins': [
            ],

            'postbuild_plugins': [
                'all_rpm_packages',
                'tag_by_labels',
                'tag_from_config',
                'tag_and_push',
                'pulp_push',
                'pulp_sync',
                'compress',
                'koji_upload',
                'pulp_pull',
            ],

            'prepublish_plugins': [
                'squash',
            ],

            'exit_plugins': [
                'delete_from_registry',
                'store_metadata_in_osv3',
                'remove_built_image',
            ],
        },
    }

    def test_is_default(self):
        """
        Test this is the default arrangement
        """

        # Note! If this test fails it probably means you need to
        # derive a new TestArrangementV[n] class from this class and
        # move the method to the new class.
        assert DEFAULT_ARRANGEMENT_VERSION == self.ARRANGEMENT_VERSION

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_koji_upload(self, osbs, scratch):
        additional_params = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uris': [],
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'build_image': None,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v1'],
            'kojihub': 'http://hub/',
            'koji_upload_dir': 'upload',
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_worker_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'exit_plugins', 'koji_promote')

        if scratch:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'postbuild_plugins', 'koji_upload')
            return

        args = plugin_value_get(plugins, 'postbuild_plugins',
                                         'koji_upload', 'args')

        match_args = {
            'blocksize': 10485760,
            'build_json_dir': 'inputs',
            'koji_keytab': False,
            'koji_principal': False,
            'koji_upload_dir': 'upload',
            'kojihub': 'http://koji.example.com/kojihub',
            'url': '/',
            'use_auth': False,
            'verify_ssl': False
        }
        assert set(args.keys()) == set(match_args.keys())
        assert match_args == args

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_koji_import(self, osbs, scratch):
        additional_params = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uris': [],
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'build_image': None,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v1'],
            'kojihub': 'http://hub/',
            'koji_upload_dir': 'upload',
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_orchestrator_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, 'exit_plugins', 'koji_promote')

        if scratch:
            try:
                get_plugin(plugins, 'exit_plugins', 'koji_import')
            except NoSuchPluginException:
                return

        args = plugin_value_get(plugins, 'exit_plugins',
                                         'koji_import', 'args')

        match_args = {
            'koji_keytab': False,
            'kojihub': 'http://koji.example.com/kojihub',
            'url': '/',
            'use_auth': False,
            'verify_ssl': False
        }
        assert set(args.keys()) == set(match_args.keys())
        assert match_args == args

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_fetch_worker_metadata(self, osbs, scratch):
        additional_params = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uris': [],
            'openshift_uri': "http://openshift/",
            'builder_openshift_url': "http://openshift/",
            'build_image': None,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_api_versions': ['v1'],
            'kojihub': 'http://hub/',
            'koji_upload_dir': 'upload',
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_orchestrator_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if scratch:
            try:
                get_plugin(plugins, 'postbuild_plugins', 'fetch_worker_metadata')
            except NoSuchPluginException:
                return

        args = plugin_value_get(plugins, 'postbuild_plugins',
                                         'fetch_worker_metadata', 'args')

        match_args = {}
        assert set(args.keys()) == set(match_args.keys())
        assert match_args == args
