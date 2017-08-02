# -*- coding: utf-8 -*-
"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.

These tests are moved to a separate file due to https://github.com/bkabrda/flexmock/issues/13
"""
import logging
import sys

from flexmock import flexmock
import pytest

import requests
import six
from requests.packages.urllib3.util import Retry
from osbs.http import HttpSession, HttpStream
from osbs.exceptions import OsbsNetworkException, OsbsResponseException
from osbs.constants import HTTP_RETRIES_STATUS_FORCELIST
from osbs.core import Openshift

try:
    # py2
    import httplib
except ImportError:
    # py3
    import http.client as httplib

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


# Replace real retry with fake version to speed up testing
fake_retry = Retry(total=1,
                   backoff_factor=1,
                   status_forcelist=HTTP_RETRIES_STATUS_FORCELIST)


@pytest.mark.skipif(not has_connection(),
                    reason="requires internet connection")
class TestHttpRetries(object):
    @pytest.mark.parametrize('status_code', HTTP_RETRIES_STATUS_FORCELIST)
    def test_fail_after_retries(self, s, status_code):
        flexmock(Retry).new_instances(fake_retry)
        # latest python-requests throws OsbsResponseException, 2.6.x - OsbsNetworkException
        with pytest.raises((OsbsNetworkException, OsbsResponseException)) as exc_info:
            s.get('http://httpbin.org/status/%s' % status_code).json()
        if isinstance(exc_info, OsbsResponseException):
            assert exc_info.value.status_code == status_code

    def test_stream_logs_not_decoded(self, caplog):
        flexmock(Retry).new_instances(fake_retry)
        server = Openshift('http://oapi/v1/', 'v1', 'http://oauth/authorize',
                           k8s_api_url='http://api/v1/')

        logs = (
            u'Lógs'.encode('utf-8'),
            u'Lðgs'.encode('utf-8'),
        )

        fake_response = flexmock(status_code=httplib.OK, headers={})

        (fake_response
            .should_receive('iter_lines')
            .and_yield(*logs)
            .with_args(decode_unicode=False))

        (flexmock(requests)
            .should_receive('request')
            .and_return(fake_response))

        with caplog.atLevel(logging.ERROR):
            for result in server.stream_logs('anything'):
                assert isinstance(result, six.binary_type)
