# -*- coding: utf-8 -*-
"""
Copyright (c) 2015-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

from flexmock import flexmock
import pytest
import json
import copy
import sys
import datetime
import random
from tempfile import NamedTemporaryFile

from osbs.api import OSBS, osbsapi
from osbs.conf import Configuration
from osbs.exceptions import (OsbsValidationException, OsbsException, OsbsResponseException)
from osbs.constants import (REPO_CONTAINER_CONFIG, PRUN_TEMPLATE_USER_PARAMS,
                            PRUN_TEMPLATE_REACTOR_CONFIG_WS, PRUN_TEMPLATE_BUILD_DIR_WS,
                            PRUN_TEMPLATE_CONTEXT_DIR_WS)
from osbs import utils
from osbs.utils.labels import Labels
from osbs.repo_utils import RepoInfo, RepoConfiguration, ModuleSpec
from osbs.build.user_params import BuildUserParams, SourceContainerUserParams

from tests.constants import (TEST_COMPONENT, TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI,
                             TEST_TARGET, TEST_USER, TEST_KOJI_TASK_ID, TEST_VERSION,
                             TEST_PIPELINE_RUN_TEMPLATE, TEST_PIPELINE_REPLACEMENTS_TEMPLATE,
                             TEST_OCP_NAMESPACE)
from osbs.tekton import PipelineRun


REQUIRED_BUILD_ARGS = {
    'git_uri': TEST_GIT_URI,
    'git_ref': TEST_GIT_REF,
    'git_branch': TEST_GIT_BRANCH,
    'user': TEST_USER,
}

REQUIRED_SOURCE_CONTAINER_BUILD_ARGS = {
    'user': TEST_USER,
    'sources_for_koji_build_nvr': 'test-1-123',
    'component': TEST_COMPONENT,
}

TEST_MODULES = ['mod_name:mod_stream:mod_version']


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


class Mock_Start_Pipeline(object):
    def json(self):
        return {}


class TestOSBS(object):

    def mock_repo_info(self, mock_df_parser=None, mock_config=None):
        mock_df_parser = mock_df_parser or MockDfParser()
        config = mock_config or MockConfiguration()
        return RepoInfo(mock_df_parser, config)

    def mock_start_pipeline(self):
        flexmock(PipelineRun).should_receive('start_pipeline_run').and_return(Mock_Start_Pipeline())

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
        with pytest.raises(OsbsException):
            dummy_api_function()

    def test_create_build_invalid_yaml(self, osbs_binary, tmpdir, monkeypatch):  # noqa
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
            osbs_binary.create_binary_container_pipeline_run(**kwargs)

        err_msg = 'Failed to load or validate container file "{}"'.format(repo_config)
        assert err_msg in str(exc_info.value)

    def test_create_build_missing_name_label(self, osbs_binary):  # noqa
        class MockParser(object):
            labels = {}
            baseimage = 'fedora23/python'
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(MockParser())))

        with pytest.raises(OsbsValidationException):
            osbs_binary.create_binary_container_pipeline_run(target=TEST_TARGET,
                                                             **REQUIRED_BUILD_ARGS)

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
    def test_missing_required_labels(self, osbs_binary, caplog, all_labels, error_msgs):
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
            osbs_binary.create_binary_container_pipeline_run(target=TEST_TARGET, **required_args)

        if error_msgs:
            exc_msg = 'required label missing from Dockerfile'
            assert exc_msg in str(exc.value)
            for error in error_msgs:
                assert error in caplog.text
        else:
            exc_msg = "Please only define signing_intent -OR- compose_ids, not both"
            assert exc_msg in str(exc.value)

    @pytest.mark.parametrize('name,should_succeed', [  # noqa:F811
        ('fedora-25.1/something', False),
        ('fedora-25-1/something', True),
    ])
    def test_reject_invalid_name(self, osbs_binary, name, should_succeed):
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
        }

        if should_succeed:
            self.mock_start_pipeline()
            osbs_binary.create_binary_container_build(**create_build_args)
        else:
            with pytest.raises(OsbsValidationException):
                osbs_binary.create_binary_container_build(**create_build_args)

    @pytest.mark.parametrize('component_label_name', ['com.redhat.component', 'BZComponent'])  # noqa
    def test_component_is_changed_from_label(self, osbs_binary, component_label_name):
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

        self.mock_start_pipeline()
        prun = osbs_binary.create_binary_container_build(target=TEST_TARGET, **REQUIRED_BUILD_ARGS)

        for param in prun.input_data['spec']['params']:
            if param['name'] == PRUN_TEMPLATE_USER_PARAMS:
                up = json.loads(param['value'])
                assert up['component'] == component_override

    def test_missing_component_argument_doesnt_break_build(self, osbs_binary):  # noqa
        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        self.mock_start_pipeline()
        response = osbs_binary.create_binary_container_build(**REQUIRED_BUILD_ARGS)
        assert isinstance(response, PipelineRun)

    def test_create_build_flatpak(self, osbs_binary):  # noqa
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
        }

        # Sanity check the user params we create
        old = osbs_binary.get_user_params

        def get_user_params(**kwargs):
            user_params = old(**kwargs)
            assert user_params.base_image is None
            assert user_params.component == 'mod_name'
            return user_params
        osbs_binary.get_user_params = get_user_params

        self.mock_start_pipeline()
        response = osbs_binary.create_binary_container_build(**kwargs)
        assert isinstance(response, PipelineRun)

    @pytest.mark.parametrize(('build_flatpak', 'repo_flatpak', 'match_exception'), [
        (True, False, "repository doesn't have a container.yaml with a flatpak: section"),
        (False, True, "repository has a container.yaml with a flatpak: section"),
    ])
    def test_create_build_flatpak_mismatch(self, osbs_binary,
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
        }

        with pytest.raises(OsbsException, match=match_exception):
            osbs_binary.create_binary_container_build(**kwargs)

    @pytest.mark.parametrize('isolated', [True, False])  # noqa
    def test_flatpak_and_isolated(self, osbs_binary, isolated):
        """Test if flatpak with isolated option works"""

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=MockConfiguration(modules=TEST_MODULES,
                                                                          is_flatpak=True))))

        kwargs = {'git_uri': TEST_GIT_URI,
                  'git_ref': TEST_GIT_REF,
                  'git_branch': TEST_GIT_BRANCH,
                  'user': TEST_USER,
                  'release': '1.0',
                  'flatpak': True,
                  'isolated': isolated}

        self.mock_start_pipeline()
        response = osbs_binary.create_binary_container_build(**kwargs)
        assert isinstance(response, PipelineRun)

    @pytest.mark.parametrize('isolated', [True, False])  # noqa
    def test_scratch_and_isolated(self, osbs_binary, isolated):
        """Test if scratch with isolated option raises proper exception"""

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=MockConfiguration(modules=TEST_MODULES))))

        kwargs = {'git_uri': TEST_GIT_URI,
                  'git_ref': TEST_GIT_REF,
                  'git_branch': TEST_GIT_BRANCH,
                  'user': TEST_USER,
                  'scratch': True,
                  'isolated': isolated}

        if not isolated:
            self.mock_start_pipeline()
            response = osbs_binary.create_binary_container_build(**kwargs)
            assert isinstance(response, PipelineRun)
        else:
            with pytest.raises(OsbsValidationException) as exc_info:
                osbs_binary.create_binary_container_build(**kwargs)

            msg = 'Build variations are mutually exclusive. ' \
                  'Must set either scratch, isolated, or none.'
            assert msg in str(exc_info.value)

    @pytest.mark.parametrize('release', [None, '1'])
    def test_isolated_release_checks(self, osbs_binary, release):
        """Test if scratch with isolated option raises proper exception"""

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=MockConfiguration(modules=TEST_MODULES))))

        kwargs = {'git_uri': TEST_GIT_URI,
                  'git_ref': TEST_GIT_REF,
                  'git_branch': TEST_GIT_BRANCH,
                  'user': TEST_USER,
                  'release': release,
                  'isolated': True}

        with pytest.raises(OsbsValidationException) as exc_info:
            osbs_binary.create_binary_container_build(**kwargs)

        if release:
            msg = 'For isolated builds, the release value must be in the format:'
        else:
            msg = 'The release parameter is required for isolated builds.'

        assert msg in str(exc_info.value)

    @pytest.mark.parametrize('isolated', [True, False])
    def test_operator_csv_modifications_url_cli(self, osbs_binary, isolated):
        """Test if operator_csv_modifications_url option without isolated
        option raises proper exception"""

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info(mock_config=MockConfiguration(modules=TEST_MODULES))))

        kwargs = {'git_uri': TEST_GIT_URI,
                  'git_ref': TEST_GIT_REF,
                  'git_branch': TEST_GIT_BRANCH,
                  'user': TEST_USER,
                  'release': '1.0',
                  'isolated': isolated,
                  'operator_csv_modifications_url': "https://example.com/updates.json"}

        if isolated:
            self.mock_start_pipeline()
            response = osbs_binary.create_binary_container_build(**kwargs)
            assert isinstance(response, PipelineRun)
        else:
            with pytest.raises(OsbsException) as exc_info:
                osbs_binary.create_binary_container_build(**kwargs)
            assert (
                "Only isolated build can update operator CSV metadata" in str(exc_info.value)
            )

    @pytest.mark.parametrize('branch_name', [  # noqa
        TEST_GIT_BRANCH,
        '',
        None
    ])
    def test_do_create_prod_build_branch_required(self, osbs_binary, branch_name):
        repo_info = self.mock_repo_info()
        self.mock_start_pipeline()

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=branch_name, depth=None)
            .and_return(repo_info))

        kwargs = {'git_uri': TEST_GIT_URI,
                  'git_ref': TEST_GIT_REF,
                  'git_branch': branch_name,
                  'user': TEST_USER}

        if branch_name:
            response = osbs_binary.create_binary_container_build(**kwargs)
            assert isinstance(response, PipelineRun)
        else:
            with pytest.raises(OsbsException):
                osbs_binary.create_binary_container_build(**kwargs)

    def test_do_create_prod_build_missing_params(self, osbs_binary, caplog):  # noqa
        with pytest.raises(OsbsException):
            osbs_binary.create_binary_container_pipeline_run(user=TEST_USER)
            assert 'git_uri' in caplog.text
            assert 'git_ref' in caplog.text
            assert 'git_branch' in caplog.text

        with pytest.raises(OsbsException):
            osbs_binary.create_binary_container_pipeline_run(git_uri=TEST_GIT_URI, user=TEST_USER)
            assert 'git_uri' not in caplog.text
            assert 'git_ref' in caplog.text
            assert 'git_branch' in caplog.text

        with pytest.raises(OsbsException):
            osbs_binary.create_binary_container_pipeline_run(git_uri=TEST_GIT_URI,
                                                             git_ref=TEST_GIT_REF,
                                                             user=TEST_USER)
            assert 'git_uri' not in caplog.text
            assert 'git_ref' not in caplog.text
            assert 'git_branch' in caplog.text

    def test_retries_disabled(self, osbs_binary):  # noqa
        pipeline_run_name = 'test-pipeline'
        prun = PipelineRun(os=osbs_binary.os, pipeline_run_name=pipeline_run_name,
                           pipeline_run_data={})
        get_info_url = f"/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/" \
                       f"pipelineruns/{pipeline_run_name}"

        (flexmock(osbs_binary.os._con)
            .should_receive('get')
            .with_args(get_info_url, headers={},
                       verify_ssl=True, retries_enabled=False).and_return(Mock_Start_Pipeline()))

        with osbs_binary.retries_disabled():
            response_list = prun.get_info()
            assert response_list is not None

        pipeline_run_name = 'test-pipeline2'
        prun = PipelineRun(os=osbs_binary.os, pipeline_run_name=pipeline_run_name,
                           pipeline_run_data={})
        get_info_url = f"/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/" \
                       f"pipelineruns/{pipeline_run_name}"

        (flexmock(osbs_binary.os._con)
            .should_receive('get')
            .with_args(get_info_url, headers={},
                       verify_ssl=True, retries_enabled=True).and_return(Mock_Start_Pipeline()))

        # Verify that retries are re-enabled after contextmanager exits
        prun.get_info()
        assert response_list is not None

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
    def test_raise_error_if_release_or_version_label_is_invalid(self, osbs_binary, label_type,
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
            osbs_binary.create_binary_container_pipeline_run(target=TEST_TARGET, **required_args)
        assert exc_msg in str(exc.value)

    @pytest.mark.parametrize(('flatpak'), (True, False))  # noqa
    def test_create_binary_pipeline_no_dockerfile(self, osbs_binary, flatpak, tmpdir):
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
        }
        if not flatpak:
            with pytest.raises(OsbsException) as exc:
                osbs_binary.create_binary_container_pipeline_run(**kwargs)
            assert 'Could not parse Dockerfile in %s' % tmpdir in str(exc.value)
        else:
            kwargs['flatpak'] = True
            kwargs['reactor_config_override'] = {'flatpak': {'base_image': 'base_image'},
                                                 'source_registry': {'url': 'source_registry'}}
            self.mock_start_pipeline()
            response = osbs_binary.create_binary_container_pipeline_run(**kwargs)
            assert isinstance(response, PipelineRun)

    @pytest.mark.parametrize(('isolated', 'scratch', 'release'), [
        (True, False, "1.0"),
        (False, True, None),
        (False, False, None),
        (False, False, "1.0"),
    ])
    @pytest.mark.parametrize('koji_task_id', [None, TEST_KOJI_TASK_ID])
    def test_create_binary_container_pipeline_run(self, koji_task_id,
                                                  isolated, scratch, release):
        rcm = 'rcm'
        rcm_scratch = 'rcm_scratch'
        with NamedTemporaryFile(mode="wt") as fp:
            fp.write("""
    [default_binary]
    openshift_url = /
    namespace = {namespace}
    use_auth = false
    pipeline_run_path = {pipeline_run_path}
    reactor_config_map = {rcm}
    reactor_config_map_scratch = {rcm_scratch}
    """.format(namespace=TEST_OCP_NAMESPACE, pipeline_run_path=TEST_PIPELINE_RUN_TEMPLATE,
               rcm=rcm, rcm_scratch=rcm_scratch))
            fp.flush()
            dummy_config = Configuration(fp.name, conf_section='default_binary')
            osbs = OSBS(dummy_config)

        random_postfix = 'sha-timestamp'
        (flexmock(utils)
            .should_receive('generate_random_postfix')
            .and_return(random_postfix))

        name = utils.make_name_from_git(TEST_GIT_URI, TEST_GIT_BRANCH)
        pipeline_run_name = utils.make_name_from_git(TEST_GIT_URI, TEST_GIT_BRANCH)
        if isolated:
            pipeline_run_name = f'isolated-{random_postfix}'
        if scratch:
            pipeline_run_name = f'scratch-{random_postfix}'

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        rand = '67890'
        timestr = '20170731111111'
        (flexmock(sys.modules['osbs.build.user_params'])
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))

        (flexmock(random)
            .should_receive('randrange')
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        image_tag = f'{TEST_USER}/{TEST_COMPONENT}:{TEST_TARGET}-{rand}-{timestr}'

        self.mock_start_pipeline()
        signing_intent = 'signing_intent'
        pipeline_run = osbs.create_binary_container_pipeline_run(
            target=TEST_TARGET,
            signing_intent=signing_intent,
            koji_task_id=koji_task_id,
            isolated=isolated,
            scratch=scratch,
            release=release,
            **REQUIRED_BUILD_ARGS
        )
        assert isinstance(pipeline_run, PipelineRun)

        assert pipeline_run.input_data['metadata']['name'] == pipeline_run_name

        for ws in pipeline_run.input_data['spec']['workspaces']:
            if ws['name'] == PRUN_TEMPLATE_REACTOR_CONFIG_WS:
                if scratch:
                    assert ws['configmap']['name'] == rcm_scratch
                else:
                    assert ws['configmap']['name'] == rcm

            if ws['name'] in [PRUN_TEMPLATE_BUILD_DIR_WS, PRUN_TEMPLATE_CONTEXT_DIR_WS]:
                assert ws['volumeClaimTemplate']['metadata']['namespace'] == TEST_OCP_NAMESPACE

        for param in pipeline_run.input_data['spec']['params']:
            if param['name'] == PRUN_TEMPLATE_USER_PARAMS:
                assert param['value'] != {}

                up = json.loads(param['value'])

                expect_up = {}
                if scratch:
                    expect_up['reactor_config_map'] = rcm_scratch
                    expect_up['scratch'] = True
                else:
                    expect_up['reactor_config_map'] = rcm
                expect_up['base_image'] = MockDfParser.baseimage
                expect_up['component'] = TEST_COMPONENT
                expect_up['git_branch'] = TEST_GIT_BRANCH
                expect_up['git_ref'] = TEST_GIT_REF
                expect_up['git_uri'] = TEST_GIT_URI
                expect_up['kind'] = BuildUserParams.KIND
                if koji_task_id:
                    expect_up['koji_task_id'] = koji_task_id
                expect_up['name'] = name
                expect_up['koji_target'] = TEST_TARGET
                expect_up['user'] = TEST_USER
                expect_up['signing_intent'] = signing_intent
                if isolated:
                    expect_up['isolated'] = True
                if release:
                    expect_up['release'] = release
                expect_up['image_tag'] = image_tag

                assert up == expect_up

    def test_create_binary_container_start_fails(self, osbs_binary):
        error_msg = 'failed to create pipeline run'

        (flexmock(utils)
            .should_receive('get_repo_info')
            .with_args(TEST_GIT_URI, TEST_GIT_REF, git_branch=TEST_GIT_BRANCH, depth=None)
            .and_return(self.mock_repo_info()))

        (flexmock(PipelineRun)
            .should_receive('start_pipeline_run')
            .and_raise(OsbsResponseException(error_msg, 400)))

        with pytest.raises(OsbsResponseException) as exc:
            osbs_binary.create_binary_container_build(**REQUIRED_BUILD_ARGS)

        assert error_msg == str(exc.value)

    @pytest.mark.parametrize('scratch', [True, False])
    @pytest.mark.parametrize('koji_task_id', [None, TEST_KOJI_TASK_ID])
    def test_create_source_container_pipeline_run(self, koji_task_id, scratch):
        rcm = 'rcm'
        rcm_scratch = 'rcm_scratch'
        with NamedTemporaryFile(mode="wt") as fp:
            fp.write("""
    [default_source]
    openshift_url = /
    namespace = {namespace}
    use_auth = false
    pipeline_run_path = {pipeline_run_path}
    reactor_config_map = {rcm}
    reactor_config_map_scratch = {rcm_scratch}
    """.format(namespace=TEST_OCP_NAMESPACE, pipeline_run_path=TEST_PIPELINE_RUN_TEMPLATE,
               rcm=rcm, rcm_scratch=rcm_scratch))
            fp.flush()
            dummy_config = Configuration(fp.name, conf_section='default_source')
            osbs = OSBS(dummy_config)

        random_postfix = 'sha-timestamp'
        (flexmock(utils)
            .should_receive('generate_random_postfix')
            .and_return(random_postfix))

        pipeline_run_name = f'source-{random_postfix}'
        sources_for_koji_build_id = 123456
        signing_intent = 'signing_intent'

        rand = '67890'
        timestr = '20170731111111'
        (flexmock(sys.modules['osbs.build.user_params'])
            .should_receive('utcnow').once()
            .and_return(datetime.datetime.strptime(timestr, '%Y%m%d%H%M%S')))

        (flexmock(random)
            .should_receive('randrange')
            .with_args(10**(len(rand) - 1), 10**len(rand))
            .and_return(int(rand)))

        image_tag = f'{TEST_USER}/{TEST_COMPONENT}:{TEST_TARGET}-{rand}-{timestr}'

        self.mock_start_pipeline()
        pipeline_run = osbs.create_source_container_build(
            target=TEST_TARGET,
            signing_intent=signing_intent,
            koji_task_id=koji_task_id,
            scratch=scratch,
            sources_for_koji_build_id=sources_for_koji_build_id,
            **REQUIRED_SOURCE_CONTAINER_BUILD_ARGS
        )
        assert isinstance(pipeline_run, PipelineRun)

        assert pipeline_run.input_data['metadata']['name'] == pipeline_run_name

        for ws in pipeline_run.input_data['spec']['workspaces']:
            if ws['name'] == PRUN_TEMPLATE_REACTOR_CONFIG_WS:
                if scratch:
                    assert ws['configmap']['name'] == rcm_scratch
                else:
                    assert ws['configmap']['name'] == rcm

            if ws['name'] in [PRUN_TEMPLATE_BUILD_DIR_WS, PRUN_TEMPLATE_CONTEXT_DIR_WS]:
                assert ws['volumeClaimTemplate']['metadata']['namespace'] == TEST_OCP_NAMESPACE

        for param in pipeline_run.input_data['spec']['params']:
            if param['name'] == PRUN_TEMPLATE_USER_PARAMS:
                assert param['value'] != {}

                up = json.loads(param['value'])

                expect_up = {}
                if scratch:
                    expect_up['reactor_config_map'] = rcm_scratch
                    expect_up['scratch'] = True
                else:
                    expect_up['reactor_config_map'] = rcm
                expect_up['component'] = TEST_COMPONENT
                expect_up['kind'] = SourceContainerUserParams.KIND
                if koji_task_id:
                    expect_up['koji_task_id'] = koji_task_id
                expect_up['koji_target'] = TEST_TARGET
                expect_up['user'] = TEST_USER
                expect_up['image_tag'] = image_tag
                expect_up['sources_for_koji_build_id'] = sources_for_koji_build_id
                expect_up['sources_for_koji_build_nvr'] = 'test-1-123'
                expect_up['signing_intent'] = signing_intent

                assert up == expect_up


    @pytest.mark.parametrize(('additional_kwargs'), (  # noqa
        {'component': None, 'sources_for_koji_build_nvr': 'build_nvr'},
        {'component': 'build_component', 'sources_for_koji_build_nvr': None},
        {'component': None, 'sources_for_koji_build_nvr': None},
    ))
    def test_create_source_container_required_args(self, osbs_source, additional_kwargs):
        self.mock_start_pipeline()
        with pytest.raises(OsbsValidationException):
            osbs_source.create_source_container_build(
                target=TEST_TARGET,
                signing_intent='signing_intent',
                **additional_kwargs)

    def test_create_source_container_start_fails(self, osbs_source):
        error_msg = 'failed to create pipeline run'
        (flexmock(PipelineRun)
            .should_receive('start_pipeline_run')
            .and_raise(OsbsResponseException(error_msg, 400)))

        with pytest.raises(OsbsResponseException) as exc:
            osbs_source.create_source_container_build(**REQUIRED_SOURCE_CONTAINER_BUILD_ARGS)

        assert error_msg == str(exc.value)

    def test_get_build_name(self, osbs_binary):
        pipeline_run_name = 'test_pipeline'
        pipeline_run = PipelineRun(os={}, pipeline_run_name=pipeline_run_name)

        assert pipeline_run_name == osbs_binary.get_build_name(pipeline_run)

    def test_get_build(self, osbs_binary):
        resp = {'metadata': {'name': 'run_name'}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        assert resp == osbs_binary.get_build('run_name')

    def test_get_build_reason(self, osbs_binary):
        reason = 'my_reason'
        resp = {'metadata': {'name': 'run_name'}, 'status': {'conditions': [{'reason': reason}]}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        assert reason == osbs_binary.get_build_reason('run_name')

    @pytest.mark.parametrize(('reason', 'output'), [
        ('Succeeded', True),
        ('Failed', False),
    ])
    def test_build_has_succeeded(self, osbs_binary, reason, output):
        resp = {'metadata': {'name': 'run_name'}, 'status': {'conditions': [{'reason': reason}]}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        assert output == osbs_binary.build_has_succeeded('run_name')

    @pytest.mark.parametrize(('status', 'reason', 'output'), [
        ('Unknown', 'Running', True),
        ('Unknown', 'Started', True),
        ('Unknown', 'PipelineRunCancelled', False),
        ('True', 'Succeeded', False),
        ('False', 'Failed', False),
    ])
    def test_build_not_finished(self, osbs_binary, status, reason, output):
        resp = {'metadata': {'name': 'run_name'},
                'status': {'conditions': [{'reason': reason, 'status': status}]}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        assert output == osbs_binary.build_not_finished('run_name')

    @pytest.mark.parametrize(('reason', 'output'), [
        ('Running', False),
        ('PipelineRunCancelled', True),
        ('Succeeded', False),
        ('Failed', False),
    ])
    def test_build_was_cancelled(self, osbs_binary, reason, output):
        resp = {'metadata': {'name': 'run_name'},
                'status': {'conditions': [{'reason': reason}]}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        assert output == osbs_binary.build_was_cancelled('run_name')

    def test_get_build_annotations(self, osbs_binary):
        annotations = {'some': 'ann1', 'some2': 'ann2'}
        resp = {'metadata': {'name': 'run_name', 'annotations': annotations}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        assert annotations == osbs_binary.get_build_annotations('run_name')

    def test_cancel_build(self, osbs_binary):
        resp = {'metadata': {'name': 'run_name'}}

        flexmock(PipelineRun).should_receive('cancel_pipeline_run').and_return(resp)

        assert resp == osbs_binary.cancel_build('run_name')

    def test_update_annotations(self, osbs_binary):
        annotations = {'some': 'ann1', 'some2': 'ann2'}
        resp = {'metadata': {'name': 'run_name', 'annotations': annotations}}

        (flexmock(PipelineRun)
            .should_receive('update_annotations')
            .with_args(annotations).and_return(resp))

        assert resp == osbs_binary.update_annotations_on_build('run_name', annotations)

    @pytest.mark.parametrize(('follow', 'wait'), [
        (True, True),
        (False, True),
        (True, False),
        (False, False),
    ])
    def test_get_build_logs(self, osbs_binary, follow, wait):
        logs = ['first', 'second']
        kwargs = {'follow': follow, 'wait': wait}

        (flexmock(PipelineRun)
            .should_receive('get_logs')
            .with_args(**kwargs).and_return(logs))

        assert logs == osbs_binary.get_build_logs('run_name', follow=follow, wait=wait)

    def test_get_build_error_message(self, osbs_binary):
        metadata = '{"errors": {"plugin1": "error1"}}'
        steps = [{'name': 'step1', 'terminated': {'exitCode': 0}},
                 {'name': 'step2', 'terminated': {'exitCode': 128, 'reason': 'bad thing'}}]
        taskruns = {'task1': {'status': {'conditions': [{'reason': 'Succeeded'}]}},
                    'task2': {'status': {'conditions': [{'reason': 'Failed'}], 'steps': steps}}}
        resp = {'metadata': {'name': 'run_name', 'annotations': {'plugins-metadata': metadata}},
                'status': {'taskRuns': taskruns}}

        flexmock(PipelineRun).should_receive('get_info').and_return(resp)

        error_msg = "Error in plugin plugin1: error1\n\npipeline run errors:\n"
        error_msg += "pipeline task 'task2' failed:\n"
        error_msg += "task step 'step2' failed with exit code: 128 and reason: 'bad thing'"

        assert error_msg == osbs_binary.get_build_error_message('run_name')

    def test_get_build_results(self, osbs_binary):
        pipeline_results = [
            {'name': 'number', 'value': '42'},
            {'name': 'string', 'value': '"spam"'},
            {'name': 'filter_me_out', 'value': 'null'},
        ]
        flexmock(PipelineRun).should_receive('pipeline_results').and_return(pipeline_results)

        assert osbs_binary.get_build_results('run_name') == {'number': 42, 'string': 'spam'}

    def test_get_build_results_invalid_json(self, osbs_binary):
        pipeline_results = [
            {'name': 'invalid_string', 'value': 'spam'},
        ]
        flexmock(PipelineRun).should_receive('pipeline_results').and_return(pipeline_results)

        err_msg = "invalid_string value is not valid JSON: 'spam'"

        with pytest.raises(OsbsValidationException, match=err_msg):
            osbs_binary.get_build_results('run_name')

    @pytest.mark.parametrize('func', [
        '_get_binary_container_pipeline_data',
        '_get_source_container_pipeline_data',
    ])
    def test_template_substitution(self, func):
        """Testcase for making sure that template variables are replaced"""
        pipeline_run_name = 'test_pipeline'
        rcm = 'rcm'
        rcm_scratch = 'rcm_scratch'
        user_params_json = '{"status": "it works!"}'

        with NamedTemporaryFile(mode="wt") as fp:
            fp.write("""
        [default]
        openshift_url = /
        namespace = {namespace}
        use_auth = false
        pipeline_run_path = {pipeline_run_path}
        reactor_config_map = {rcm}
        reactor_config_map_scratch = {rcm_scratch}
        """.format(namespace=TEST_OCP_NAMESPACE,
                   pipeline_run_path=TEST_PIPELINE_REPLACEMENTS_TEMPLATE,
                   rcm=rcm, rcm_scratch=rcm_scratch))
            fp.flush()
            dummy_config = Configuration(fp.name, conf_section='default')
            osbs = OSBS(dummy_config)

        user_params = flexmock(
                reactor_config_map=rcm,
                to_json=lambda: user_params_json,
        )

        data = getattr(osbs, func)(
            user_params=user_params,
            pipeline_run_name=pipeline_run_name
        )

        expected = {
            'config_map': rcm,
            'namespace': TEST_OCP_NAMESPACE,
            'pipeline_run_name': pipeline_run_name,
            'user_params_json': user_params_json,
        }
        assert data == expected
