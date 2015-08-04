"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import sys
import logging
import pytest

from osbs.http import HttpSession

logger = logging.getLogger("osbs.tests")

@pytest.fixture
def s():
    return HttpSession(verbose=True)

def test_single_multi_secure_without_redirs(s):
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


def test_single_multi_without_redirs(s):
    response_single = s.get("http://httpbin.org/get")
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("http://httpbin.org/stream/3", stream=True)
    with response_multi as r:
        for line in r.iter_lines():
            logger.debug(line)


def test_single_multi_secure(s):
    response_single = s.get("https://httpbin.org/get", allow_redirects=False)
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("https://httpbin.org/stream/3", stream=True, allow_redirects=False)
    with response_multi as r:
        for line in r.iter_lines():
            logger.debug(line)


def test_single_multi(s):
    response_single = s.get("http://httpbin.org/get", allow_redirects=False)
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("http://httpbin.org/stream/3", stream=True, allow_redirects=False)
    with response_multi as r:
        for line in r.iter_lines():
            logger.debug(line)


def test_multi_multi(s):
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


def test_single_multi_multi(s):
    response_single = s.get("http://httpbin.org/basic-auth/user/pwd", username="user", password="pwd")
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


def test_multi_single(s):
    response_multi = s.get("http://httpbin.org/stream/3", stream=True)
    logger.debug(response_multi.headers)
    with response_multi as r:
        for line in r.iter_lines():
            logger.debug(line)
    response_single = s.get("http://httpbin.org/get")
    logger.debug(response_single.headers)
    logger.debug(response_single.content)


def test_utf8_encoding(s):
    response_multi = s.get("http://httpbin.org/encoding/utf8")
    logger.debug(response_multi.headers)
    logger.debug(response_multi.content)

def test_raise(s):
    with pytest.raises(RuntimeError):
        with s.get("http://httpbin.org/stream/3", stream=True) as s:
            raise RuntimeError("hi")
    assert s.closed
