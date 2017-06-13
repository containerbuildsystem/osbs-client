"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import logging
import sys

from flexmock import flexmock
import pytest
import requests

try:
    # py2
    import httplib
except ImportError:
    # py3
    import http.client as httplib

from osbs.http import HttpSession, HttpStream
from osbs.exceptions import OsbsNetworkException, OsbsException

logger = logging.getLogger(__file__)


@pytest.fixture
def s():
    return HttpSession(verbose=True)


def has_connection():
    # In case we run tests in an environment without internet connection.

    if sys.version_info < (2, 7):
        # py 2.6 doesn't have SNI support, required for httpbin, as it has SSLv3 certificates
        return False

    try:
        HttpStream("https://httpbin.org/get", "get")
        return True
    except OsbsNetworkException:
        return False


@pytest.mark.skipif(not has_connection(),
                    reason="requires internet connection")
class TestHttpSession(object):
    def test_single_multi_secure_without_redirs(self, s):
        response_single = s.get("https://httpbin.org/get")
        logger.debug(response_single.headers)
        logger.debug(response_single.text)
        assert len(response_single.headers) > 2
        assert response_single.headers['content-type'] == 'application/json'
        response_multi = s.get("https://httpbin.org/stream/3", stream=True)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)
        assert len(response_multi.headers) > 2
        assert response_multi.headers['content-type'] == 'application/json'

    def test_single_multi_without_redirs(self, s):
        response_single = s.get("http://httpbin.org/get")
        logger.debug(response_single.headers)
        logger.debug(response_single.text)
        response_multi = s.get("http://httpbin.org/stream/3", stream=True)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_single_multi_secure(self, s):
        response_single = s.get("https://httpbin.org/get", allow_redirects=False)
        logger.debug(response_single.headers)
        logger.debug(response_single.text)
        response_multi = s.get("https://httpbin.org/stream/3", stream=True, allow_redirects=False)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_single_multi(self, s):
        response_single = s.get("http://httpbin.org/get", allow_redirects=False)
        logger.debug(response_single.headers)
        logger.debug(response_single.text)
        response_multi = s.get("http://httpbin.org/stream/3", stream=True, allow_redirects=False)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_multi_multi(self, s):
        response = s.get("http://httpbin.org/stream/3", stream=True)
        logger.debug(response.headers)
        with response as r:
            for line in r.iter_lines():
                logger.debug(line)
        response = s.get("http://httpbin.org/stream/3", stream=True)
        logger.debug(response.headers)
        with response as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_single_multi_multi(self, s):
        response_single = s.get("http://httpbin.org/basic-auth/user/pwd",
                                username="user", password="pwd")
        logger.debug(response_single.headers)
        logger.debug(response_single.text)
        response = s.get("http://httpbin.org/stream/3", stream=True)
        logger.debug(response.headers)
        with response as r:
            for line in r.iter_lines():
                logger.debug(line)
        response = s.get("http://httpbin.org/stream/5", stream=True)
        logger.debug(response.headers)
        with response as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_multi_single(self, s):
        response_multi = s.get("http://httpbin.org/stream/3", stream=True)
        logger.debug(response_multi.headers)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)
        response_single = s.get("http://httpbin.org/get")
        logger.debug(response_single.headers)
        logger.debug(response_single.text)

    def test_utf8_encoding(self, s):
        response_multi = s.get("http://httpbin.org/encoding/utf8")
        logger.debug(response_multi.headers)
        logger.debug(response_multi.text)

    def test_raise(self, s):
        with pytest.raises(RuntimeError):
            with s.get("http://httpbin.org/stream/3", stream=True) as s:
                raise RuntimeError("hi")
        assert s.closed

    @pytest.mark.parametrize('raise_exc', (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        httplib.IncompleteRead,
    ))
    def test_osbs_exception_wrapping(self, s, raise_exc):
        (flexmock(HttpStream)
            .should_receive('__init__')
            .and_raise(raise_exc('')))
        with pytest.raises(OsbsException) as exc_info:
            s.get('http://httpbin.org/get')

        assert not isinstance(exc_info.value, OsbsNetworkException)
        assert isinstance(exc_info.value.cause, raise_exc)

    @pytest.mark.parametrize('raise_exc', (
        requests.exceptions.HTTPError,
    ))
    def test_osbs_network_exception_wrapping(self, s, raise_exc):
        response = flexmock(status_code=409)
        (flexmock(HttpStream)
            .should_receive('__init__')
            .and_raise(raise_exc(response=response)))
        with pytest.raises(OsbsNetworkException) as exc_info:
            s.get('http://httpbin.org/get')

        assert isinstance(exc_info.value.cause, raise_exc)
