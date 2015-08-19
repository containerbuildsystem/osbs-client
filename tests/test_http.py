"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import logging

from flexmock import flexmock
import pycurl
import pytest

import osbs.http as osbs_http
from osbs.http import parse_headers, HttpSession, HttpStream

from tests.fake_api import Connection, ResponseMapping

logger = logging.getLogger(__file__)


@pytest.fixture
def s():
    return HttpSession(verbose=True)


class TestParseHeaders(object):
    def test_parse_headers(self):
        conn = Connection("0.5.4")
        rm = ResponseMapping("0.5.4", lookup=conn.get_definition_for)

        key, value = conn.get_definition_for("/oauth/authorize")
        file_name = value["get"]["file"]
        raw_headers = rm.get_response_content(file_name)

        headers = parse_headers(raw_headers)

        assert headers is not None
        assert len(headers.items()) > 0
        assert headers["location"]


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


class TestHttpStream(object):
    @pytest.mark.parametrize('chunks,expected_content', [
        ([b'foo', b'', b'bar', b'baz'], u'foobarbaz'),
        ([b'a', b'b', b'\xc4', b'\x8d', b'x'], u'ab\u010dx'),
        ([b'\xe2', b'\x8a', b'\x86'], u'\u2286'),
        ([b'\xe2\x8a', b'\x86'], u'\u2286'),
        ([b'\xe2', b'\x8a\x86'], u'\u2286'),
        ([b'aaaa', b'\xe2\x8a', b'\x86'], u'aaaa\u2286'),
        ([b'aaaa\xe2\x8a', b'\x86'], u'aaaa\u2286'),
        ([b'\xe2\x8a', b'\x86ffff'], u'\u2286ffff'),
    ])
    def test_http_multibyte_decoding(self, chunks, expected_content):
        class Whatever(object):
            def __getattr__(self, name):
                return self

            def __call__(self, *args, **kwargs):
                return self
        flexmock(pycurl).should_receive('Curl').and_return(Whatever())
        flexmock(pycurl).should_receive('CurlMulti').and_return(Whatever())
        (flexmock(osbs_http).should_receive('parse_headers')
                            .and_return({'content-type': 'application/json; charset=utf-8'}))
        flexmock(HttpStream, _select=lambda: None)

        def mock_perform(self):
            if chunks:
                self.response_buffer.write(chunks.pop(0))
            else:
                self.finished = True

        try:
            orig_perform = HttpStream._perform
            HttpStream._perform = mock_perform

            r = HttpSession(verbose=True).get('http://')
            assert r.content == expected_content
        finally:
            HttpStream._perform = orig_perform
