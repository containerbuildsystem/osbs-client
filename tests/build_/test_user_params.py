"""
Copyright (c) 2015-2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

import pytest
from flexmock import flexmock

import datetime
import random
import sys
import json

from osbs.build.user_params import (
    BuildIDParam,
    RegistryURIsParam,
    BuildUserParams,
    SourceContainerUserParams,
    load_user_params_from_json,
)
from osbs.exceptions import OsbsValidationException
from osbs.constants import BUILD_TYPE_WORKER, REACTOR_CONFIG_ARRANGEMENT_VERSION
from tests.constants import (TEST_COMPONENT, TEST_FILESYSTEM_KOJI_TASK_ID,
                             TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI,
                             TEST_IMAGESTREAM, TEST_KOJI_TASK_ID, TEST_USER)


class TestBuildIDParam(object):
    def test_build_id_param_shorten_id(self):
        p = BuildIDParam()
        p.value = "x" * 64

        val = p.value

        assert len(val) == 63

    def test_build_id_param_raise_exc(self):
        p = BuildIDParam()
        with pytest.raises(OsbsValidationException):
            p.value = r"\\\\@@@@||||"


class TestRegistryURIsParam(object):
    @pytest.mark.parametrize('suffix', ['', '/'])
    def test_registry_uris_param_api_implicit(self, suffix):
        p = RegistryURIsParam()
        p.value = ['registry.example.com:5000{suffix}'.format(suffix=suffix)]

        assert p.value[0].uri == 'registry.example.com:5000'  # pylint: disable=no-member
        assert p.value[0].docker_uri == 'registry.example.com:5000'  # pylint: disable=no-member
        assert p.value[0].version == 'v2'  # pylint: disable=no-member

    def test_registry_uris_param_v2(self):
        p = RegistryURIsParam()
        p.value = ['registry.example.com:5000/v2']

        assert p.value[0].uri == 'registry.example.com:5000'  # pylint: disable=no-member
        assert p.value[0].docker_uri == 'registry.example.com:5000'  # pylint: disable=no-member
        assert p.value[0].version == 'v2'  # pylint: disable=no-member

    def test_registry_uris_param_v1(self):
        p = RegistryURIsParam()
        with pytest.raises(OsbsValidationException):
            p.value = ['registry.example.com:5000/v1']


class TestBuildUserParams(object):
    def test_validate_missing_required(self):
        kwargs = {
            'base_image': 'base_image',
            'git_uri': TEST_GIT_URI,
            'name_label': 'name_label',
            'build_from': 'image:buildroot:latest',
        }
        spec = BuildUserParams()
        spec.set_params(**kwargs)

        with pytest.raises(OsbsValidationException):
            spec.validate()

    def get_minimal_kwargs(self):
        return {
            # Params needed to avoid exceptions.
            'user': TEST_USER,
            'base_image': 'base_image',
            'name_label': 'name_label',
            'git_uri': TEST_GIT_URI,
            'build_from': 'image:buildroot:latest',
        }

    def test_v2_spec_name2(self):
        kwargs = self.get_minimal_kwargs()
        kwargs.update({
            'git_uri': TEST_GIT_URI,
            'git_branch': TEST_GIT_BRANCH,
        })

        spec = BuildUserParams()
        spec.set_params(**kwargs)

        assert spec.name.value.startswith('path-master')

    @pytest.mark.parametrize('rand,timestr', [
        ('12345', '20170501123456'),
        ('67890', '20170731111111'),
    ])
    @pytest.mark.parametrize(('platform'), (
        ('x86_64'),
        (None),
    ))
    def test_v2_image_tag(self, rand, timestr, platform):
        kwargs = self.get_minimal_kwargs()
        kwargs.update({
            'component': 'foo',
            'koji_target': 'tothepoint',
        })
        if platform:
            kwargs['platform'] = platform

        (flexmock(sys.modules['osbs.build.user_params'])
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))

        (flexmock(random)
            .should_receive('randrange').once()
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        spec = BuildUserParams()
        spec.set_params(**kwargs)

        img_tag = '{user}/{component}:{koji_target}-{random_number}-{time_string}'
        if platform:
            img_tag += '-{platform}'
        img_tag = img_tag.format(random_number=rand, time_string=timestr, **kwargs)
        assert spec.image_tag.value == img_tag

    def test_user_params_bad_json(self):
        required_json = json.dumps({
            'arrangement_version': 6,
            'customize_conf': 'worker_customize.json',
            'git_ref': 'master',
            'kind': 'build_user_params',
        }, sort_keys=True)
        spec = BuildUserParams()

        spec.from_json(None)
        assert spec.to_json() == required_json
        spec.from_json("")
        assert spec.to_json() == required_json
        assert '{}'.format(spec)

    @pytest.mark.parametrize(('missing_arg'), (
        'name_label',
        'base_image',
    ))
    def test_user_params_bad_none_flatpak(self, missing_arg):
        kwargs = self.get_minimal_kwargs()
        kwargs['flatpak'] = False
        kwargs.pop(missing_arg)
        spec = BuildUserParams()

        with pytest.raises(OsbsValidationException):
            spec.set_params(**kwargs)

    def test_user_params_bad_compose_ids(self):
        kwargs = self.get_minimal_kwargs()
        kwargs['compose_ids'] = True
        spec = BuildUserParams()

        with pytest.raises(OsbsValidationException):
            spec.set_params(**kwargs)

    def test_user_params_bad_build_from(self):
        kwargs = self.get_minimal_kwargs()
        # does not have an "image:" prefix:
        kwargs['build_from'] = 'registry.example.com/buildroot'
        spec = BuildUserParams()

        with pytest.raises(OsbsValidationException) as e:
            spec.set_params(**kwargs)
        assert 'build_from must be "source_type:source_value"' in str(e.value)

    @pytest.mark.parametrize(('signing_intent', 'compose_ids', 'yum_repourls', 'exc'), (
        ('release', [1, 2], ['http://example.com/my.repo'], OsbsValidationException),
        ('release', [1, 2], None, OsbsValidationException),
        (None, [1, 2], ['http://example.com/my.repo'], None),
        ('release', None, ['http://example.com/my.repo'], None),
        ('release', None, None, None),
        (None, [1, 2], None, None),
        (None, None, ['http://example.com/my.repo'], None),
        (None, None, None, None),
    ))
    def test_v2_compose_ids_and_signing_intent(self, signing_intent, compose_ids, yum_repourls,
                                               exc):
        kwargs = self.get_minimal_kwargs()
        if signing_intent:
            kwargs['signing_intent'] = signing_intent
        if compose_ids:
            kwargs['compose_ids'] = compose_ids
        if yum_repourls:
            kwargs['yum_repourls'] = yum_repourls

        kwargs.update({
            'git_uri': 'https://github.com/user/reponame.git',
            'git_branch': 'master',
        })

        spec = BuildUserParams()

        if exc:
            with pytest.raises(exc):
                spec.set_params(**kwargs)
        else:
            spec.set_params(**kwargs)

            if yum_repourls:
                assert spec.yum_repourls.value == yum_repourls
            if signing_intent:
                assert spec.signing_intent.value == signing_intent
            if compose_ids:
                assert spec.compose_ids.value == compose_ids

    def test_v2_all_values_and_json(self):
        # all values that BuildUserParams stores
        param_kwargs = {
            # 'arrangement_version': self.arrangement_version,  # calculated value
            'base_image': 'buildroot:old',
            # 'build_from': 'buildroot:old',  # only one of build_*
            # 'build_json_dir': self.build_json_dir,  # init paramater
            'build_image': 'buildroot:latest',
            # 'build_imagestream': 'buildroot:name_label',
            'build_type': BUILD_TYPE_WORKER,
            'component': TEST_COMPONENT,
            'compose_ids': [1, 2],
            'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
            'flatpak': False,
            # 'flatpak_base_image': self.flatpak_base_image,  # not used with false flatpack
            'git_branch': TEST_GIT_BRANCH,
            'git_ref': TEST_GIT_REF,
            'git_uri': TEST_GIT_URI,
            'image_tag': 'user/None:none-0-0',
            'imagestream_name': TEST_IMAGESTREAM,
            'isolated': False,
            'koji_parent_build': 'fedora-26-9',
            'koji_target': 'tothepoint',
            "orchestrator_deadline": 4,
            'parent_images_digests': {
                'registry.fedorahosted.org/fedora:29': {
                    'x86_64': 'registry.fedorahosted.org/fedora@sha256:8b96f2f9f88179a065738b2b37'
                              '35e386efb2534438c2a2f45b74358c0f344c81'
                }
            },
            # 'name': self.name,  # calculated value
            'platform': 'x86_64',
            'platforms': ['x86_64', ],
            'reactor_config_map': 'reactor-config-map',
            'reactor_config_override': 'reactor-config-override',
            'release': '29',
            'scratch': False,
            'signing_intent': False,
            'task_id': TEST_KOJI_TASK_ID,
            'trigger_imagestreamtag': 'base_image:latest',
            'user': TEST_USER,
            # 'yum_repourls': ,  # not used with compose_ids
            "worker_deadline": 3,
        }
        # additional values that BuildUserParams requires but stores under different names
        param_kwargs.update({
            'name_label': 'name_label',
        })
        rand = '12345'
        timestr = '20170731111111'
        (flexmock(sys.modules['osbs.build.user_params'])
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))

        (flexmock(random)
            .should_receive('randrange').once()
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        build_json_dir = 'inputs'
        spec = BuildUserParams(build_json_dir)
        spec.set_params(**param_kwargs)
        expected_json = {
            "arrangement_version": REACTOR_CONFIG_ARRANGEMENT_VERSION,
            "base_image": "buildroot:old",
            "build_image": "buildroot:latest",
            "build_json_dir": build_json_dir,
            "build_type": "worker",
            "component": TEST_COMPONENT,
            "compose_ids": [1, 2],
            "customize_conf": "worker_customize.json",
            "filesystem_koji_task_id": TEST_FILESYSTEM_KOJI_TASK_ID,
            "git_branch": TEST_GIT_BRANCH,
            "git_ref": TEST_GIT_REF,
            "git_uri": TEST_GIT_URI,
            "image_tag": "{}/{}:tothepoint-{}-{}-x86_64".format(TEST_USER, TEST_COMPONENT,
                                                                rand, timestr),
            "imagestream_name": "name_label",
            "kind": "build_user_params",
            "koji_parent_build": "fedora-26-9",
            "koji_target": "tothepoint",
            "name": "path-master-cd1e4",
            "orchestrator_deadline": 4,
            'parent_images_digests': {
                'registry.fedorahosted.org/fedora:29': {
                    'x86_64': 'registry.fedorahosted.org/fedora@sha256:8b96f2f9f88179a065738b2b37'
                              '35e386efb2534438c2a2f45b74358c0f344c81'
                }
            },
            "platform": "x86_64",
            "platforms": ["x86_64"],
            "reactor_config_map": "reactor-config-map",
            "reactor_config_override": "reactor-config-override",
            "release": "29",
            "trigger_imagestreamtag": "buildroot:old",
            "user": TEST_USER,
            "worker_deadline": 3,
        }
        assert spec.to_json() == json.dumps(expected_json, sort_keys=True)

        spec2 = BuildUserParams()
        spec2.from_json(spec.to_json())
        assert spec2.to_json() == json.dumps(expected_json, sort_keys=True)

    def test_from_json_failure(self, caplog):
        spec = BuildUserParams()
        with pytest.raises(ValueError):
            spec.from_json('{"this is not valid json": }')
        assert 'failed to convert {"this is not valid json": }' in caplog.text

    def test_from_json_continue(self):
        spec = BuildUserParams()
        expected_json = {
            "arrangement_version": REACTOR_CONFIG_ARRANGEMENT_VERSION,
            "base_image": "buildroot:old",
            "build_image": "buildroot:latest",
            "build_json_dir": "build_dir",
            "build_type": "worker",
            "component": TEST_COMPONENT,
            "compose_ids": [1, 2],
            "customize_conf": "prod_customize.json",
            "filesystem_koji_task_id": TEST_FILESYSTEM_KOJI_TASK_ID,
            "git_branch": TEST_GIT_BRANCH,
            "git_ref": TEST_GIT_REF,
            "git_uri": TEST_GIT_URI,
            "image_tag": "latest",
            "imagestream_name": "name_label",
            "koji_parent_build": "fedora-26-9",
            "koji_target": "tothepoint",
            "name": "path-master-cd1e4",
            "orchestrator_deadline": 4,
            "platform": "x86_64",
            "platforms": ["x86_64"],
            "reactor_config_map": "reactor-config-map",
            "reactor_config_override": "reactor-config-override",
            "release": "29",
            "trigger_imagestreamtag": "buildroot:old",
            "this is not a valid key": "this is not a valid field",
            "user": TEST_USER,
            "worker_deadline": 3,
            "triggered_after_koji_task": 12345,
        }
        spec.from_json(json.dumps(expected_json))


class TestSourceContainerUserParams(object):
    """Tests for source container user params"""

    def get_minimal_kwargs(self):
        return {
            # Params needed to avoid exceptions.
            "build_from": "image:buildroot:latest",
            'user': TEST_USER,
            'sources_for_koji_build_nvr': 'test-1-123',
        }

    def test_validate_missing_required(self):
        kwargs = {
            "build_from": "image:buildroot:latest",
            'user': TEST_USER,
        }
        spec = SourceContainerUserParams()
        spec.set_params(**kwargs)

        with pytest.raises(OsbsValidationException):
            spec.validate()

    def test_all_values_and_json(self):
        param_kwargs = self.get_minimal_kwargs()
        param_kwargs.update({
            'component': TEST_COMPONENT,
            "koji_target": "tothepoint",
            "orchestrator_deadline": 5,
            "platform": "x86_64",
            'scratch': True,
            "worker_deadline": 3,
        })

        rand = '12345'
        timestr = '20170731111111'
        (flexmock(sys.modules['osbs.build.user_params'])
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))

        (flexmock(random)
            .should_receive('randrange').once()
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        build_json_dir = 'inputs'
        spec = SourceContainerUserParams(build_json_dir)
        spec.set_params(**param_kwargs)

        expected_json = {
            "arrangement_version": REACTOR_CONFIG_ARRANGEMENT_VERSION,
            "build_image": "buildroot:latest",
            "build_json_dir": build_json_dir,
            'component': TEST_COMPONENT,
            "image_tag": "{}/{}:tothepoint-{}-{}-x86_64".format(
                TEST_USER, TEST_COMPONENT, rand, timestr),
            "kind": "source_containers_user_params",
            "sources_for_koji_build_nvr": "test-1-123",
            "koji_target": "tothepoint",
            "name": "test-source",
            "orchestrator_deadline": 5,
            "platform": "x86_64",
            'scratch': True,
            "user": TEST_USER,
            "worker_deadline": 3,
        }
        assert spec.to_json() == json.dumps(expected_json, sort_keys=True)

        spec2 = SourceContainerUserParams()
        spec2.from_json(spec.to_json())
        assert spec2.to_json() == json.dumps(expected_json, sort_keys=True)


@pytest.mark.parametrize('user_params,expected', [
    ({"kind": "build_user_params"}, BuildUserParams),
    ({"kind": "source_containers_user_params"}, SourceContainerUserParams),
])
def test_load_user_params_from_json(user_params, expected):
    user_params_json = json.dumps(user_params)
    user_params_obj = load_user_params_from_json(user_params_json)
    assert isinstance(user_params_obj, expected)
