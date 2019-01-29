"""
Copyright (c) 2015-2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import pytest
from flexmock import flexmock

import datetime
import random
import sys
import json

from osbs.build.user_params import BuildUserParams
from osbs.exceptions import OsbsValidationException
from osbs.constants import BUILD_TYPE_WORKER, REACTOR_CONFIG_ARRANGEMENT_VERSION
from tests.constants import (TEST_COMPONENT, TEST_FILESYSTEM_KOJI_TASK_ID,
                             TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI,
                             TEST_IMAGESTREAM, TEST_KOJI_TASK_ID, TEST_USER)


class TestBuildUserParams(object):

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

        (flexmock(sys.modules['osbs.build.spec'])
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
            'customize_conf': 'prod_customize.json',
            'git_ref': 'master'
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
        }
        # additional values that BuildUserParams requires but stores under different names
        param_kwargs.update({
            'name_label': 'name_label',
        })
        rand = '12345'
        timestr = '20170731111111'
        (flexmock(sys.modules['osbs.build.spec'])
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
            "customize_conf": "prod_customize.json",
            "filesystem_koji_task_id": TEST_FILESYSTEM_KOJI_TASK_ID,
            "git_branch": TEST_GIT_BRANCH,
            "git_ref": TEST_GIT_REF,
            "git_uri": TEST_GIT_URI,
            "image_tag": "{}/{}:tothepoint-{}-{}-x86_64".format(TEST_USER, TEST_COMPONENT,
                                                                rand, timestr),
            "imagestream_name": "name_label",
            "koji_parent_build": "fedora-26-9",
            "koji_target": "tothepoint",
            "name": "path-master-cd1e4",
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
            "user": TEST_USER
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
