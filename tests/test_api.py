# -*- coding: utf-8 -*-
"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

from types import GeneratorType

from flexmock import flexmock, MethodCallError
from textwrap import dedent
import json
from pkg_resources import parse_version
import os
import pytest
import six
import stat
import copy
import getpass
import sys
import yaml
from tempfile import NamedTemporaryFile

from osbs.api import OSBS, osbsapi
from osbs.conf import Configuration
from osbs.build.user_params import BuildUserParams
from osbs.build.build_requestv2 import BuildRequestV2
from osbs.build.build_response import BuildResponse
from osbs.build.pod_response import PodResponse
from osbs.build.config_map_response import ConfigMapResponse
from osbs.exceptions import (OsbsValidationException, OsbsException, OsbsResponseException,
                             OsbsOrchestratorNotEnabled)
from osbs.osbs_http import HttpResponse
from osbs.cli.main import cmd_build
from osbs.constants import (DEFAULT_OUTER_TEMPLATE, WORKER_OUTER_TEMPLATE,
                            DEFAULT_CUSTOMIZE_CONF, WORKER_CUSTOMIZE_CONF,
                            ORCHESTRATOR_OUTER_TEMPLATE,
                            ORCHESTRATOR_CUSTOMIZE_CONF,
                            BUILD_TYPE_WORKER, BUILD_TYPE_ORCHESTRATOR,
                            REPO_CONTAINER_CONFIG, PRUN_TEMPLATE_USER_PARAMS,
                            PRUN_TEMPLATE_REACTOR_CONFIG_WS, PRUN_TEMPLATE_BUILD_DIR_WS)
from osbs import utils
from osbs.utils.labels import Labels
from osbs.repo_utils import RepoInfo, RepoConfiguration, ModuleSpec

from tests.constants import (TEST_ARCH, TEST_BUILD, TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF,
                             TEST_GIT_URI, TEST_TARGET, TEST_USER, INPUTS_PATH,
                             TEST_KOJI_TASK_ID, TEST_FILESYSTEM_KOJI_TASK_ID, TEST_VERSION,
                             TEST_ORCHESTRATOR_BUILD, TEST_PIPELINE_RUN_TEMPLATE)
from osbs.core import Openshift
from osbs.tekton import PipelineRun
# needed for mocking input() (because it is imported from six)
from osbs import api as _osbs_api
from six.moves import http_client


# Expected log return lines for test_orchestrator_build_logs_api
ORCHESTRATOR_LOGS = [u'2017-06-23 17:18:41,791 platform:- - '
                     u'atomic_reactor.foo - DEBUG - this is from the orchestrator build',

                     u'2017-06-23 17:18:41,791 - I really like bacon']
WORKER_LOGS = [u'2017-06-23 17:18:41,400 atomic_reactor.foo -  '
               u'DEBUG - this is from a worker build',

               u'"ContainersPaused": 0,']

REQUIRED_BUILD_ARGS = {
    'git_uri': TEST_GIT_URI,
    'git_ref': TEST_GIT_REF,
    'git_branch': TEST_GIT_BRANCH,
    'user': TEST_USER,
    'build_type': BUILD_TYPE_ORCHESTRATOR,
    'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
}

REQUIRED_SOURCE_CONTAINER_BUILD_ARGS = {
    'user': TEST_USER,
    'sources_for_koji_build_nvr': 'test-1-123',
    'component': 'test_component',
}

TEST_MODULES = ['mod_name:mod_stream:mod_version']


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


class MockDfParser(object):
    labels = {
        'name': 'fedora23/something',
        'com.redhat.component': TEST_COMPONENT,
        'version': TEST_VERSION,
    }
    baseimage = 'fedora23/python'


class MockDfParserFromScratch(object):
    labels = {
        'name': 'fedora23/something',
        'com.redhat.component': TEST_COMPONENT,
        'version': TEST_VERSION,
    }
    baseimage = 'scratch'


class MockDfParserBaseImage(object):
    labels = {
        'name': 'fedora23/something',
        'com.redhat.component': TEST_COMPONENT,
        'version': TEST_VERSION,
    }
    baseimage = 'koji/image-build'


class MockConfiguration(object):
    def __init__(self,
                 is_flatpak=False,
                 modules=None):
        self.container = {'compose': {'modules': modules}}
        safe_modules = modules or []
        self.container_module_specs = [ModuleSpec.from_str(module) for module in safe_modules]
        self.depth = 0
        self.is_flatpak = is_flatpak
        self.flatpak_base_image = None
        self.flatpak_component = None
        self.flatpak_name = None
        self.git_uri = TEST_GIT_URI
        self.git_ref = TEST_GIT_REF
        self.git_branch = TEST_GIT_BRANCH

    def is_autorebuild_enabled(self):
        return False


class Mock_Start_Pipeline(object):
    def json(self):
        return {}


class TestOSBS(object):

    def mock_repo_info(self, mock_df_parser=None, mock_config=None):
        mock_df_parser = mock_df_parser or MockDfParser()
        config = mock_config or MockConfiguration()
        return RepoInfo(mock_df_parser, config)

    def test_osbsapi_wrapper(self):
        """
        Test that a .never() expectation works inside a .raises()
        block.
        """

        (flexmock(utils)
            .should_receive('get_repo_info')
            .never())

        @osbsapi
        def dummy_api_function():
            """A function that calls something it's not supposed to"""
            utils.get_repo_info(TEST_GIT_URI, TEST_GIT_REF)

        # Check we get the correct exception
        with pytest.raises(MethodCallError):
            dummy_api_function()

    @pytest.mark.parametrize('kwargs', (  # noqa
        {},
        {'koji_task_id': TEST_KOJI_TASK_ID},
        {'running': True},
        {'field_selector': 'foo=oof'},
        {'running': True, 'field_selector': 'foo=oof'},
    ))
    def test_list_builds_api(self, osbs, kwargs):
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
        with pytest.raises(OsbsException):
            pod = osbs.get_pod_for_build('Not a valid pod')

    # osbs is a fixture here
    def test_create_build_with_deprecated_params(self, osbs):  # noqa
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'target': TEST_TARGET,
            'yum_repourls': None,
            'koji_task_id': None,
            'scratch': False,
            'build_type': BUILD_TYPE_ORCHESTRATOR,
            # Stuff that should be ignored and not cause erros
            'labels': {'Release': 'bacon'},
            'spam': 'maps',
            'build_from': 'image:test',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
        }

        response = osbs.create_build(**kwargs)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    def test_create_build_invalid_yaml(self, osbs, tmpdir, monkeypatch):  # noqa
        """Test that errors caused by invalid yaml have a useful error message"""
        repo_config = tmpdir.join(REPO_CONTAINER_CONFIG)
        repo_config.write('\n'.join(['hallo: 1', 'bye']))

        def get_repo_info(*args, **kwargs):
            # will fail because of invalid yaml
            return RepoConfiguration(dir_path=str(tmpdir))

        monkeypatch.setattr(utils, 'get_repo_info', get_repo_info)

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER
        }

        with pytest.raises(OsbsException) as exc_info:
            osbs.create_build(**kwargs)

        err_msg = 'Failed to load or validate container file "{}"'.format(repo_config)
        assert err_msg in str(exc_info.value)

    # osbs is a fixture here
    @pytest.mark.parametrize('name_label_name', ['Name', 'name'])  # noqa
    def test_create_build(self, osbs, name_label_name):

        class MockParser(object):
            labels = {
                name_label_name: 'fedora23/something',
                'com.redhat.component': TEST_COMPONENT,
                'version': TEST_VERSION,
            }
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_df_parser=MockParser())))
        response = osbs.create_build(target=TEST_TARGET,
                                     **REQUIRED_BUILD_ARGS)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize(
        ('outer_template', 'customize_conf'), [(WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF)],
    )
    def test_create_build_build_request(self, osbs, outer_template, customize_conf):
        repo_info = self.mock_repo_info()

        user_params = BuildUserParams.make_params(
            build_json_dir=osbs.os_conf.get_build_json_store(),
            base_image='fedora23/python',
            build_from='image:whatever',
            build_conf=osbs.os_conf,
            name_label='whatever',
            repo_info=repo_info,
            **REQUIRED_BUILD_ARGS
        )

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(repo_info))

        (flexmock(osbs)
            .should_receive('get_user_params')
            .and_return(user_params))

        (flexmock(osbs)
            .should_call('get_build_request')
            .with_args(outer_template=outer_template,
                       customize_conf=customize_conf,
                       user_params=user_params,
                       repo_info=repo_info)
            .once())

        response = osbs.create_build(outer_template=outer_template,
                                     customize_conf=customize_conf,
                                     **REQUIRED_BUILD_ARGS)
        assert isinstance(response, BuildResponse)

    @pytest.mark.skip(reason="tekton openshift class doesn't have create_build")
    @pytest.mark.parametrize('has_task_id', [True, False])
    def test_create_build_remove_koji_task_id(self, has_task_id):

        build_config = {
            'metadata': {
                'name': 'name',
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                }
            },
            'spec': {'triggers': []}
        }

        if has_task_id:
            build_config['metadata']['labels']['koji-task-id'] = 123

        def mock_create_build(request):

            class Response(object):
                def json(self):
                    return ''

            return Response()

        config = Configuration(conf_file=None,
                               openshift_url="www.example.com",
                               build_json_dir="inputs", build_from='image:buildroot:latest')
        osbs_obj = OSBS(config)

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        (flexmock(osbs_obj.os)
            .should_receive('create_build')
            .replace_with(mock_create_build))

        osbs_obj.create_build(outer_template=DEFAULT_OUTER_TEMPLATE,
                              customize_conf=DEFAULT_CUSTOMIZE_CONF,
                              **REQUIRED_BUILD_ARGS)

    # osbs is a fixture here
    @pytest.mark.parametrize(('platform', 'release', 'raises_exception'), [  # noqa
        (None, None, True),
        ('', '', True),
        ('spam', None, True),
        (None, 'bacon', True),
        ('spam', 'bacon', False),
    ])
    def test_create_worker_build_missing_param(self, osbs, platform, release, raises_exception):
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
        }
        if platform is not None:
            kwargs['platform'] = platform
        if release is not None:
            kwargs['release'] = release

        expected_kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'platform': platform,
            'build_type': BUILD_TYPE_WORKER,
            'release': release,
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
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
    @pytest.mark.parametrize(
        ('outer_template', 'customize_conf'),
        (
            (WORKER_OUTER_TEMPLATE, WORKER_CUSTOMIZE_CONF),
            (WORKER_OUTER_TEMPLATE, None),
            (None, WORKER_CUSTOMIZE_CONF),
            (None, None),
        ),
    )
    def test_create_worker_build(self, osbs, outer_template, customize_conf):
        branch = TEST_GIT_BRANCH
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch)
            .and_return(self.mock_repo_info()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': TEST_USER,
            'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
            'platform': 'spam',
            'release': 'bacon',
        }
        if branch:
            kwargs['git_branch'] = branch

        expected_kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': branch,
            'user': TEST_USER,
            'filesystem_koji_task_id': TEST_FILESYSTEM_KOJI_TASK_ID,
            'platform': kwargs['platform'],
            'build_type': BUILD_TYPE_WORKER,
            'release': kwargs['release'],
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
        }

        if outer_template is not None:
            kwargs['outer_template'] = outer_template
            expected_kwargs['outer_template'] = outer_template
        if customize_conf is not None:
            kwargs['customize_conf'] = customize_conf
            expected_kwargs['customize_conf'] = customize_conf

        (flexmock(osbs)
            .should_receive('_do_create_prod_build')
            .with_args(**expected_kwargs)
            .once())

        osbs.create_worker_build(**kwargs)

    # osbs is a fixture here
    @pytest.mark.parametrize(  # noqa
        ('outer_template', 'customize_conf'),
        (
            (ORCHESTRATOR_OUTER_TEMPLATE, ORCHESTRATOR_CUSTOMIZE_CONF),
            (ORCHESTRATOR_OUTER_TEMPLATE, None),
            (None, ORCHESTRATOR_CUSTOMIZE_CONF),
            (None, None),
        ),
    )
    @pytest.mark.parametrize('platforms', (
        None, [], ['spam'], ['spam', 'bacon'],
    ))
    @pytest.mark.parametrize(('flatpak'), (True, False))
    def test_create_orchestrator_build(self, osbs, outer_template, customize_conf,
                                       platforms,
                                       flatpak):
        branch = TEST_GIT_BRANCH
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch)
            .and_return(self.mock_repo_info()))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': TEST_USER,
            'release': '1'
        }
        if branch:
            kwargs['git_branch'] = branch
        if platforms is not None:
            kwargs['platforms'] = platforms
        if outer_template is not None:
            kwargs['outer_template'] = outer_template
        if customize_conf is not None:
            kwargs['customize_conf'] = customize_conf

        if flatpak:
            kwargs['flatpak'] = True

        expected_kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': branch,
            'user': TEST_USER,
            'build_type': BUILD_TYPE_ORCHESTRATOR,
            'outer_template': ORCHESTRATOR_OUTER_TEMPLATE,
            'customize_conf': ORCHESTRATOR_CUSTOMIZE_CONF,
            'release': '1'
        }
        if platforms is not None:
            expected_kwargs['platforms'] = platforms

        if flatpak:
            expected_kwargs['flatpak'] = True

        (flexmock(osbs)
            .should_receive('_do_create_prod_build')
            .with_args(**expected_kwargs)
            .and_return(BuildResponse({}))
            .once())

        response = osbs.create_orchestrator_build(**kwargs)
        assert isinstance(response, BuildResponse)

    # osbs_cant_orchestrate is a fixture here
    def test_create_orchestrator_build_cant_orchestrate(self, osbs_cant_orchestrate):  # noqa
        """
        Test we get OsbsOrchestratorNotEnabled when can_orchestrate
        isn't true
        """
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        with pytest.raises(OsbsOrchestratorNotEnabled) as ex:
            osbs_cant_orchestrate.create_orchestrator_build(
                git_uri=TEST_GIT_URI,
                git_ref=TEST_GIT_REF,
                git_branch=TEST_GIT_BRANCH,
                user=TEST_USER,
                platforms=['spam'])

        assert 'can\'t create orchestrate build' in ex.value.message

    # osbs is a fixture here
    def test_create_build_missing_name_label(self, osbs):  # noqa
        class MockParser(object):
            labels = {}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(MockParser())))
        with pytest.raises(OsbsValidationException):
            osbs.create_build(target=TEST_TARGET,
                              **REQUIRED_BUILD_ARGS)

    # osbs is a fixture here
    @pytest.mark.parametrize(('all_labels', 'error_msgs' ), (  # noqa
        ({'BZComponent': 'component'},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : version"]),

        ({'BZComponent': ''},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : version",
          "required label doesn't have explicit value in Dockerfile : BZComponent"]),

        ({'com.redhat.component': 'component'},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : version"]),

        ({'com.redhat.component': ''},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : version",
          "required label doesn't have explicit value in Dockerfile : com.redhat.component"]),

        ({'name': 'name'},
         ["required label missing from Dockerfile : com.redhat.component",
          "required label missing from Dockerfile : version"]),

        ({'name': ''},
         ["required label missing from Dockerfile : com.redhat.component",
          "required label missing from Dockerfile : version",
          "required label doesn't have explicit value in Dockerfile : name"]),

        ({'Name': 'name'},
         ["required label missing from Dockerfile : com.redhat.component",
          "required label missing from Dockerfile : version"]),

        ({'Name': ''},
         ["required label missing from Dockerfile : com.redhat.component",
          "required label missing from Dockerfile : version",
          "required label doesn't have explicit value in Dockerfile : Name"]),

        ({'version': 'version'},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : com.redhat.component"]),

        ({'version': ''},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : com.redhat.component"]),

        ({'Version': 'version'},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : com.redhat.component"]),

        ({'Version': ''},
         ["required label missing from Dockerfile : name",
          "required label missing from Dockerfile : com.redhat.component"]),

        ({'Name': 'name', 'BZComponent': 'component', 'Version': 'version'},
         []),

        ({'Name': 'name', 'BZComponent': 'component', 'Version': ''},
         []),

        ({'name': 'name', 'com.redhat.component': 'component', 'version': 'version'},
         []),

        ({'name': 'name', 'com.redhat.component': 'component', 'version': ''},
         []),
    ))
    def test_missing_required_labels(self, osbs, caplog, all_labels, error_msgs):
        """
        tests if raises exception if required lables are missing
        """

        class MockParser(object):
            labels = all_labels
            baseimage = 'fedora23/python'

        required_args = copy.deepcopy(REQUIRED_BUILD_ARGS)
        # just so we stop right after checking labels when error_msgs are empty
        required_args['signing_intent'] = 'release'
        required_args['compose_ids'] = [1, 2, 3, 4]

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(MockParser())))

        with pytest.raises(OsbsValidationException) as exc:
            osbs.create_build(target=TEST_TARGET,
                              **required_args)
        if error_msgs:
            exc_msg = 'required label missing from Dockerfile'
            assert exc_msg in str(exc.value)
            for error in error_msgs:
                assert error in caplog.text
        else:
            exc_msg = "Please only define signing_intent -OR- compose_ids, not both"
            assert exc_msg in str(exc.value)

    # osbs is a fixture here
    @pytest.mark.parametrize('name,should_succeed', [  # noqa:F811
        ('fedora-25.1/something', False),
        ('fedora-25-1/something', True),
    ])
    def test_reject_invalid_name(self, osbs, name, should_succeed):
        """
        tests invalid name label is rejected
        """

        class MockParser(object):
            labels = {
                'name': name,
                'com.redhat.component': TEST_COMPONENT,
                'version': TEST_VERSION,
            }
            baseimage = 'fedora:25'
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(MockParser())))
        create_build_args = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'target': TEST_TARGET,
            'build_type': BUILD_TYPE_ORCHESTRATOR,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
        }
        if should_succeed:
            osbs.create_build(**create_build_args)
        else:
            with pytest.raises(OsbsValidationException):
                osbs.create_build(**create_build_args)

        create_build_args['platforms'] = [TEST_ARCH]
        if should_succeed:
            osbs.create_orchestrator_build(**create_build_args)
        else:
            with pytest.raises(OsbsValidationException):
                osbs.create_orchestrator_build(**create_build_args)

    # osbs is a fixture here
    @pytest.mark.parametrize('component_label_name', ['com.redhat.component', 'BZComponent'])  # noqa
    def test_component_is_changed_from_label(self, osbs, component_label_name):
        """
        tests if component is changed in create_build
        with value from component label
        """
        component_override = 'different_{}'.format(TEST_COMPONENT)

        class MockParser(object):
            labels = {
                'name': 'fedora23/something',
                component_label_name: component_override,
                'version': TEST_VERSION,
            }
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_df_parser=MockParser())))
        flexmock(OSBS, _create_build_directly=request_as_response)
        req = osbs.create_build(target=TEST_TARGET,
                                **REQUIRED_BUILD_ARGS)
        assert req.user_params.component == component_override

    # osbs is a fixture here
    def test_missing_component_argument_doesnt_break_build(self, osbs):  # noqa
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))
        response = osbs.create_build(**REQUIRED_BUILD_ARGS)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    def test_create_build_set_required_version(self, osbs106):  # noqa
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))
        (flexmock(BuildRequestV2)
            .should_receive('set_openshift_required_version')
            .with_args(parse_version('1.0.6'))
            .once())
        osbs106.create_build(target=TEST_TARGET,
                             **REQUIRED_BUILD_ARGS)

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
    def test_cancel_build(self, osbs):  # noqa
        response = osbs.cancel_build(TEST_BUILD)
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
        assert isinstance(build, BuildRequestV2)

    # osbs is a fixture here
    @pytest.mark.parametrize(('cpu', 'memory', 'storage', 'set_resource'), (  # noqa
        (None, None, None, False),
        ('spam', None, None, True),
        (None, 'spam', None, True),
        (None, None, 'spam', True),
        ('spam', 'spam', 'spam', True),
    ))
    def test_get_build_request_api(self, osbs, cpu, memory, storage, set_resource):
        outer_template = 'outer.json'
        build_json_store = 'build/json/store'

        flexmock(osbs.os_conf).should_receive('get_cpu_limit').and_return(cpu)
        flexmock(osbs.os_conf).should_receive('get_memory_limit').and_return(memory)
        flexmock(osbs.os_conf).should_receive('get_storage_limit').and_return(storage)

        flexmock(osbs.os_conf).should_receive('get_build_json_store').and_return(build_json_store)

        set_resource_limits_kwargs = {
            'cpu': cpu,
            'memory': memory,
            'storage': storage,
        }

        (flexmock(BuildRequestV2)
            .should_receive('set_resource_limits')
            .with_args(**set_resource_limits_kwargs)
            .times(1 if set_resource else 0))

        get_build_request_kwargs = {
            'outer_template': outer_template,
        }
        osbs.get_build_request(**get_build_request_kwargs)

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
                (flexmock(_osbs_api)
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
    @pytest.mark.parametrize('decode', [True, False])  # noqa
    def test_build_logs_api(self, osbs, decode):
        logs = osbs.get_build_logs(TEST_BUILD, decode=decode)
        if decode:
            assert isinstance(logs, six.string_types)
            assert logs == u"   líne 1"
        else:
            assert isinstance(logs, six.binary_type)
            assert logs == u"   líne 1   \n".encode('utf-8')

    # osbs is a fixture here
    @pytest.mark.parametrize('decode', [True, False])  # noqa
    def test_build_logs_api_follow(self, osbs, decode):
        logs = osbs.get_build_logs(TEST_BUILD, follow=True, decode=decode)
        assert isinstance(logs, GeneratorType)
        content = next(logs)
        if decode:
            assert isinstance(content, six.string_types)
            assert content == u"   líne 1"
        else:
            assert isinstance(content, six.binary_type)
            assert content == u"   líne 1   \n".encode('utf-8')
        with pytest.raises(StopIteration):
            assert next(logs)

    # osbs is a fixture here
    @pytest.mark.parametrize('follow', [True, False])  # noqa
    def test_orchestrator_build_logs_api(self, osbs, follow):
        logs = osbs.get_orchestrator_build_logs(TEST_ORCHESTRATOR_BUILD, follow=follow)
        assert isinstance(logs, GeneratorType)
        orchestrator_logs = []
        worker_logs = []
        for entry in logs:
            assert entry.platform is None or entry.platform == u'x86_64'
            assert isinstance(entry.line, six.string_types)
            if entry.platform is None:
                orchestrator_logs.append(entry.line)
            else:
                worker_logs.append(entry.line)

        assert orchestrator_logs == ORCHESTRATOR_LOGS
        assert worker_logs == WORKER_LOGS

    # osbs is a fixture here
    def test_orchestrator_build_logs_api_badlog(self, osbs):  # noqa
        logs = osbs.get_orchestrator_build_logs(TEST_BUILD)
        assert isinstance(logs, GeneratorType)
        (platform, content) = next(logs)
        assert platform is None
        assert isinstance(content, six.string_types)
        assert content == u"   líne 1"

    def test_orchestrator_build_logs_no_logs(self, osbs):  # noqa:F811
        flexmock(osbs).should_receive('get_build_logs').and_return(None)
        logs = osbs.get_orchestrator_build_logs(TEST_BUILD)
        assert isinstance(logs, GeneratorType)
        assert list(logs) == []

    @pytest.mark.parametrize(('build_from', 'is_image', 'valid'), (
        ('image:registry.example.com/buildroot:2.0', True, 'registry.example.com/buildroot:2.0'),
        ('imagestream:buildroot-stream:v1.0', False, 'buildroot-stream:v1.0'),
        ('registry.example.com/buildroot:2.0', False, False)
    ))
    def test_build_image(self, build_from, is_image, valid):
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                [default]
                openshift_url = /
                build_from = {build_from}
                """.format(build_json_dir='inputs', build_from=build_from)))
            fp.flush()
            config = Configuration(fp.name, conf_section='default')
            osbs_obj = OSBS(config)

        assert config.get_build_from() == build_from

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        flexmock(OSBS, _create_build_directly=request_as_response)

        if valid:
            req = osbs_obj.create_build(target=TEST_TARGET,
                                        **REQUIRED_BUILD_ARGS)
        else:
            with pytest.raises(OsbsValidationException):
                req = osbs_obj.create_build(target=TEST_TARGET,
                                            **REQUIRED_BUILD_ARGS)
            return

        img = req.json['spec']['strategy']['customStrategy']['from']['name']
        kind = req.json['spec']['strategy']['customStrategy']['from']['kind']

        assert img == valid
        if is_image:
            assert kind == 'DockerImage'
        else:
            assert kind == 'ImageStreamTag'

    def test_worker_build_image_with_platform_node(self):
        build_image = 'registry.example.com/buildroot:2.0'
        build_from = 'image:' + build_image
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = {build_json_dir}
                [default]
                openshift_url = /
                node_selector.meal = breakfast=bacon.com, lunch=ham.com
                build_from = {build_from}
                """.format(build_json_dir='inputs', build_from=build_from)))
            fp.flush()
            config = Configuration(fp.name, conf_section='default')
            osbs_obj = OSBS(config)

        assert config.get_build_from() == build_from

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'platform': 'meal',
            'release': 'bacon',
            'outer_template': WORKER_OUTER_TEMPLATE,
            'customize_conf': WORKER_CUSTOMIZE_CONF,
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
        }

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        flexmock(OSBS, _create_build_directly=request_as_response)

        req = osbs_obj.create_worker_build(**kwargs)
        img = req.json['spec']['strategy']['customStrategy']['from']['name']
        assert img == build_image
        node_selector = req.json['spec']['nodeSelector']
        assert node_selector == {'breakfast': 'bacon.com', 'lunch': 'ham.com'}

    @pytest.mark.parametrize(('koji_task_id', 'count'), (  # noqa:F811
        (123456789, 2),
        (123459876, 1),
        (987654321, 0),
    ))
    def test_not_cancelled_buils(self, openshift, koji_task_id, count):
        config = Configuration(conf_name=None)
        osbs_obj = OSBS(config)
        osbs_obj.os = openshift

        builds_list = osbs_obj._get_not_cancelled_builds_for_koji_task(koji_task_id)
        assert len(builds_list) == count

    # osbs is a fixture here
    def test_create_build_flatpak(self, osbs):  # noqa
        mock_config = MockConfiguration(is_flatpak=True, modules=TEST_MODULES)
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=mock_config)))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'flatpak': True,
            'user': TEST_USER,
            'yum_repourls': None,
            'koji_task_id': None,
            'scratch': False,
            'build_type': 'orchestrator',
            'reactor_config_override': {'flatpak': {'base_image': 'base_image'},
                                        'source_registry': {'url': 'source_registry'}},
        }

        # Sanity check the user params we create
        old = osbs.get_user_params

        def get_user_params(**kwargs):
            user_params = old(**kwargs)
            assert user_params.base_image is None
            assert user_params.component == 'mod_name'
            assert user_params.imagestream_name == 'mod_name'
            return user_params
        osbs.get_user_params = get_user_params

        response = osbs.create_build(**kwargs)
        assert isinstance(response, BuildResponse)

    # osbs is a fixture here
    @pytest.mark.parametrize(('build_flatpak', 'repo_flatpak', 'match_exception'), [
        (True, False, "repository doesn't have a container.yaml with a flatpak: section"),
        (False, True, "repository has a container.yaml with a flatpak: section"),
    ])
    def test_create_build_flatpak_mismatch(self, osbs,
                                           build_flatpak, repo_flatpak, match_exception):  # noqa
        mock_config = MockConfiguration(is_flatpak=repo_flatpak, modules=TEST_MODULES)
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=mock_config)))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'flatpak': build_flatpak,
            'user': TEST_USER,
            'yum_repourls': None,
            'koji_task_id': None,
            'scratch': False,
            'build_type': 'orchestrator',
            'reactor_config_override': {'flatpak': {'base_image': 'base_image'},
                                        'source_registry': {'url': 'source_registry'}},
        }

        with pytest.raises(OsbsException, match=match_exception):
            osbs.create_build(**kwargs)

    @pytest.mark.skip(reason="tekton openshift class doesn't have istag manipulation")
    @pytest.mark.parametrize(('kind', 'expect_name'), [
        ('ImageStreamTag', 'registry:5000/buildroot:latest'),
        ('DockerImage', 'buildroot:latest'),
    ])
    @pytest.mark.parametrize(('build_variation', 'running_builds', 'check_running', 'fail'), (
        ('scratch', False, False, False),
        ('isolated', False, True, False),
        ('isolated', True, True, True),
    ))
    def test_direct_build(self, kind, expect_name, build_variation, running_builds,
                          check_running, fail):
        config = Configuration(conf_name=None)
        osbs_obj = OSBS(config)

        build_json = {
            'apiVersion': "image.openshift.io/v1",

            'metadata': {
                'labels': {
                    'git-repo-name': 'reponame',
                    'git-branch': 'branch',
                    build_variation: 'true',
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
        if build_variation == 'isolated':
            build_json['metadata']['labels']['isolated-release'] = "1.1"

        build_request = flexmock(
            render=lambda: build_json,
            has_ist_trigger=lambda: False,
            scratch=(build_variation == 'scratch'),
            isolated=(build_variation == 'isolated'),
        )

        updated_build_json = copy.deepcopy(build_json)
        updated_build_json['kind'] = 'Build'
        updated_build_json['spec']['serviceAccount'] = 'builder'
        updated_build_json['metadata']['annotations'] = {}
        updated_build_json['metadata']['annotations']['from'] = json.dumps({
            'kind': kind,
            'name': 'buildroot:latest'})

        img = updated_build_json['spec']['strategy']['customStrategy']['from']
        img['kind'] = 'DockerImage'
        img['name'] = expect_name

        if kind == 'ImageStreamTag':
            (flexmock(osbs_obj.os)
                .should_receive('get_image_stream_tag')
                .with_args('buildroot:latest')
                .once()
                .and_return(flexmock(json=lambda: {
                    "apiVersion": "image.openshift.io/v1",
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
            .times(0 if fail else 1))

        if check_running:
            builds_list = []
            if running_builds:
                build = flexmock()
                build.should_receive('get_build_name').and_return('build-1')
                builds_list.append(build)

            (flexmock(osbs_obj)
                .should_receive('list_builds')
                .with_args(running=True, labels=build_json['metadata']['labels'])
                .once()
                .and_return(builds_list))

        if build_variation == 'scratch':
            create_method = osbs_obj._create_scratch_build
        else:
            create_method = osbs_obj._create_isolated_build

        if fail:
            with pytest.raises(RuntimeError) as exc_info:
                create_method(build_request)
            assert 'already running' in str(exc_info.value)
        else:
            build_response = create_method(build_request)
            assert build_response.json == {'spam': 'maps'}

    @pytest.mark.parametrize(('variation', 'delegate_method'), (
        ('isolated', '_create_isolated_build'),
        ('scratch', '_create_scratch_build'),
    ))
    def test_create_direct_build(self, variation, delegate_method):
        config = Configuration(conf_file=None, build_from='image:buildroot:latest')
        osbs_obj = OSBS(config)

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'component': TEST_COMPONENT,
            'target': TEST_TARGET,
            'yum_repourls': None,
            'koji_task_id': None,
            variation: True,
        }

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        (flexmock(osbs_obj)
            .should_receive(delegate_method)
            .once()
            .and_return(flexmock(json=lambda: {'spam': 'maps'})))

        build_response = osbs_obj.create_build(**kwargs)
        assert build_response.json() == {'spam': 'maps'}

    @pytest.mark.parametrize(('variation', 'delegate_method'), (  # noqa:F811
        ('isolated', '_create_isolated_build'),
        ('scratch', '_create_scratch_build'),
        (None, '_create_build_directly'),
    ))
    @pytest.mark.parametrize(('koji_task_id', 'use_build', 'exc'), (
        (123456789, False, OsbsException),
        (123459876, True, None),
        (987654321, False, None),
    ))
    def test_use_already_created_build(self, openshift, variation, delegate_method,
                                       koji_task_id, use_build, exc):
        config = Configuration(conf_file=None, build_from='image:buildroot:latest')
        osbs_obj = OSBS(config)
        osbs_obj.os = openshift

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'component': TEST_COMPONENT,
            'target': TEST_TARGET,
            'yum_repourls': None,
            'koji_task_id': koji_task_id,
            'build_type': BUILD_TYPE_ORCHESTRATOR,
        }
        if variation:
            kwargs[variation] = True

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        if use_build:
            (flexmock(osbs_obj)
                .should_receive(delegate_method)
                .never())

            (flexmock(osbs_obj.os)
                .should_receive('get_build')
                .once()
                .and_return(flexmock(json=lambda: {'spam': 'maps'})))
        elif exc:
            (flexmock(osbs_obj)
                .should_receive(delegate_method)
                .never())
        else:
            (flexmock(osbs_obj)
                .should_receive(delegate_method)
                .once()
                .and_return(BuildResponse({'spam': 'maps'})))

        if exc:
            with pytest.raises(OsbsException) as exc_info:
                osbs_obj.create_build(**kwargs)
            assert "Multiple builds 2 for koji task id %s" % koji_task_id in exc_info.value.message
        else:
            build_response = osbs_obj.create_build(**kwargs)
            assert build_response.json == {'spam': 'maps'}

    @pytest.mark.skip(reason="tekton openshift class doesn't have istag manipulation")
    def test_get_image_stream_tag(self):
        config = Configuration(conf_name=None)
        osbs_obj = OSBS(config)

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

    @pytest.mark.skip(reason="tekton openshift class doesn't have istag manipulation")
    def test_get_image_stream_tag_with_retry(self):
        config = Configuration(conf_name=None)
        osbs_obj = OSBS(config)

        name = 'buildroot:latest'
        (flexmock(osbs_obj.os)
            .should_receive('get_image_stream_tag_with_retry')
            .with_args(name)
            .once()
            .and_return(flexmock(json=lambda: {
                'image': {
                    'dockerImageReference': 'spam:maps',
                }
            })))

        response = osbs_obj.get_image_stream_tag_with_retry(name)
        ref = response.json()['image']['dockerImageReference']
        assert ref == 'spam:maps'

    @pytest.mark.skip(reason="tekton openshift class doesn't have istag manipulation")
    def test_get_image_stream_tag_with_retry_retries(self):
        config = Configuration(conf_name=None)
        osbs_obj = OSBS(config)

        name = 'buildroot:latest'
        (flexmock(osbs_obj.os)
            .should_call('get_image_stream_tag_with_retry')
            .with_args(name))

        class MockResponse(object):
            def __init__(self, status, json=None, content=None):
                self.status_code = status
                self._json = json or {}
                if content:
                    self.content = content

            def json(self):
                return self._json

        test_json = {'image': {'dockerImageReference': 'spam:maps'}}
        bad_response = MockResponse(http_client.NOT_FOUND, None, "not found failure")
        good_response = MockResponse(http_client.OK, test_json)

        (flexmock(osbs_obj.os)
            .should_receive('_get')
            .times(2)
            .and_return(bad_response)
            .and_return(good_response))

        response = osbs_obj.get_image_stream_tag_with_retry(name)

        ref = response.json()['image']['dockerImageReference']
        assert ref == 'spam:maps'

    def test_reactor_config_secret(self):
        with NamedTemporaryFile(mode='wt') as fp:
            fp.write(dedent("""\
                [general]
                build_json_dir = inputs
                [default]
                openshift_url = /
                build_from = image:buildroot:latest
                """))
            fp.flush()
            config = Configuration(fp.name, conf_section='default')
            osbs_obj = OSBS(config)

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        flexmock(OSBS, _create_build_directly=request_as_response)

        required_args = copy.deepcopy(REQUIRED_BUILD_ARGS)
        reactor_config_override = {'required_secrets': ['mysecret'],
                                   'source_registry': {'url': 'source_registry'}}
        required_args['reactor_config_override'] = reactor_config_override
        req = osbs_obj.create_build(target=TEST_TARGET, **required_args)
        secrets = req.json['spec']['strategy']['customStrategy']['secrets']
        expected_secret = {
            'mountPath': '/var/run/secrets/atomic-reactor/mysecret',
            'secretSource': {
                'name': 'mysecret',
            }
        }
        assert expected_secret in secrets

    # osbs is a fixture here
    @pytest.mark.parametrize('isolated', [True, False])  # noqa
    def test_flatpak_args_from_cli(self, caplog, osbs, isolated):
        class MockArgs(object):
            def __init__(self, isolated):
                self.platform = None
                self.release = None
                self.platforms = 'platforms'
                self.worker = False
                self.orchestrator = True
                self.scratch = None
                self.isolated = isolated
                self.koji_upload_dir = None
                self.git_uri = None
                self.git_ref = None
                self.git_branch = TEST_GIT_BRANCH
                self.koji_parent_build = None
                self.flatpak = True
                self.signing_intent = 'release'
                self.compose_ids = [1, 2]
                self.operator_csv_modifications_url = None
                self.instance = 'default'
                self.config = 'default'
                self.can_orchestrate = True
                self.build_from = 'some'

        expected_kwargs = {
            'platform': None,
            'scratch': None,
            'isolated': False,
            'platforms': 'platforms',
            'release': None,
            'flatpak': True,
            'git_uri': None,
            'git_ref': None,
            'git_branch': TEST_GIT_BRANCH,
            'user': None,
            'tag': None,
            'target': None,
            'yum_repourls': None,
            'dependency_replacements': None,
            'koji_parent_build': None,
            'signing_intent': 'release',
            'compose_ids': [1, 2],
            'operator_csv_modifications_url': None,
        }

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=MockConfiguration(modules=TEST_MODULES))))

        args = MockArgs(isolated)

        if not isolated:
            # and_raise is called to prevent cmd_build to continue
            # as we only want to check if arguments are correct
            (flexmock(OSBS)
                .should_receive("create_orchestrator_build")
                .once()
                .with_args(**expected_kwargs)
                .and_raise(CustomTestException))
            with pytest.raises(CustomTestException):
                cmd_build(args)
        else:
            with pytest.raises(OsbsException) as exc_info:
                cmd_build(args)
            assert isinstance(exc_info.value.cause, ValueError)
            assert "Flatpak build cannot be isolated" in exc_info.value.message

    @pytest.mark.parametrize('isolated', [True, False])
    def test_operator_csv_modifications_url_cli(self, osbs, isolated):
        """Test if operator_csv_modifications_url option without isolated
        option raises proper exception"""
        class MockArgs(object):
            def __init__(self, isolated):
                self.platform = None
                self.release = None
                self.platforms = 'platforms'
                self.worker = False
                self.orchestrator = True
                self.scratch = None
                self.isolated = isolated
                self.koji_upload_dir = None
                self.git_uri = None
                self.git_ref = None
                self.git_branch = TEST_GIT_BRANCH
                self.koji_parent_build = None
                self.signing_intent = 'release'
                self.compose_ids = None
                self.operator_csv_modifications_url = "https://example.com/updates.json"
                self.instance = 'default'
                self.config = 'default'
                self.can_orchestrate = True
                self.build_from = 'some'

        expected_kwargs = {
            'platform': None,
            'scratch': None,
            'isolated': True,
            'platforms': 'platforms',
            'release': None,
            'git_uri': None,
            'git_ref': None,
            'git_branch': TEST_GIT_BRANCH,
            'user': None,
            'tag': None,
            'target': None,
            'yum_repourls': None,
            'dependency_replacements': None,
            'koji_parent_build': None,
            'signing_intent': 'release',
            'compose_ids': None,
            'operator_csv_modifications_url': "https://example.com/updates.json",
        }

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(None, None, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=MockConfiguration(modules=TEST_MODULES))))

        args = MockArgs(isolated)

        if isolated:
            # and_raise is called to prevent cmd_build to continue
            # as we only want to check if arguments are correct
            (flexmock(OSBS)
                .should_receive("create_orchestrator_build")
                .once()
                .with_args(**expected_kwargs)
                .and_raise(CustomTestException))
            with pytest.raises(CustomTestException):
                cmd_build(args)
        else:
            with pytest.raises(OsbsException) as exc_info:
                cmd_build(args)
            assert (
                "Only isolated build can update operator CSV metadata" in str(exc_info.value)
            )

    # osbs is a fixture here
    @pytest.mark.parametrize('branch_name', [  # noqa
        TEST_GIT_BRANCH,
        '',
        None
    ])
    def test_do_create_prod_build_branch_required(self, osbs, branch_name):
        outer_template = DEFAULT_OUTER_TEMPLATE
        customize_conf = DEFAULT_CUSTOMIZE_CONF
        repo_info = self.mock_repo_info()

        kwargs = {'reactor_config_override': {'source_registry': {'url': 'source_registry'}}}
        user_params = BuildUserParams.make_params(
            build_json_dir=osbs.os_conf.get_build_json_store(),
            base_image='fedora23/python',
            build_from='image:whatever',
            build_conf=osbs.os_conf,
            name_label='whatever', repo_info=repo_info, user=TEST_USER,
            build_type=BUILD_TYPE_ORCHESTRATOR,
            **kwargs
        )

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch_name, depth=None)
            .and_return(repo_info))

        (flexmock(osbs)
            .should_receive('get_user_params')
            .and_return(user_params))

        (flexmock(osbs)
            .should_call('get_build_request')
            .with_args(outer_template=outer_template,
                       customize_conf=customize_conf,
                       user_params=user_params,
                       repo_info=repo_info))

        if branch_name:
            response = osbs._do_create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                                  branch_name, user=TEST_USER,
                                                  outer_template=outer_template,
                                                  customize_conf=customize_conf,
                                                  build_type=BUILD_TYPE_ORCHESTRATOR)
            assert isinstance(response, BuildResponse)
        else:
            with pytest.raises(OsbsException):
                osbs._do_create_prod_build(TEST_GIT_URI, TEST_GIT_REF,
                                           branch_name, user=TEST_USER,
                                           outer_template=outer_template,
                                           customize_conf=customize_conf,
                                           build_type=BUILD_TYPE_ORCHESTRATOR)

    def test_do_create_prod_build_missing_params(self, osbs, caplog):  # noqa
        with pytest.raises(OsbsException):
            osbs._do_create_prod_build(user=TEST_USER,
                                       outer_template=DEFAULT_OUTER_TEMPLATE,
                                       customize_conf=DEFAULT_CUSTOMIZE_CONF,
                                       build_type=BUILD_TYPE_ORCHESTRATOR)
            assert 'git_uri' in caplog.text

        with pytest.raises(OsbsException):
            osbs._do_create_prod_build(TEST_GIT_URI, user=TEST_USER,
                                       outer_template=DEFAULT_OUTER_TEMPLATE,
                                       customize_conf=DEFAULT_CUSTOMIZE_CONF,
                                       build_type=BUILD_TYPE_ORCHESTRATOR)
            assert 'git_uri' not in caplog.text
            assert 'git_ref' in caplog.text

        with pytest.raises(OsbsException):
            osbs._do_create_prod_build(TEST_GIT_URI, TEST_GIT_REF, user=TEST_USER,
                                       outer_template=DEFAULT_OUTER_TEMPLATE,
                                       customize_conf=DEFAULT_CUSTOMIZE_CONF,
                                       build_type=BUILD_TYPE_ORCHESTRATOR)
            assert 'git_uri' not in caplog.text
            assert 'git_ref' not in caplog.text
            assert 'git_branch' in caplog.text

        with pytest.raises(OsbsException):
            osbs._do_create_prod_build(user=TEST_USER,
                                       outer_template=DEFAULT_OUTER_TEMPLATE,
                                       customize_conf=DEFAULT_CUSTOMIZE_CONF,
                                       build_type=BUILD_TYPE_ORCHESTRATOR)
            assert 'git_uri' in caplog.text
            assert 'git_ref' in caplog.text
            assert 'git_branch' in caplog.text

    # osbs is a fixture here
    def test_config_map(self, osbs):  # noqa
        with open(os.path.join(INPUTS_PATH, "config_map.json")) as fp:
            raw = fp.read()
        mock = flexmock(sys.modules['__builtin__' if six.PY2 else 'builtins'])
        mock.should_call('open')  # set the fall-through
        (mock.should_receive('open')
            .with_args('inputs/config_map.json')
            .and_return(flexmock(read=lambda: raw)))

        conf_name = 'special-config'
        none_str = "special.none"

        data = {
            "special.how": "very",
            "special.type": {"quark": "charm"},
            "config.yaml": "version:1",
            "config.yml": "version:2",
            "config.ymlll": {"version": 3},
            "config.json": {"version": 4}
        }

        config_map = osbs.create_config_map(conf_name, data)
        assert isinstance(config_map, ConfigMapResponse)

        (flexmock(yaml)
            .should_call('load')
            .times(6))  # 2*2 in get_data, 2 in get_data_by_key

        assert config_map.get_data() == data
        config_map = osbs.get_config_map(conf_name)
        assert isinstance(config_map, ConfigMapResponse)
        assert config_map.get_data() == data
        assert not config_map.get_data_by_key(none_str)

        for key, value in data.items():
            assert config_map.get_data_by_key(key) == value

        config_map = osbs.delete_config_map(conf_name)
        assert config_map is None

    def test_retries_disabled(self, osbs):  # noqa
        (flexmock(osbs.os._con)
            .should_call('get')
            .with_args("/apis/build.openshift.io/v1/namespaces/default/builds/", headers={},
                       verify_ssl=True, retries_enabled=False))
        with osbs.retries_disabled():
            response_list = osbs.list_builds()
            assert response_list is not None

        (flexmock(osbs.os._con)
            .should_call('get')
            .with_args("/api/v1/namespaces/default/configmaps/test", headers={},
                       verify_ssl=True, retries_enabled=True))
        # Verify that retries are re-enabled after contextmanager exits
        with pytest.raises(OsbsException):
            osbs.get_config_map('test')

    @pytest.mark.parametrize(('label_type', 'label_value', 'raise_exception'), [
        (Labels.LABEL_TYPE_RELEASE, '1release with space', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1release_with/slash', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1release_with-dash', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, 'release.1', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1release.1.', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1release.1_', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1release..1', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1release__1', OsbsValidationException),
        (Labels.LABEL_TYPE_RELEASE, '1.release_with5', None),
        (Labels.LABEL_TYPE_RELEASE, '1_release_with5', None),
        (Labels.LABEL_TYPE_RELEASE, '123.54.release_with5', None),
        (Labels.LABEL_TYPE_RELEASE, '123.54.release_withalpha', None),
        (Labels.LABEL_TYPE_RELEASE, '', None),
        (Labels.LABEL_TYPE_VERSION, '1versionwith space', None),
        (Labels.LABEL_TYPE_VERSION, '1version_with/slash', None),
        (Labels.LABEL_TYPE_VERSION, '1version_with-dash', OsbsValidationException),
        (Labels.LABEL_TYPE_VERSION, 'version.1', None),
        (Labels.LABEL_TYPE_VERSION, '1version.1.', None),
        (Labels.LABEL_TYPE_VERSION, '', None),
    ])
    def test_raise_error_if_release_or_version_label_is_invalid(self, osbs, label_type,
                                                                label_value, raise_exception):
        required_args = copy.deepcopy(REQUIRED_BUILD_ARGS)
        # just so we stop right after checking labels when error_msgs are empty
        required_args['signing_intent'] = 'release'
        required_args['compose_ids'] = [1, 2, 3, 4]

        if raise_exception:
            if label_type == Labels.LABEL_TYPE_RELEASE:
                exc_msg = "release label doesn't have proper format"
            elif label_type == Labels.LABEL_TYPE_VERSION:
                exc_msg = "version label '{}' contains not allowed chars".format(label_value)
        else:
            exc_msg = "Please only define signing_intent -OR- compose_ids, not both"

        mocked_df_parser = MockDfParser()
        mocked_df_parser.labels[Labels.LABEL_NAMES[label_type][0]] = label_value

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_df_parser=mocked_df_parser)))

        with pytest.raises(OsbsValidationException) as exc:
            osbs.create_build(target=TEST_TARGET, **required_args)
        assert exc_msg in str(exc.value)

    @pytest.mark.parametrize(('flatpak'), (True, False))  # noqa
    def test_do_create_prod_build_no_dockerfile(self, osbs, flatpak, tmpdir):
        class MockDfParserNoDf(object):
            dockerfile_path = tmpdir

            @property
            def labels(self):
                raise IOError

        (flexmock(utils)
         .should_receive('get_repo_info')
         .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
         .and_return(self.mock_repo_info(mock_df_parser=MockDfParserNoDf(),
                                         mock_config=MockConfiguration(is_flatpak=flatpak,
                                                                       modules=TEST_MODULES))))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'outer_template': DEFAULT_OUTER_TEMPLATE,
            'customize_conf': DEFAULT_CUSTOMIZE_CONF,
            'build_type': 'orchestrator',
        }
        if not flatpak:
            with pytest.raises(RuntimeError) as exc:
                osbs._do_create_prod_build(**kwargs)
            assert 'Could not parse Dockerfile in %s' % tmpdir in str(exc.value)
        else:
            kwargs['flatpak'] = True
            kwargs['reactor_config_override'] = {'flatpak': {'base_image': 'base_image'},
                                                 'source_registry': {'url': 'source_registry'}}
            response = osbs._do_create_prod_build(**kwargs)
            assert isinstance(response, BuildResponse)

    @pytest.mark.parametrize(('field_selector'), (None, 'test'))  # noqa
    def test_watch_builds(self, osbs, field_selector):
        mocked_df_parser = MockDfParser()
        (flexmock(utils)
         .should_receive('get_repo_info')
         .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
         .and_return(self.mock_repo_info(mock_df_parser=mocked_df_parser)))

        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': TEST_USER,
            'outer_template': DEFAULT_OUTER_TEMPLATE,
            'customize_conf': DEFAULT_CUSTOMIZE_CONF,
            'build_type': 'orchestrator',
            'reactor_config_override': {'source_registry': {'url': 'source_registry'}}
        }
        osbs._do_create_prod_build(**kwargs)
        with pytest.raises(ValueError):
            for changetype, _ in osbs.watch_builds(field_selector):
                assert changetype is None

    def test_create_source_container_pipeline_run(self):
        buildroot_image = 'buildroot:latest'
        rcm = 'rcm'
        namespace = 'test-namespace'
        with NamedTemporaryFile(mode="wt") as fp:
            fp.write("""
    [general]
    build_json_dir = {build_json_dir}
    [default_source]
    openshift_url = /
    namespace = {namespace}
    use_auth = false
    pipeline_run_path = {pipeline_run_path}
    build_from = image:{buildroot_image}
    reactor_config_map = {rcm}
    """.format(build_json_dir="inputs", namespace=namespace,
               pipeline_run_path=TEST_PIPELINE_RUN_TEMPLATE,
               buildroot_image=buildroot_image, rcm=rcm))
            fp.flush()
            dummy_config = Configuration(fp.name, conf_section='default_source')
            osbs = OSBS(dummy_config)

        with open(TEST_PIPELINE_RUN_TEMPLATE) as f:
            yaml_data = f.read()
        pipeline_run_expect = yaml.safe_load(yaml_data)
        random_postfix = 'sha-timestamp'
        (flexmock(utils)
            .should_receive('generate_random_postfix')
            .and_return(random_postfix))

        pipeline_name = pipeline_run_expect['spec']['pipelineRef']['name']
        pipeline_run_name = f'{pipeline_name}-{random_postfix}'

        flexmock(PipelineRun).should_receive('start_pipeline_run').and_return(Mock_Start_Pipeline())
        pipeline_run = osbs.create_source_container_pipeline_run(
            target=TEST_TARGET,
            signing_intent='signing_intent',
            **REQUIRED_SOURCE_CONTAINER_BUILD_ARGS
        )
        assert isinstance(pipeline_run, PipelineRun)

        assert pipeline_run.data['metadata']['name'] == pipeline_run_name

        for ws in pipeline_run.data['spec']['workspaces']:
            if ws['name'] == PRUN_TEMPLATE_REACTOR_CONFIG_WS:
                assert ws['configmap']['name'] == rcm

            if ws['name'] == PRUN_TEMPLATE_BUILD_DIR_WS:
                assert ws['volumeClaimTemplate']['metadata']['namespace'] == namespace

        for param in pipeline_run.data['spec']['params']:
            if param['name'] == PRUN_TEMPLATE_USER_PARAMS:
                assert param['value'] != {}

    # osbs_source is a fixture here
    @pytest.mark.parametrize(('additional_kwargs'), (  # noqa
        {'component': None, 'sources_for_koji_build_nvr': 'build_nvr'},
        {'component': 'build_component', 'sources_for_koji_build_nvr': None},
        {'component': None, 'sources_for_koji_build_nvr': None},
    ))
    def test_create_source_container_required_args(self, osbs_source, additional_kwargs):
        flexmock(PipelineRun).should_receive('start_pipeline_run').and_return(Mock_Start_Pipeline())
        with pytest.raises(OsbsValidationException):
            osbs_source.create_source_container_pipeline_run(
                target=TEST_TARGET,
                signing_intent='signing_intent',
                **additional_kwargs)
