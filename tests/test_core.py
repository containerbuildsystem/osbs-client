# -*- coding: utf-8 -*-
"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

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
from osbs.exceptions import (OsbsResponseException, OsbsException,
                             OsbsNetworkException, OsbsWatchBuildNotFound,
                             ImportImageFailed)
from osbs.core import check_response, Openshift

from tests.constants import (TEST_BUILD, TEST_CANCELLED_BUILD, TEST_LABEL,
                             TEST_LABEL_VALUE, TEST_IMAGESTREAM, TEST_IMAGESTREAM_NO_TAGS,
                             TEST_IMAGESTREAM_WITH_ANNOTATION,
                             TEST_IMAGESTREAM_WITHOUT_IMAGEREPOSITORY)
from tests.conftest import OAPI_PREFIX, API_VER
from tests.test_utils import JsonMatcher

import requests
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

        logged = [(l.getMessage(), l.levelno) for l in caplog.records]
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

        logged = [(l.getMessage(), l.levelno) for l in caplog.records]
        assert len(logged) == 1
        assert logged[0][0] == '[{code}] {message}'.format(code=status_code,
                                                           message=content)
        assert logged[0][1] == log_type


class TestOpenshift(object):
    def test_set_labels_on_build(self, openshift):  # noqa
        labels = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
        assert labels.json() is not None

    @pytest.mark.parametrize('exc', [  # noqa
        requests.ConnectionError('Connection aborted.', http_client.BadStatusLine("''",)),
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
        response = openshift.get_user()
        assert response.json() is not None

    def test_watch_resource_and_wait_to_build_timeouts(self, caplog, openshift):  # noqa:F811
        class MockResponse(object):
            def __init__(self):
                self.status_code = http_client.OK

            def iter_lines(self):
                return []

        mock_reponse = MockResponse()
        flexmock(openshift).should_receive('_get').and_return(mock_reponse)
        flexmock(time).should_receive('sleep').and_return(None)
        for _ in openshift.watch_resource("builds", 12):
            # watch_resource failed and never yielded, so we shouldn't hit the assert
            assert False

        with pytest.raises(OsbsWatchBuildNotFound):
            openshift.wait(12, None)

        with pytest.raises(OsbsException):
            openshift.wait_for_build_to_get_scheduled(12)

        with pytest.raises(OsbsException):
            openshift.wait_for_build_to_finish(12)

        assert any('Retry #143' in log.getMessage() for log in caplog.records)

    @pytest.mark.parametrize('fail', (True, False))  # noqa:F811
    def test_watch_response_hiccup(self, fail, openshift):
        class MockResponse(object):
            def __init__(self, status, lines=None, content=None):
                self.status_code = status
                self.lines = lines or []
                if content:
                    self.content = content

            def iter_lines(self):
                return self.lines

        test_json = json.dumps({"object": "test", "type": "test"}).encode('utf-8')
        bad_response = MockResponse(http_client.FORBIDDEN, None, "failure")
        good_response = MockResponse(http_client.OK, [test_json])
        flexmock(time).should_receive('sleep').and_return(None)
        if fail:
            (flexmock(openshift)
                .should_receive('_get')
                .and_return(bad_response, bad_response, bad_response, bad_response, good_response)
                .one_by_one())
            with pytest.raises(OsbsResponseException) as exc:
                for changetype, obj in openshift.watch_resource("builds", 12):
                    continue
            assert str(exc.value).startswith('failure')
        else:
            (flexmock(openshift)
                .should_receive('_get')
                .and_return(good_response, bad_response, good_response)
                .one_by_one())

            for changetype, obj in openshift.watch_resource("builds", 12):
                assert changetype == 'test'
                assert obj == 'test'

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

    def test_get_build_config_by_labels_filtered(self, openshift):  # noqa:F811
        mock_response = {
            "items": [
                {"spam": "spam"},
                {
                    "spam": "maps",
                    "maps": {"spam": "value-3"}
                }
            ]
        }
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
        response = openshift.get_build_config_by_labels_filtered(label_selectors, "maps.spam",
                                                                 'value-3')
        assert response['spam'] == 'maps'

    def test_get_missing_build_config_by_labels(self, openshift):  # noqa:F811
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

    def test_get_multiple_build_config_by_labels(self, openshift):  # noqa:F811
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

    @pytest.mark.parametrize(('items', 'filter_value', 'error'), [  # noqa:F811
        ([], "value-3", "Build config not found for labels"),
        ([{"spam": "maps"}], "value-3", "Build config not found for labels"),
        ([{"spam": "maps", "maps": {"spam": "value-3"}},
          {"spam": "maps", "maps": {"spam": "value-3"}}], "value-3",
         "More than one build config found for labels"),
        ([{"spam": "maps", "maps": {"spam": "value-3"}},
          {"spam": "maps", "maps": {"spam": "value-3"}}], "value-4",
         "Build config not found for labels"),
        ([], None, "Build config not found for labels"),
        ([{"spam": "maps", "maps": {"spam": "value-3"}},
          {"spam": "maps", "maps": {"spam": "value-3"}}], None,
          "More than one build config found for labels"),
    ])
    def test_get_build_config_by_labels_filtered_fail(self, openshift,
                                                      items, filter_value, error):
        mock_response = {"items": items}
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
            openshift.get_build_config_by_labels_filtered(label_selectors, "maps.spam",
                                                          filter_value)
        assert str(exc.value).startswith(error)

    def test_get_build_config_by_labels_filtered_no_filter(self, openshift):  # noqa:F811
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

        response = openshift.get_build_config_by_labels_filtered(label_selectors, None, None)
        assert response["spam"] == "maps"

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
    @pytest.mark.parametrize('s_annotations', (
        {ANNOTATION_INSECURE_REPO: 'true'},
        {ANNOTATION_INSECURE_REPO: 'false'},
        {ANNOTATION_SOURCE_REPO: 'repo_from_annotations.com/spam'},
        {ANNOTATION_SOURCE_REPO: 'repo_from_annotations.com/spam'},
        {ANNOTATION_INSECURE_REPO: 'true',
         ANNOTATION_SOURCE_REPO: 'repo_from_annotations.com/spam'},
        {ANNOTATION_INSECURE_REPO: 'false',
         ANNOTATION_SOURCE_REPO: 'repo_from_annotations.com/spam'},
        {},
        None,
    ))
    @pytest.mark.parametrize('status_code', (200, 404, 500))
    @pytest.mark.parametrize(('repository', 'insecure'), (
        (None, None),
        (None, False),
        (None, True),
        ('repo_from_kwargs.com/spam', None),
        ('repo_from_kwargs.com/spam', True),
        ('repo_from_kwargs.com/spam', False),
    ))
    def test_ensure_image_stream_tag(self, existing_scheduled, existing_insecure,
                                     expected_scheduled, s_annotations, status_code,
                                     repository, insecure, openshift):
        stream_name = 'spam'
        dockerImage_stream_repo = 'repo_from_dockerImageR.com/spam'
        stream_repo = dockerImage_stream_repo
        if repository:
            stream_repo = repository
        elif s_annotations and ANNOTATION_SOURCE_REPO in s_annotations:
            stream_repo = s_annotations[ANNOTATION_SOURCE_REPO]

        expected_insecure = False
        if repository:
            if insecure is not None:
                expected_insecure = insecure
        elif s_annotations:
            expected_insecure = s_annotations.get(ANNOTATION_INSECURE_REPO) == 'true'

        stream = {
            'metadata': {'name': stream_name},
            'spec': {'dockerImageRepository': dockerImage_stream_repo}
        }
        if s_annotations is not None:
            stream['metadata']['annotations'] = s_annotations

        tag_name = 'maps'
        tag_id = '{}:{}'.format(stream_name, tag_name)

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
                        '{}:{}'.format(stream_repo, tag_name))

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

        kwargs = {}
        if repository:
            kwargs['repository'] = repository
        if insecure:
            kwargs['insecure'] = insecure
        if expected_error:
            with pytest.raises(OsbsResponseException):
                openshift.ensure_image_stream_tag(
                    stream, tag_name, self._make_tag_template(), expected_scheduled, **kwargs)

        else:
            assert (openshift.ensure_image_stream_tag(
                        stream,
                        tag_name,
                        self._make_tag_template(),
                        expected_scheduled,
                        **kwargs) == expected_change)

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

    @pytest.mark.parametrize('tags', (  # noqa:F811
        None,
        [],
        ['7.2.username-66'],
        ['7.2.username-66', '7.2.username-67'],
    ))
    @pytest.mark.parametrize(('imagestream_name', 'expect_update', 'expect_import'), (
        (TEST_IMAGESTREAM, True, True),
        (TEST_IMAGESTREAM_NO_TAGS, True, False),
        (TEST_IMAGESTREAM_WITH_ANNOTATION, False, True),
    ))
    def test_import_image(self, openshift, tags, imagestream_name, expect_update, expect_import):
        """
        tests that import_image return True
        regardless if tags were changed
        """
        this_file = inspect.getfile(TestCheckResponse)
        this_dir = os.path.dirname(this_file)

        json_path = os.path.join(this_dir, "mock_jsons", openshift._con.version, 'imagestream.json')
        with open(json_path) as f:
            template_resource_json = json.load(f)

        modified_resource_json = deepcopy(template_resource_json)
        source_repo = modified_resource_json['spec'].pop('dockerImageRepository')
        modified_resource_json['metadata']['annotations'][ANNOTATION_SOURCE_REPO] = source_repo

        stream_import = {'metadata': {'name': 'FOO'}, 'spec': {'images': []}}
        stream_import_json = deepcopy(stream_import)
        stream_import_json['metadata']['name'] = imagestream_name
        if not expect_import:
            modified_resource_json['spec']['tags'] = []

        if tags:
            for tag in modified_resource_json['spec']['tags']:
                if tag['name'] in tags:
                    image_import = {
                        'from': tag['from'],
                        'to': {'name': tag['name']},
                        'importPolicy': tag.get('importPolicy'),
                        'referencePolicy': tag.get('referencePolicy'),
                    }
                    stream_import_json['spec']['images'].append(image_import)
        else:
            for tag in modified_resource_json['spec']['tags']:
                image_import = {
                    'from': tag['from'],
                    'to': {'name': tag['name']},
                    'importPolicy': tag.get('importPolicy'),
                    'referencePolicy': tag.get('referencePolicy'),
                }
                stream_import_json['spec']['images'].append(image_import)

        put_url = openshift._build_url("imagestreams/%s" % imagestream_name)
        post_url = openshift._build_url("imagestreamimports/")
        (flexmock(openshift)
            .should_call('_put')
            .times(1 if expect_update else 0)
            .with_args(put_url, data=JsonMatcher(modified_resource_json), use_json=True))
        (flexmock(openshift)
            .should_call('_post')
            .times(1 if expect_import else 0)
            .with_args(post_url, data=JsonMatcher(stream_import_json), use_json=True))

        assert openshift.import_image(imagestream_name, stream_import,
                                      tags=tags) is expect_import

    @pytest.mark.parametrize('tags', (  # noqa:F811
        None,
        [],
        ['7.2.username-66'],
        ['7.2.username-66', '7.2.username-67'],
    ))
    @pytest.mark.parametrize(('imagestream_name', 'expect_update', 'remove_tags'), (
        (TEST_IMAGESTREAM, True, False),
        (TEST_IMAGESTREAM_NO_TAGS, True, True),
        (TEST_IMAGESTREAM_WITH_ANNOTATION, True, False),
        (TEST_IMAGESTREAM_WITHOUT_IMAGEREPOSITORY, False, True),
    ))
    @pytest.mark.parametrize('insecure', (True, False))
    def test_import_image_tags(self, openshift, tags, imagestream_name, expect_update, insecure,
                               remove_tags):
        """
        tests that import_image return True
        regardless if tags were changed
        """
        this_file = inspect.getfile(TestCheckResponse)
        this_dir = os.path.dirname(this_file)

        json_path = os.path.join(this_dir, "mock_jsons", openshift._con.version, 'imagestream.json')
        with open(json_path) as f:
            template_resource_json = json.load(f)

        modified_resource_json = deepcopy(template_resource_json)
        for annotation in ANNOTATION_SOURCE_REPO, ANNOTATION_INSECURE_REPO:
            modified_resource_json['metadata']['annotations'].pop(annotation, None)
        source_repo = modified_resource_json['spec'].pop('dockerImageRepository')
        if remove_tags:
            modified_resource_json['spec']['tags'] = []
        expect_import = False
        if tags:
            expect_import = True

        stream_import = {'metadata': {'name': 'FOO'}, 'spec': {'images': []}}
        stream_import_json = deepcopy(stream_import)
        stream_import_json['metadata']['name'] = imagestream_name

        if tags:
            for tag in set(tags):
                image_import = {
                    'from': {"kind": "DockerImage",
                             "name": '{}:{}'.format(source_repo, tag)},
                    'to': {'name': tag},
                    'importPolicy': {'insecure': insecure},
                }
                stream_import_json['spec']['images'].append(image_import)

        put_url = openshift._build_url("imagestreams/%s" % imagestream_name)
        post_url = openshift._build_url("imagestreamimports/")
        (flexmock(openshift)
            .should_call('_put')
            .times(1 if expect_update else 0)
            .with_args(put_url, data=JsonMatcher(modified_resource_json), use_json=True))
        (flexmock(openshift)
            .should_call('_post')
            .times(1 if expect_import else 0)
            .with_args(post_url, data=JsonMatcher(stream_import_json), use_json=True))
        assert openshift.import_image_tags(imagestream_name, stream_import,
                                           tags, source_repo, insecure) is expect_import

    @pytest.mark.parametrize(('image_status', 'expect_retry'), (  # noqa:F811
        ({'status': 'Failure', 'code': 500, 'reason': 'InternalError'}, True),
        ({'status': 'Failure', 'code': 400, 'reason': 'BadRequest'}, False),
    ))
    def test_import_image_retry(self, openshift, image_status, expect_retry):
        imagestream_name = TEST_IMAGESTREAM

        # Load example API response
        this_file = inspect.getfile(TestCheckResponse)
        this_dir = os.path.dirname(this_file)
        json_path = os.path.join(this_dir, "mock_jsons", openshift._con.version,
                                 'imagestreamimport.json')
        with open(json_path) as f:
            content_json = json.load(f)

        # Assume mocked data contains a good response
        good_resp = HttpResponse(200, {}, content=json.dumps(content_json).encode('utf-8'))

        # Create a bad response by marking the first image as failed
        for key in ('status', 'code', 'reason'):
            content_json['status']['images'][0]['status'][key] = image_status[key]
        bad_resp = HttpResponse(200, {}, content=json.dumps(content_json).encode('utf-8'))

        # Make time go faster
        flexmock(time).should_receive('sleep')

        stream_import = {'metadata': {'name': 'FOO'}, 'spec': {'images': []}}
        tags = ['7.2.username-66', '7.2.username-67']

        if expect_retry:
            (flexmock(openshift)
                .should_receive('_post')
                .times(3)
                .and_return(bad_resp)
                .and_return(bad_resp)
                .and_return(good_resp))
            assert openshift.import_image(imagestream_name, stream_import, tags=tags) is True
        else:
            (flexmock(openshift)
                .should_receive('_post')
                .once()
                .and_return(bad_resp))
            with pytest.raises(ImportImageFailed):
                openshift.import_image(imagestream_name, stream_import, tags=tags)
