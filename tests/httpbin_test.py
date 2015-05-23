"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import sys
import logging
import pytest

from osbs.http import get_http_session

logger = logging.getLogger("osbs.tests")


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_single_multi_secure_without_redirs():
    s = get_http_session(verbose=True)
    response_single = s.get("https://httpbin.org/get")
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("https://httpbin.org/stream/3", stream=True)
    for line in response_multi.iter_lines():
        logger.debug(line)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_single_multi_without_redirs():
    s = get_http_session(verbose=True)
    response_single = s.get("http://httpbin.org/get")
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("http://httpbin.org/stream/3", stream=True)
    for line in response_multi.iter_lines():
        logger.debug(line)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_single_multi_secure():
    s = get_http_session(verbose=True)
    response_single = s.get("https://httpbin.org/get", allow_redirects=False)
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("https://httpbin.org/stream/3", stream=True, allow_redirects=False)
    for line in response_multi.iter_lines():
        logger.debug(line)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_single_multi():
    s = get_http_session(verbose=True)
    response_single = s.get("http://httpbin.org/get", allow_redirects=False)
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response_multi = s.get("http://httpbin.org/stream/3", stream=True, allow_redirects=False)
    for line in response_multi.iter_lines():
        logger.debug(line)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_multi_multi():
    s = get_http_session(verbose=True)
    response = s.get("http://httpbin.org/stream/3", stream=True)
    logger.debug(response.headers)
    logger.debug(response.content)
    for line in response.iter_lines():
        logger.debug(line)
    response = s.get("http://httpbin.org/stream/3", stream=True)
    logger.debug(response.headers)
    logger.debug(response.content)
    for line in response.iter_lines():
        logger.debug(line)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_single_multi_multi():
    s = get_http_session(verbose=True)
    response_single = s.get("http://httpbin.org/basic-auth/user/pwd", username="user", password="pwd")
    logger.debug(response_single.headers)
    logger.debug(response_single.content)
    response = s.get("http://httpbin.org/stream/3", stream=True)
    logger.debug(response.headers)
    logger.debug(response.content)
    for line in response.iter_lines():
        logger.debug(line)
    response = s.get("http://httpbin.org/stream/5", stream=True)
    logger.debug(response.headers)
    logger.debug(response.content)
    for line in response.iter_lines():
        logger.debug(line)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_multi_single():
    s = get_http_session(verbose=True)
    response_multi = s.get("http://httpbin.org/stream/3", stream=True)
    logger.debug(response_multi.headers)
    logger.debug(response_multi.content)
    for line in response_multi.iter_lines():
        logger.debug(line)
    response_single = s.get("http://httpbin.org/get")
    logger.debug(response_single.headers)
    logger.debug(response_single.content)


@pytest.mark.skipif(sys.version_info[0] >= 3,
                    reason="known not to work on Python 3 (#74)")
def test_utf8_encoding():
    s = get_http_session(verbose=True)
    response_multi = s.get("http://httpbin.org/encoding/utf8")
    logger.debug(response_multi.headers)
    logger.debug(response_multi.content)
