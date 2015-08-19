"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from types import GeneratorType

from flexmock import flexmock
import pytest
import six

from osbs.constants import PROD_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE, SIMPLE_BUILD_TYPE
from osbs.build.build_request import BuildRequest, SimpleBuild, ProductionBuild
from osbs.build.build_response import BuildResponse
from osbs.http import HttpResponse
from osbs import utils

from tests.constants import (TEST_ARCH, TEST_BUILD, TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_TARGET, TEST_USER)
from tests.fake_api import openshift, osbs


class TestOSBS(object):
    def test_list_builds_api(self, osbs):
        response_list = osbs.list_builds()
        # We should get a response
        assert response_list is not None
        assert len(response_list) > 0
        # response_list is a list of BuildResponse objects
        assert isinstance(response_list[0], BuildResponse)

    def test_create_prod_build(self, osbs):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER,
                                          TEST_COMPONENT, TEST_TARGET, TEST_ARCH)
        assert isinstance(response, BuildResponse)

    def test_create_prod_with_secret_build(self, osbs):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_with_secret_build(TEST_GIT_URI, TEST_GIT_REF,
                                                      TEST_GIT_BRANCH, TEST_USER,
                                                      TEST_COMPONENT, TEST_TARGET,
                                                      TEST_ARCH)
        assert isinstance(response, BuildResponse)

    def test_create_prod_without_koji_build(self, osbs):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_without_koji_build(TEST_GIT_URI, TEST_GIT_REF,
                                                       TEST_GIT_BRANCH, TEST_USER,
                                                       TEST_COMPONENT, TEST_ARCH)
        assert isinstance(response, BuildResponse)

    def test_wait_for_build_to_finish(self, osbs):
        build_response = osbs.wait_for_build_to_finish(TEST_BUILD)
        assert isinstance(build_response, BuildResponse)

    def test_get_build_api(self, osbs):
        response = osbs.get_build(TEST_BUILD)
        # We should get a BuildResponse
        assert isinstance(response, BuildResponse)

    def test_get_build_request_api(self, osbs):
        build = osbs.get_build_request()
        assert isinstance(build, BuildRequest)
        simple = osbs.get_build_request(SIMPLE_BUILD_TYPE)
        assert isinstance(simple, SimpleBuild)
        prod = osbs.get_build_request(PROD_BUILD_TYPE)
        assert isinstance(prod, ProductionBuild)
        prodwithoutkoji = osbs.get_build_request(PROD_WITHOUT_KOJI_BUILD_TYPE)
        assert isinstance(prodwithoutkoji, ProductionBuild)

    def test_set_labels_on_build_api(self, osbs):
        labels = {'label1': 'value1', 'label2': 'value2'}
        response = osbs.set_labels_on_build(TEST_BUILD, labels)
        assert isinstance(response, HttpResponse)

    def test_set_annotations_on_build_api(self, osbs):
        annotations = {'ann1': 'value1', 'ann2': 'value2'}
        response = osbs.set_annotations_on_build(TEST_BUILD, annotations)
        assert isinstance(response, HttpResponse)

    def test_get_token_api(self, osbs):
        assert isinstance(osbs.get_token(), bytes)

    def test_get_user_api(self, osbs):
        assert 'name' in osbs.get_user()['metadata']

    def test_build_logs_api(self, osbs):
        logs = osbs.get_build_logs(TEST_BUILD)
        assert isinstance(logs, six.string_types)
        assert logs == "line 1"

    def test_build_logs_api_follow(self, osbs):
        logs = osbs.get_build_logs(TEST_BUILD, follow=True)
        assert isinstance(logs, GeneratorType)
        assert next(logs) == "line 1"
        with pytest.raises(StopIteration):
            assert next(logs)

    @pytest.mark.parametrize('decode_docker_logs', [True, False])
    def test_build_logs_api_from_docker(self, osbs, decode_docker_logs):
        logs = osbs.get_docker_build_logs(TEST_BUILD, decode_logs=decode_docker_logs)
        assert isinstance(logs, tuple(list(six.string_types) + [bytes]))
        assert logs.split('\n')[0].find("Step ") != -1
