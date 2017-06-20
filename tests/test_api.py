# -*- coding: utf-8 -*-
"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from types import GeneratorType

from flexmock import flexmock, MethodCallError
from textwrap import dedent
import json
from pkg_resources import parse_version
import os
import pytest
import shutil
import six
import stat
import copy
import datetime
import getpass
import sys
from tempfile import NamedTemporaryFile

from osbs.api import OSBS, osbsapi
from osbs.conf import Configuration
from osbs.build.build_request import BuildRequest
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.build.config_map_response import ConfigMapResponse
from osbs.build.spec import BuildSpec
from osbs.exceptions import OsbsValidationException, OsbsException, OsbsResponseException
from osbs.http import HttpResponse
from osbs.cli.main import cmd_build
from osbs.constants import (DEFAULT_OUTER_TEMPLATE, WORKER_OUTER_TEMPLATE,
                            DEFAULT_INNER_TEMPLATE, WORKER_INNER_TEMPLATE,
                            DEFAULT_CUSTOMIZE_CONF, WORKER_CUSTOMIZE_CONF,
                            ORCHESTRATOR_OUTER_TEMPLATE, ORCHESTRATOR_INNER_TEMPLATE,
                            DEFAULT_ARRANGEMENT_VERSION,
                            ORCHESTRATOR_CUSTOMIZE_CONF)
from osbs import utils

from tests.constants import (TEST_ARCH, TEST_BUILD, TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_TARGET, TEST_USER, INPUTS_PATH,
                             TEST_KOJI_TASK_ID, TEST_VERSION)
from tests.build_.test_build_request import get_sample_prod_params
from osbs.core import Openshift
# These are used as fixtures
from tests.fake_api import openshift, osbs, osbs106, osbs_cant_orchestrate  # noqa
try:
    # py2
    import httplib
except ImportError:
    # py3
    import http.client as httplib


def request_as_response(request):
    """
    Return the request as the response so we can check it
    """

    request.json = request.render()
    return request


class CustomTestException(Exception):
    """
    Custom Exception used to prematurely end function call
    """
    pass


class MockDfParser(object):
    labels = {
        'name': 'fedora23/something',
        'com.redhat.component': TEST_COMPONENT,
        'version': TEST_VERSION,
    }
    baseimage = 'fedora23/python'


class TestOSBS(object):
    def test_osbsapi_wrapper(self):
        """
        Test that a .never() expectation works inside a .raises()
        block.
        """

        (flexmock(utils)
            .should_receive('get_df_parser')
            .never())

        @osbsapi
        def dummy_api_function():
            """A function that calls something it's not supposed to"""
            utils.get_df_parser(TEST_GIT_URI, TEST_GIT_REF)

        # Check we get the correct exception
        with pytest.raises(MethodCallError):
            dummy_api_function()

    @pytest.mark.parametrize('koji_task_id', [None, TEST_KOJI_TASK_ID])  # noqa
    def test_list_builds_api(self, osbs, koji_task_id):
        kwargs = {}
        if koji_task_id:
            kwargs['koji_task_id'] = koji_task_id

        response_list = osbs.list_builds(**kwargs)
        # We should get a response
        assert response_list is not None
        assert len(response_list) > 0
        # response_list is a list of BuildResponse objects
        assert isinstance(response_list[0], BuildResponse)
        # All the timestamps are understood
        for build in response_list:
            assert build.get_time_created_in_seconds() != 0.0

    def test_get_pod_for_build(self, osbs):  # noqa
        pod = osbs.get_pod_for_build(TEST_BUILD)
        assert isinstance(pod, PodResponse)
        images = pod.get_container_image_ids()
        assert isinstance(images, dict)
        assert 'buildroot:latest' in images
        image_id = images['buildroot:latest']
        assert not image_id.startswith("docker:")

    # osbs is a fixture here
    def test_create_build_with_deprecated_params(self, osbs):  # noqa
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'component': TEST_COMPONENT,
            'target': TEST_TARGET,
            'architecture': TEST_ARCH,
            'yum_repourls': None,
            'koji_task_id': None,
            'scratch': False,
            # Stuff that should be ignored and not cause erros
            'labels': {'Release': 'bacon'},
            'spam': 'maps',
        }

        response = osbs.create_build(**kwargs)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize('name_label_name', ['Name', 'name'])  # noqa
    def test_create_prod_build(self, osbs, name_label_name):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {
                name_label_name: 'fedora23/something',
                'com.redhat.component': TEST_COMPONENT,
                'version': TEST_VERSION,
            }
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER,
                                          TEST_COMPONENT, TEST_TARGET, TEST_ARCH)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize(('inner_template', 'outer_template', 'customize_conf'), (  # noqa
        (DEFAULT_INNER_TEMPLATE, DEFAULT_OUTER_TEMPLATE, DEFAULT_CUSTOMIZE_CONF),
        (WORKER_INNER_TEMPLATE.format(
            arrangement_version=DEFAULT_ARRANGEMENT_VERSION),
         WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF),
    ))
    def test_create_prod_build_build_request(self, osbs, inner_template,
                                             outer_template, customize_conf):
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        (flexmock(osbs)
            .should_call('get_build_request')
            .with_args(inner_template=inner_template,
                       outer_template=outer_template,
                       customize_conf=customize_conf)
            .once())
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER,
                                          inner_template=inner_template,
                                          outer_template=outer_template,
                                          customize_conf=customize_conf)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize(('platform', 'release', 'arrangement_version', 'raises_exception'), [  # noqa
        (None, None, None, True),
        ('', '', DEFAULT_ARRANGEMENT_VERSION, True),
        ('spam', None, DEFAULT_ARRANGEMENT_VERSION, True),
        (None, 'bacon', DEFAULT_ARRANGEMENT_VERSION, True),
        ('spam', 'bacon', None, True),
        ('spam', 'bacon', DEFAULT_ARRANGEMENT_VERSION, False),
    ])
    def test_create_worker_build_missing_param(self, osbs, platform, release,
                                               arrangement_version,
                                               raises_exception):
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
        }
        if platform is not None:
            kwargs['platform'] = platform
        if release is not None:
            kwargs['release'] = release
        if arrangement_version is not None:
            kwargs['arrangement_version'] = arrangement_version

        expected_kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'platform': platform,
            'release': release,
            'inner_template': WORKER_INNER_TEMPLATE.format(arrangement_version=arrangement_version),
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
        }

        (flexmock(osbs)
            .should_call('_do_create_prod_build')
            .with_args(**expected_kwargs)
            .times(0 if raises_exception else 1))

        if raises_exception:
            with pytest.raises(OsbsException):
                osbs.create_worker_build(**kwargs)
        else:
            response = osbs.create_worker_build(**kwargs)
            assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize(('inner_template', 'outer_template',  # noqa
                              'customize_conf', 'arrangement_version',
                              'exp_inner_template_if_different'), (
        (WORKER_INNER_TEMPLATE.format(
            arrangement_version=DEFAULT_ARRANGEMENT_VERSION),
         WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF,
         DEFAULT_ARRANGEMENT_VERSION, None),

        (None, WORKER_OUTER_TEMPLATE, None,
         DEFAULT_ARRANGEMENT_VERSION, None),

        (None, WORKER_OUTER_TEMPLATE, None, 1,
         # Expect specified arrangement_version to be used
         WORKER_INNER_TEMPLATE.format(arrangement_version=1)),

        (WORKER_INNER_TEMPLATE.format(
            arrangement_version=DEFAULT_ARRANGEMENT_VERSION),
         None, None,
         DEFAULT_ARRANGEMENT_VERSION, None),

        (None, None, WORKER_CUSTOMIZE_CONF,
         DEFAULT_ARRANGEMENT_VERSION, None),

        (None, None, WORKER_CUSTOMIZE_CONF, 1,
         # Expect specified arrangement_version to be used
         WORKER_INNER_TEMPLATE.format(arrangement_version=1)),

        (None, None, None,
         DEFAULT_ARRANGEMENT_VERSION, None),

        (None, None, None, DEFAULT_ARRANGEMENT_VERSION + 1,
         # Expect specified arrangement_version to be used
         WORKER_INNER_TEMPLATE.format(
             arrangement_version=DEFAULT_ARRANGEMENT_VERSION + 1)),
    ))
    def test_create_worker_build(self, osbs, inner_template, outer_template,
                                 customize_conf, arrangement_version,
                                 exp_inner_template_if_different):
        branch = TEST_GIT_BRANCH
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch)
            .and_return(MockDfParser()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': TEST_USER,
            'platform': 'spam',
            'release': 'bacon',
            'arrangement_version': arrangement_version,
        }
        if branch:
            kwargs['git_branch'] = branch

        expected_kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': branch,
            'user': TEST_USER,
            'platform': kwargs['platform'],
            'release': kwargs['release'],
            'inner_template': WORKER_INNER_TEMPLATE.format(
                arrangement_version=DEFAULT_ARRANGEMENT_VERSION),
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
        }

        if inner_template is not None:
            kwargs['inner_template'] = inner_template
            expected_kwargs['inner_template'] = inner_template
        if outer_template is not None:
            kwargs['outer_template'] = outer_template
            expected_kwargs['outer_template'] = outer_template
        if customize_conf is not None:
            kwargs['customize_conf'] = customize_conf
            expected_kwargs['customize_conf'] = customize_conf

        if exp_inner_template_if_different:
            expected_kwargs['inner_template'] = exp_inner_template_if_different

        (flexmock(osbs)
            .should_receive('_do_create_prod_build')
            .with_args(**expected_kwargs)
            .once())

        osbs.create_worker_build(**kwargs)

    # osbs is a fixture here
    def test_create_worker_build_invalid_arrangement_version(self, osbs):  # noqa
        """
        Test we get OsbsValidationException for an invalid
        arrangement_version value
        """
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        invalid_version = DEFAULT_ARRANGEMENT_VERSION + 1
        with pytest.raises(OsbsValidationException) as ex:
            osbs.create_worker_build(git_uri=TEST_GIT_URI, git_ref=TEST_GIT_REF,
                                     git_branch=TEST_GIT_BRANCH, user=TEST_USER,
                                     platform='spam', release='bacon',
                                     arrangement_version=invalid_version)

        assert 'arrangement_version' in ex.value.message

    # osbs is a fixture here
    def test_create_worker_build_ioerror(self, osbs):  # noqa
        """
        Test IOError raised by create_worker_build with valid arrangement_version is handled correct
         i.e. OsbsException wraps it.
        """
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_raise(IOError))

        with pytest.raises(OsbsException) as ex:
            osbs.create_worker_build(TEST_GIT_URI, TEST_GIT_REF,
                                     TEST_GIT_BRANCH, TEST_USER,
                                     platform='spam', release='bacon',
                                     arrangement_version=DEFAULT_ARRANGEMENT_VERSION)

        assert not isinstance(ex, OsbsValidationException)

    # osbs is a fixture here
    @pytest.mark.parametrize(  # noqa
        ('inner_template_fmt', 'outer_template', 'customize_conf', 'arrangement_version'), (
            (ORCHESTRATOR_INNER_TEMPLATE, ORCHESTRATOR_OUTER_TEMPLATE, ORCHESTRATOR_CUSTOMIZE_CONF,
             None),

            (ORCHESTRATOR_INNER_TEMPLATE, None, None, None),

            (None, ORCHESTRATOR_OUTER_TEMPLATE, None, None),

            (None, None, ORCHESTRATOR_CUSTOMIZE_CONF, None),

            (None, None, None, DEFAULT_ARRANGEMENT_VERSION),

            (None, None, None, None),
        )
    )
    @pytest.mark.parametrize(('platforms', 'raises_exception'), (
        (None, True),
        ([], True),
        (['spam'], False),
        (['spam', 'bacon'], False),
    ))
    def test_create_orchestrator_build(self, osbs, inner_template_fmt,
                                       outer_template, customize_conf,
                                       arrangement_version,
                                       platforms, raises_exception):
        branch = TEST_GIT_BRANCH
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch)
            .and_return(MockDfParser()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': TEST_USER,
        }
        if branch:
            kwargs['git_branch'] = branch
        if platforms is not None:
            kwargs['platforms'] = platforms
        if inner_template_fmt is not None:
            kwargs['inner_template'] = inner_template_fmt.format(
                arrangement_version=arrangement_version or DEFAULT_ARRANGEMENT_VERSION)
        if outer_template is not None:
            kwargs['outer_template'] = outer_template
        if customize_conf is not None:
            kwargs['customize_conf'] = customize_conf

        expected_kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': branch,
            'user': TEST_USER,
            'platforms': platforms,
            'inner_template': ORCHESTRATOR_INNER_TEMPLATE.format(
                arrangement_version=DEFAULT_ARRANGEMENT_VERSION),
            'outer_template': ORCHESTRATOR_OUTER_TEMPLATE,
            'customize_conf': ORCHESTRATOR_CUSTOMIZE_CONF,
            'arrangement_version': DEFAULT_ARRANGEMENT_VERSION,
        }

        (flexmock(osbs)
            .should_receive('_do_create_prod_build')
            .with_args(**expected_kwargs)
            .times(0 if raises_exception else 1)
            .and_return(BuildResponse({})))

        if raises_exception:
            with pytest.raises(OsbsException):
                osbs.create_orchestrator_build(**kwargs)
        else:
            response = osbs.create_orchestrator_build(**kwargs)
            assert isinstance(response, BuildResponse)

    # osbs_cant_orchestrate is a fixture here
    def test_create_orchestrator_build_cant_orchestrate(self, osbs_cant_orchestrate):  # noqa
        """
        Test we get OsbsValidationException when can_orchestrate
        isn't true
        """
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        with pytest.raises(OsbsValidationException) as ex:
            osbs_cant_orchestrate.create_orchestrator_build(
                git_uri=TEST_GIT_URI,
                git_ref=TEST_GIT_REF,
                git_branch=TEST_GIT_BRANCH,
                user=TEST_USER,
                platforms=['spam'],
                arrangement_version=DEFAULT_ARRANGEMENT_VERSION)

        assert 'can\'t create orchestrate build' in ex.value.message

    # osbs is a fixture here
    def test_create_orchestrator_build_invalid_arrangement_version(self, osbs):  # noqa
        """
        Test we get OsbsValidationException for an invalid
        arrangement_version value
        """
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        invalid_version = DEFAULT_ARRANGEMENT_VERSION + 1
        with pytest.raises(OsbsValidationException) as ex:
            osbs.create_orchestrator_build(git_uri=TEST_GIT_URI, git_ref=TEST_GIT_REF,
                                           git_branch=TEST_GIT_BRANCH, user=TEST_USER,
                                           platforms=['spam'],
                                           arrangement_version=invalid_version)

        assert 'arrangement_version' in ex.value.message

    # osbs is a fixture here
    def test_create_prod_build_missing_name_label(self, osbs):  # noqa
        class MockParser(object):
            labels = {}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        with pytest.raises(OsbsValidationException):
            osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                   TEST_GIT_BRANCH, TEST_USER,
                                   TEST_COMPONENT, TEST_TARGET, TEST_ARCH)

    # osbs is a fixture here
    @pytest.mark.parametrize('label_name', ['BZComponent', 'com.redhat.component', 'Name', 'name'])  # noqa
    def test_missing_component_and_name_labels(self, osbs, label_name):
        """
        tests if raises exception if there is only component
        or only name in labels
        """

        class MockParser(object):
            labels = {label_name: 'something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        with pytest.raises(OsbsValidationException):
            osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                   TEST_GIT_BRANCH, TEST_USER,
                                   TEST_COMPONENT, TEST_TARGET, TEST_ARCH)

    # osbs is a fixture here
    @pytest.mark.parametrize('component_label_name', ['com.redhat.component', 'BZComponent'])  # noqa
    def test_component_is_changed_from_label(self, osbs, component_label_name):
        """
        tests if component is changed in create_prod_build
        with value from component label
        """

        class MockParser(object):
            labels = {
                'name': 'fedora23/something',
                component_label_name: TEST_COMPONENT,
                'version': TEST_VERSION,
            }
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        flexmock(OSBS, _create_build_config_and_build=request_as_response)
        req = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                     TEST_GIT_BRANCH, TEST_USER,
                                     TEST_COMPONENT, TEST_TARGET,
                                     TEST_ARCH)
        assert req.spec.component.value == TEST_COMPONENT

    # osbs is a fixture here
    def test_missing_component_argument_doesnt_break_build(self, osbs):  # noqa
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    def test_create_prod_build_set_required_version(self, osbs106):  # noqa
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))
        (flexmock(BuildRequest)
            .should_receive('set_openshift_required_version')
            .with_args(parse_version('1.0.6'))
            .once())
        osbs106.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                  TEST_GIT_BRANCH, TEST_USER,
                                  TEST_COMPONENT, TEST_TARGET,
                                  TEST_ARCH)

    # osbs is a fixture here
    def test_create_prod_with_secret_build(self, osbs):  # noqa
        # TODO: test situation when a buildconfig already exists
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))
        response = osbs.create_prod_with_secret_build(TEST_GIT_URI, TEST_GIT_REF,
                                                      TEST_GIT_BRANCH, TEST_USER,
                                                      TEST_COMPONENT, TEST_TARGET,
                                                      TEST_ARCH)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    def test_create_prod_without_koji_build(self, osbs):  # noqa
        # TODO: test situation when a buildconfig already exists
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))
        response = osbs.create_prod_without_koji_build(TEST_GIT_URI, TEST_GIT_REF,
                                                       TEST_GIT_BRANCH, TEST_USER,
                                                       TEST_COMPONENT, TEST_ARCH)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    def test_wait_for_build_to_finish(self, osbs):  # noqa
        build_response = osbs.wait_for_build_to_finish(TEST_BUILD)
        assert isinstance(build_response, BuildResponse)

    # osbs is a fixture here
    def test_get_build_api(self, osbs):  # noqa
        response = osbs.get_build(TEST_BUILD)
        # We should get a BuildResponse
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize('build_type', (  # noqa
        None,
        'simple',
        'prod',
        'prod-without-koji'
    ))
    def test_get_build_request_api_build_type(self, osbs, build_type):
        """Verify deprecated build_type param behave properly."""
        if build_type:
            build = osbs.get_build_request(build_type)
        else:
            build = osbs.get_build_request()
        assert isinstance(build, BuildRequest)

    # osbs is a fixture here
    @pytest.mark.parametrize(('cpu', 'memory', 'storage', 'set_resource'), (  # noqa
        (None, None, None, False),
        ('spam', None, None, True),
        (None, 'spam', None, True),
        (None, None, 'spam', True),
        ('spam', 'spam', 'spam', True),
    ))
    def test_get_build_request_api(self, osbs, cpu, memory, storage, set_resource):
        inner_template = 'inner.json'
        outer_template = 'outer.json'
        build_json_store = 'build/json/store'

        flexmock(osbs.build_conf).should_receive('get_cpu_limit').and_return(cpu)
        flexmock(osbs.build_conf).should_receive('get_memory_limit').and_return(memory)
        flexmock(osbs.build_conf).should_receive('get_storage_limit').and_return(storage)

        flexmock(osbs.os_conf).should_receive('get_build_json_store').and_return(build_json_store)

        set_resource_limits_kwargs = {
            'cpu': cpu,
            'memory': memory,
            'storage': storage,
        }

        (flexmock(BuildRequest)
            .should_receive('set_resource_limits')
            .with_args(**set_resource_limits_kwargs)
            .times(1 if set_resource else 0))

        get_build_request_kwargs = {
            'inner_template': inner_template,
            'outer_template': outer_template,
        }
        osbs.get_build_request(**get_build_request_kwargs)

    # osbs is a fixture here
    def test_create_build_from_buildrequest(self, osbs):  # noqa
        api_version = osbs.os_conf.get_openshift_api_version()
        build_json = {
            'apiVersion': api_version,
        }
        build_request = flexmock(
            render=lambda: build_json,
            set_openshift_required_version=lambda x: api_version,
            has_ist_trigger=lambda: False,
            scratch=False)
        response = osbs.create_build_from_buildrequest(build_request)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    def test_set_labels_on_build_api(self, osbs):  # noqa
        labels = {'label1': 'value1', 'label2': 'value2'}
        response = osbs.set_labels_on_build(TEST_BUILD, labels)
        assert isinstance(response, HttpResponse)

    # osbs is a fixture here
    def test_set_annotations_on_build_api(self, osbs):  # noqa
        annotations = {'ann1': 'value1', 'ann2': 'value2'}
        response = osbs.set_annotations_on_build(TEST_BUILD, annotations)
        assert isinstance(response, HttpResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize('token', [None, 'token'])  # noqa
    def test_get_token_api(self, osbs, token):
        osbs.os.token = token
        if token:
            assert isinstance(osbs.get_token(), six.string_types)
            assert token == osbs.get_token()
        else:
            with pytest.raises(OsbsValidationException):
                osbs.get_token()

    # osbs is a fixture here
    def test_get_token_api_kerberos(self, osbs):  # noqa
        token = "token"
        osbs.os.use_kerberos = True
        (flexmock(Openshift)
            .should_receive('get_oauth_token')
            .and_return(token))

        assert token == osbs.get_token()

    # osbs is a fixture here
    def test_get_user_api(self, osbs):  # noqa
        assert 'name' in osbs.get_user()['metadata']

    # osbs is a fixture here
    @pytest.mark.parametrize(('token', 'username', 'password'), (  # noqa
        (None, None, None),
        ('token', None, None),
        (None, 'username', None),
        (None, None, 'password'),
        ('token', 'username', None),
        ('token', None, 'password'),
        (None, 'username', 'password'),
        ('token', 'username', 'password'),
    ))
    @pytest.mark.parametrize('subdir', [None, 'new-dir'])
    @pytest.mark.parametrize('not_valid', [True, False])
    def test_login_api(self, tmpdir, osbs, token, username, password, subdir, not_valid):
        token_file_dir = str(tmpdir)
        if subdir:
            token_file_dir = os.path.join(token_file_dir, subdir)
        token_file_path = os.path.join(token_file_dir, 'test-token')

        (flexmock(utils)
            .should_receive('get_instance_token_file_name')
            .with_args(osbs.os_conf.conf_section)
            .and_return(token_file_path))

        class TestResponse:
            status_code = httplib.UNAUTHORIZED

        if not token:
            (flexmock(Openshift)
                .should_receive('get_oauth_token')
                .once()
                .and_return("token"))
            if not password:
                (flexmock(getpass)
                    .should_receive('getpass')
                    .once()
                    .and_return("password"))
            if not username:
                try:
                    (flexmock(sys.modules['__builtin__'])
                        .should_receive('raw_input')
                        .once()
                        .and_return('username'))
                except:
                    (flexmock(sys.modules['builtins'])
                        .should_receive('input')
                        .once()
                        .and_return('username'))

        if not_valid:
            (flexmock(osbs.os)
                .should_receive('get_user')
                .once()
                .and_raise(OsbsResponseException('Unauthorized',
                                                 status_code=401)))

            with pytest.raises(OsbsValidationException):
                osbs.login(token, username, password)

        else:
            osbs.login(token, username, password)

            if not token:
                token = "token"

            with open(token_file_path) as token_file:
                assert token == token_file.read().strip()

            file_mode = os.stat(token_file_path).st_mode
            # File owner permission
            assert file_mode & stat.S_IRWXU
            # Group permission
            assert not file_mode & stat.S_IRWXG
            # Others permission
            assert not file_mode & stat.S_IRWXO

    # osbs is a fixture here
    def test_login_api_kerberos(self, osbs):  # noqa
        osbs.os.use_kerberos = True
        with pytest.raises(OsbsValidationException):
            osbs.login("", "", "")

    # osbs is a fixture here
    def test_build_logs_api(self, osbs):  # noqa
        logs = osbs.get_build_logs(TEST_BUILD)
        assert isinstance(logs, six.binary_type)
        assert logs == u"líne 1".encode('utf-8')

    # osbs is a fixture here
    def test_build_logs_api_follow(self, osbs):  # noqa
        logs = osbs.get_build_logs(TEST_BUILD, follow=True)
        assert isinstance(logs, GeneratorType)
        content = next(logs)
        assert isinstance(content, six.binary_type)
        assert content == u"líne 1".encode('utf-8')
        with pytest.raises(StopIteration):
            assert next(logs)

    # osbs is a fixture here
    def test_pause_builds(self, osbs):  # noqa
        osbs.pause_builds()

    # osbs is a fixture here
    def test_resume_builds(self, osbs):  # noqa
        osbs.resume_builds()

    # osbs is a fixture here
    @pytest.mark.parametrize('decode_docker_logs', [True, False])  # noqa
    def test_build_logs_api_from_docker(self, osbs, decode_docker_logs):
        logs = osbs.get_docker_build_logs(TEST_BUILD, decode_logs=decode_docker_logs)
        assert isinstance(logs, tuple(list(six.string_types) + [bytes]))
        assert logs.split('\n')[0].find("Step ") != -1

    # osbs is a fixture here
    def test_backup(self, osbs):  # noqa
        osbs.dump_resource("builds")

    # osbs is a fixture here
    def test_restore(self, osbs):  # noqa
        build = {
            "status": {
                "phase": "Complete",
                "completionTimestamp": "2015-09-16T19:37:35Z",
                "startTimestamp": "2015-09-16T19:25:55Z",
                "duration": 700000000000
            },
            "spec": {},
            "metadata": {
                "name": "aos-f5-router-docker-20150916-152551",
                "namespace": "default",
                "resourceVersion": "141714",
                "creationTimestamp": "2015-09-16T19:25:52Z",
                "selfLink":
                    "/oapi/v1/namespaces/default/builds/aos-f5-router-docker-20150916-152551",
                "uid": "be5dbec5-5ca8-11e5-af58-6cae8b5467ca"
            }
        }
        osbs.restore_resource("builds", {"items": [build], "kind": "BuildList", "apiVersion": "v1"})

    @pytest.mark.parametrize(('compress', 'args', 'raises', 'expected'), [
        # compress plugin not run
        (False, None, None, None),

        # run with no args
        (True, {}, None, '.gz'),
        (True, {'args': {}}, None, '.gz'),

        # run with args
        (True, {'args': {'method': 'gzip'}}, None, '.gz'),
        (True, {'args': {'method': 'lzma'}}, None, '.xz'),

        # run with method not known to us
        (True, {'args': {'method': 'unknown'}}, OsbsValidationException, None),
    ])
    def test_get_compression_extension(self, tmpdir, compress, args,
                                       raises, expected):
        # Make temporary copies of the JSON files
        for basename in [DEFAULT_OUTER_TEMPLATE, DEFAULT_INNER_TEMPLATE]:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(str(tmpdir), basename))

        # Create an inner JSON description with the specified compress
        # plugin method
        with open(os.path.join(str(tmpdir), DEFAULT_INNER_TEMPLATE),
                  'r+') as inner:
            inner_json = json.load(inner)

            postbuild_plugins = inner_json['postbuild_plugins']
            inner_json['postbuild_plugins'] = [plugin
                                               for plugin in postbuild_plugins
                                               if plugin['name'] != 'compress']

            if compress:
                plugin = {'name': 'compress'}
                plugin.update(args)
                inner_json['postbuild_plugins'].insert(0, plugin)

            inner.seek(0)
            json.dump(inner_json, inner)
            inner.truncate()

        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                [default]
                openshift_url = /
                registry_uri = registry.example.com
                """.format(build_json_dir=str(tmpdir))))
            fp.flush()
            config = Configuration(fp.name)
            osbs_obj = OSBS(config, config)

        if raises:
            with pytest.raises(raises):
                osbs_obj.get_compression_extension()
        else:
            assert osbs_obj.get_compression_extension() == expected

    @pytest.mark.parametrize(('build_image', 'build_imagestream', 'valid'), (
        ('registry.example.com/buildroot:2.0', '', True),
        ('', 'buildroot-stream:v1.0', True),
        ('registry.example.com/buildroot:2.0', 'buildroot-stream:v1.0', False)
    ))
    def test_build_image(self, build_image, build_imagestream, valid):
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                [default]
                openshift_url = /
                sources_command = /bin/true
                vendor = Example, Inc
                registry_uri = registry.example.com
                build_host = localhost
                authoritative_registry = localhost
                distribution_scope = private
                build_image = {build_image}
                build_imagestream = {build_imagestream}
                """.format(build_json_dir='inputs', build_image=build_image,
                           build_imagestream=build_imagestream)))
            fp.flush()
            config = Configuration(fp.name)
            osbs_obj = OSBS(config, config)

        assert config.get_build_image() == build_image
        assert config.get_build_imagestream() == build_imagestream

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        flexmock(OSBS, _create_build_config_and_build=request_as_response)

        if valid:
            req = osbs_obj.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                             TEST_GIT_BRANCH, TEST_USER,
                                             TEST_COMPONENT, TEST_TARGET,
                                             TEST_ARCH)
        else:
            with pytest.raises(OsbsValidationException):
                req = osbs_obj.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                                 TEST_GIT_BRANCH, TEST_USER,
                                                 TEST_COMPONENT, TEST_TARGET,
                                                 TEST_ARCH)
            return

        img = req.json['spec']['strategy']['customStrategy']['from']['name']
        kind = req.json['spec']['strategy']['customStrategy']['from']['kind']

        if build_image:
            assert kind == 'DockerImage'
            assert img == build_image

        if build_imagestream:
            assert kind == 'ImageStreamTag'
            assert img == build_imagestream

    def test_worker_build_image_with_platform_node(self):
        build_image = 'registry.example.com/buildroot:2.0'
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                [default]
                openshift_url = /
                sources_command = /bin/true
                vendor = Example, Inc
                registry_uri = registry.example.com
                build_host = localhost
                authoritative_registry = localhost
                distribution_scope = private
                node_selector.meal = breakfast=bacon.com, lunch=ham.com
                build_image = {build_image}
                """.format(build_json_dir='inputs', build_image=build_image)))
            fp.flush()
            config = Configuration(fp.name)
            osbs_obj = OSBS(config, config)

        assert config.get_build_image() == build_image

        arrangement = DEFAULT_ARRANGEMENT_VERSION
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'platform': 'meal',
            'release': 'bacon',
            'arrangement_version': arrangement,
            'inner_template': WORKER_INNER_TEMPLATE.format(arrangement_version=arrangement),
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
        }

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        flexmock(OSBS, _create_build_config_and_build=request_as_response)

        req = osbs_obj.create_worker_build(**kwargs)
        img = req.json['spec']['strategy']['customStrategy']['from']['name']
        assert img == build_image
        node_selector = req.json['spec']['nodeSelector']
        assert node_selector == {'breakfast': 'bacon.com', 'lunch': 'ham.com'}

    def test_get_existing_build_config_by_labels(self):
        build_config = {
            'metadata': {
                'name': 'name',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                }
            },
        }

        existing_build_config = copy.deepcopy(build_config)
        existing_build_config['_from'] = 'from-labels'

        config = Configuration()
        osbs_obj = OSBS(config, config)

        (flexmock(osbs_obj.os)
            .should_receive('get_build_config_by_labels')
            .with_args([('git-repo-name', 'reponame'), ('git-branch', 'branch')])
            .once()
            .and_return(existing_build_config))
        (flexmock(osbs_obj.os)
            .should_receive('get_build_config')
            .never())

        actual_build_config = osbs_obj._get_existing_build_config(build_config)
        assert actual_build_config == existing_build_config
        assert actual_build_config['_from'] == 'from-labels'

    def test_get_existing_build_config_by_name(self):
        build_config = {
            'metadata': {
                'name': 'name',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                }
            },
        }

        existing_build_config = copy.deepcopy(build_config)
        existing_build_config['_from'] = 'from-name'

        config = Configuration()
        osbs_obj = OSBS(config, config)

        (flexmock(osbs_obj.os)
            .should_receive('get_build_config_by_labels')
            .with_args([('git-repo-name', 'reponame'), ('git-branch', 'branch')])
            .once()
            .and_raise(OsbsException))
        (flexmock(osbs_obj.os)
            .should_receive('get_build_config')
            .with_args('name')
            .once()
            .and_return(existing_build_config))

        actual_build_config = osbs_obj._get_existing_build_config(build_config)
        assert actual_build_config == existing_build_config
        assert actual_build_config['_from'] == 'from-name'

    def test_get_existing_build_config_missing(self):
        build_config = {
            'metadata': {
                'name': 'name',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                }
            },
        }
        config = Configuration()
        osbs_obj = OSBS(config, config)

        (flexmock(osbs_obj.os)
            .should_receive('get_build_config_by_labels')
            .with_args([('git-repo-name', 'reponame'), ('git-branch', 'branch')])
            .once()
            .and_raise(OsbsException))
        (flexmock(osbs_obj.os)
            .should_receive('get_build_config')
            .with_args('name')
            .once()
            .and_raise(OsbsException))

        assert osbs_obj._get_existing_build_config(build_config) is None

    def test_verify_no_running_builds_zero(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        (flexmock(osbs_obj)
            .should_receive('_get_running_builds_for_build_config')
            .with_args('build_config_name')
            .once()
            .and_return([]))

        osbs_obj._verify_no_running_builds('build_config_name')

    def test_verify_no_running_builds_one(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        (flexmock(osbs_obj)
            .should_receive('_get_running_builds_for_build_config')
            .with_args('build_config_name')
            .once()
            .and_return([
                flexmock(status='Running', get_build_name=lambda: 'build-1'),
            ]))

        with pytest.raises(OsbsException) as exc:
            osbs_obj._verify_no_running_builds('build_config_name')
        assert str(exc.value).startswith('Build build-1 for build_config_name')

    def test_verify_no_running_builds_many(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        (flexmock(osbs_obj)
            .should_receive('_get_running_builds_for_build_config')
            .with_args('build_config_name')
            .once()
            .and_return([
                flexmock(status='Running', get_build_name=lambda: 'build-1'),
                flexmock(status='Running', get_build_name=lambda: 'build-2'),
            ]))

        with pytest.raises(OsbsException) as exc:
            osbs_obj._verify_no_running_builds('build_config_name')
        assert str(exc.value).startswith('Multiple builds for')

    def test_create_build_config_bad_version(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)
        build_json = {'apiVersion': 'spam'}
        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

        with pytest.raises(OsbsValidationException):
            osbs_obj._create_build_config_and_build(build_request)

    def test_create_build_config_label_mismatch(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        build_json = {
            'apiVersion': osbs_obj.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {},
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'build'
        existing_build_json['metadata']['labels']['git-repo-name'] = 'reponame2'
        existing_build_json['metadata']['labels']['git-branch'] = 'branch2'

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

        spec = BuildSpec()
        # Params needed to avoid exceptions.
        spec.set_params(
            user='user',
            base_image='fedora23/python',
            name_label='name_label',
            source_registry_uri='source_registry_uri',
            git_uri='https://github.com/user/reponame.git',
            registry_uris=['http://registry.example.com:5000/v2'],
        )
        # Cannot use spec keyword arg in flexmock constructor
        # because it appears to be used by flexmock itself
        build_request.spec = spec

        (flexmock(osbs_obj)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(existing_build_json))

        with pytest.raises(OsbsValidationException) as exc:
            osbs_obj._create_build_config_and_build(build_request)

        assert 'Git labels collide' in str(exc.value)

    def test_create_build_config_already_running(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        build_json = {
            'apiVersion': osbs_obj.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {},
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'existing-build'

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)
        spec = BuildSpec()
        # Params needed to avoid exceptions.
        spec.set_params(
            user='user',
            base_image='fedora23/python',
            name_label='name_label',
            source_registry_uri='source_registry_uri',
            git_uri='https://github.com/user/reponame.git',
            registry_uris=['http://registry.example.com:5000/v2'],
        )
        # Cannot use spec keyword arg in flexmock constructor
        # because it appears to be used by flexmock itself
        build_request.spec = spec

        (flexmock(osbs_obj)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(existing_build_json))

        (flexmock(osbs_obj)
            .should_receive('_get_running_builds_for_build_config')
            .once()
            .and_return([
                flexmock(status='Running', get_build_name=lambda: 'build-1'),
            ]))

        with pytest.raises(OsbsException):
            osbs_obj._create_build_config_and_build(build_request)

    def test_create_build_config_update(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        build_json = {
            'apiVersion': osbs_obj.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {},
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'existing-build'
        existing_build_json['metadata']['labels']['new-label'] = 'new-value'

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

        spec = BuildSpec()
        # Params needed to avoid exceptions.
        spec.set_params(
            user='user',
            base_image='fedora23/python',
            name_label='name_label',
            source_registry_uri='source_registry_uri',
            git_uri='https://github.com/user/reponame.git',
            registry_uris=['http://registry.example.com:5000/v2'],
        )
        # Cannot use spec keyword arg in flexmock constructor
        # because it appears to be used by flexmock itself
        build_request.spec = spec

        (flexmock(osbs_obj)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(existing_build_json))

        (flexmock(osbs_obj)
            .should_receive('_get_running_builds_for_build_config')
            .once()
            .and_return([]))

        (flexmock(osbs_obj.os)
            .should_receive('update_build_config')
            .with_args('existing-build', json.dumps(existing_build_json))
            .once())

        (flexmock(osbs_obj.os)
            .should_receive('start_build')
            .with_args('existing-build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs_obj._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    def test_create_build_config_create(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        build_json = {
            'apiVersion': osbs_obj.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {},
        }

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

        spec = BuildSpec()
        # Params needed to avoid exceptions.
        spec.set_params(
            user='user',
            base_image='fedora23/python',
            name_label='name_label',
            source_registry_uri='source_registry_uri',
            git_uri='https://github.com/user/reponame.git',
            registry_uris=['http://registry.example.com:5000/v2'],
        )
        # Cannot use spec keyword arg in flexmock constructor
        # because it appears to be used by flexmock itself
        build_request.spec = spec

        (flexmock(osbs_obj)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(None))

        (flexmock(osbs_obj.os)
            .should_receive('create_build_config')
            .with_args(json.dumps(build_json))
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps', 'spec': {}})))

        (flexmock(osbs_obj.os)
            .should_receive('start_build')
            .with_args('build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs_obj._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    @pytest.mark.parametrize('existing_bc', [True, False])
    @pytest.mark.parametrize('existing_is', [True, False])
    @pytest.mark.parametrize('existing_ist', [True, False])
    def test_create_build_config_auto_start(self, existing_bc, existing_is,
                                            existing_ist):
        # If ImageStream exists, always expect auto instantiated
        # build, because this test assumes an auto_instantiated BuildRequest
        expect_auto = existing_is
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = inputs
                """))
            fp.flush()
            config = Configuration(fp.name)

        osbs_obj = OSBS(config, config)

        build_json = {
            'apiVersion': 'v1',
            'kind': 'Build',
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {
                'triggers': [{'name': 'new_trigger'}]
            },
        }

        build_config_json = {
            'apiVersion': 'v1',
            'kind': 'BuildConfig',
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {
                'triggers': [{'name': 'old_trigger'}]
            },
            'status': {'lastVersion': 'lastVersion'},
        }

        image_stream_json = {'apiVersion': 'v', 'kind': 'ImageStream'}
        image_stream_tag_json = {'apiVersion': 'v1', 'kind': 'ImageStreamTag'}

        spec = BuildSpec()
        # Params needed to avoid exceptions.
        spec.set_params(
            user='user',
            base_image='fedora23/python',
            name_label='name_label',
            source_registry_uri='source_registry_uri',
            git_uri='https://github.com/user/reponame.git',
            registry_uris=['http://registry.example.com:5000/v2'],
        )

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: True,
            scratch=False)
        # Cannot use spec keyword arg in flexmock constructor
        # because it appears to be used by flexmock itself
        build_request.spec = spec
        build_config_created = False

        def mock_get_existing_build_config(*args, **kwargs):
            if existing_bc or build_config_created:
                return build_config_json
            else:
                return None
        (flexmock(osbs_obj)
            .should_receive('_get_existing_build_config')
            .once()
            .replace_with(mock_get_existing_build_config))

        def mock_get_image_stream(*args, **kwargs):
            if not existing_is:
                raise OsbsResponseException('missing ImageStream',
                                            status_code=404)

            return flexmock(json=lambda: image_stream_json)
        (flexmock(osbs_obj.os)
            .should_receive('get_image_stream')
            .with_args('fedora23-python')
            .once()
            .replace_with(mock_get_image_stream))

        if existing_is:
            def mock_get_image_stream_tag(*args, **kwargs):
                if not existing_ist:
                    raise OsbsResponseException('missing ImageStreamTag',
                                                status_code=404)
                return flexmock(json=lambda: image_stream_tag_json)
            (flexmock(osbs_obj.os)
                .should_receive('get_image_stream_tag')
                .with_args('fedora23-python:latest')
                .once()
                .replace_with(mock_get_image_stream_tag))

            (flexmock(osbs_obj.os)
                .should_receive('ensure_image_stream_tag')
                .with_args(image_stream_json, 'latest', dict, True)
                .once()
                .and_return(True))

        update_build_config_times = 0

        if existing_bc:
            (flexmock(osbs_obj.os)
                .should_receive('list_builds')
                .with_args(build_config_id='build')
                .once()
                .and_return(flexmock(json=lambda: {'items': []})))
            update_build_config_times += 1
        else:
            temp_build_json = copy.deepcopy(build_json)
            temp_build_json['spec'].pop('triggers', None)
            global build_config_created

            def mock_create_build_config(encoded_build_json):
                global build_config_created
                build_config_created = True
                assert json.loads(encoded_build_json) == temp_build_json
                return flexmock(json=lambda: build_config_json)
            (flexmock(osbs_obj.os)
                .should_receive('create_build_config')
                .replace_with(mock_create_build_config)
                .once())

        (flexmock(osbs_obj.os)
            .should_receive('update_build_config')
            .with_args('build', str)
            .times(update_build_config_times))
        (flexmock(osbs_obj.os)
            .should_receive('patch_build_config')
            .with_args('build', str))
        if expect_auto:
            (flexmock(osbs_obj.os)
                .should_receive('wait_for_new_build_config_instance')
                .with_args('build', 'lastVersion')
                .once()
                .and_return('build-id'))

            (flexmock(osbs_obj.os)
                .should_receive('get_build')
                .with_args('build-id')
                .once()
                .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        else:
            (flexmock(osbs_obj.os)
                .should_receive('start_build')
                .with_args('build')
                .once()
                .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs_obj._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    @pytest.mark.parametrize(('kind', 'expect_name'), [
        ('ImageStreamTag', 'registry:5000/buildroot:latest'),
        ('DockerImage', 'buildroot:latest'),
    ])
    def test_scratch_build_config(self, kind, expect_name):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        build_json = {
            'apiVersion': osbs_obj.os_conf.get_openshift_api_version(),

            'metadata': {
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },

            'spec': {
                'strategy': {
                    'customStrategy': {
                        'from': {
                            'kind': kind,
                            'name': 'buildroot:latest',
                        },
                    },
                },
                'output': {
                    'to': {
                        'name': 'cindarella/foo:bar-12345-20001010112233'
                    }
                }
            },
        }

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=True,
            low_priority_node_selector=None)

        updated_build_json = copy.deepcopy(build_json)
        updated_build_json['kind'] = 'Build'
        updated_build_json['metadata']['labels']['scratch'] = 'true'
        updated_build_json['spec']['serviceAccount'] = 'builder'
        img = updated_build_json['spec']['strategy']['customStrategy']['from']
        img['kind'] = 'DockerImage'
        img['name'] = expect_name
        updated_build_json['metadata']['name'] = 'scratch-%s' % (
            build_json['spec']['output']['to']['name'][-20:])

        if kind == 'ImageStreamTag':
            (flexmock(osbs_obj.os)
                .should_receive('get_image_stream_tag')
                .with_args('buildroot:latest')
                .once()
                .and_return(flexmock(json=lambda: {
                    "apiVersion": "v1",
                    "kind": "ImageStreamTag",
                    "image": {
                        "dockerImageReference": expect_name,
                    },
                })))
        else:
            (flexmock(osbs_obj.os)
                .should_receive('get_image_stream_tag')
                .never())

        def verify_build_json(passed_build_json):
            assert passed_build_json == updated_build_json
            return flexmock(json=lambda: {'spam': 'maps'})

        (flexmock(osbs_obj.os)
            .should_receive('create_build')
            .replace_with(verify_build_json)
            .once())

        (flexmock(osbs_obj.os)
            .should_receive('create_build_config')
            .never())

        (flexmock(osbs_obj.os)
            .should_receive('update_build_config')
            .never())

        build_response = osbs_obj._create_scratch_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    @pytest.mark.parametrize(('platform', 'platform_nodeselector', 'platform_nodeselector_dict',
                              'lowpriority_nodeselector', 'lowpriority_nodeselector_dict'), [
        (None, '', {}, None, {}),
        (None, '', {}, 'foo=  bar', {'foo': 'bar'}),
        (None, '', {}, 'foo=bar ,  baz=foo', {'foo': 'bar', 'baz': 'foo'}),
        ('meal', 'breakfast=bacon.com, lunch=ham.com',
         {'breakfast': 'bacon.com', 'lunch': 'ham.com'}, None, {}),
        ('meal', 'breakfast=bacon.com, lunch=ham.com',
         {'breakfast': 'bacon.com', 'lunch': 'ham.com'}, 'foo=  bar', {'foo': 'bar'}),
        ('meal', 'breakfast=bacon.com, lunch=ham.com',
         {'breakfast': 'bacon.com', 'lunch': 'ham.com'}, 'foo=bar ,  baz=foo',
         {'foo': 'bar', 'baz': 'foo'}),
    ])
    def test_scratch_build_nodeselector(self, platform, platform_nodeselector,
                                        platform_nodeselector_dict,
                                        lowpriority_nodeselector, lowpriority_nodeselector_dict):
        if platform or lowpriority_nodeselector:
            with NamedTemporaryFile(mode='wt') as fp:
                fp.write(dedent("""\
                    [general]
                    build_json_dir = {inputs}
                    [default]
                    """.format(inputs=INPUTS_PATH)))
                if platform:
                    fp.write(dedent("""\
                        node_selector.{platform_str} = {platform_nodeselector_str}
                    """.format(platform_str=platform,
                               platform_nodeselector_str=platform_nodeselector)))
                if lowpriority_nodeselector:
                    fp.write(dedent("""\
                        low_priority_node_selector = {lowpriority_nodeselector_str}
                    """.format(lowpriority_nodeselector_str=lowpriority_nodeselector)))
                fp.flush()
                config = Configuration(fp.name)
        else:
            config = Configuration()
        osbs_obj = OSBS(config, config)

        build_request = BuildRequest(INPUTS_PATH)
        kwargs = get_sample_prod_params()
        kwargs['scratch'] = True
        kwargs['low_priority_node_selector'] = config.get_low_priority_node_selector()
        if platform:
            kwargs['platform_node_selector'] = config.get_platform_node_selector(platform)
        build_request.set_params(**kwargs)
        build_request_copy = copy.deepcopy(build_request)
        build_json = build_request_copy.render()

        updated_build_json = copy.deepcopy(build_json)
        updated_build_json['kind'] = 'Build'
        updated_build_json['metadata']['labels']['scratch'] = 'true'
        updated_build_json['spec']['serviceAccount'] = 'builder'
        if lowpriority_nodeselector_dict:
            updated_build_json['spec']['nodeSelector'].update(lowpriority_nodeselector_dict)
        build_name = 'scratch-%s' % datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        updated_build_json['metadata']['name'] = build_name

        def verify_build_json(passed_build_json):
            # Don't compare metadata.name directly as we can't control it
            assert (isinstance(passed_build_json['metadata'].pop('name'),
                               six.string_types))
            del updated_build_json['metadata']['name']

            assert passed_build_json == updated_build_json
            if platform or lowpriority_nodeselector:
                valid_dict = platform_nodeselector_dict
                valid_dict.update(lowpriority_nodeselector_dict)
                assert passed_build_json['spec']['nodeSelector'] == valid_dict
            return flexmock(json=lambda: {'spam': 'maps'})

        (flexmock(osbs_obj.os)
            .should_receive('create_build')
            .replace_with(verify_build_json)
            .once())

        (flexmock(osbs_obj.os)
            .should_receive('create_build_config')
            .never())

        (flexmock(osbs_obj.os)
            .should_receive('update_build_config')
            .never())

        build_response = osbs_obj._create_scratch_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    def test_scratch_param_to_create_build(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'component': TEST_COMPONENT,
            'target': TEST_TARGET,
            'architecture': TEST_ARCH,
            'yum_repourls': None,
            'koji_task_id': None,
            'scratch': True,
        }

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        (flexmock(osbs_obj)
            .should_receive('_create_scratch_build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        (flexmock(osbs_obj.os)
            .should_receive('create_build_config')
            .never())

        (flexmock(osbs_obj.os)
            .should_receive('update_build_config')
            .never())

        build_response = osbs_obj.create_build(**kwargs)
        assert build_response.json() == {'spam': 'maps'}

    def test_get_image_stream_tag(self):
        config = Configuration()
        osbs_obj = OSBS(config, config)

        name = 'buildroot:latest'
        (flexmock(osbs_obj.os)
            .should_receive('get_image_stream_tag')
            .with_args(name)
            .once()
            .and_return(flexmock(json=lambda: {
                'image': {
                    'dockerImageReference': 'spam:maps',
                }
            })))

        response = osbs_obj.get_image_stream_tag(name)
        ref = response.json()['image']['dockerImageReference']
        assert ref == 'spam:maps'

    def test_ensure_image_stream_tag(self):
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                """.format(build_json_dir='inputs')))
            fp.flush()
            config = Configuration(fp.name)
            osbs_obj = OSBS(config, config)

        stream = {'type': 'stream'}
        tag_name = 'latest'
        scheduled = False
        (flexmock(osbs_obj.os)
            .should_receive('ensure_image_stream_tag')
            .with_args(stream, tag_name, dict, scheduled)
            .once()
            .and_return('eggs'))

        response = osbs_obj.ensure_image_stream_tag(stream, tag_name, scheduled)
        assert response == 'eggs'

    def test_reactor_config_secret(self):
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = inputs
                [default]
                openshift_url = /
                sources_command = /bin/true
                vendor = Example, Inc
                authoritative_registry = localhost
                reactor_config_secret = mysecret
                """))
            fp.flush()
            config = Configuration(fp.name)
            osbs_obj = OSBS(config, config)

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockDfParser()))

        flexmock(OSBS, _create_build_config_and_build=request_as_response)

        req = osbs_obj.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                         TEST_GIT_BRANCH, TEST_USER,
                                         TEST_COMPONENT, TEST_TARGET,
                                         TEST_ARCH)
        secrets = req.json['spec']['strategy']['customStrategy']['secrets']
        expected_secret = {
            'mountPath': '/var/run/secrets/atomic-reactor/mysecret',
            'secretSource': {
                'name': 'mysecret',
            }
        }
        assert expected_secret in secrets

    @pytest.mark.parametrize(('platform', 'release', 'platforms',  # noqa
                              'worker', 'orchestrator',
                              'arrangement_version', 'raises_exception'), [
        # worker build
        ("plat", 'rel', None, True, False, 1, False),
        ("plat", 'rel', None, True, False, None, True),
        # orchestrator build
        (None, None, 'platforms', False, True, 1, False),
        (None, None, 'platforms', False, True, None, False),
        # prod build
        (None, None, None, False, False, 1, False),
        (None, None, None, False, False, None, False),
    ])
    def test_arrangement_version(self, osbs, platform, release, platforms,
                                 worker, orchestrator,
                                 arrangement_version, raises_exception):
        class MockArgs(object):
            def __init__(self, platform, release, platforms, arrangement_version,
                         worker, orchestrator):
                self.platform = platform
                self.release = release
                self.platforms = platforms
                self.arrangement_version = arrangement_version
                self.worker = worker
                self.orchestrator = orchestrator
                self.scratch = None

        expected_kwargs = {
            'platform': platform,
            'scratch': None,
            'platforms': platforms,
            'release': release,
            'git_uri': None,
            'git_ref': None,
            'git_branch': None,
            'user': None,
            'tag': None,
            'target': None,
            'architecture': None,
            'yum_repourls': None,
        }
        if arrangement_version:
            expected_kwargs['arrangement_version'] = arrangement_version

        if not raises_exception:
            # and_raise is called to prevent cmd_build to continue
            # as we only want to check if arguments are correct
            if worker:
                (flexmock(osbs)
                    .should_receive("create_worker_build")
                    .once()
                    .with_args(**expected_kwargs)
                    .and_raise(CustomTestException))

            if orchestrator:
                (flexmock(osbs)
                    .should_receive("create_orchestrator_build")
                    .once()
                    .with_args(**expected_kwargs)
                    .and_raise(CustomTestException))

            if not worker and not orchestrator:
                (flexmock(osbs)
                    .should_receive("create_prod_build")
                    .once()
                    .with_args(**expected_kwargs)
                    .and_raise(CustomTestException))

        if raises_exception:
            with pytest.raises(OsbsException):
                cmd_build(MockArgs(platform, release, platforms, arrangement_version,
                          worker, orchestrator), osbs)
        else:
            with pytest.raises(CustomTestException):
                cmd_build(MockArgs(platform, release, platforms, arrangement_version,
                                   worker, orchestrator), osbs)

    # osbs is a fixture here
    @pytest.mark.parametrize('branch_name', [  # noqa
        TEST_GIT_BRANCH,
        '',
        None
    ])
    def test_do_create_prod_build_branch_required(self, osbs, branch_name):
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch_name)
            .and_return(MockDfParser()))

        inner_template = DEFAULT_INNER_TEMPLATE
        outer_template = DEFAULT_OUTER_TEMPLATE
        customize_conf = DEFAULT_CUSTOMIZE_CONF

        (flexmock(osbs)
            .should_call('get_build_request')
            .with_args(inner_template=inner_template,
                       outer_template=outer_template,
                       customize_conf=customize_conf)
            .once())

        if branch_name:
            response = osbs._do_create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                                  branch_name, TEST_USER,
                                                  inner_template=inner_template,
                                                  outer_template=outer_template,
                                                  customize_conf=customize_conf)
            assert isinstance(response, BuildResponse)
        else:
            with pytest.raises(OsbsException):
                osbs._do_create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                           branch_name, TEST_USER,
                                           inner_template=inner_template,
                                           outer_template=outer_template,
                                           customize_conf=customize_conf)

    # osbs is a fixture here
    @pytest.mark.parametrize(('name', 'data'), [  # noqa
        ('special-config',
         {
             "special.how": "very",
             "special.type": "charm"
         }),
    ])
    def test_config_map(self, osbs, name, data):
        obj = {
            'apiVersion': 'v1',
            'kind': 'ConfigMap',
            'metadata': {'name': name},
            'data': data,
        }
        (flexmock(osbs.os)
            .should_call('create_config_map')
            .with_args(obj))

        config_map = osbs.create_config_map(name, data)
        assert isinstance(config_map, ConfigMapResponse)
        assert config_map.get_data() == data
        config_map = osbs.get_config_map(name)
        assert isinstance(config_map, ConfigMapResponse)
        assert config_map.get_data() == data
        config_map = osbs.delete_config_map(name)
        assert config_map is None

    @pytest.mark.parametrize('platforms', [None, "x86"])
    def test_create_build_config_clear_nodeselector(self, caplog, platforms):
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = inputs

                [default]
                low_priority_node_selector = "lowpri=yes, scratch=yes"
                """))

            fp.flush()
            config = Configuration(fp.name)

        osbs_obj = OSBS(config, config)

        build_config_json = {
            'apiVersion': 'v1',
            'kind': 'BuildConfig',
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
            'spec': {
                'nodeSelector': {
                   'nodeselector1': 'node1_val',
                   'nodeselector2': 'node2_val',
                }
            },
            'status': {'lastVersion': 'lastVersion'},
        }
        bc_nodeselector = build_config_json['spec']['nodeSelector']
        bc_json_without_lp = copy.deepcopy(build_config_json)
        bc_json_without_lp['spec'].pop('nodeSelector')

        spec = BuildSpec()
        # Params needed to avoid exceptions.
        spec.set_params(
            user='user',
            base_image='fedora23/python',
            name_label='name_label',
            source_registry_uri='source_registry_uri',
            git_uri='https://github.com/user/reponame.git',
            registry_uris=['http://registry.example.com:5000/v2'],
            platforms=platforms
        )

        build_request = flexmock(
            render=lambda: build_config_json,
            has_ist_trigger=lambda: False,
            scratch=False)
        # Cannot use spec keyword arg in flexmock constructor
        # because it appears to be used by flexmock itself
        build_request.spec = spec
        build_request.low_priority_node_selector = config.get_low_priority_node_selector()

        def mock_get_existing_build_config(*args, **kwargs):
                return build_config_json
        (flexmock(osbs_obj)
            .should_receive('_get_existing_build_config')
            .once()
            .replace_with(mock_get_existing_build_config))

        (flexmock(osbs_obj.os)
            .should_receive('list_builds')
            .with_args(build_config_id='build')
            .once()
            .and_return(flexmock(json=lambda: {'items': []})))

        def mock_update_build_config(name, *args):
                assert name == 'build'
                bc_json = json.loads(args[0])
                assert bc_json_without_lp == bc_json
        (flexmock(osbs_obj.os)
            .should_receive('update_build_config')
            .once()
            .replace_with(mock_update_build_config))

        if platforms:
            patch_nodeselector = {'spec': {
                        'nodeSelector': config.get_low_priority_node_selector()}}
            (flexmock(osbs_obj.os)
                .should_receive('patch_build_config')
                .with_args('build', json.dumps(patch_nodeselector))
                .once())

        (flexmock(osbs_obj.os)
            .should_receive('start_build')
            .with_args('build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs_obj._create_build_config_and_build(build_request)

        assert "removing build config's nodeSelector %s" % \
               bc_nodeselector in caplog.text()
        if platforms:
            assert "setting back build config's log priority nodeSelector %s" % \
                   config.get_low_priority_node_selector() in caplog.text()

        assert build_response.json == {'spam': 'maps'}
