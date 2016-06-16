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
import copy
from tempfile import NamedTemporaryFile

from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.build.build_request import BuildRequest
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.exceptions import OsbsValidationException, OsbsException
from osbs.http import HttpResponse
from osbs.constants import DEFAULT_OUTER_TEMPLATE, DEFAULT_INNER_TEMPLATE
from osbs import utils

from tests.constants import (TEST_ARCH, TEST_BUILD, TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_TARGET, TEST_USER, INPUTS_PATH,
                             TEST_KOJI_TASK_ID)
from tests.fake_api import openshift, osbs, osbs106


def request_as_response(request):
    """
    Return the request as the response so we can check it
    """

    request.json = request.render()
    return request


class TestOSBS(object):
    @pytest.mark.parametrize('koji_task_id', [None, TEST_KOJI_TASK_ID])
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

    def test_create_prod_build_missing_name_label(self, osbs):
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

    def test_create_prod_build_missing_args(self, osbs):
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
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
        simple = osbs.get_build_request("simple")
        assert isinstance(simple, BuildRequest)
        prod = osbs.get_build_request("prod")
        assert isinstance(prod, BuildRequest)
        prodwithoutkoji = osbs.get_build_request("prod-without-koji")
        assert isinstance(prodwithoutkoji, BuildRequest)

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
            fp.write("""
[general]
build_json_dir = {build_json_dir}
[default]
openshift_url = /
registry_uri = registry.example.com
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

        flexmock(OSBS, _create_build_config_and_build=request_as_response)

        req = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                     TEST_GIT_BRANCH, TEST_USER,
                                     TEST_COMPONENT, TEST_TARGET,
                                     TEST_ARCH)
        img = req.json['spec']['strategy']['customStrategy']['from']['name']
        assert img == build_image

    def test_explicit_labels(self, osbs):
        class MockParser(object):
            labels = {'Name': 'fedora23/something'}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

        flexmock(OSBS, _create_build_config_and_build=request_as_response)

        key = 'Release'
        value = '4'
        req = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                     TEST_GIT_BRANCH, TEST_USER,
                                     TEST_COMPONENT, TEST_TARGET,
                                     TEST_ARCH,
                                     labels={key: value})
        env_vars = req.json['spec']['strategy']['customStrategy']['env']
        plugins_var = [env_var for env_var in env_vars
                       if env_var['name'] == 'ATOMIC_REACTOR_PLUGINS']
        plugins = json.loads(plugins_var[0]['value'])
        add = [plugin for plugin in plugins['prebuild_plugins']
               if plugin['name'] == 'add_labels_in_dockerfile']
        add_labels = add[0]['args']['labels']
        assert add_labels[key] == value

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
        osbs = OSBS(config, config)

        (flexmock(osbs.os)
            .should_receive('get_build_config_by_labels')
            .with_args([('git-repo-name', 'reponame'), ('git-branch', 'branch')])
            .once()
            .and_return(existing_build_config))
        (flexmock(osbs.os)
            .should_receive('get_build_config')
            .never())

        actual_build_config = osbs._get_existing_build_config(build_config)
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
        osbs = OSBS(config, config)

        (flexmock(osbs.os)
            .should_receive('get_build_config_by_labels')
            .with_args([('git-repo-name', 'reponame'), ('git-branch', 'branch')])
            .once()
            .and_raise(OsbsException))
        (flexmock(osbs.os)
            .should_receive('get_build_config')
            .with_args('name')
            .once()
            .and_return(existing_build_config))

        actual_build_config = osbs._get_existing_build_config(build_config)
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
        osbs = OSBS(config, config)

        (flexmock(osbs.os)
            .should_receive('get_build_config_by_labels')
            .with_args([('git-repo-name', 'reponame'), ('git-branch', 'branch')])
            .once()
            .and_raise(OsbsException))
        (flexmock(osbs.os)
            .should_receive('get_build_config')
            .with_args('name')
            .once()
            .and_raise(OsbsException))

        assert osbs._get_existing_build_config(build_config) is None

    def test_verify_no_running_builds_zero(self):
        config = Configuration()
        osbs = OSBS(config, config)

        (flexmock(osbs)
            .should_receive('_get_running_builds_for_build_config')
            .with_args('build_config_name')
            .once()
            .and_return([]))

        osbs._verify_no_running_builds('build_config_name')

    def test_verify_no_running_builds_one(self):
        config = Configuration()
        osbs = OSBS(config, config)

        (flexmock(osbs)
            .should_receive('_get_running_builds_for_build_config')
            .with_args('build_config_name')
            .once()
            .and_return([
                flexmock(status='Running', get_build_name=lambda: 'build-1'),
            ]))

        with pytest.raises(OsbsException) as exc:
            osbs._verify_no_running_builds('build_config_name')
        assert str(exc.value).startswith('Build build-1 for build_config_name')

    def test_verify_no_running_builds_many(self):
        config = Configuration()
        osbs = OSBS(config, config)

        (flexmock(osbs)
            .should_receive('_get_running_builds_for_build_config')
            .with_args('build_config_name')
            .once()
            .and_return([
                flexmock(status='Running', get_build_name=lambda: 'build-1'),
                flexmock(status='Running', get_build_name=lambda: 'build-2'),
            ]))

        with pytest.raises(OsbsException) as exc:
            osbs._verify_no_running_builds('build_config_name')
        assert str(exc.value).startswith('Multiple builds for')

    def test_create_build_config_bad_version(self):
        config = Configuration()
        osbs = OSBS(config, config)
        build_json = {'apiVersion': 'spam'}
        build_request = flexmock(
            render=lambda: build_json,
            is_auto_instantiated=lambda: False)

        with pytest.raises(OsbsValidationException):
            osbs._create_build_config_and_build(build_request)

    def test_create_build_config_label_mismatch(self):
        config = Configuration()
        osbs = OSBS(config, config)

        build_json = {
            'apiVersion': osbs.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'build'
        existing_build_json['metadata']['labels']['git-repo-name'] = 'reponame2'
        existing_build_json['metadata']['labels']['git-branch'] = 'branch2'

        build_request = flexmock(
            render=lambda: build_json,
            is_auto_instantiated=lambda: False)

        (flexmock(osbs)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(existing_build_json))

        with pytest.raises(OsbsValidationException) as exc:
            osbs._create_build_config_and_build(build_request)

        assert 'Git labels collide' in str(exc.value)

    def test_create_build_config_already_running(self):
        config = Configuration()
        osbs = OSBS(config, config)

        build_json = {
            'apiVersion': osbs.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'existing-build'

        build_request = flexmock(
            render=lambda: build_json,
            is_auto_instantiated=lambda: False)

        (flexmock(osbs)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(existing_build_json))

        (flexmock(osbs)
            .should_receive('_get_running_builds_for_build_config')
            .once()
            .and_return([
                flexmock(status='Running', get_build_name=lambda: 'build-1'),
            ]))

        with pytest.raises(OsbsException):
            osbs._create_build_config_and_build(build_request)

    def test_create_build_config_update(self):
        config = Configuration()
        osbs = OSBS(config, config)

        build_json = {
            'apiVersion': osbs.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'existing-build'
        existing_build_json['metadata']['labels']['new-label'] = 'new-value'

        build_request = flexmock(
            render=lambda: build_json,
            is_auto_instantiated=lambda: False)

        (flexmock(osbs)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(existing_build_json))

        (flexmock(osbs)
            .should_receive('_get_running_builds_for_build_config')
            .once()
            .and_return([]))

        (flexmock(osbs.os)
            .should_receive('update_build_config')
            .with_args('existing-build', json.dumps(existing_build_json))
            .once())

        (flexmock(osbs.os)
            .should_receive('start_build')
            .with_args('existing-build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    def test_create_build_config_create(self):
        config = Configuration()
        osbs = OSBS(config, config)

        build_json = {
            'apiVersion': osbs.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
        }

        build_request = flexmock(
            render=lambda: build_json,
            is_auto_instantiated=lambda: False)

        (flexmock(osbs)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(None))

        (flexmock(osbs.os)
            .should_receive('create_build_config')
            .with_args(json.dumps(build_json))
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        (flexmock(osbs.os)
            .should_receive('start_build')
            .with_args('build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    def test_create_build_config_auto_start(self):
        config = Configuration()
        osbs = OSBS(config, config)

        build_json = {
            'apiVersion': osbs.os_conf.get_openshift_api_version(),
            'metadata': {
                'name': 'build',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                },
            },
        }

        build_request = flexmock(
            render=lambda: build_json,
            is_auto_instantiated=lambda: True)

        (flexmock(osbs)
            .should_receive('_get_existing_build_config')
            .once()
            .and_return(None))

        (flexmock(osbs.os)
            .should_receive('create_build_config')
            .with_args(json.dumps(build_json))
            .once()
            .and_return(flexmock(json=lambda: {
                'status': {'lastVersion': 'lastVersion'}}
            )))

        (flexmock(osbs.os)
            .should_receive('wait_for_new_build_config_instance')
            .with_args('build', 'lastVersion')
            .once()
            .and_return('build-id'))

        (flexmock(osbs.os)
            .should_receive('get_build')
            .with_args('build-id')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}
