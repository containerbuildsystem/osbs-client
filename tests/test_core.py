# -*- coding: utf-8 -*-
"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from copy import deepcopy
from flexmock import flexmock
from textwrap import dedent
import six
import time
import json
import logging
import inspect
import os

from osbs.http import HttpResponse
from osbs.constants import (BUILD_FINISHED_STATES, BUILD_CANCELLED_STATE,
                            OS_CONFLICT_MAX_RETRIES,
                            ANNOTATION_SOURCE_REPO, ANNOTATION_INSECURE_REPO)
from osbs.exceptions import (OsbsResponseException, OsbsException, OsbsNetworkException)
from osbs.core import check_response, Openshift

from tests.constants import (TEST_BUILD, TEST_CANCELLED_BUILD, TEST_LABEL,
                             TEST_LABEL_VALUE, TEST_IMAGESTREAM, TEST_IMAGESTREAM_NO_TAGS,
                             TEST_IMAGESTREAM_WITH_ANNOTATION)
from tests.fake_api import openshift, OAPI_PREFIX, API_VER  # noqa
from tests.test_utils import JsonMatcher
from requests.exceptions import ConnectionError
import pytest

from six.moves import http_client


class Response(object):
    def __init__(self, status_code, content=None, iterable=None):
        self.status_code = status_code
        self.iterable = iterable
        if content is not None:
            self.content = content

    def iter_lines(self):
        for line in self.iterable:
            yield line


def make_json_response(obj):
    return HttpResponse(200,
                        headers={"Content-Type": "application/json"},
                        content=json.dumps(obj).encode('utf-8'))


class TestCheckResponse(object):
    @pytest.mark.parametrize('content', [None, b'OK'])
    @pytest.mark.parametrize('status_code', [http_client.OK, http_client.CREATED])
    def test_check_response_ok(self, status_code, content):
        response = Response(status_code, content=content)
        check_response(response)

    @pytest.mark.parametrize('log_errors', (True, False))
    def test_check_response_bad_stream(self, caplog, log_errors):
        iterable = [b'iter', b'lines']
        status_code = http_client.CONFLICT
        response = Response(status_code, iterable=iterable)
        if log_errors:
            log_type = logging.ERROR
        else:
            log_type = logging.DEBUG

        with pytest.raises(OsbsResponseException):
            if log_errors:
                check_response(response)
            else:
                check_response(response, log_level=log_type)

        logged = [(l.getMessage(), l.levelno) for l in caplog.records()]
        assert len(logged) == 1
        assert logged[0][0] == '[{code}] {message}'.format(code=status_code,
                                                           message=b'iterlines')
        assert logged[0][1] == log_type

    @pytest.mark.parametrize('log_errors', (True, False))
    def test_check_response_bad_nostream(self, caplog, log_errors):
        status_code = http_client.CONFLICT
        content = b'content'
        response = Response(status_code, content=content)
        if log_errors:
            log_type = logging.ERROR
        else:
            log_type = logging.DEBUG

        with pytest.raises(OsbsResponseException):
            if log_errors:
                check_response(response)
            else:
                check_response(response, log_level=log_type)

        logged = [(l.getMessage(), l.levelno) for l in caplog.records()]
        assert len(logged) == 1
        assert logged[0][0] == '[{code}] {message}'.format(code=status_code,
                                                           message=content)
        assert logged[0][1] == log_type


class TestOpenshift(object):
    def test_set_labels_on_build(self, openshift):  # noqa
        labels = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
        assert labels.json() is not None

    @pytest.mark.parametrize('exc', [  # noqa
        ConnectionError('Connection aborted.', http_client.BadStatusLine("''",)),
    ])
    def test_stream_logs_bad_initial_connection(self, openshift, exc):
        response = flexmock(status_code=http_client.OK)
        (response
            .should_receive('iter_lines')
            .and_return([b"{'stream': 'foo\n'}"])
            .and_raise(StopIteration))

        wrapped_exc = OsbsNetworkException('http://spam.com', str(exc), status_code=None,
                                           cause=exc)
        (flexmock(openshift)
            .should_receive('_get')
            # First: simulate initial connection problem
            .and_raise(wrapped_exc)
            # Next: return a real response
            .and_return(response))

        (flexmock(time)
            .should_receive('time')
            .and_return(0)
            .and_return(100))

        logs = openshift.stream_logs(TEST_BUILD)
        assert len([log for log in logs]) == 1

    def test_stream_logs_utf8(self, openshift):  # noqa
        response = flexmock(status_code=http_client.OK)
        (response
            .should_receive('iter_lines')
            .and_return([u"{'stream': 'Uňícode íš hářd\n'}".encode('utf-8')])
            .and_raise(StopIteration))

        (flexmock(openshift)
            .should_receive('_get')
            .and_return(response))

        logs = openshift.stream_logs(TEST_BUILD)
        assert len([log for log in logs]) == 1

    def test_list_builds(self, openshift):  # noqa
        list_builds = openshift.list_builds()
        assert list_builds is not None
        assert bool(list_builds.json())  # is there at least something

    def test_list_pods(self, openshift):  # noqa
        response = openshift.list_pods(label="openshift.io/build.name=%s" %
                                       TEST_BUILD)
        assert isinstance(response, HttpResponse)

    def test_get_oauth_token(self, openshift):  # noqa
        token = openshift.get_oauth_token()
        assert token is not None

    def test_get_user(self, openshift):  # noqa
        l = openshift.get_user()
        assert l.json() is not None

    def test_watch_build(self, openshift):  # noqa
        response = openshift.wait_for_build_to_finish(TEST_BUILD)
        status_lower = response["status"]["phase"].lower()
        assert response["metadata"]["name"] == TEST_BUILD
        assert status_lower in BUILD_FINISHED_STATES
        assert isinstance(TEST_BUILD, six.text_type)
        assert isinstance(status_lower, six.text_type)

    def test_create_build(self, openshift):  # noqa
        response = openshift.create_build({})
        assert response is not None
        assert response.json()["metadata"]["name"] == TEST_BUILD
        assert response.json()["status"]["phase"].lower() in BUILD_FINISHED_STATES

    def test_cancel_build(self, openshift):  # noqa
        response = openshift.cancel_build(TEST_CANCELLED_BUILD)
        assert response is not None
        assert response.json()["metadata"]["name"] == TEST_CANCELLED_BUILD
        assert response.json()["status"]["phase"].lower() in BUILD_CANCELLED_STATE

    def test_get_build_config(self, openshift):  # noqa
        mock_response = {"spam": "maps"}
        build_config_name = 'some-build-config-name'
        expected_url = openshift._build_url("buildconfigs/%s/" % build_config_name)
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(make_json_response(mock_response)))
        response = openshift.get_build_config(build_config_name)
        assert response['spam'] == 'maps'

    def test_get_missing_build_config(self, openshift):  # noqa
        build_config_name = 'some-build-config-name'
        expected_url = openshift._build_url("buildconfigs/%s/" % build_config_name)
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(404, {}, b'')))
        with pytest.raises(OsbsResponseException):
            openshift.get_build_config(build_config_name)

    def test_get_build_config_by_labels(self, openshift):  # noqa
        mock_response = {"items": [{"spam": "maps"}]}
        label_selectors = (
            ('label-1', 'value-1'),
            ('label-2', 'value-2'),
        )
        expected_url = openshift._build_url(
            "buildconfigs/?labelSelector=label-1%3Dvalue-1%2Clabel-2%3Dvalue-2")
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(make_json_response(mock_response)))
        response = openshift.get_build_config_by_labels(label_selectors)
        assert response['spam'] == 'maps'

    def test_get_missing_build_config_by_labels(self, openshift):  # noqa
        mock_response = {"items": []}
        label_selectors = (
            ('label-1', 'value-1'),
            ('label-2', 'value-2'),
        )
        expected_url = openshift._build_url(
            "buildconfigs/?labelSelector=label-1%3Dvalue-1%2Clabel-2%3Dvalue-2")
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(make_json_response(mock_response)))

        with pytest.raises(OsbsException) as exc:
            openshift.get_build_config_by_labels(label_selectors)
        assert str(exc.value).startswith('Build config not found')

    def test_get_multiple_build_config_by_labels(self, openshift):  # noqa
        mock_response = {"items": [{"spam": "maps"}, {"eggs": "sgge"}]}
        label_selectors = (
            ('label-1', 'value-1'),
            ('label-2', 'value-2'),
        )
        expected_url = openshift._build_url(
            "buildconfigs/?labelSelector=label-1%3Dvalue-1%2Clabel-2%3Dvalue-2")
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(make_json_response(mock_response)))

        with pytest.raises(OsbsException) as exc:
            openshift.get_build_config_by_labels(label_selectors)
        assert str(exc.value).startswith('More than one build config found')

    @pytest.mark.parametrize(('status_codes', 'should_raise'), [  # noqa
        ([http_client.OK], False),
        ([http_client.CONFLICT, http_client.CONFLICT, http_client.OK], False),
        ([http_client.CONFLICT, http_client.OK], False),
        ([http_client.CONFLICT, http_client.CONFLICT, http_client.UNAUTHORIZED], True),
        ([http_client.UNAUTHORIZED], True),
        ([http_client.CONFLICT for _ in range(OS_CONFLICT_MAX_RETRIES + 1)], True),
    ])
    @pytest.mark.parametrize('update_or_set', ['update', 'set'])
    @pytest.mark.parametrize('attr_type', ['labels', 'annotations'])
    @pytest.mark.parametrize('object_type', ['build', 'build_config'])
    def test_retry_update_attributes(self, openshift,
                                     status_codes, should_raise,
                                     update_or_set,
                                     attr_type,
                                     object_type):
        try:
            fn = getattr(openshift,
                         "{update}_{attr}_on_{object}"
                         .format(update=update_or_set,
                                 attr=attr_type,
                                 object=object_type))
        except AttributeError:
            return  # not every combination is implemented

        get_expectation = (flexmock(openshift)
                           .should_receive('_get')
                           .times(len(status_codes)))
        put_expectation = (flexmock(openshift)
                           .should_receive('_put')
                           .times(len(status_codes)))
        for status_code in status_codes:
            get_response = make_json_response({"metadata": {}})
            put_response = HttpResponse(status_code,
                                        headers={},
                                        content=b'')
            get_expectation = get_expectation.and_return(get_response)
            put_expectation = put_expectation.and_return(put_response)

        (flexmock(time)
            .should_receive('sleep')
            .and_return(None))

        args = ('any-object-id', {'key': 'value'})
        if should_raise:
            with pytest.raises(OsbsResponseException):
                fn(*args)
        else:
            fn(*args)

    def test_put_image_stream_tag(self, openshift):  # noqa
        tag_name = 'spam'
        tag_id = 'maps:' + tag_name
        mock_data = {
          'kind': 'ImageStreamTag',
          'apiVersion': 'v1',
          'tag': {
            'name': tag_name
          }
        }

        expected_url = openshift._build_url('imagestreamtags/' + tag_id)
        (flexmock(openshift)
            .should_receive("_put")
            .with_args(expected_url, data=json.dumps(mock_data),
                       headers={"Content-Type": "application/json"})
            .once()
            .and_return(make_json_response(mock_data)))

        openshift.put_image_stream_tag(tag_id, mock_data)

    def _make_tag_template(self):
        # TODO: Just read from inputs folder
        return json.loads(dedent('''\
            {
              "kind": "ImageStreamTag",
              "apiVersion": "v1",
              "metadata": {
                "name": "{{IMAGE_STREAM_ID}}:{{TAG_ID}}"
              },
              "tag": {
                "name": "{{TAG_ID}}",
                "from": {
                  "kind": "DockerImage",
                  "name": "{{REPOSITORY}}:{{TAG_ID}}"
                },
                "importPolicy": {}
              }
            }
        '''))

    @pytest.mark.parametrize('existing_scheduled', (True, False, None))  # noqa
    @pytest.mark.parametrize('existing_insecure', (True, False, None))
    @pytest.mark.parametrize('expected_scheduled', (True, False))
    @pytest.mark.parametrize(('s_annotations', 'expected_insecure'), (
        ({ANNOTATION_INSECURE_REPO: 'true'}, True),
        ({ANNOTATION_INSECURE_REPO: 'false'}, False),
        ({}, False),
        (None, False),
    ))
    @pytest.mark.parametrize('status_code', (200, 404, 500))
    def test_ensure_image_stream_tag(self,
                                     existing_scheduled,
                                     existing_insecure,
                                     expected_scheduled,
                                     s_annotations,
                                     expected_insecure,
                                     status_code,
                                     openshift):
        stream_name = 'spam'
        stream_repo = 'some.registry.com/spam'
        stream = {
            'metadata': {'name': stream_name},
            'spec': {'dockerImageRepository': stream_repo}
        }
        if s_annotations is not None:
            stream['metadata']['annotations'] = s_annotations

        tag_name = 'maps'
        tag_id = '{0}:{1}'.format(stream_name, tag_name)

        expected_url = openshift._build_url('imagestreamtags/' +
                                            tag_id)

        def verify_image_stream_tag(*args, **kwargs):
            data = json.loads(kwargs['data'])

            assert (bool(data['tag']['importPolicy'].get('insecure')) ==
                    expected_insecure)
            assert (bool(data['tag']['importPolicy'].get('scheduled')) ==
                    expected_scheduled)

            # Also verify new image stream tags are created properly.
            if status_code == 404:
                assert data['metadata']['name'] == tag_id
                assert data['tag']['name'] == tag_name
                assert (data['tag']['from']['name'] ==
                        '{0}:{1}'.format(stream_repo, tag_name))

            return make_json_response({})

        expected_change = False
        expected_error = status_code == 500

        mock_response = {}

        expectation = (flexmock(openshift)
                       .should_receive("_get")
                       .with_args(expected_url)
                       .once())

        if status_code == 200:
            existing_image_stream_tag = {'tag': {'importPolicy': {}}}

            if existing_insecure is not None:
                existing_image_stream_tag['tag']['importPolicy']['insecure'] = \
                    existing_insecure

            if existing_scheduled is not None:
                existing_image_stream_tag['tag']['importPolicy']['scheduled'] = \
                    existing_scheduled

            mock_response = existing_image_stream_tag

            if expected_insecure != bool(existing_insecure) or \
               expected_scheduled != bool(existing_scheduled):
                    expected_change = True

            expectation.and_return(make_json_response(mock_response))

        else:
            expectation.and_return(HttpResponse(status_code,
                                                headers={},
                                                content=b''))

        if status_code == 404:
            expected_change = True

        if expected_change:
            (flexmock(openshift)
                .should_receive("_put")
                .with_args(expected_url, data=str,
                           headers={"Content-Type": "application/json"})
                .replace_with(verify_image_stream_tag)
                .once())

        if expected_error:
            with pytest.raises(OsbsResponseException):
                openshift.ensure_image_stream_tag(
                    stream, tag_name, self._make_tag_template(), expected_scheduled)

        else:
            assert (openshift.ensure_image_stream_tag(
                        stream,
                        tag_name,
                        self._make_tag_template(),
                        expected_scheduled) == expected_change)

    @pytest.mark.parametrize(('status_codes', 'should_raise'), [  # noqa
        ([http_client.OK], False),
        ([http_client.CONFLICT, http_client.CONFLICT, http_client.OK], False),
        ([http_client.CONFLICT, http_client.OK], False),
        ([http_client.CONFLICT, http_client.CONFLICT, http_client.UNAUTHORIZED], True),
        ([http_client.UNAUTHORIZED], True),
        ([http_client.CONFLICT for _ in range(OS_CONFLICT_MAX_RETRIES + 1)], True),
    ])
    def test_retry_ensure_image_stream_tag(self, openshift,
                                           status_codes, should_raise):
        get_expectation = (flexmock(openshift)
                           .should_receive('_get')
                           .times(len(status_codes)))
        put_expectation = (flexmock(openshift)
                           .should_receive('_put')
                           .times(len(status_codes)))
        for status_code in status_codes:
            get_response = HttpResponse(http_client.NOT_FOUND,
                                        headers={},
                                        content=b'')
            put_response = HttpResponse(status_code,
                                        headers={},
                                        content=b'')
            get_expectation = get_expectation.and_return(get_response)
            put_expectation = put_expectation.and_return(put_response)

        (flexmock(time)
            .should_receive('sleep')
            .and_return(None))

        fn = openshift.ensure_image_stream_tag
        args = (
            {
                'kind': 'ImageStream',
                'metadata': {
                    'name': 'imagestream',
                },
                'spec': {
                    'dockerImageRepository': 'registry.example.com/repo',
                },
            },
            'tag',
            {
                'kind': 'ImageStreamTag',
                'metadata': {
                    'name': 'imagestream:tag',
                },
                'tag': {
                    'name': 'tag',
                    'from': {
                        'kind': 'DockerImage',
                        'name': 'registry.example.com/repo:tag',
                    },
                    'importPolicy': {},
                },
            })

        if should_raise:
            with pytest.raises(OsbsResponseException):
                fn(*args)
        else:
            fn(*args)

    @pytest.mark.parametrize(('kwargs', 'called'), (
        ({'use_auth': True, 'use_kerberos': True}, False),
        ({'use_auth': True, 'username': 'foo', 'password': 'bar'}, False),
        ({'use_auth': True, 'token': 'foo'}, False),
        ({'use_auth': False, 'use_kerberos': True}, False),
        ({'use_auth': False, 'username': 'foo', 'password': 'bar'}, False),
        ({'use_auth': False, 'token': 'foo'}, False),
        ({'use_kerberos': True}, False),
        ({'username': 'foo', 'password': 'bar'}, False),
        ({'token': 'foo'}, False),
        ({'use_auth': False}, True),
        ({}, True),
    ))
    def test_use_service_account_token(self, kwargs, called):
        openshift_mock = flexmock(Openshift).should_receive('can_use_serviceaccount_token')
        if called:
            openshift_mock.once()
        else:
            openshift_mock.never()
        Openshift(OAPI_PREFIX, API_VER, "/oauth/authorize", **kwargs)

    @pytest.mark.parametrize(('imagestream_name', 'expect_update', 'expect_import'), (  # noqa:F811
        (TEST_IMAGESTREAM, True, True),
        (TEST_IMAGESTREAM_NO_TAGS, True, False),
        (TEST_IMAGESTREAM_WITH_ANNOTATION, False, True),
    ))
    def test_import_image(self, openshift, imagestream_name, expect_update, expect_import):
        """
        tests that import_image return True
        regardless if tags were changed
        """
        this_file = inspect.getfile(TestCheckResponse)
        this_dir = os.path.dirname(this_file)

        json_path = os.path.join(this_dir, "mock_jsons", openshift._con.version, 'imagestream.json')
        with open(json_path) as f:
            template_resource_json = json.load(f)

        initial_resource_json = deepcopy(template_resource_json)

        modified_resource_json = deepcopy(template_resource_json)
        source_repo = modified_resource_json['spec'].pop('dockerImageRepository')
        modified_resource_json['metadata']['annotations'][ANNOTATION_SOURCE_REPO] = source_repo
        if not expect_import:
            modified_resource_json['spec']['tags'] = []

        put_url = openshift._build_url("imagestreams/%s" % imagestream_name)
        (flexmock(openshift)
            .should_call('_put')
            .times(1 if expect_update else 0)
            .with_args(put_url, data=JsonMatcher(modified_resource_json), use_json=True))

        stream_import = {'metadata': {'name': 'FOO'}, 'spec': {'images': []}}
        assert openshift.import_image(imagestream_name, stream_import) is expect_import
