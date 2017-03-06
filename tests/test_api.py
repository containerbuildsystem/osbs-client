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
from tempfile import NamedTemporaryFile

from osbs.api import OSBS, osbsapi
from osbs.conf import Configuration
from osbs.build.build_request import BuildRequest
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.build.spec import BuildSpec
from osbs.exceptions import OsbsValidationException, OsbsException, OsbsResponseException
from osbs.http import HttpResponse
from osbs.constants import (DEFAULT_OUTER_TEMPLATE, WORKER_OUTER_TEMPLATE,
                            DEFAULT_INNER_TEMPLATE, WORKER_INNER_TEMPLATE,
                            DEFAULT_CUSTOMIZE_CONF, WORKER_CUSTOMIZE_CONF)
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

    def test_create_build_with_deprecated_params(self, osbs):
        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

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

    @pytest.mark.parametrize('name_label_name', ['Name', 'name'])
    def test_create_prod_build(self, osbs, name_label_name):
        # TODO: test situation when a buildconfig already exists
        class MockParser(object):
            labels = {name_label_name: 'fedora23/something', 'com.redhat.component':  TEST_COMPONENT}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER,
                                          TEST_COMPONENT, TEST_TARGET, TEST_ARCH)
        assert isinstance(response, BuildResponse)

    @pytest.mark.parametrize(('inner_template', 'outer_template', 'customize_conf'), (
        (DEFAULT_INNER_TEMPLATE, DEFAULT_OUTER_TEMPLATE, DEFAULT_CUSTOMIZE_CONF),
        (WORKER_INNER_TEMPLATE, WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF),
    ))
    def test_create_prod_build_build_request(self, osbs, inner_template,
                                             outer_template, customize_conf):
        class MockParser(object):
            labels = {'name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
            baseimage = 'fedora23/python'

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

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

    @pytest.mark.parametrize(('inner_template', 'outer_template', 'customize_conf'), (
        (WORKER_INNER_TEMPLATE, WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF),
        (None, WORKER_OUTER_TEMPLATE, None),
        (WORKER_INNER_TEMPLATE, None, None),
        (None, None, WORKER_CUSTOMIZE_CONF),
        (None, None, None),
    ))
    @pytest.mark.parametrize(('platform', 'release', 'raises_exception'), (
        (None, None, True),
        ('', '', True),
        ('spam', None, True),
        (None, 'bacon', True),
        ('spam', 'bacon', False),
    ))
    def test_create_worker_build(self, osbs, inner_template, outer_template,
                                 customize_conf, platform, release,
                                 raises_exception):
        class MockParser(object):
            labels = {'name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
            baseimage = 'fedora23/python'

        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

        args = [TEST_GIT_URI, TEST_GIT_REF, TEST_GIT_BRANCH, TEST_USER]

        kwargs = {}
        if platform is not None:
            kwargs['platform'] = platform
        if release is not None:
            kwargs['release'] = release
        if inner_template is not None:
            kwargs['inner_template'] = inner_template
        if outer_template is not None:
            kwargs['outer_template'] = outer_template
        if customize_conf is not None:
            kwargs['customize_conf'] = customize_conf

        expected_kwargs = {
            'platform': platform,
            'release': release,
            'inner_template': WORKER_INNER_TEMPLATE,
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
        }

        (flexmock(osbs)
            .should_call('create_prod_build')
            .with_args(*args, **expected_kwargs)
            .times(0 if raises_exception else 1))

        if raises_exception:
            with pytest.raises(OsbsException):
                osbs.create_worker_build(*args, **kwargs)
        else:
            response = osbs.create_worker_build(*args, **kwargs)
            assert isinstance(response, BuildResponse)

    @pytest.mark.parametrize('unique_tag_only', [True, False, None])
    def test_create_prod_build_unique_tag_only(self, osbs, unique_tag_only):
        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))

        original_create_build_config_and_build = osbs._create_build_config_and_build
        def check_build_request(build_request):
            assert build_request.spec.unique_tag_only.value == unique_tag_only
            return original_create_build_config_and_build(build_request)

        (flexmock(osbs)
            .should_receive('_create_build_config_and_build')
            .replace_with(check_build_request)
            .once())
        (flexmock(osbs.build_conf)
            .should_receive('get_unique_tag_only')
            .and_return(unique_tag_only))
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

    @pytest.mark.parametrize('label_name', ['BZComponent', 'com.redhat.component', 'Name', 'name'])
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

    def test_create_prod_build_missing_args(self, osbs):
        """
        tests if setdefault for arguments works in create_build
        """

        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
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
                       architecture=TEST_ARCH)
            .once()
            .and_return(None))
        response = osbs.create_build(git_uri=TEST_GIT_URI,
                                     git_ref=TEST_GIT_REF,
                                     user=TEST_USER,
                                     component=TEST_COMPONENT,
                                     architecture=TEST_ARCH)

    @pytest.mark.parametrize('component_label_name', ['com.redhat.component', 'BZComponent'])
    def test_component_is_changed_from_label(self, osbs, component_label_name):
        """
        tests if component is changed in create_prod_build
        with value from component label
        """

        class MockParser(object):
            labels = {'Name': 'fedora23/something', component_label_name: TEST_COMPONENT}
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

    def test_missing_component_argument_doesnt_break_build(self, osbs):
        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_df_parser')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH)
            .and_return(MockParser()))
        response = osbs.create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                          TEST_GIT_BRANCH, TEST_USER)
        assert isinstance(response, BuildResponse)

    def test_create_prod_build_set_required_version(self, osbs106):
        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
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
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
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
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
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

    @pytest.mark.parametrize('build_type', (
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

    @pytest.mark.parametrize(('cpu', 'memory', 'storage', 'set_resource'), (
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

    def test_create_build_from_buildrequest(self, osbs):
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

    @pytest.mark.parametrize('subdir', [None, 'new-dir'])
    def test_login_api(self, tmpdir, osbs, subdir):
        token = 'spam-bacon-eggs'
        token_file_dir = str(tmpdir)
        if subdir:
            token_file_dir = os.path.join(token_file_dir, subdir)
        token_file_path = os.path.join(token_file_dir, 'test-token')

        (flexmock(utils)
            .should_receive('get_instance_token_file_name')
            .with_args(osbs.os_conf.conf_section)
            .and_return(token_file_path))

        osbs.login(token)

        with open(token_file_path) as token_file:
            assert token == token_file.read().strip()

        file_mode = os.stat(token_file_path).st_mode
        # File owner permission
        assert file_mode & stat.S_IRWXU
        # Group permission
        assert not file_mode & stat.S_IRWXG
        # Others permission
        assert not file_mode & stat.S_IRWXO

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
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                [default]
                openshift_url = /
                registry_uri = registry.example.com
                """.format(build_json_dir=str(tmpdir))))
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
                """.format(build_json_dir='inputs', build_image=build_image)))
            fp.flush()
            config = Configuration(fp.name)
            osbs = OSBS(config, config)

        assert config.get_build_image() == build_image

        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
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
            has_ist_trigger=lambda: False,
            scratch=False)

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
            'spec': {},
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'existing-build'

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

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
            'spec': {},
        }

        existing_build_json = copy.deepcopy(build_json)
        existing_build_json['metadata']['name'] = 'existing-build'
        existing_build_json['metadata']['labels']['new-label'] = 'new-value'

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

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
            'spec': {},
        }

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=False)

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

        osbs = OSBS(config, config)

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

        get_existing_build_config_times = 1
        if existing_bc and build_request.has_ist_trigger():
            get_existing_build_config_times += 1

        def mock_get_existing_build_config(*args, **kwargs):
            return build_config_json if existing_bc else None
        (flexmock(osbs)
            .should_receive('_get_existing_build_config')
            .times(get_existing_build_config_times)
            .replace_with(mock_get_existing_build_config))

        def mock_get_image_stream(*args, **kwargs):
            if not existing_is:
                raise OsbsResponseException('missing ImageStream',
                                            status_code=404)

            return flexmock(json=lambda: image_stream_json)
        (flexmock(osbs.os)
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
            (flexmock(osbs.os)
                .should_receive('get_image_stream_tag')
                .with_args('fedora23-python:latest')
                .once()
                .replace_with(mock_get_image_stream_tag))

            (flexmock(osbs.os)
                .should_receive('ensure_image_stream_tag')
                .with_args(image_stream_json, 'latest', dict, True)
                .once()
                .and_return(True))

        update_build_config_times = 0

        if existing_bc:
            (flexmock(osbs.os)
                .should_receive('list_builds')
                .with_args(build_config_id='build')
                .once()
                .and_return(flexmock(json=lambda: {'items': []})))
            update_build_config_times += 1

        else:
            temp_build_json = copy.deepcopy(build_json)
            temp_build_json['spec'].pop('triggers', None)

            def mock_create_build_config(encoded_build_json):
                assert json.loads(encoded_build_json) == temp_build_json
                return flexmock(json=lambda: build_config_json)
            (flexmock(osbs.os)
                .should_receive('create_build_config')
                .replace_with(mock_create_build_config)
                .once())

        if build_request.has_ist_trigger():
            update_build_config_times += 1

        (flexmock(osbs.os)
            .should_receive('update_build_config')
            .with_args('build', str)
            .times(update_build_config_times))

        if expect_auto:
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

        else:
            (flexmock(osbs.os)
                .should_receive('start_build')
                .with_args('build')
                .once()
                .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs._create_build_config_and_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    @pytest.mark.parametrize(('kind', 'expect_name'), [
        ('ImageStreamTag', 'registry:5000/buildroot:latest'),
        ('DockerImage', 'buildroot:latest'),
    ])
    def test_scratch_build_config(self, kind, expect_name):
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

            'spec': {
                'strategy': {
                    'customStrategy': {
                        'from': {
                            'kind': kind,
                            'name': 'buildroot:latest',
                        },
                    },
                },
            },
        }

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=True)

        updated_build_json = copy.deepcopy(build_json)
        updated_build_json['kind'] = 'Build'
        updated_build_json['metadata']['labels']['scratch'] = 'true'
        updated_build_json['spec']['serviceAccount'] = 'builder'
        img = updated_build_json['spec']['strategy']['customStrategy']['from']
        img['kind'] = 'DockerImage'
        img['name'] = expect_name
        build_name = 'scratch-%s' % datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        updated_build_json['metadata']['name'] = build_name

        if kind == 'ImageStreamTag':
            (flexmock(osbs.os)
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
            (flexmock(osbs.os)
                .should_receive('get_image_stream_tag')
                .never())

        def verify_build_json(passed_build_json):
            # Don't compare metadata.name directly as we can't control it
            assert (isinstance(passed_build_json['metadata'].pop('name'),
                               six.string_types))
            del updated_build_json['metadata']['name']

            assert passed_build_json == updated_build_json
            return flexmock(json=lambda: {'spam': 'maps'})

        (flexmock(osbs.os)
            .should_receive('create_build')
            .replace_with(verify_build_json)
            .once())

        (flexmock(osbs.os)
            .should_receive('create_build_config')
            .never())

        (flexmock(osbs.os)
            .should_receive('update_build_config')
            .never())

        build_response = osbs._create_scratch_build(build_request)
        assert build_response.json == {'spam': 'maps'}

    def test_scratch_param_to_create_build(self):
        config = Configuration()
        osbs = OSBS(config, config)

        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
            baseimage = 'fedora23/python'

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
            .and_return(MockParser()))

        (flexmock(osbs)
            .should_receive('_create_scratch_build')
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        (flexmock(osbs.os)
            .should_receive('create_build_config')
            .never())

        (flexmock(osbs.os)
            .should_receive('update_build_config')
            .never())

        build_response = osbs.create_build(**kwargs)
        assert build_response.json() == {'spam': 'maps'}

    def test_get_image_stream_tag(self):
        config = Configuration()
        osbs = OSBS(config, config)

        name = 'buildroot:latest'
        (flexmock(osbs.os)
            .should_receive('get_image_stream_tag')
            .with_args(name)
            .once()
            .and_return(flexmock(json=lambda: {
                'image': {
                    'dockerImageReference': 'spam:maps',
                }
            })))

        response = osbs.get_image_stream_tag(name)
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
            osbs = OSBS(config, config)

        stream = {'type': 'stream'}
        tag_name = 'latest'
        scheduled = False
        (flexmock(osbs.os)
            .should_receive('ensure_image_stream_tag')
            .with_args(stream, tag_name, dict, scheduled)
            .once()
            .and_return('eggs'))

        response = osbs.ensure_image_stream_tag(stream, tag_name, scheduled)
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
            osbs = OSBS(config, config)

        class MockParser(object):
            labels = {'Name': 'fedora23/something', 'com.redhat.component': TEST_COMPONENT}
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
        secrets = req.json['spec']['strategy']['customStrategy']['secrets']
        expected_secret = {
            'mountPath': '/var/run/secrets/atomic-reactor/mysecret',
            'secretSource': {
                'name': 'mysecret',
            }
        }
        assert expected_secret in secrets
