"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

import copy
import glob
import json
import os
import fnmatch
import shutil
from textwrap import dedent
import yaml
from copy import deepcopy

from osbs.build.build_requestv2 import (
    BuildRequestV2,
    SourceBuildRequest,
)
from osbs.build.user_params import BuildUserParams, SourceContainerUserParams
from osbs.conf import Configuration
from osbs.constants import (DEFAULT_OUTER_TEMPLATE, WORKER_OUTER_TEMPLATE,
                            ORCHESTRATOR_OUTER_TEMPLATE, BUILD_TYPE_WORKER,
                            BUILD_TYPE_ORCHESTRATOR, SECRETS_PATH,
                            REPO_CONFIG_FILE, REPO_CONTAINER_CONFIG)
from osbs.exceptions import OsbsValidationException, OsbsException
from osbs.utils.labels import Labels
from osbs.repo_utils import ModuleSpec, RepoInfo, RepoConfiguration
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


class MockDFParser(object):
    def __init__(self, labels=None):
        self.labels = labels or {}
        self.baseimage = 'fedora:latest'


def mock_repo_info(git_args=None, labels=None):
    if not git_args:
        git_args = {}
    git_args.setdefault('git_uri', TEST_GIT_URI)
    git_args.setdefault('git_branch', TEST_GIT_BRANCH)
    git_args.setdefault('git_ref', TEST_GIT_REF)

    repo_conf = RepoConfiguration(**git_args)
    return RepoInfo(dockerfile_parser=MockDFParser(labels), configuration=repo_conf)


def get_sample_user_params(build_json_store=INPUTS_PATH, conf_args=None, git_args=None,
                           update_args=None, labels=None, no_source=False):
    if not conf_args:
        conf_args = {'build_from': 'image:buildroot:latest'}
    # scratch handling is tricky
    if update_args:
        conf_args.setdefault('scratch', update_args.get('scratch'))

    repo_info = mock_repo_info(git_args=git_args, labels=labels)

    build_conf = Configuration(conf_file=None, **conf_args)
    kwargs = {
        'build_json_dir': build_json_store,
        'user': 'john-foo',
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'koji_target': 'koji-target',
        'platforms': ['x86_64'],
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
        'build_conf': build_conf,
        'build_type': BUILD_TYPE_WORKER,
        'repo_info': repo_info,
    }
    if not no_source:
        kwargs['reactor_config_override'] = {'source_registry': {'url': 'source_registry'}}
    if update_args:
        kwargs.update(update_args)
    user_params = BuildUserParams.make_params(**kwargs)
    return user_params


def get_autorebuild_git_args(tmpdir, add_timestamp=None):
    with open(os.path.join(str(tmpdir), REPO_CONFIG_FILE), 'w') as f:
        f.write(dedent("""\
            [autorebuild]
            enabled=true"""))
    if add_timestamp:
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent("""\
                compose:
                    modules:
                    - mod_name:mod_stream:mod_version
                autorebuild:
                    add_timestamp_to_release: true
                """))
    git_args = {'dir_path': str(tmpdir)}
    return git_args


class TestBuildRequestV2(object):
    def test_customize_conf(self):
        br = BuildRequestV2('something')
        with pytest.raises(RuntimeError):
            br.customize_conf   # pylint: disable=pointless-statement; is a property

    def test_build_request_has_ist_trigger(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        user_params = get_sample_user_params()
        br = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.has_ist_trigger() is True
        assert br.trigger_imagestreamtag == 'fedora:latest'

    def test_build_request_isnt_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        build_json['spec']['triggers'] = []
        user_params = get_sample_user_params()
        br = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.has_ist_trigger() is False

    def test_set_label(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        user_params = get_sample_user_params()
        br = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
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
        user_params = get_sample_user_params()
        br = BuildRequestV2(osbs_api=None, user_params=user_params)
        with pytest.raises(OsbsValidationException):
            br.render()

    @pytest.mark.parametrize(('extra_kwargs', 'valid'), (  # noqa:F811
        ({'scratch': True}, True),
        ({'is_auto': True, 'scratch': False}, True),
        ({'isolated': True, 'release': '1.0', 'scratch': False}, True),
        ({'scratch': True, 'isolated': True, 'release': '1.0'}, False),
        ({'scratch': True, 'is_auto': True}, False),
        ({'is_auto': True, 'isolated': True, 'release': '1.0'}, False),
    ))
    def test_mutually_exclusive_build_variation(self, extra_kwargs, valid):  # noqa:F811
        if valid:
            user_params = get_sample_user_params(update_args=extra_kwargs)
            build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
            build_request.render()
        else:
            with pytest.raises(OsbsValidationException) as exc_info:
                get_sample_user_params(update_args=extra_kwargs)
            assert 'mutually exclusive' in str(exc_info.value)

    def test_render_simple_request(self):
        trigger_after_koji_task = '12345'
        conf_args = {
            'build_from': 'image:buildroot:latest',
            'reactor_config_map': 'reactor-config-map',
        }
        extra_kwargs = {
            'triggered_after_koji_task': trigger_after_koji_task,
        }

        user_params = get_sample_user_params(conf_args=conf_args, update_args=extra_kwargs,
                                             no_source=True)
        config_map_data = {'source_registry': {'url': 'source_registry'}}
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(config_map_data),
                                       user_params=user_params)
        build_json = build_request.render()

        assert build_request.user_params.triggered_after_koji_task == trigger_after_koji_task
        assert build_request.triggered_after_koji_task == trigger_after_koji_task
        assert build_request.base_image == user_params.base_image

        assert build_json["metadata"]["name"] is not None
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF

        expected_output = "john-foo/component:koji-target-"
        assert build_json["spec"]["output"]["to"]["name"].startswith(expected_output)

        rendered_build_image = build_json["spec"]["strategy"]["customStrategy"]["from"]["name"]
        assert rendered_build_image == 'buildroot:latest'

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

    @pytest.mark.parametrize(('build_from', 'is_image', 'valid'), (
        (None, False, False),
        ('image:ultimate-buildroot:v1.0', True, 'ultimate-buildroot:v1.0'),
        ('imagestream:buildroot-stream:v1.0', False, 'buildroot-stream:v1.0'),
        ('buildroot-stream:v1.0', False, False)
    ))
    def test_render_prod_request_with_repo(self, build_from, is_image, valid):
        name_label = "fedora/resultingimage"
        koji_task_id = 4567
        extra_kwargs = {
            'name_label': name_label,
            'koji_task_id': koji_task_id,
            'yum_repourls': ["http://example.com/my.repo"],
        }
        conf_args = {
            'build_from': build_from,
        }

        if not valid:
            with pytest.raises(OsbsValidationException):
                user_params = get_sample_user_params(conf_args=conf_args, update_args=extra_kwargs)
            return

        user_params = get_sample_user_params(conf_args=conf_args, update_args=extra_kwargs)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
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
        assert rendered_build_image == valid
        if not is_image:
            assert build_json["spec"]["strategy"]["customStrategy"]["from"]["kind"] == \
                "ImageStreamTag"

    def test_render_prod_request_without_repo(self, caplog):
        user_params = get_sample_user_params()
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        build_request.render()
        assert 'repo info not set' in caplog.text

    def test_render_prod_request(self):
        user_params = get_sample_user_params()
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
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
        extra_kwargs = {
            'koji_target': None,
        }

        user_params = get_sample_user_params(update_args=extra_kwargs)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
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
        extra_kwargs = {
            'platform': platform,
            'scratch': scratch,
        }

        user_params = get_sample_user_params(update_args=extra_kwargs)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
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
        user_params = get_sample_user_params(update_args=extra_kwargs)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
        build_json = build_request.render()

        assert fnmatch.fnmatch(build_json['metadata']['name'], expected_name)

    def test_render_with_yum_repourls(self):
        extra_kwargs = {
            'yum_repourls': 'should be a list',
        }

        with pytest.raises(OsbsValidationException):
            get_sample_user_params(update_args=extra_kwargs)

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
        user_params = get_sample_user_params(build_json_store=str(tmpdir))
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        build_request.render()

    @pytest.mark.parametrize(('scratch', 'isolated'), (
        (True, False),
        (False, True),
        (False, False),
    ))
    def test_render_prod_request_with_trigger(self, tmpdir, scratch, isolated):
        self.create_image_change_trigger_json(str(tmpdir))
        kwargs = {'is_autorebuild': True}
        if scratch:
            kwargs['scratch'] = scratch
        if isolated:
            kwargs['isolated'] = isolated
            kwargs['release'] = '1.1'

        git_args = get_autorebuild_git_args(tmpdir)
        user_params = get_sample_user_params(build_json_store=str(tmpdir), update_args=kwargs,
                                             git_args=git_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
        build_json = build_request.render()

        if scratch or isolated:
            assert "triggers" not in build_json["spec"]
        else:
            assert "triggers" in build_json["spec"]
            from_name = build_json["spec"]["triggers"][0]["imageChange"]["from"]["name"]
            assert from_name == 'source_registry-fedora:latest'

    @pytest.mark.parametrize('koji_parent_build', ('fedora-26-9', None))
    def test_render_custom_base_image_with_trigger(self, tmpdir, koji_parent_build):
        kwargs = {'base_image': 'koji/image-build'}
        if koji_parent_build:
            kwargs['koji_parent_build'] = koji_parent_build

        user_params = get_sample_user_params(update_args=kwargs)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
        build_json = build_request.render()

        assert build_request.is_custom_base_image() is True
        # Verify the triggers are now disabled
        assert "triggers" not in build_json["spec"]

    def test_render_from_scratch_image_with_trigger(self, tmpdir):
        self.create_image_change_trigger_json(str(tmpdir))
        kwargs = {'base_image': 'scratch'}

        user_params = get_sample_user_params(update_args=kwargs, build_json_store=str(tmpdir))
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)
        build_json = build_request.render()

        assert build_request.is_from_scratch_image() is True

        # Verify the triggers are now disabled
        assert "triggers" not in build_json["spec"]

    @pytest.mark.parametrize(('extra_kwargs', 'expected_error'), (
        ({'isolated': True}, 'release parameter is required'),
        ({'isolated': True, 'release': '1'}, 'must be in the format'),
        ({'isolated': True, 'release': '1.1'}, None),
    ))
    def test_adjust_for_isolated(self, tmpdir, extra_kwargs, expected_error):
        self.create_image_change_trigger_json(str(tmpdir))

        user_params = get_sample_user_params(update_args=extra_kwargs, build_json_store=str(tmpdir))
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        assert isinstance(build_request, BuildRequestV2)

        if expected_error:
            with pytest.raises(OsbsValidationException) as exc_info:
                build_request.render()
            assert expected_error in str(exc_info.value)
        else:
            build_json = build_request.render()

            assert 'triggers' not in build_json['spec']
            assert build_json['metadata']['labels']['isolated'] == 'true'
            assert build_json['metadata']['labels']['isolated-release'] == extra_kwargs['release']

    @pytest.mark.parametrize(('autorebuild_enabled', 'release_label', 'add_timestamp',
                              'expected'), (
        (True, None, True, True),
        (True, None, False, True),
        (True, 'release', True, True),
        (True, 'release', False, RuntimeError),
        (True, 'Release', True, True),
        (True, 'Release', False, RuntimeError),
        (False, 'release', True, False),
        (False, 'release', False, False),
        (False, 'Release', True, False),
        (False, 'Release', False, False),
    ))
    def test_render_prod_request_with_repo_info(self, tmpdir,
                                                autorebuild_enabled, release_label,
                                                add_timestamp, expected):
        labels = None
        if release_label:
            labels = {Labels.LABEL_TYPE_RELEASE: release_label}
        self.create_image_change_trigger_json(str(tmpdir))
        git_args = get_autorebuild_git_args(tmpdir, add_timestamp) if autorebuild_enabled else None
        user_params = get_sample_user_params(build_json_store=str(tmpdir),
                                             git_args=git_args, labels=labels)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params,
                                       repo_info=mock_repo_info(git_args, labels))

        if isinstance(expected, type):
            with pytest.raises(expected):
                build_json = build_request.render()
            return

        build_json = build_request.render()
        base_image = '{}-{}'.format(build_request.source_registry['url'],
                                    user_params.base_image)

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
    def test_prod_is_custom_base_image(self, base_image, is_custom):
        update_args = {'base_image': base_image}
        user_params = get_sample_user_params(update_args=update_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        build_request.render()

        assert build_request.is_custom_base_image() == is_custom

    @pytest.mark.parametrize(('base_image', 'is_from_scratch'), [
        ('fedora', False),
        ('fedora:latest', False),
        ('koji/image-build', False),
        ('koji/image-build:spam.conf', False),
        ('scratch', True),
    ])
    def test_prod_is_from_scratch_image(self, base_image, is_from_scratch):
        update_args = {'base_image': base_image}
        user_params = get_sample_user_params(update_args=update_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        build_request.render()

        assert build_request.is_from_scratch_image() == is_from_scratch

    @pytest.mark.parametrize('base_image, msg, keep_triggers', (
        ('fedora', None, True),
        ('scratch', 'from request because FROM scratch image', False),
        ('koji/image-build', 'from request because custom base image', False),
    ))
    def test_adjust_for_triggers_base_builds(self, tmpdir, caplog, base_image, msg, keep_triggers):
        """Test if triggers are properly adjusted for base and FROM scratch builds"""
        self.create_image_change_trigger_json(str(tmpdir))
        git_args = get_autorebuild_git_args(tmpdir)

        update_args = {'base_image': base_image}
        user_params = get_sample_user_params(build_json_store=str(tmpdir), update_args=update_args,
                                             git_args=git_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        build_request.render()

        assert bool(build_request.template['spec'].get('triggers', [])) == keep_triggers

        if msg is not None:
            assert msg in caplog.text

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
            'x86': 'plx86a=yes, plx86b=yes',
            'ppc': 'plppc1=yes, plppc2=yes',
        }

        if platforms:
            update_args = {
                'platforms': [platforms],
                'build_type': BUILD_TYPE_ORCHESTRATOR,
            }
        else:
            update_args = {
                'platforms': None,
                'build_type': BUILD_TYPE_WORKER,
            }

        update_args['is_auto'] = is_auto
        update_args['scratch'] = scratch
        update_args['isolated'] = isolated
        if isolated:
            update_args['release'] = '1.0'
        conf_args = {
            'build_from': 'image:buildroot:latest',
            'auto_build_node_selector': 'auto1=yes, auto2=yes',
            'explicit_build_node_selector': 'explicit1=yes, explicit2=yes',
            'isolated_build_node_selector': 'isolated1=yes, isolated2=yes',
            'scratch_build_node_selector': 'scratch1=yes, scratch2=yes',
        }
        if platform:
            conf_args['node_selector.' + platform] = platform_nodeselectors[platform]
            update_args['platform'] = platform

        user_params = get_sample_user_params(conf_args=conf_args, update_args=update_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)
        build_json = build_request.render()

        if expected:
            assert build_json['spec']['nodeSelector'] == expected
        else:
            assert 'nodeSelector' not in build_json['spec']

    @pytest.mark.parametrize('build_type', [
        BUILD_TYPE_WORKER,
        BUILD_TYPE_ORCHESTRATOR,
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        {'version': 1, 'source_registry': {'url': 'source_registry'}},
    ])
    @pytest.mark.parametrize('reactor_config_map', [
        None,
        'reactor-config-map',
    ])
    def test_set_config_map(self, build_type, reactor_config_map, reactor_config_override):
        outer_template = WORKER_OUTER_TEMPLATE
        if build_type == BUILD_TYPE_ORCHESTRATOR:
            outer_template = ORCHESTRATOR_OUTER_TEMPLATE

        conf_args = {
            'build_from': 'image:buildroot:latest',
            'reactor_config_map': reactor_config_map,
        }
        update_args = {
            'reactor_config_override': reactor_config_override,
            'build_type': build_type,
        }
        user_params = get_sample_user_params(conf_args=conf_args, update_args=update_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params,
                                       outer_template=outer_template)
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
        {'required_secrets': ['secret4', 'secret5'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret4', 'secret5', 'reactor_secret'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret4', 'secret5'],
         'worker_token_secrets': [],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret4', 'secret5', 'reactor_secret'],
         'worker_token_secrets': [],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret4', 'secret5'],
         'worker_token_secrets': ['secret7', 'secret8'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret4', 'secret5'],
         'worker_token_secrets': ['secret4', 'secret5', 'secret9'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret4', 'secret5', 'reactor_secret'],
         'worker_token_secrets': ['secret7', 'secret8'],
         'source_registry': {'url': 'source_registry'}},
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        None,
        {},
        {'required_secrets': ['secret1', 'secret2'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2', 'reactor_secret'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': [],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2', 'reactor_secret'],
         'worker_token_secrets': [],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': [],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': ['secret10', 'secret11'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2'],
         'worker_token_secrets': ['secret1', 'secret2', 'secret11'],
         'source_registry': {'url': 'source_registry'}},
        {'required_secrets': ['secret1', 'secret2', 'reactor_secret'],
         'worker_token_secrets': ['secret10', 'secret11'],
         'source_registry': {'url': 'source_registry'}},
    ])
    def test_set_required_secrets(self, build_type, existing, reactor_config_map,
                                  reactor_config_override):
        # we need source_registry defined in reactor_config_map
        if not reactor_config_map and not reactor_config_override:
            return

        outer_template = WORKER_OUTER_TEMPLATE
        if build_type == BUILD_TYPE_ORCHESTRATOR:
            outer_template = ORCHESTRATOR_OUTER_TEMPLATE

        conf_args = {
            'build_from': 'image:buildroot:latest',
            'reactor_config_map': reactor_config_map,
        }
        update_args = {
            'reactor_config_override': reactor_config_override,
            'build_type': build_type,
        }
        user_params = get_sample_user_params(conf_args=conf_args, update_args=update_args)

        all_secrets = deepcopy(reactor_config_map)
        mock_api = MockOSBSApi(all_secrets)

        build_request = BuildRequestV2(osbs_api=mock_api, user_params=user_params,
                                       outer_template=outer_template)

        if not reactor_config_override and not reactor_config_map:
            with pytest.raises(RuntimeError) as exc:
                build_request.render()
            log_msg = 'mandatory "source_registry" is not defined in reactor_config'
            assert log_msg in str(exc.value)
            return

        build_json = build_request.render()

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
         'source_registry': {'url': 'registry_in_cm'}},
        {'registries_organization': 'organization_in_cm',
         'source_registry': {'url': 'registry_in_cm'},
         'pull_registries': [{'uri': 'pull_registry'}]},
    ])
    @pytest.mark.parametrize('reactor_config_override', [
        None,
        {},
        {'registries_organization': 'organization_in_override',
         'source_registry': {'url': 'registry_in_override'}},
        {'registries_organization': 'organization_in_override',
         'source_registry': {'url': 'registry_in_override'},
         'pull_registries': [{'uri': 'pull_registry'}]},
    ])
    def test_set_data_from_reactor_config(self, build_type, reactor_config_map,
                                          reactor_config_override):
        outer_template = WORKER_OUTER_TEMPLATE
        if build_type == BUILD_TYPE_ORCHESTRATOR:
            outer_template = ORCHESTRATOR_OUTER_TEMPLATE

        conf_args = {
            'build_from': 'image:buildroot:latest',
            'reactor_config_map': 'reactor_config_map',
        }
        update_args = {
            'reactor_config_override': reactor_config_override,
            'build_type': build_type,
        }
        user_params = get_sample_user_params(conf_args=conf_args, update_args=update_args,
                                             no_source=True)

        all_secrets = deepcopy(reactor_config_map)
        mock_api = MockOSBSApi(all_secrets)

        build_request = BuildRequestV2(osbs_api=mock_api, user_params=user_params,
                                       outer_template=outer_template)

        if not reactor_config_override and not reactor_config_map:
            with pytest.raises(RuntimeError) as exc:
                build_request.render()
            log_msg = 'mandatory "source_registry" is not defined in reactor_config'
            assert log_msg in str(exc.value)
            return

        build_request.render()

        expected_registry = None
        expected_organization = None
        expected_pull_registries = None
        if reactor_config_override:
            if 'source_registry' in reactor_config_override:
                expected_registry = reactor_config_override['source_registry']
            if 'registries_organization' in reactor_config_override:
                expected_organization = reactor_config_override['registries_organization']
            if 'pull_registries' in reactor_config_override:
                expected_pull_registries = reactor_config_override['pull_registries']

        elif reactor_config_map:
            if 'source_registry' in reactor_config_map:
                expected_registry = reactor_config_map['source_registry']
            if 'registries_organization' in reactor_config_map:
                expected_organization = reactor_config_map['registries_organization']
            if 'pull_registries' in reactor_config_map:
                expected_pull_registries = reactor_config_map['pull_registries']

        assert expected_registry == build_request.source_registry
        assert expected_organization == build_request.organization
        assert expected_pull_registries == build_request.pull_registries

    @pytest.mark.parametrize('build_type', [
        BUILD_TYPE_WORKER,
        BUILD_TYPE_ORCHESTRATOR,
    ])
    @pytest.mark.parametrize('config_as_override', [True, False])
    @pytest.mark.parametrize('config, expected_envs, expected_error', [
        # No conflicts
        ({'build_env_vars': [
            {'name': 'HTTP_PROXY', 'value': 'example.proxy.net'},
            {'name': 'NO_PROXY', 'value': 'example.no-proxy.net'},
         ]},
         [{'name': 'HTTP_PROXY', 'value': 'example.proxy.net'},
          {'name': 'NO_PROXY', 'value': 'example.no-proxy.net'}],
         None),
        # Conflicts with special environment variables
        ({'build_env_vars': [
            {'name': 'USER_PARAMS', 'value': '{"scratch": true}'},
        ]},
         [],
         'Cannot set environment variable from reactor config (already exists): USER_PARAMS'),
        # Conflicts with special environment variables
        ({'build_env_vars': [
            {'name': 'REACTOR_CONFIG', 'value': 'arrangement_version: 5'},
        ]},
         [],
         'Cannot set environment variable from reactor config (already exists): REACTOR_CONFIG'),
        # Conflicts with itself
        ({'build_env_vars': [
            {'name': 'HTTP_PROXY', 'value': 'example.proxy.net'},
            {'name': 'HTTP_PROXY', 'value': 'example.other-proxy.net'},
        ]},
         [],
         'Cannot set environment variable from reactor config (already exists): HTTP_PROXY'),
    ])
    def test_set_build_env_vars(self, build_type, config_as_override, config,
                                expected_envs, expected_error, caplog):
        outer_template = WORKER_OUTER_TEMPLATE
        if build_type == BUILD_TYPE_ORCHESTRATOR:
            outer_template = ORCHESTRATOR_OUTER_TEMPLATE

        conf_args = {
            'build_from': 'image:buildroot:latest',
        }
        update_args = {
            'build_type': build_type,
        }
        config_map = None
        if config_as_override:
            update_args['reactor_config_override'] = deepcopy(config)
            update_args['reactor_config_override']['source_registry'] = {'url': 'source_registry'}
        else:
            conf_args['reactor_config_map'] = 'reactor-config-map'
            config_map = deepcopy(config)
            config_map['source_registry'] = {'url': 'source_registry'}

        user_params = get_sample_user_params(conf_args=conf_args, update_args=update_args,
                                             no_source=True)
        mock_api = MockOSBSApi(config_map)
        build_request = BuildRequestV2(osbs_api=mock_api, user_params=user_params,
                                       outer_template=outer_template)

        if expected_error is None:
            build_json = build_request.render()
        else:
            with pytest.raises(OsbsValidationException) as exc_info:
                build_request.render()
            assert str(exc_info.value) == expected_error
            return

        json_env = build_json['spec']['strategy']['customStrategy']['env']
        for env in expected_envs:
            assert env in json_env
            msg = 'Set environment variable from reactor config: {}'.format(env['name'])
            assert msg in caplog.text

    @pytest.mark.parametrize('build_type', [
        BUILD_TYPE_WORKER,
        BUILD_TYPE_ORCHESTRATOR,
    ])
    @pytest.mark.parametrize('user_params,config_map,config_override,expected', [
        (None, None, None, None),
        (None, '', None, None),  # config map exists, but doesn't set a value
        ('base_image1', None, None, 'base_image1'),
        ('base_image2', '', None, 'base_image2'),
        ('base_image2', 'base_image1', None, 'base_image2'),
        (None, 'base_image1', None, 'base_image1'),
        (None, 'base_image1', 'base_image2', 'base_image2'),
    ])
    def test_set_flatpak_base_image(self,
                                    build_type,
                                    user_params,
                                    config_map,
                                    config_override,
                                    expected):

        outer_template = WORKER_OUTER_TEMPLATE
        if build_type == BUILD_TYPE_ORCHESTRATOR:
            outer_template = ORCHESTRATOR_OUTER_TEMPLATE

        # autorebuild only has an effect on orchestrator builds,
        # so combine testing both into one branch
        autorebuild = build_type == BUILD_TYPE_ORCHESTRATOR

        mock_configuration = flexmock(
            autorebuild={},
            container={},
            container_module_specs=[
                ModuleSpec.from_str('eog:stable')
            ],
            depth=0,
            is_autorebuild_enabled=lambda: autorebuild,
            is_flatpak=True,
            flatpak_base_image=user_params,
            flatpak_name=None,
            flatpak_component=None,
            git_branch=TEST_GIT_BRANCH,
            git_ref=TEST_GIT_REF,
            git_uri=TEST_GIT_URI)

        repo_info = RepoInfo(configuration=mock_configuration)

        source_registry_url = 'source_registry'

        reactor_config_map = None
        if config_map is not None:
            reactor_config_map = {
                'flatpak': {'base_image': config_map},
                'source_registry': {'url': source_registry_url}
            }

        reactor_config_override = None
        if config_override is not None:
            reactor_config_override = {
                'flatpak': {'base_image': config_override},
                'source_registry': {'url': source_registry_url}
            }

        conf_args = {
            'build_from': 'image:buildroot:latest',
            'reactor_config_map': reactor_config_map,
        }
        update_args = {
            'reactor_config_override': reactor_config_override,
            'base_image': user_params,
            'build_type': build_type,
            'flatpak': True,
            'repo_info': repo_info,
        }
        user_params = get_sample_user_params(conf_args=conf_args, update_args=update_args,
                                             no_source=True)

        all_secrets = deepcopy(reactor_config_map)
        mock_api = MockOSBSApi(all_secrets)

        build_request = BuildRequestV2(osbs_api=mock_api, user_params=user_params,
                                       outer_template=outer_template, repo_info=repo_info)

        if expected is None:
            with pytest.raises(OsbsValidationException):
                build_request.render()
            return
        elif config_map is None and config_override is None:
            with pytest.raises(RuntimeError):
                build_request.render()
            return

        build_request.render()
        imagetstreamtag_name = '{}-{}:{}'.format(source_registry_url, expected, 'latest')
        assert user_params.base_image == expected
        assert user_params.trigger_imagestreamtag == imagetstreamtag_name

        if autorebuild:
            trigger = build_request.build_json['spec']['triggers'][0]
            assert trigger['imageChange']['from']['name'] == imagetstreamtag_name

    @pytest.mark.parametrize('cpu', ['None', 100])
    @pytest.mark.parametrize('memory', ['None', 50])
    @pytest.mark.parametrize('storage', ['None', 25])
    def test_set_resource_limits(self, cpu, memory, storage):
        user_params = get_sample_user_params()
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)

        build_request.set_resource_limits(cpu, memory, storage)
        assert build_request._resource_limits['cpu'] == cpu
        assert build_request._resource_limits['memory'] == memory
        assert build_request._resource_limits['storage'] == storage
        build_request.render()
        assert build_request._resource_limits['cpu'] == cpu
        assert build_request._resource_limits['memory'] == memory
        assert build_request._resource_limits['storage'] == storage
        expected_resources = {
           'limits': {
              'cpu': cpu,
              'memory': memory,
              'storage': storage,
           }
        }
        assert build_request.template['spec']['resources'] == expected_resources

    @pytest.mark.parametrize('openshift_version', ['None', 25])
    def test_set_os_version(self, openshift_version):
        user_params = get_sample_user_params()
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)

        build_request.set_openshift_required_version(openshift_version)
        assert build_request._openshift_required_version == openshift_version

    def test_build_id(self):
        user_params = get_sample_user_params()
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)

        build_request.render()
        assert build_request.build_id == 'path-master-cd1e4'

    def test_bad_template_path(self):
        user_params = get_sample_user_params()
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params,
                                       outer_template='invalid')
        with pytest.raises(OsbsException):
            build_request.render()

    @pytest.mark.parametrize(('worker_max', 'orchestrator_max', 'build_type', 'expected'), [
        (None, None, BUILD_TYPE_ORCHESTRATOR, 4),
        (None, None, BUILD_TYPE_WORKER, 3),
        (6, 7, BUILD_TYPE_ORCHESTRATOR, 7),
        (6, 7, BUILD_TYPE_WORKER, 6),
        ("6", "invalid string", BUILD_TYPE_ORCHESTRATOR, 4),
        ("invalid string", "7", BUILD_TYPE_WORKER, 3),
        ({"6": "hours"}, {"7": "hours"}, BUILD_TYPE_ORCHESTRATOR, 4),
        ({"6": "hours"}, {"7": "hours"}, BUILD_TYPE_WORKER, 3),
        ("6", "7", BUILD_TYPE_ORCHESTRATOR, 7),
        ("6", "7", BUILD_TYPE_WORKER, 6),
        ("6", "-1", BUILD_TYPE_ORCHESTRATOR, None),
        ("0", "7", BUILD_TYPE_WORKER, None),
    ])
    def test_set_deadlines(self, worker_max, orchestrator_max, build_type, expected):
        conf_args = {
           'worker_max_run_hours': worker_max,
           'orchestrator_max_run_hours': orchestrator_max,
           'build_from': 'image:fedora:latest',
        }
        update_args = {
           'build_type': build_type,
        }
        user_params = get_sample_user_params(update_args=update_args, conf_args=conf_args)
        build_request = BuildRequestV2(osbs_api=MockOSBSApi(), user_params=user_params)

        build_json = build_request.render()
        if expected:
            expected_hours = expected * 3600
            assert build_json['spec']['completionDeadlineSeconds'] == expected_hours
        else:
            with pytest.raises(KeyError):
                assert build_json['spec']['completionDeadlineSeconds']


def get_sample_source_params(build_json_store=INPUTS_PATH, conf_args=None,
                             update_args=None, labels=None):
    if not conf_args:
        conf_args = {'build_from': 'image:buildroot:latest'}
    # scratch handling is tricky
    if update_args:
        conf_args.setdefault('scratch', update_args.get('scratch'))
    conf_args.setdefault('reactor_config_map', 'reactor-config-map')

    build_conf = Configuration(conf_file=None, **conf_args)
    kwargs = {
        'build_json_dir': build_json_store,
        'user': 'john-foo',
        'component': TEST_COMPONENT,
        'base_image': 'fedora:latest',
        'name_label': 'fedora/resultingimage',
        'koji_target': 'koji-target',
        'platforms': ['x86_64'],
        'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
        'build_conf': build_conf,
        'build_type': BUILD_TYPE_WORKER,
        'sources_for_koji_build_nvr': "name-1.0-123",
    }
    if update_args:
        kwargs.update(update_args)
    user_params = SourceContainerUserParams.make_params(**kwargs)
    return user_params


class TestSourceBuildRequest(object):
    """Test suite for SourceBuildRequest"""
    def test_render_simple_source_request(self):
        user_params = get_sample_source_params()
        build_request = SourceBuildRequest(osbs_api=MockOSBSApi())
        build_request.set_params(user_params=user_params)

        build_json = build_request.render()

        assert build_json["metadata"]["name"] is not None
        assert "triggers" not in build_json["spec"]

        expected_output = "john-foo/component:koji-target-"
        assert build_json["spec"]["output"]["to"]["name"].startswith(expected_output)

        rendered_build_image = build_json["spec"]["strategy"]["customStrategy"]["from"]["name"]
        assert rendered_build_image == 'buildroot:latest'

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

    def test_render_scratch_source_request(self):
        user_params = get_sample_source_params(update_args={'scratch': True})
        user_params.image_tag = 'test-salt-time'
        build_request = SourceBuildRequest(osbs_api=MockOSBSApi(), user_params=user_params)
        build_request.render()
        assert build_request.template['metadata']['name'] == 'scratch-sources-salt-time'
