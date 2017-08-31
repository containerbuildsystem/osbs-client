"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals
import shutil
import os
import json
from osbs.api import OSBS
from osbs.constants import (DEFAULT_ARRANGEMENT_VERSION,
                            ORCHESTRATOR_INNER_TEMPLATE,
                            WORKER_INNER_TEMPLATE,
                            SECRETS_PATH,
                            ORCHESTRATOR_OUTER_TEMPLATE)
from osbs import utils
from osbs.repo_utils import RepoInfo
from osbs.build.build_request import BuildRequest
from tests.constants import (TEST_GIT_URI,
                             TEST_GIT_REF,
                             TEST_GIT_BRANCH,
                             TEST_COMPONENT,
                             TEST_VERSION,
                             TEST_FILESYSTEM_KOJI_TASK_ID,
                             INPUTS_PATH)
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
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(RepoInfo(MockParser())))

        # Trick create_orchestrator_build into return the *request* JSON
        flexmock(OSBS, _create_build_config_and_build=request_as_response)
        flexmock(OSBS, _create_scratch_build=request_as_response)

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
        build_request = osbs.get_build_request(inner_template=inner_template)
        inner = build_request.inner_template
        phases = ('prebuild_plugins',
                  'buildstep_plugins',
                  'prepublish_plugins',
                  'postbuild_plugins',
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

    def get_pulp_sync_registry(self, conf):
        """Return the docker registry used by pulp content sync."""
        for registry_uri in conf.get_registry_uris():
            registry = utils.RegistryURI(registry_uri)
            if registry.version == 'v2':
                return registry.docker_uri


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
        'openshift_uri': 'http://openshift/',
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

            'prepublish_plugins': [
            ],

            'postbuild_plugins': [
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

            'prepublish_plugins': [
                'squash',
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

            'prepublish_plugins': [
            ],

            'postbuild_plugins': [
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

            'prepublish_plugins': [
                'squash',
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
    - fetch_worker_metadata, koji_import, koji_tag_build, sendmail,
      check_and_set_rebuild, run in the orchestrator build
    - koji_upload runs in the worker build
    - koji_promote does not run
    """

    ARRANGEMENT_VERSION = 3

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
                'check_and_set_rebuild',
            ],

            'buildstep_plugins': [
                'orchestrate_build',
            ],

            'prepublish_plugins': [
            ],

            'postbuild_plugins': [
                'fetch_worker_metadata',
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

            'prepublish_plugins': [
                'squash',
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
                'koji_upload',
            ],

            'exit_plugins': [
                'delete_from_registry',
                'store_metadata_in_osv3',
                'remove_built_image',
            ],
        },
    }

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_koji_upload(self, osbs, scratch):
        additional_params = {
            'base_image': 'fedora:latest',
            'koji_upload_dir': 'upload',
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_worker_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

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
            'verify_ssl': False,
            'buildstep_logs': 'x86_64-build.log',
        }
        assert match_args == args

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_koji_import(self, osbs, scratch):
        additional_params = {
            'base_image': 'fedora:latest',
            'koji_upload_dir': 'upload',
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_orchestrator_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if scratch:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'exit_plugins', 'koji_import')
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
        assert match_args == args

    @pytest.mark.parametrize('scratch', [False, True])  # noqa:F811
    def test_fetch_worker_metadata(self, osbs, scratch):
        additional_params = {
            'base_image': 'fedora:latest',
        }
        if scratch:
            additional_params['scratch'] = True
        params, build_json = self.get_orchestrator_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if scratch:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'postbuild_plugins', 'fetch_worker_metadata')
            return

        args = plugin_value_get(plugins, 'postbuild_plugins',
                                         'fetch_worker_metadata', 'args')

        match_args = {}
        assert match_args == args

    @pytest.mark.parametrize('triggers', [False, True])  # noqa:F811
    def test_check_and_set_rebuild(self, tmpdir, osbs, triggers):

        imagechange = [
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
            (flexmock(BuildRequest)
                .should_receive('adjust_for_repo_info')
                .and_return(True))

        additional_params = {
            'base_image': 'fedora:latest',
        }
        params, build_json = self.get_orchestrator_build_request(osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        if not triggers:
            with pytest.raises(NoSuchPluginException):
                get_plugin(plugins, 'prebuild_plugins', 'check_and_set_rebuild')
            return

        args = plugin_value_get(plugins, 'prebuild_plugins',
                                         'check_and_set_rebuild', 'args')

        match_args = {
            "label_key": "is_autorebuild",
            "label_value": "true",
            "url": "/",
            "verify_ssl": False,
            'use_auth': False,
        }
        assert match_args == args


class TestArrangementV4(TestArrangementV3):
    """
    Orchestrator build differences from arrangement version 3:
    - tag_from_config enabled
    - pulp_tag enabled
    - pulp_sync enabled
    - pulp_sync takes an additional "publish":false argument
    - pulp_publish enabled
    - pulp_pull enabled
    - group_manifests enabled

    Worker build differences from arrangement version 3:
    - tag_from_config takes "tag_suffixes" argument
    - tag_by_labels disabled
    - pulp_push takes an additional "publish":false argument
    - pulp_sync disabled
    - pulp_pull disabled
    - delete_from_registry disabled
    """

    ARRANGEMENT_VERSION = 4

    DEFAULT_PLUGINS = {
        # Changing this? Add test methods
        ORCHESTRATOR_INNER_TEMPLATE: {
            'prebuild_plugins': [
                'reactor_config',
                'add_filesystem',
                'inject_parent_image',
                'pull_base_image',
                'bump_release',
                'add_labels_in_dockerfile',
                'koji_parent',
                'check_and_set_rebuild',
            ],

            'buildstep_plugins': [
                'orchestrate_build',
            ],

            'prepublish_plugins': [
            ],

            'postbuild_plugins': [
                'fetch_worker_metadata',
                'tag_from_config',
                'group_manifests',
                'pulp_tag',
                'pulp_sync',
            ],

            'exit_plugins': [
                'pulp_publish',
                'pulp_pull',
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
                'inject_parent_image',
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

            'prepublish_plugins': [
                'squash',
            ],

            'postbuild_plugins': [
                'all_rpm_packages',
                'tag_from_config',
                'tag_and_push',
                'pulp_push',
                'compress',
                'koji_upload',
            ],

            'exit_plugins': [
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
        }
        additional_params.update(params)
        _, build_json = self.get_build_request(build_type, osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'postbuild_plugins', 'tag_from_config', 'args')

        assert set(args.keys()) == set(['tag_suffixes'])
        assert set(args['tag_suffixes'].keys()) == set(['unique', 'primary'])

        unique_tags = args['tag_suffixes']['unique']
        assert len(unique_tags) == 1
        unique_tag_suffix = ''
        if has_plat_tag:
            unique_tag_suffix = '-' + additional_params.get('platform')
        assert unique_tags[0].endswith(unique_tag_suffix)

        primary_tags = args['tag_suffixes']['primary']
        if has_primary_tag:
            assert set(primary_tags) == set(['latest', '{version}', '{version}-{release}'])

    def test_pulp_push(self, openshift):  # noqa:F811
        platform_descriptors = {'x86_64': {'enable_v1': True}}
        osbs_api = osbs_with_pulp(openshift, platform_descriptors=platform_descriptors)
        additional_params = {
            'base_image': 'fedora:latest',
        }
        _, build_json = self.get_worker_build_request(osbs_api, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'postbuild_plugins', 'pulp_push', 'args')

        build_conf = osbs_api.build_conf
        # Use first docker registry and strip off /v2
        pulp_registry_name = build_conf.get_pulp_registry()
        pulp_secret_path = '/'.join([SECRETS_PATH, build_conf.get_pulp_secret()])

        expected_args = {
            'pulp_registry_name': pulp_registry_name,
            'pulp_secret_path': pulp_secret_path,
            'load_exported_image': True,
            'dockpulp_loglevel': 'INFO',
            'publish': False
        }

        assert args == expected_args

    def test_pulp_tag(self, osbs_with_pulp):  # noqa:F811
        additional_params = {
            'base_image': 'fedora:latest',
        }
        _, build_json = self.get_orchestrator_build_request(osbs_with_pulp, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'postbuild_plugins', 'pulp_tag', 'args')
        build_conf = osbs_with_pulp.build_conf
        pulp_registry_name = build_conf.get_pulp_registry()
        pulp_secret_path = '/'.join([SECRETS_PATH, build_conf.get_pulp_secret()])

        expected_args = {
            'pulp_registry_name': pulp_registry_name,
            'pulp_secret_path': pulp_secret_path,
            'dockpulp_loglevel': 'INFO',
        }

        assert args == expected_args

    def test_pulp_sync(self, osbs_with_pulp):  # noqa:F811
        additional_params = {
            'base_image': 'fedora:latest',
        }
        _, build_json = self.get_orchestrator_build_request(osbs_with_pulp, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'postbuild_plugins', 'pulp_sync', 'args')

        build_conf = osbs_with_pulp.build_conf
        docker_registry = self.get_pulp_sync_registry(build_conf)
        pulp_registry_name = build_conf.get_pulp_registry()
        pulp_secret_path = '/'.join([SECRETS_PATH, build_conf.get_pulp_secret()])
        expected_args = {
            'docker_registry': docker_registry,
            'pulp_registry_name': pulp_registry_name,
            'pulp_secret_path': pulp_secret_path,
            'dockpulp_loglevel': 'INFO',
            'publish': False
        }

        assert args == expected_args

    def test_pulp_publish(self, osbs_with_pulp):  # noqa:F811
        additional_params = {
            'base_image': 'fedora:latest',
        }
        _, build_json = self.get_orchestrator_build_request(osbs_with_pulp, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'exit_plugins', 'pulp_publish', 'args')
        build_conf = osbs_with_pulp.build_conf
        pulp_registry_name = build_conf.get_pulp_registry()
        pulp_secret_path = '/'.join([SECRETS_PATH, build_conf.get_pulp_secret()])

        expected_args = {
            'pulp_registry_name': pulp_registry_name,
            'pulp_secret_path': pulp_secret_path,
            'dockpulp_loglevel': 'INFO',
        }

        assert args == expected_args

    def test_pulp_pull(self, osbs_with_pulp):  # noqa:F811
        additional_params = {
            'base_image': 'fedora:latest',
        }
        _, build_json = self.get_orchestrator_build_request(osbs_with_pulp, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'exit_plugins', 'pulp_pull', 'args')
        expected_args = {'insecure': True}
        assert args == expected_args

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

        _, build_json = self.get_orchestrator_build_request(osbs_with_pulp, additional_params)
        plugins = get_plugins_from_build_json(build_json)
        args = plugin_value_get(plugins, phase, plugin, 'args')

        docker_registry = self.get_pulp_sync_registry(osbs_with_pulp.build_conf)
        assert args == {'registries': {docker_registry: {'insecure': True}}}

    @pytest.mark.parametrize('group', (  # noqa:F811
        True,
        False,
    ))
    def test_group_manifests(self, openshift, group):
        platform_descriptors = {'x86_64': {'architecture': 'amd64'}}
        osbs_api = osbs_with_pulp(openshift, platform_descriptors=platform_descriptors,
                                  group_manifests=group)
        additional_params = {
            'base_image': 'fedora:latest',
        }
        _, build_json = self.get_orchestrator_build_request(osbs_api, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'postbuild_plugins', 'group_manifests', 'args')
        docker_registry = self.get_pulp_sync_registry(osbs_api.build_conf)

        expected_args = {
            'goarch': {'x86_64': 'amd64'},
            'group': group,
            'registries': {docker_registry: {'insecure': True, 'version': 'v2'}}
        }
        assert args == expected_args

    @pytest.mark.parametrize('build_type', (  # noqa:F811
        'orchestrator',
        'worker',
    ))
    def test_inject_parent_image(self, osbs, build_type):
        additional_params = {
            'base_image': 'foo',
            'koji_parent_build': 'fedora-26-9',
        }
        _, build_json = self.get_build_request(build_type, osbs, additional_params)
        plugins = get_plugins_from_build_json(build_json)

        args = plugin_value_get(plugins, 'prebuild_plugins', 'inject_parent_image', 'args')
        expected_args = {
            'koji_parent_build': 'fedora-26-9',
            'koji_hub': osbs.build_conf.get_kojihub()
        }
        assert args == expected_args
