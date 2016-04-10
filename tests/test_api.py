"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from types import GeneratorType

from flexmock import flexmock
import json
from pkg_resources import parse_version
import os
import pytest
import shutil
import six
from tempfile import NamedTemporaryFile

from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.constants import PROD_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE, SIMPLE_BUILD_TYPE
from osbs.build.build_request import BuildRequest, SimpleBuild, ProductionBuild
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.exceptions import OsbsValidationException
from osbs.http import HttpResponse
from osbs import utils

from tests.constants import (TEST_ARCH, TEST_BUILD, TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_TARGET, TEST_USER, INPUTS_PATH)
from tests.fake_api import openshift, osbs, osbs106


class TestOSBS(object):
    def test_list_builds_api(self, osbs):
        response_list = osbs.list_builds()
        # We should get a response
        assert response_list is not None
        assert len(response_list) > 0
        # response_list is a list of BuildResponse objects
        assert isinstance(response_list[0], BuildResponse)
        # All the timestamps are understood
        for build in response_list:
            assert build.get_time_created_in_seconds() != 0.0

    def test_get_pod_for_build(self, osbs):
        pod = osbs.get_pod_for_build(TEST_BUILD)
        assert isinstance(pod, PodResponse)
        images = pod.get_container_image_ids()
        assert isinstance(images, dict)
        assert 'buildroot:latest' in images
        image_id = images['buildroot:latest']
        assert not image_id.startswith("docker:")

    def test_create_prod_build(self, osbs):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER,
                                          TEST_COMPONENT, TEST_TARGET, TEST_ARCH)
        assert isinstance(response, BuildResponse)

    def test_create_prod_build_missing_args(self, osbs):
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        (flexmock(osbs.build_conf)
            .should_receive('get_build_type')
            .and_return(PROD_BUILD_TYPE))
        (flexmock(osbs)
            .should_receive('create_prod_build')
            .with_args(git_uri=TEST_GIT_URI,
                       git_ref=TEST_GIT_REF,
                       git_branch=None,
                       user=TEST_USER,
                       component=TEST_COMPONENT,
                       target=None,
                       architecture=TEST_ARCH)
            .once()
            .and_return(None))
        response = osbs.create_build(git_uri=TEST_GIT_URI,
                                     git_ref=TEST_GIT_REF,
                                     user=TEST_USER,
                                     component=TEST_COMPONENT,
                                     architecture=TEST_ARCH)

    def test_create_prod_build_set_required_version(self, osbs106):
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        (flexmock(BuildRequest)
            .should_receive('set_openshift_required_version')
            .with_args(parse_version('1.0.6'))
            .once())
        response = osbs106.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                             TEST_GIT_BRANCH, TEST_USER,
                                             TEST_COMPONENT, TEST_TARGET,
                                             TEST_ARCH)

    def test_create_prod_with_secret_build(self, osbs):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
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
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
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
        assert isinstance(osbs.get_token(), six.string_types)

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

    def test_pause_builds(self, osbs):
        osbs.pause_builds()

    def test_resume_builds(self, osbs):
        osbs.resume_builds()

    @pytest.mark.parametrize('decode_docker_logs', [True, False])
    def test_build_logs_api_from_docker(self, osbs, decode_docker_logs):
        logs = osbs.get_docker_build_logs(TEST_BUILD, decode_logs=decode_docker_logs)
        assert isinstance(logs, tuple(list(six.string_types) + [bytes]))
        assert logs.split('\n')[0].find("Step ") != -1

    def test_backup(self, osbs):
        osbs.dump_resource("builds")

    def test_restore(self, osbs):
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
                "selfLink": "/oapi/v1/namespaces/default/builds/aos-f5-router-docker-20150916-152551",
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
        for basename in ['simple.json', 'simple_inner.json']:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(str(tmpdir), basename))

        # Create an inner JSON description with the specified compress
        # plugin method
        with open(os.path.join(str(tmpdir),'simple_inner.json'),
                  'r+') as inner:
            inner_json = json.load(inner)

            postbuild_plugins = inner_json['postbuild_plugins']
            inner_json['postbuild_plugins'] = [plugin
                                               for plugin in postbuild_plugins
                                               if plugin['name'] != 'compress']

            if compress:
                plugin = { 'name': 'compress' }
                plugin.update(args)
                inner_json['postbuild_plugins'].insert(0, plugin)

            inner.seek(0)
            json.dump(inner_json, inner)
            inner.truncate()

        with NamedTemporaryFile(mode='wt') as fp:
            fp.write("""
[general]
build_json_dir = {build_json_dir}
[default]
openshift_url = /
registry_uri = registry.example.com
build_type = simple
""".format(build_json_dir=str(tmpdir)))
            fp.flush()
            config = Configuration(fp.name)
            osbs = OSBS(config, config)

        if raises:
            with pytest.raises(raises):
                osbs.get_compression_extension()
        else:
            assert osbs.get_compression_extension() == expected

    def test_build_image(self):
        build_image = 'registry.example.com/buildroot:2.0'
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write("""
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
build_type = prod
build_image = {build_image}
""".format(build_json_dir='inputs', build_image=build_image))
            fp.flush()
            config = Configuration(fp.name)
            osbs = OSBS(config, config)

        assert config.get_build_image() == build_image

        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

        # Return the request as the response so we can check it
        def request_as_response(request):
            request.json = request.render()
            return request

        flexmock(OSBS, _create_build_config_and_build=request_as_response)

        req = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                     TEST_GIT_BRANCH, TEST_USER,
                                     TEST_COMPONENT, TEST_TARGET,
                                     TEST_ARCH)
        img = req.json['spec']['strategy']['customStrategy']['from']['name']
        assert img == build_image
