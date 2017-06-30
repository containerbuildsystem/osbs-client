"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals
from osbs.api import OSBS
from osbs import utils
from tests.constants import (TEST_GIT_URI,
                             TEST_GIT_REF,
                             TEST_GIT_BRANCH,
                             TEST_COMPONENT,
                             TEST_VERSION,
                             TEST_FILESYSTEM_KOJI_TASK_ID)
from tests.fake_api import openshift, osbs  # noqa:F401
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

    def get_orchestrator_build_request(self, osbs,  # noqa:F811
                                       additional_params=None):
        self.mock_env(base_image=additional_params.get('base_image'))
        params = self.COMMON_PARAMS.copy()
        params.update(self.ORCHESTRATOR_ADD_PARAMS)
        params.update(additional_params or {})
        params['arrangement_version'] = self.ARRANGEMENT_VERSION
        return params, osbs.create_orchestrator_build(**params).json

    def get_worker_build_request(self, osbs,  # noqa:F811
                                 additional_params=None):
        self.mock_env(base_image=additional_params.get('base_image'))
        params = self.COMMON_PARAMS.copy()
        params.update(self.WORKER_ADD_PARAMS)
        params.update(additional_params or {})
        params['arrangement_version'] = self.ARRANGEMENT_VERSION
        return params, osbs.create_worker_build(**params).json

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

    @pytest.mark.parametrize('base_image', [  # noqa:F811
        'koji/image-build',
        'foo',
    ])
    @pytest.mark.parametrize('scratch', [False, True])
    def test_add_filesystem_in_orchestrator(self, osbs, scratch, base_image):
        """
        Orchestrator builds should not run add_filesystem
        """
        additional_params = {
            'base_image': base_image,
            'yum_repourls': ['https://example.com/my.repo'],
        }
        if scratch:
            additional_params['scratch'] = True

        (_, build_json) = self.get_orchestrator_build_request(osbs,
                                                              additional_params)

        self.assert_plugin_not_present(build_json,
                                       'prebuild_plugins',
                                       'add_filesystem')

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
                'koji_proxyuser',
                'repos',
            ])
            assert set(args.keys()) <= allowed_args
            assert 'koji_hub' in args
            assert args['repos'] == params['yum_repourls']

    # ...


class TestArrangementV2(TestArrangementV1):
    """
    Differences from arrangement version 1:
    - add_filesystem runs with different parameters
    - add_filesystem also runs in orchestrator build
    """

    ARRANGEMENT_VERSION = 2

    WORKER_ADD_PARAMS = {
        'platform': 'x86_64',
        'release': 1,
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
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
                'koji_proxyuser',
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
                'koji_proxyuser',
                'repos',
                'from_task_id',
            ])
            assert set(args.keys()) <= allowed_args
            assert 'koji_hub' in args
            assert args['repos'] == params['yum_repourls']
            assert args['from_task_id'] == params['filesystem_koji_task_id']
