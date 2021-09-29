# -*- coding: utf-8 -*-
"""
Copyright (c) 2015, 2019 Red Hat, Inc
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
from tests.conftest import APIS_PREFIX
from tests.util import JsonMatcher

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

        logged = [(log.getMessage(), log.levelno) for log in caplog.records]
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

        logged = [(log.getMessage(), log.levelno) for log in caplog.records]
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
    def test_stream_logs_bad_initial_connection_and_error_in_iter(self, openshift, exc):
        response = flexmock(status_code=http_client.OK)
        (response
            .should_receive('iter_lines')
            .and_return([b"{'stream': 'foo\n'}"])
            .and_raise(requests.exceptions.ConnectionError)
            .and_return([b"{'stream': 'ham\n'}"]))

        wrapped_exc = OsbsNetworkException('http://spam.com', str(exc), status_code=None,
                                           cause=exc)
        (flexmock(openshift)
            .should_receive('_get')
            # First: simulate initial connection problem
            .and_raise(wrapped_exc)
            # Next: return a real response
            .and_return(response)
            .and_return(response)
            # we want to call it just as many times so we read all from response,
            # but not more times because it would start again looping from 1st raise
            .and_return(response).times(4))

        mocked_time = (flexmock(time).should_receive('time'))
        # times are tricky, because we use time.time() explicitly in
        # stream logs, BUT also those two log.debug use it
        # so this is caluculated that it will be increasing times for all operations
        # we want, after that it will stop iteration
        for num in range(0, 1500, 100):
            mocked_time.and_return(num)

        logs = openshift.stream_logs(TEST_BUILD)
        assert len([log for log in logs]) == 2

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

            def json(self):
                return {
                    'metadata': {'name': ''},
                    'status': {'phase': 'pending'},
                }

        mock_reponse = MockResponse()
        flexmock(openshift).should_receive('_get').and_return(mock_reponse)
        flexmock(time).should_receive('sleep').and_return(None)
        for change_type, _ in openshift.watch_resource("builds", 12):
            # watch_resource only returns a fresh copy, no updates
            assert change_type is None

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
                self.content = content

            def iter_lines(self):
                return self.lines

            def json(self):
                return json.loads(self.content)

        test_json = json.dumps({"object": "test", "type": "test"}).encode('utf-8')
        bad_response = MockResponse(http_client.FORBIDDEN, None, "failure")
        good_response = MockResponse(http_client.OK, [test_json])
        fresh_response = MockResponse(http_client.OK, content='"test"')
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
                .and_return(good_response, fresh_response,
                            bad_response,
                            good_response, fresh_response)
                .one_by_one())

            yielded = 0
            expected = [
                (None, 'test'),    # fresh object
                ('test', 'test'),  # update
                (None, 'test'),    # fresh object after reconnect
                ('test', 'test'),  # update
            ]
            for (changetype, obj), (exp_changetype, exp_obj) in zip(
                    openshift.watch_resource("builds", 12), expected):
                yielded += 1
                assert changetype == exp_changetype
                assert obj == exp_obj

            assert yielded == len(expected)

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
        expected_url = openshift._build_url(
            "build.openshift.io/v1",
            "buildconfigs/%s/" % build_config_name
        )
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(make_json_response(mock_response)))
        response = openshift.get_build_config(build_config_name)
        assert response['spam'] == 'maps'

    def test_get_missing_build_config(self, openshift):  # noqa
        build_config_name = 'some-build-config-name'
        expected_url = openshift._build_url(
            "build.openshift.io/v1",
            "buildconfigs/%s/" % build_config_name
        )
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
            "build.openshift.io/v1",
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
            "build.openshift.io/v1",
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
            "build.openshift.io/v1",
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
            "build.openshift.io/v1",
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
          {"spam": "maps", "maps": {"spam": "value-3"}}],
         None,
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
            "build.openshift.io/v1",
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
            "build.openshift.io/v1",
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

        expected_url = openshift._build_url(
            "image.openshift.io/v1",
            'imagestreamtags/' + tag_id
        )
        (flexmock(openshift)
            .should_receive("_put")
            .with_args(expected_url, data=json.dumps(mock_data),
                       headers={"Content-Type": "application/json"})
            .once()
            .and_return(make_json_response(mock_data)))

        openshift.put_image_stream_tag(tag_id, mock_data)

    def _make_tag_template(self):
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
    @pytest.mark.parametrize('status_code', (200, 404, 500))
    @pytest.mark.parametrize('insecure', (None, True, False))
    def test_ensure_image_stream_tag(self, existing_scheduled, existing_insecure,
                                     expected_scheduled, status_code, insecure, openshift):

        tag_name = 'maps_tag'
        stream_name = 'repo_from_kwargs.com-spam'
        repository = 'repo_from_kwargs.com/spam:{}'.format(tag_name)

        expected_insecure = False
        if insecure is not None:
            expected_insecure = insecure

        stream = {
            'metadata': {'name': stream_name},
        }

        tag_id = '{}:{}'.format(stream_name, tag_name)

        expected_url = openshift._build_url(
            "image.openshift.io/v1",
            'imagestreamtags/' + tag_id)

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
                assert data['tag']['from']['name'] == repository

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
        if insecure:
            kwargs['insecure'] = insecure
        if expected_error:
            with pytest.raises(OsbsResponseException):
                openshift.ensure_image_stream_tag(
                    stream, tag_name, self._make_tag_template(), repository,
                    expected_scheduled, **kwargs)

        else:
            assert (openshift.ensure_image_stream_tag(
                        stream,
                        tag_name,
                        self._make_tag_template(),
                        repository,
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
            },
            'registry.example.com/repo')

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
        Openshift(APIS_PREFIX, "/oauth/authorize", **kwargs)

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
    @pytest.mark.parametrize(('code_status', 'failed_image_status', 'expect_retry'), (
        (None, None, False),
        (200, {'status': 'Failure', 'code': 400, 'reason': 'BadRequest'}, False),
        (200, {'status': 'Failure', 'code': 500, 'reason': 'InternalError'}, True),
        (504, {'status': 'Failure', 'code': 504, 'reason': 'TimeOut'}, True),
    ))
    @pytest.mark.parametrize('insecure', (True, False))
    def test_import_image_tags(self, openshift, tags, imagestream_name, expect_update, insecure,
                               remove_tags, code_status, failed_image_status, expect_retry):
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

        if modified_resource_json['spec'].get('dockerImageRepository'):
            source_repo = modified_resource_json['spec'].pop('dockerImageRepository')
        else:
            source_repo = modified_resource_json['status'].get('dockerImageRepository')
            modified_resource_json['spec']['dockerImageRepository'] = source_repo
        if modified_resource_json['metadata']['annotations'].get(ANNOTATION_SOURCE_REPO):
            modified_resource_json['metadata']['annotations'][ANNOTATION_SOURCE_REPO] = source_repo

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

        put_url = openshift._build_url(
            "image.openshift.io/v1",
            "imagestreams/%s" % imagestream_name
        )
        if expect_update:
            (flexmock(openshift)
                .should_call('_put')
                .with_args(put_url, data=JsonMatcher(modified_resource_json), use_json=True))
        else:
            (flexmock(openshift)
                .should_call('_put')
                .never()
                .with_args(put_url, data=JsonMatcher(modified_resource_json), use_json=True))

        # Load example API response
        this_file = inspect.getfile(TestCheckResponse)
        this_dir = os.path.dirname(this_file)
        json_path = os.path.join(this_dir, "mock_jsons", openshift._con.version,
                                 'imagestreamimport.json')
        with open(json_path) as f:
            content_json = json.load(f)
        good_resp = HttpResponse(200, {}, content=json.dumps(content_json).encode('utf-8'))

        if failed_image_status:
            for key in ('status', 'code', 'reason'):
                content_json['status']['images'][0]['status'][key] = failed_image_status[key]
            bad_resp = HttpResponse(code_status, {},
                                    content=json.dumps(content_json).encode('utf-8'))

        post_url = openshift._build_url("image.openshift.io/v1", "imagestreamimports/")
        if expect_import:
            if not failed_image_status:
                (flexmock(openshift)
                    .should_receive('_post')
                    .once()
                    .with_args(post_url, data=JsonMatcher(stream_import_json), use_json=True)
                    .and_return(good_resp))
            elif expect_retry:
                (flexmock(openshift)
                    .should_receive('_post')
                    .times(2)
                    .with_args(post_url, data=JsonMatcher(stream_import_json), use_json=True)
                    .and_return(bad_resp)
                    .and_return(good_resp))
            else:
                (flexmock(openshift)
                    .should_receive('_post')
                    .once()
                    .with_args(post_url, data=JsonMatcher(stream_import_json), use_json=True)
                    .and_return(bad_resp))
        else:
            (flexmock(openshift)
                .should_call('_post')
                .times(0)
                .with_args(post_url, data=JsonMatcher(stream_import_json), use_json=True))

        # Make time go faster
        flexmock(time).should_receive('sleep')

        if expect_import and failed_image_status and not expect_retry:
            with pytest.raises(ImportImageFailed):
                openshift.import_image_tags(imagestream_name, stream_import,
                                            tags, source_repo, insecure)
        else:
            assert openshift.import_image_tags(imagestream_name, stream_import,
                                               tags, source_repo, insecure) is expect_import
