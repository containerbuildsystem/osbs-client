# -*- coding: utf-8 -*-
"""
Copyright (c) 2015, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

from flexmock import flexmock
import six
import time
import json
import logging

from osbs.osbs_http import HttpResponse
from osbs.constants import (BUILD_FINISHED_STATES, BUILD_CANCELLED_STATE,
                            OS_CONFLICT_MAX_RETRIES)
from osbs.exceptions import (OsbsResponseException, OsbsException,
                             OsbsNetworkException, OsbsWatchBuildNotFound)
from osbs.core import check_response, Openshift

from tests.constants import (TEST_BUILD, TEST_CANCELLED_BUILD, TEST_LABEL, TEST_LABEL_VALUE)
from tests.conftest import APIS_PREFIX

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
