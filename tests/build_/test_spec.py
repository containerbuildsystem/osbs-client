"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import pytest
from flexmock import flexmock

from osbs.build.spec import BuildIDParam, RegistryURIsParam, BuildSpec
from osbs.exceptions import OsbsValidationException

import datetime
import random
import sys


class TestBuildIDParam(object):
    def test_build_id_param_shorten_id(self):
        p = BuildIDParam()
        p.value = "x" * 63

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

        assert p.value[0].uri == 'registry.example.com:5000'
        assert p.value[0].docker_uri == 'registry.example.com:5000'
        assert p.value[0].version == 'v1'

    def test_registry_uris_param_v2(self):
        p = RegistryURIsParam()
        p.value = ['registry.example.com:5000/v2']

        assert p.value[0].uri == 'registry.example.com:5000'
        assert p.value[0].docker_uri == 'registry.example.com:5000'
        assert p.value[0].version == 'v2'


class TestBuildSpec(object):

    def get_minimal_kwargs(self):
        return {
            # Params needed to avoid exceptions.
            'user': 'user',
            'base_image': 'base_image',
            'name_label': 'name_label',
            'source_registry_uri': 'source_registry_uri',
            'git_uri': 'https://github.com/user/reponame.git',
            'registry_uris': ['http://registry.example.com:5000/v2'],
            'build_from': 'image:buildroot:latest',
        }

    def test_spec_name(self):
        kwargs = self.get_minimal_kwargs()
        kwargs.update({
            'git_uri': 'https://github.com/user/reponame.git',
            'git_branch': 'master',
        })

        spec = BuildSpec()
        spec.set_params(**kwargs)

        assert spec.name.value.startswith('reponame-master')
        registry = spec.registry_uris.value[0]
        assert registry.uri == 'http://registry.example.com:5000'
        assert registry.docker_uri == 'registry.example.com:5000'
        assert registry.version == 'v2'

    @pytest.mark.parametrize('rand,timestr', [
        ('12345', '20170501123456'),
        ('67890', '20170731111111'),
    ])
    @pytest.mark.parametrize(('platform', 'arrangement', 'has_platform_suffix'), (
        ('x86_64', 4, True),
        ('ppc64le', 4, True),
        ('x86_64', 3, False),
        (None, 3, False),
        (None, 4, False),
    ))
    def test_image_tag(self, rand, timestr, platform, arrangement, has_platform_suffix):
        kwargs = self.get_minimal_kwargs()
        kwargs.update({
            'component': 'foo',
            'koji_target': 'tothepoint',
            'arrangement_version': arrangement,
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

        spec = BuildSpec()
        spec.set_params(**kwargs)

        img_tag = '{user}/{component}:{koji_target}-{random_number}-{time_string}'
        if has_platform_suffix:
            img_tag += '-{platform}'
        img_tag = img_tag.format(random_number=rand, time_string=timestr, **kwargs)
        assert spec.image_tag.value == img_tag

    @pytest.mark.parametrize(('odcs_enabled', 'signing_intent', 'compose_ids',
                              'yum_repourls', 'exc'), (
        (False, 'release', [1, 2], ['http://example.com/my.repo'], OsbsValidationException),
        (False, 'release', [1, 2], None, OsbsValidationException),
        (False, None, [1, 2], ['http://example.com/my.repo'], OsbsValidationException),
        (False, 'release', None, ['http://example.com/my.repo'], OsbsValidationException),
        (False, 'release', None, None, OsbsValidationException),
        (False, None, [1, 2], None, OsbsValidationException),
        (False, None, None, ['http://example.com/my.repo'], None),
        (False, None, None, None, None),
        (True, 'release', [1, 2], ['http://example.com/my.repo'], OsbsValidationException),
        (True, 'release', [1, 2], None, OsbsValidationException),
        (True, None, [1, 2], ['http://example.com/my.repo'], OsbsValidationException),
        (True, 'release', None, ['http://example.com/my.repo'], None),
        (True, 'release', None, None, None),
        (True, None, [1, 2], None, None),
        (True, None, 1, None, OsbsValidationException),
        (True, None, None, ['http://example.com/my.repo'], None),
        (True, None, None, None, None),
    ))
    def test_compose_ids_and_signing_intent(self, odcs_enabled, signing_intent, compose_ids,
                                            yum_repourls, exc):
        kwargs = self.get_minimal_kwargs()
        if odcs_enabled:
            kwargs.update({
                'odcs_url': "http://odcs.com",
            })
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

        spec = BuildSpec()

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
