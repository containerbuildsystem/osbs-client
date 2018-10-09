"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import copy
import glob
import json
import os
import fnmatch
import shutil
import yaml
from copy import deepcopy

from osbs.build.build_requestv2 import BuildRequestV2
from osbs.constants import (DEFAULT_OUTER_TEMPLATE, BUILD_TYPE_WORKER,
                            BUILD_TYPE_ORCHESTRATOR, SECRETS_PATH)
from osbs.exceptions import OsbsValidationException
from osbs.repo_utils import RepoInfo, RepoConfiguration
from osbs.api import OSBS

from flexmock import flexmock
import pytest

from tests.constants import (INPUTS_PATH, TEST_BUILD_CONFIG, TEST_BUILD_JSON,
                             TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_GIT_URI_HUMAN_NAME,
                             TEST_FILESYSTEM_KOJI_TASK_ID, TEST_SCRATCH_BUILD_NAME,
                             TEST_ISOLATED_BUILD_NAME)

USE_DEFAULT_TRIGGERS = object()


class NoSuchPluginException(Exception):
    pass


def MockOSBSApi(config_map_data=None):
    class MockConfigMap(object):
        def __init__(self, data):
            self.data = data or {}

        def get_data_by_key(self, key=None):
            return self.data

    mock_osbs = flexmock(OSBS)
    flexmock(mock_osbs).should_receive('get_config_map').and_return(MockConfigMap(config_map_data))
    return mock_osbs


def get_sample_prod_params(osbs_api='blank'):
    if osbs_api == 'blank':
        osbs_api = MockOSBSApi()
    return {
        'git_uri': TEST_GIT_URI,
        'git_ref': TEST_GIT_REF,
        'git_branch': TEST_GIT_BRANCH,
        'user': 'john-foo',
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'koji_target': 'koji-target',
        'platforms': ['x86_64'],
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
        'build_from': 'image:buildroot:latest',
        'build_type': BUILD_TYPE_WORKER,
        'osbs_api': osbs_api,
    }


class TestBuildRequestV2(object):
    def test_inner_template(self):
        br = BuildRequestV2('something')
        with pytest.raises(RuntimeError):
            br.inner_template

    def test_customize_conf(self):
        br = BuildRequestV2('something')
        with pytest.raises(RuntimeError):
            br.customize_conf

    def test_dock_json(self):
        br = BuildRequestV2('something')
        with pytest.raises(RuntimeError):
            br.dj

    def test_build_request_has_ist_trigger(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        br = BuildRequestV2('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.has_ist_trigger() is True

    def test_build_request_isnt_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        build_json['spec']['triggers'] = []
        br = BuildRequestV2('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.has_ist_trigger() is False

    def test_set_label(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        br = BuildRequestV2('something')
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

    def test_render_no_api(self):
        build_request = BuildRequestV2('something')
        kwargs = get_sample_prod_params(osbs_api=None)
        build_request.set_params(**kwargs)
        with pytest.raises(OsbsValidationException):
            build_request.render()

    @pytest.mark.parametrize(('extra_kwargs', 'valid'), (  # noqa:F811
        ({'scratch': True}, True),
        ({'is_auto': True}, True),
        ({'isolated': True, 'release': '1.0'}, True),
        ({'scratch': True, 'isolated': True, 'release': '1.0'}, False),
        ({'scratch': True, 'is_auto': True}, False),
        ({'is_auto': True, 'isolated': True, 'release': '1.0'}, False),
    ))
    def test_mutually_exclusive_build_variation(self, extra_kwargs, valid):  # noqa:F811
        kwargs = get_sample_prod_params()
        kwargs.update(extra_kwargs)
        build_request = BuildRequestV2(INPUTS_PATH)

        if valid:
            build_request.set_params(**kwargs)
            build_request.render()
        else:
            with pytest.raises(OsbsValidationException) as exc_info:
                build_request.set_params(**kwargs)
            assert 'mutually exclusive' in str(exc_info.value)

    def test_render_simple_request(self):
        build_request = BuildRequestV2(INPUTS_PATH)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'build_image': 'fancy_buildroot:latestest',
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'build_type': BUILD_TYPE_WORKER,
            'osbs_api': MockOSBSApi(),
            'reactor_config_map': 'reactor-config-map',
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] is not None
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        expected_output = "john-foo/component:none-"
        assert build_json["spec"]["output"]["to"]["name"].startswith(expected_output)

        rendered_build_image = build_json["spec"]["strategy"]["customStrategy"]["from"]["name"]
        assert rendered_build_image == 'fancy_buildroot:latestest'

        json_env = build_json['spec']['strategy']['customStrategy']['env']
        envs = {}
        for env in json_env:
            envs[env['name']] = (env.get('valueFrom', None), env.get('value', None))

        configmapkeyref = {
            'name': 'reactor-config-map',
            'key': 'config.yaml'
        }
        assert 'REACTOR_CONFIG' in envs
        assert 'configMapKeyRef' in envs['REACTOR_CONFIG'][0]
        assert envs['REACTOR_CONFIG'][0]['configMapKeyRef'] == configmapkeyref

        assert 'USER_PARAMS' in envs
        assert 'ATOMIC_REACTOR_PLUGINS' not in envs

    @pytest.mark.parametrize('proxy', [  # noqa:F811
        None,
        'http://proxy.example.com',
    ])
    @pytest.mark.parametrize(('build_image', 'build_imagestream', 'valid'), (
        (None, None, False),
        ('ultimate-buildroot:v1.0', None, True),
        (None, 'buildroot-stream:v1.0', True),
        ('ultimate-buildroot:v1.0', 'buildroot-stream:v1.0', False)
    ))
    def test_render_prod_request_with_repo(self, build_image, build_imagestream,
                                           proxy, valid):
        build_request = BuildRequestV2(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        koji_task_id = 4756
        assert isinstance(build_request, BuildRequestV2)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'koji_target': "koji-target",
            'koji_task_id': koji_task_id,
            'sources_command': "make",
            'yum_repourls': ["http://example.com/my.repo"],
            'build_image': build_image,
            'build_imagestream': build_imagestream,
            'build_type': BUILD_TYPE_WORKER,
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
            "john-foo/component:"
        )

        rendered_build_image = build_json["spec"]["strategy"]["customStrategy"]["from"]["name"]
        if not build_imagestream:
            assert rendered_build_image == build_image
        else:
            assert rendered_build_image == build_imagestream
            assert build_json["spec"]["strategy"]["customStrategy"]["from"]["kind"] == \
                "ImageStreamTag"

    def test_render_prod_request(self):
        build_request = BuildRequestV2(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert fnmatch.fnmatch(build_json["metadata"]["name"], TEST_BUILD_CONFIG)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "john-foo/component:"
        )
        assert build_json["metadata"]["labels"]["git-repo-name"] == TEST_GIT_URI_HUMAN_NAME
        assert build_json["metadata"]["labels"]["git-branch"] == TEST_GIT_BRANCH

    def test_render_prod_without_koji_request(self):
        build_request = BuildRequestV2(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        assert isinstance(build_request, BuildRequestV2)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'sources_command': "make",
            'build_from': 'image:buildroot:latest',
            'build_type': BUILD_TYPE_WORKER,
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert fnmatch.fnmatch(build_json["metadata"]["name"], TEST_BUILD_CONFIG)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "john-foo/component:none-"
        )

    @pytest.mark.parametrize('platform', [None, 'x86_64'])
    @pytest.mark.parametrize('scratch', [False, True])
    def test_render_prod_request_v1_v2(self, platform, scratch):
        build_request = BuildRequestV2(INPUTS_PATH)
        name_label = "fedora/resultingimage"
        kwargs = {
            'build_from': 'image:buildroot:latest',
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'koji_target': "koji-target",
            'scratch': scratch,
            'platform': platform,
            'build_type': BUILD_TYPE_WORKER,
            'osbs_api': MockOSBSApi(),
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        expected_name = TEST_SCRATCH_BUILD_NAME if scratch else TEST_BUILD_CONFIG
        assert fnmatch.fnmatch(build_json["metadata"]["name"], expected_name)
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "john-foo/component:"
        )

    @pytest.mark.parametrize(('extra_kwargs', 'expected_name'), (
        ({'isolated': True, 'release': '1.1'}, TEST_ISOLATED_BUILD_NAME),
        ({'scratch': True}, TEST_SCRATCH_BUILD_NAME),
        ({}, TEST_BUILD_CONFIG),
    ))
    def test_render_build_name(self, tmpdir, extra_kwargs, expected_name):
        build_request = BuildRequestV2(INPUTS_PATH)

        kwargs = get_sample_prod_params()
        kwargs.update(extra_kwargs)
        build_request.set_params(**kwargs)

        build_json = build_request.render()
        assert fnmatch.fnmatch(build_json['metadata']['name'], expected_name)

    def test_render_with_yum_repourls(self):
        kwargs = get_sample_prod_params()
        build_request = BuildRequestV2(INPUTS_PATH)

        # Test validation for yum_repourls parameter
        kwargs['yum_repourls'] = 'should be a list'
        with pytest.raises(OsbsValidationException):
            build_request.set_params(**kwargs)

    @pytest.mark.parametrize('triggers', [  # noqa:F811
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
        build_request = BuildRequestV2(str(tmpdir))
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

    @pytest.mark.parametrize('use_auth', (True, False, None))
    @pytest.mark.parametrize(('scratch', 'isolated'), (
        (True, False),
        (False, True),
        (False, False),
    ))
    def test_render_prod_request_with_trigger(self, tmpdir, use_auth, scratch, isolated):
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequestV2(str(tmpdir))
        kwargs = get_sample_prod_params()
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

    @pytest.mark.parametrize('use_auth', (True, False, None))
    @pytest.mark.parametrize('koji_parent_build', ('fedora-26-9', None))
    def test_render_custom_base_image_with_trigger(self, tmpdir,  use_auth, koji_parent_build):
        # name_label = "fedora/resultingimage"
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequestV2(str(tmpdir))

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = 'koji/image-build'
        if use_auth is not None:
            kwargs['use_auth'] = use_auth
        if koji_parent_build:
            kwargs['koji_parent_build'] = koji_parent_build

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_request.is_custom_base_image() is True

        # Verify the triggers are now disabled
        assert "triggers" not in build_json["spec"]

    @pytest.mark.parametrize(('extra_kwargs', 'expected_error'), (
        ({'isolated': True}, 'release parameter is required'),
        ({'isolated': True, 'release': '1'}, 'must be in the format'),
        ({'isolated': True, 'release': '1.1'}, None),
    ))
    def test_adjust_for_isolated(self, tmpdir, extra_kwargs, expected_error):
        self.create_image_change_trigger_json(str(tmpdir))
        build_request = BuildRequestV2(str(tmpdir))

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
    def test_render_prod_request_with_repo_info(self, tmpdir,
                                                autorebuild_enabled, release_label,
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
        build_request = BuildRequestV2(str(tmpdir))
        build_request.set_params(**build_request_kwargs)
        build_request.set_repo_info(repo_info)
        if isinstance(expected, type):
            with pytest.raises(expected):
                build_json = build_request.render()
            return

        build_json = build_request.render()

        if expected:
            assert build_json["spec"]["triggers"][0]["imageChange"]["from"]["name"] == base_image

        else:
            assert 'triggers' not in build_json['spec']

    @pytest.mark.parametrize(('base_image', 'is_custom'), [
        ('fedora', False),
        ('fedora:latest', False),
        ('koji/image-build', True),
        ('koji/image-build:spam.conf', True),
    ])
    def test_prod_is_custom_base_image(self, tmpdir, base_image, is_custom):
        build_request = BuildRequestV2(INPUTS_PATH)
        # Safe to call prior to build image being set
        assert build_request.is_custom_base_image() is False

        kwargs = get_sample_prod_params()
        kwargs['base_image'] = base_image
        build_request.set_params(**kwargs)
        build_request.render()

        assert build_request.is_custom_base_image() == is_custom

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

        br = BuildRequestV2(INPUTS_PATH)
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
            assert build_json['spec']['nodeSelector'] == expected
        else:
            assert 'nodeSelector' not in build_json['spec']

    @pytest.mark.parametrize('build_type', [
        BUILD_TYPE_WORKER,
        BUILD_TYPE_ORCHESTRATOR,
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        None,
        {},
        {'version': 1},
    ])
    @pytest.mark.parametrize('reactor_config_map', [
        None,
        'reactor-config-map',
    ])
    def test_set_config_map(self, build_type, reactor_config_map, reactor_config_override):
        build_request = BuildRequestV2(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        kwargs['reactor_config_map'] = reactor_config_map
        kwargs['reactor_config_override'] = reactor_config_override
        kwargs['build_type'] = build_type

        build_request.set_params(**kwargs)
        build_json = build_request.render()

        json_env = build_json['spec']['strategy']['customStrategy']['env']
        envs = {}
        for env in json_env:
            envs[env['name']] = (env.get('valueFrom', None), env.get('value', None))

        if reactor_config_override:
            reactor_config_value = yaml.safe_dump(reactor_config_override)
            assert 'REACTOR_CONFIG' in envs
            assert envs['REACTOR_CONFIG'][1] == reactor_config_value

        elif reactor_config_map:
            configmapkeyref = {
                'name': reactor_config_map,
                'key': 'config.yaml'
            }
            assert 'REACTOR_CONFIG' in envs
            assert 'configMapKeyRef' in envs['REACTOR_CONFIG'][0]
            assert envs['REACTOR_CONFIG'][0]['configMapKeyRef'] == configmapkeyref

        else:
            assert 'REACTOR_CONFIG' not in envs

    @pytest.mark.parametrize('build_type', [
        BUILD_TYPE_WORKER,
        BUILD_TYPE_ORCHESTRATOR,
    ])
    @pytest.mark.parametrize('existing', [
        [],
        ['secret4', 'secret5'],
    ])
    @pytest.mark.parametrize('reactor_config_map', [
        None,
        {},
        {'required_secrets': ['secret4', 'secret5']},
        {'required_secrets': ['secret4', 'secret5', 'reactor_secret']},
        {'required_secrets': ['secret4', 'secret5'],
         'worker_token_secrets': []},
        {'required_secrets': ['secret4', 'secret5', 'reactor_secret'],
         'worker_token_secrets': []},
        {'required_secrets': ['secret4', 'secret5'],
         'worker_token_secrets': ['secret7', 'secret8']},
        {'required_secrets': ['secret4', 'secret5'],
         'worker_token_secrets': ['secret4', 'secret5', 'secret9']},
        {'required_secrets': ['secret4', 'secret5', 'reactor_secret'],
         'worker_token_secrets': ['secret7', 'secret8']},
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        None,
        {},
        {'required_secrets': ['secret1', 'secret2']},
        {'required_secrets': ['secret1', 'secret2', 'reactor_secret']},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': []},
        {'required_secrets': ['secret1', 'secret2', 'reactor_secret'],
         'worker_token_secrets': []},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': []},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': ['secret10', 'secret11']},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': ['secret1', 'secret2', 'secret11']},
        {'required_secrets': ['secret1', 'secret2', 'reactor_secret'],
         'worker_token_secrets': ['secret10', 'secret11']},
    ])
    def test_set_required_secrets(self, build_type, existing, reactor_config_map,
                                  reactor_config_override):
        build_request = BuildRequestV2(INPUTS_PATH)
        reactor_config_name = 'REACTOR_CONFIG'
        all_secrets = deepcopy(reactor_config_map)

        mock_api = MockOSBSApi(all_secrets)
        kwargs = get_sample_prod_params(osbs_api=mock_api)
        kwargs['reactor_config_map'] = reactor_config_name
        kwargs['reactor_config_override'] = reactor_config_override
        kwargs['build_type'] = build_type

        build_request.set_params(**kwargs)
        expect_secrets = {}
        secrets = build_request.template['spec']['strategy']['customStrategy'].\
            setdefault('secrets', [])

        for secret in existing:
            secret_path = os.path.join(SECRETS_PATH, secret)
            secrets.append({
                'secretSource': {
                    'name': secret,
                },
                'mountPath': secret_path,
            })
            expect_secrets[secret] = secret_path

        build_json = build_request.render()

        json_custom = build_json['spec']['strategy']['customStrategy']

        if not reactor_config_map and not reactor_config_override and not existing:
            assert not json_custom['secrets']
            return

        if reactor_config_override:
            for secret in reactor_config_override['required_secrets']:
                expect_secrets[secret] = os.path.join(SECRETS_PATH, secret)
            if build_type == BUILD_TYPE_ORCHESTRATOR \
                    and 'worker_token_secrets' in reactor_config_override:
                for secret in reactor_config_override['worker_token_secrets']:
                    expect_secrets[secret] = os.path.join(SECRETS_PATH, secret)

        elif reactor_config_map:
            for secret in reactor_config_map['required_secrets']:
                expect_secrets[secret] = os.path.join(SECRETS_PATH, secret)
            if build_type == BUILD_TYPE_ORCHESTRATOR \
                    and 'worker_token_secrets' in reactor_config_map:
                for secret in reactor_config_map['worker_token_secrets']:
                    expect_secrets[secret] = os.path.join(SECRETS_PATH, secret)

        got_secrets = {}
        for secret in json_custom['secrets']:
            got_secrets[secret['secretSource']['name']] = secret['mountPath']

        assert expect_secrets == got_secrets

    @pytest.mark.parametrize('build_type', [
        BUILD_TYPE_WORKER,
        BUILD_TYPE_ORCHESTRATOR,
    ])
    @pytest.mark.parametrize('reactor_config_map', [
        None,
        {},
        {'registries_organization': 'organization_in_cm',
         'source_registry': {'url': 'registry_in_cm'}}
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        None,
        {},
        {'registries_organization': 'organization_in_override',
         'source_registry': {'url': 'registry_in_override'}}
    ])
    def test_set_data_from_reactor_config(self, build_type, reactor_config_map,
                                          reactor_config_override):
        build_request = BuildRequestV2(INPUTS_PATH)
        reactor_config_name = 'REACTOR_CONFIG'
        all_secrets = deepcopy(reactor_config_map)

        mock_api = MockOSBSApi(all_secrets)
        kwargs = get_sample_prod_params(osbs_api=mock_api)
        kwargs['reactor_config_map'] = reactor_config_name
        kwargs['reactor_config_override'] = reactor_config_override
        kwargs['build_type'] = build_type

        build_request.set_params(**kwargs)

        build_request.render()

        expected_registry = None
        expected_organization = None
        if reactor_config_override and 'source_registry' in reactor_config_override:
            expected_registry = reactor_config_override['source_registry']
        elif reactor_config_map and 'source_registry' in reactor_config_map:
            expected_registry = reactor_config_map['source_registry']
        if reactor_config_override and 'registries_organization' in reactor_config_override:
            expected_organization = reactor_config_override['registries_organization']
        elif reactor_config_map and 'registries_organization' in reactor_config_map:
            expected_organization = reactor_config_map['registries_organization']

        assert expected_registry == build_request.source_registry
        assert expected_organization == build_request.organization
