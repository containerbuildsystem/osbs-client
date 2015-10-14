"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import six

from osbs.http import HttpResponse
from osbs.constants import BUILD_FINISHED_STATES
from osbs.exceptions import OsbsResponseException
from osbs.core import check_response

from tests.constants import TEST_BUILD, TEST_LABEL, TEST_LABEL_VALUE
from tests.fake_api import openshift
import pytest

try:
    # py2
    import httplib
except ImportError:
    # py3
    import http.client as httplib


class Response(object):
    def __init__(self, status_code, content=None, iterable=None):
        self.status_code = status_code
        self.iterable = iterable
        if content is not None:
            self.content = content

    def iter_lines(self):
        for line in self.iterable:
            yield line


class TestCheckResponse(object):
    @pytest.mark.parametrize('content', [None, 'OK'])
    @pytest.mark.parametrize('status_code', [httplib.OK, httplib.CREATED])
    def test_check_response_ok(self, status_code, content):
        response = Response(status_code, content=content)
        check_response(response)

    def test_check_response_bad_stream(self, caplog):
        iterable = ['iter', 'lines']
        status_code = httplib.CONFLICT
        response = Response(status_code, iterable=iterable)
        with pytest.raises(OsbsResponseException):
            check_response(response)

        logged = [l.getMessage() for l in caplog.records()]
        assert len(logged) == 1
        assert logged[0] == '[{code}] {message}'.format(code=status_code,
                                                        message='iterlines')

    def test_check_response_bad_nostream(self, caplog):
        status_code = httplib.CONFLICT
        content = 'content'
        response = Response(status_code, content=content)
        with pytest.raises(OsbsResponseException):
            check_response(response)

        logged = [l.getMessage() for l in caplog.records()]
        assert len(logged) == 1
        assert logged[0] == '[{code}] {message}'.format(code=status_code,
                                                        message=content)


class TestOpenshift(object):
    def test_set_labels_on_build(self, openshift):
        l = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
        assert l.json() is not None

    def test_list_builds(self, openshift):
        l = openshift.list_builds()
        assert l is not None
        assert bool(l.json())  # is there at least something

    def test_list_pods(self, openshift):
        response = openshift.list_pods(label="openshift.io/build.name=%s" %
                                       TEST_BUILD)
        assert isinstance(response, HttpResponse)

    def test_get_oauth_token(self, openshift):
        token = openshift.get_oauth_token()
        assert token is not None

    def test_get_user(self, openshift):
        l = openshift.get_user()
        assert l.json() is not None

    def test_watch_build(self, openshift):
        response = openshift.wait_for_build_to_finish(TEST_BUILD)
        status_lower = response["status"]["phase"].lower()
        assert response["metadata"]["name"] == TEST_BUILD
        assert status_lower in BUILD_FINISHED_STATES
        assert isinstance(TEST_BUILD, six.text_type)
        assert isinstance(status_lower, six.text_type)

    def test_create_build(self, openshift):
        response = openshift.create_build({})
        assert response is not None
        assert response.json()["metadata"]["name"] == TEST_BUILD
        assert response.json()["status"]["phase"].lower() in BUILD_FINISHED_STATES
