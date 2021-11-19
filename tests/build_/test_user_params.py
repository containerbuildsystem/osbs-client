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
    BuildUserParams,
    SourceContainerUserParams,
    load_user_params_from_json,
)
from osbs.conf import Configuration
from osbs.constants import DEFAULT_NAMESPACE
from osbs.repo_utils import RepoInfo, RepoConfiguration
from osbs.exceptions import OsbsValidationException
from tests.constants import (TEST_COMPONENT, TEST_FILESYSTEM_KOJI_TASK_ID,
                             TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI,
                             TEST_KOJI_TASK_ID, TEST_USER, INPUTS_PATH,
                             TEST_OCP_NAMESPACE)
import osbs.utils


class TestBuildIDParam(object):
    def test_build_id_param_shorten_id(self):
        p = BuildIDParam()

        obj = flexmock()
        p.__set__(obj, "x" * 64)

        assert len(p.__get__(obj)) == 63

    def test_build_id_param_raise_exc(self):
        p = BuildIDParam()
        with pytest.raises(OsbsValidationException):
            p.__set__(flexmock(), r"\\\\@@@@||||")


class TestBuildUserParams(object):
    def get_minimal_kwargs(self, git_args=None, conf_args=None):
        if not git_args:
            git_args = {}
        git_args.setdefault('git_uri', TEST_GIT_URI)
        if not conf_args:
            conf_args = {'build_from': 'image:buildroot:latest'}
        repo_conf = RepoConfiguration(**git_args)
        return {
            # Params needed to avoid exceptions.
            'build_json_dir': INPUTS_PATH,
            'base_image': 'base_image',
            'build_conf': Configuration(**conf_args),
            'name_label': 'name_label',
            'repo_info': RepoInfo(configuration=repo_conf),
            'user': TEST_USER,
        }

    @pytest.mark.parametrize(('skip_param', 'message'), [
        ('base_image', 'base_image must be provided'),
        ('name_label', 'name_label must be provided'),
        ('repo_info', 'no repo_info passed to BuildUserParams'),
    ])
    def test_incomplete_set_params(self, skip_param, message):
        kwargs = self.get_minimal_kwargs()
        kwargs[skip_param] = None
        with pytest.raises(OsbsValidationException) as exc:
            BuildUserParams.make_params(**kwargs)
            assert message in exc.message

    def test_validate_missing_required(self):
        kwargs = self.get_minimal_kwargs()
        kwargs['user'] = None
        spec = BuildUserParams.make_params(**kwargs)

        with pytest.raises(OsbsValidationException):
            spec.validate()

    def test_v2_spec_name2(self):
        git_args = {'git_branch': TEST_GIT_BRANCH}
        kwargs = self.get_minimal_kwargs(git_args=git_args)

        spec = BuildUserParams.make_params(**kwargs)

        assert spec.name.startswith('path-master')

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
            .should_receive('randrange').times(2)
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        spec = BuildUserParams.make_params(**kwargs)

        img_tag = '{user}/{component}:{koji_target}-{random_number}-{time_string}'
        if platform:
            img_tag += '-{platform}'
        img_tag = img_tag.format(random_number=rand, time_string=timestr, **kwargs)
        assert spec.image_tag == img_tag

    def test_user_params_bad_json(self):
        required_json = json.dumps({
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

        with pytest.raises(OsbsValidationException):
            BuildUserParams.make_params(**kwargs)

    def test_user_params_bad_compose_ids(self):
        kwargs = self.get_minimal_kwargs()
        kwargs['compose_ids'] = True

        with pytest.raises(OsbsValidationException):
            BuildUserParams.make_params(**kwargs)

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

        if exc:
            with pytest.raises(exc):
                BuildUserParams.make_params(**kwargs)
        else:
            spec = BuildUserParams.make_params(**kwargs)

            if yum_repourls:
                assert spec.yum_repourls == yum_repourls
            if signing_intent:
                assert spec.signing_intent == signing_intent
            if compose_ids:
                assert spec.compose_ids == compose_ids

    def test_v2_all_values_and_json(self):
        repo_conf = RepoConfiguration(git_branch=TEST_GIT_BRANCH, git_ref=TEST_GIT_REF,
                                      git_uri=TEST_GIT_URI)
        repo_info = RepoInfo(configuration=repo_conf)
        build_conf = Configuration(conf_file=None, scratch=False)
        userdata = {'custom': 'userdata'}

        # all values that BuildUserParams stores
        param_kwargs = {
            'base_image': 'buildroot:old',
            'build_json_dir': INPUTS_PATH,
            'component': TEST_COMPONENT,
            'compose_ids': [1, 2],
            'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
            'flatpak': False,
            # 'flatpak_base_image': self.flatpak_base_image,  # not used with false flatpack
            # 'git_branch': TEST_GIT_BRANCH,
            # 'git_ref': TEST_GIT_REF,
            # 'git_uri': TEST_GIT_URI,
            'image_tag': 'user/None:none-0-0',
            'include_koji_repo': True,
            'isolated': False,
            'koji_parent_build': 'fedora-26-9',
            'koji_target': 'tothepoint',
            'operator_bundle_replacement_pullspecs': {
                'foo/fedora:30': 'bar/fedora@sha256:deadbeef'
            },
            # "orchestrator_deadline": 4,  # set in config
            'parent_images_digests': {
                'registry.fedorahosted.org/fedora:29': {
                    'x86_64': 'registry.fedorahosted.org/fedora@sha256:8b96f2f9f88179a065738b2b37'
                              '35e386efb2534438c2a2f45b74358c0f344c81'
                }
            },
            # 'name': self.name,  # calculated value
            'platform': 'x86_64',
            'platforms': ['x86_64', ],
            # 'reactor_config_map': 'reactor-config-map',  # set in config
            'release': '29',
            # 'scratch': True,  # set in config
            'signing_intent': False,
            'task_id': TEST_KOJI_TASK_ID,
            # 'trigger_imagestreamtag': 'base_image:latest',  # generated from base_image
            'user': TEST_USER,
            'userdata': userdata,
            # 'yum_repourls': ,  # not used with compose_ids
            # "worker_deadline": 3,  # set in config
        }
        # additional values that BuildUserParams requires but stores under different names
        param_kwargs.update({
            'build_conf': build_conf,
            'name_label': 'name_label',
            'repo_info': repo_info,
        })
        rand = '12345'
        timestr = '20170731111111'
        (flexmock(sys.modules['osbs.build.user_params'])
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))
        (flexmock(osbs.utils)
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))

        (flexmock(random)
            .should_receive('randrange').times(2)
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        spec = BuildUserParams.make_params(**param_kwargs)
        expected_json = {
            "base_image": "buildroot:old",
            "build_json_dir": INPUTS_PATH,
            "component": TEST_COMPONENT,
            "compose_ids": [1, 2],
            "filesystem_koji_task_id": TEST_FILESYSTEM_KOJI_TASK_ID,
            "include_koji_repo": True,
            "git_branch": TEST_GIT_BRANCH,
            "git_ref": TEST_GIT_REF,
            "git_uri": TEST_GIT_URI,
            "image_tag": "{}/{}:tothepoint-{}-{}-x86_64".format(TEST_USER, TEST_COMPONENT,
                                                                rand, timestr),
            "kind": "build_user_params",
            "koji_parent_build": "fedora-26-9",
            "koji_target": "tothepoint",
            "name": "path-master-cd1e4" + f'{rand}-{timestr}',
            "namespace": DEFAULT_NAMESPACE,
            'operator_bundle_replacement_pullspecs': {
                'foo/fedora:30': 'bar/fedora@sha256:deadbeef'
            },
            'parent_images_digests': {
                'registry.fedorahosted.org/fedora:29': {
                    'x86_64': 'registry.fedorahosted.org/fedora@sha256:8b96f2f9f88179a065738b2b37'
                              '35e386efb2534438c2a2f45b74358c0f344c81'
                }
            },
            "platform": "x86_64",
            "platforms": ["x86_64"],
            "release": "29",
            "user": TEST_USER,
            "userdata": userdata,
        }
        assert spec.to_json() == json.dumps(expected_json, sort_keys=True)

        spec2 = BuildUserParams.from_json(spec.to_json())
        assert spec2.to_json() == json.dumps(expected_json, sort_keys=True)

    def test_from_json_failure(self, caplog):
        spec = BuildUserParams()
        with pytest.raises(ValueError):
            spec.from_json('{"this is not valid json": }')
        assert 'failed to convert {"this is not valid json": }' in caplog.text

    def test_from_json_continue(self):
        spec = BuildUserParams()
        expected_json = {
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
            "namespace": TEST_OCP_NAMESPACE,
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

    def test_make_params_keeps_defaults(self):
        kwargs = self.get_minimal_kwargs()
        params = BuildUserParams.make_params(**kwargs)

        assert params.include_koji_repo is False


class TestSourceContainerUserParams(object):
    """Tests for source container user params"""

    def get_minimal_kwargs(self, origin_nvr, conf_args=None):
        if not conf_args:
            conf_args = {'build_from': 'image:buildroot:latest'}
        return {
            # Params needed to avoid exceptions.
            'build_json_dir': INPUTS_PATH,
            'build_conf': Configuration(**conf_args),
            'user': TEST_USER,
            'sources_for_koji_build_nvr': origin_nvr,
        }

    def test_validate_missing_required(self):
        """Missing 'sources_for_koji_build_id' and
        `sources_for_koji_build_nvr` params"""
        kwargs = {
            "build_from": "image:buildroot:latest",
            'user': TEST_USER,
        }
        with pytest.raises(OsbsValidationException):
            SourceContainerUserParams.make_params(**kwargs)

    @pytest.mark.parametrize('scratch', [True, False])
    @pytest.mark.parametrize('origin_nvr, origin_id', [
        ('test-1-123', 12345),
        ('test-dashed-nvr-1-123', 12345),
        ('test-dashed-nvr-1-123', None),
    ])
    def test_all_values_and_json(self, scratch, origin_nvr, origin_id):
        conf_args = {
            'namespace': TEST_OCP_NAMESPACE,
            'reactor_config_map_scratch': 'reactor-config-map-scratch',
            'reactor_config_map': 'reactor-config-map',
            'scratch': scratch,
        }
        userdata = {'custom': 'userdata'}
        param_kwargs = self.get_minimal_kwargs(origin_nvr, conf_args=conf_args)
        param_kwargs.update({
            'component': TEST_COMPONENT,
            "koji_target": "tothepoint",
            "platform": "x86_64",
            "signing_intent": "test-signing-intent",
            "sources_for_koji_build_id": origin_id,
            "userdata": userdata,
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

        spec = SourceContainerUserParams.make_params(**param_kwargs)

        expected_json = {
            "build_json_dir": INPUTS_PATH,
            'component': TEST_COMPONENT,
            "image_tag": "{}/{}:tothepoint-{}-{}-x86_64".format(
                TEST_USER, TEST_COMPONENT, rand, timestr),
            "kind": "source_containers_user_params",
            "signing_intent": "test-signing-intent",
            "sources_for_koji_build_nvr": origin_nvr,
            "koji_target": "tothepoint",
            "namespace": TEST_OCP_NAMESPACE,
            "platform": "x86_64",
            'reactor_config_map': 'reactor-config-map',
            "user": TEST_USER,
            "userdata": userdata,
        }
        if scratch:
            expected_json['reactor_config_map'] = 'reactor-config-map-scratch'
            expected_json['scratch'] = scratch

        if origin_id:
            expected_json['sources_for_koji_build_id'] = origin_id

        assert spec.to_json() == json.dumps(expected_json, sort_keys=True)

        spec2 = SourceContainerUserParams.from_json(spec.to_json())
        assert spec2.to_json() == json.dumps(expected_json, sort_keys=True)


@pytest.mark.parametrize('user_params,expected', [
    ({"kind": "build_user_params"}, BuildUserParams),
    ({"kind": "source_containers_user_params"}, SourceContainerUserParams),
])
def test_load_user_params_from_json(user_params, expected):
    user_params_json = json.dumps(user_params)
    user_params_obj = load_user_params_from_json(user_params_json)
    assert isinstance(user_params_obj, expected)
