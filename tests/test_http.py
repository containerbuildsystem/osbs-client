"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import logging

from flexmock import flexmock
import pytest

import osbs.http as osbs_http
from osbs.http import HttpSession, HttpStream
from osbs.exceptions import OsbsNetworkException

from tests.fake_api import Connection, ResponseMapping

logger = logging.getLogger(__file__)


@pytest.fixture
def s():
    return HttpSession(verbose=True)


def has_connection():
    # In case we run tests in an environment without internet connection.
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
        logger.debug(response_single.content)
        assert len(response_single.headers) > 2
        assert response_single.headers['content-type'] == 'application/json'
        response_multi = s.get("https://httpbin.org/stream/3", stream=True)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)
        assert len(response_multi.headers) > 2
        assert response_multi.headers['content-type'] == 'application/json'

    def test_decoded_json(self, s):
        inp = ['foo', 'bar', 'baz']
        assert list(osbs_http.decoded_json(inp)) == inp

    def test_single_multi_without_redirs(self, s):
        response_single = s.get("http://httpbin.org/get")
        logger.debug(response_single.headers)
        logger.debug(response_single.content)
        response_multi = s.get("http://httpbin.org/stream/3", stream=True)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_single_multi_secure(self, s):
        response_single = s.get("https://httpbin.org/get", allow_redirects=False)
        logger.debug(response_single.headers)
        logger.debug(response_single.content)
        response_multi = s.get("https://httpbin.org/stream/3", stream=True, allow_redirects=False)
        with response_multi as r:
            for line in r.iter_lines():
                logger.debug(line)

    def test_single_multi(self, s):
        response_single = s.get("http://httpbin.org/get", allow_redirects=False)
        logger.debug(response_single.headers)
        logger.debug(response_single.content)
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
        logger.debug(response_single.content)
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
        logger.debug(response_single.content)

    def test_utf8_encoding(self, s):
        response_multi = s.get("http://httpbin.org/encoding/utf8")
        logger.debug(response_multi.headers)
        logger.debug(response_multi.content)

    def test_raise(self, s):
        with pytest.raises(RuntimeError):
            with s.get("http://httpbin.org/stream/3", stream=True) as s:
                raise RuntimeError("hi")
        assert s.closed
