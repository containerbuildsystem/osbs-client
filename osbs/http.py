"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


abstraction on top of http api calls
"""

from __future__ import print_function, absolute_import, unicode_literals

import sys
import logging
import json
from six.moves import http_client


from osbs.exceptions import OsbsException, OsbsNetworkException, OsbsResponseException
from osbs.constants import (
    HTTP_MAX_RETRIES, HTTP_BACKOFF_FACTOR, HTTP_RETRIES_STATUS_FORCELIST,
    HTTP_RETRIES_METHODS_WHITELIST, HTTP_REQUEST_TIMEOUT)

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util import Retry
from requests.exceptions import HTTPError, RetryError, Timeout
from requests.utils import guess_json_utf
try:
    from requests_kerberos import HTTPKerberosAuth
except ImportError:
    HTTPKerberosAuth = None
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger(__name__)


class HttpSession(object):
    def __init__(self, verbose=False):
        self.verbose = verbose

    def get(self, url, **kwargs):
        return self.request(url, "get", **kwargs)

    def post(self, url, **kwargs):
        return self.request(url, "post", **kwargs)

    def put(self, url, **kwargs):
        return self.request(url, "put", **kwargs)

    def delete(self, url, **kwargs):
        return self.request(url, "delete", **kwargs)

    def request(self, url, *args, **kwargs):
        try:
            stream = HttpStream(url, *args, verbose=self.verbose, **kwargs)
            if kwargs.get('stream', False):
                return stream

            with stream as s:
                content = s.req.content
                return HttpResponse(s.status_code, s.headers, content)
        # Timeout will catch both ConnectTimout and ReadTimeout
        except (RetryError, Timeout) as ex:
            raise OsbsNetworkException(url, str(ex), '',
                                       cause=ex, traceback=sys.exc_info()[2])
        except HTTPError as ex:
            raise OsbsNetworkException(url, str(ex), ex.response.status_code,
                                       cause=ex, traceback=sys.exc_info()[2])
        except Exception as ex:
            raise OsbsException(cause=ex, traceback=sys.exc_info()[2])


class HttpStream(object):
    """
    Handle on HTTP response that is mostly useful for reading the server response incrementally when
    Transfer-Encoding: chunked is used.

    Users of this class should explicitly free the curl resources associated with it. The preferred
    way is to use it as a context manager which ensures that it is closed when exception is raised
    in the middle of reading the stream. Because it doesn't fit into our current API, the class also
    tries to free the resources when it finishes reading the http stream and also when it's garbage
    collected.
    """

    def __init__(self, url, method, data=None, kerberos_auth=False,
                 allow_redirects=True, verify_ssl=True, ca=None, use_json=False,
                 headers=None, stream=False, username=None, password=None,
                 client_cert=None, client_key=None, verbose=False, retries_enabled=True):
        self.finished = False  # have we read all data?
        self.closed = False    # have we destroyed curl resources?

        self.status_code = 0
        self.headers = None

        retry = Retry(
            total=HTTP_MAX_RETRIES,
            connect=HTTP_MAX_RETRIES,
            backoff_factor=HTTP_BACKOFF_FACTOR,
            status_forcelist=HTTP_RETRIES_STATUS_FORCELIST,
            method_whitelist=HTTP_RETRIES_METHODS_WHITELIST
        )
        self.session = requests.Session()
        if retries_enabled:
            self.session.mount('http://', HTTPAdapter(max_retries=retry))
            self.session.mount('https://', HTTPAdapter(max_retries=retry))

        self.url = url
        headers = headers or {}
        method = method.lower()

        if method not in ['post', 'get', 'put', 'delete']:
            raise RuntimeError("Unsupported method '%s' for curl call!" % method)

        args = {}

        if method in ['post', 'put']:
            headers['Expect'] = ''

        if not verify_ssl:
            args['verify'] = False
        else:
            if ca:
                args['verify'] = ca
            else:
                args['verify'] = True

        if username and password:
            args['auth'] = (username, password)

        if client_cert and client_key:
            args['cert'] = (client_cert, client_key)

        if data:
            args['data'] = data

        if use_json:
            headers['Content-Type'] = 'application/json'

        args['allow_redirects'] = allow_redirects

        if kerberos_auth:
            if not HTTPKerberosAuth:
                raise RuntimeError('Kerberos auth unavailable')
            args['auth'] = HTTPKerberosAuth()

        if stream:
            args['stream'] = True

        args['headers'] = headers
        args['timeout'] = HTTP_REQUEST_TIMEOUT

        self.req = self.session.request(method, url, **args)

        self.headers = self.req.headers
        self.status_code = self.req.status_code

    def _get_received_data(self):
        return self.req.text

    def iter_chunks(self):
        return self.req.iter_content(None)

    def iter_lines(self):
        kwargs = {
            # OpenShift does not respond with any encoding value.
            # This causes requests module to guess it as ISO-8859-1.
            # Likely, the encoding is actually UTF-8, but we can't
            # guarantee it. Therefore, we take the approach of simply
            # passing through the encoded data with no effort to
            # attempt decoding it.
            'decode_unicode': False
        }
        if requests.__version__.startswith('2.6.'):
            kwargs['chunk_size'] = 1
        # if this fails for any reason other than ChunkedEncodingError
        # or IncompleteRead (either of which may happen when no bytes
        # are received), let someone else handle the exception
        try:
            for line in self.req.iter_lines(**kwargs):
                yield line
        except (requests.exceptions.ChunkedEncodingError,
                http_client.IncompleteRead):
            return

    def close(self):
        if not self.closed:
            logger.debug("cleaning up")
            if hasattr(self, 'req'):
                del self.req
        self.closed = True

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class HttpResponse(object):
    def __init__(self, status_code, headers, content):
        self.status_code = status_code
        self.headers = headers
        self.content = content

    def json(self, check=True):
        encoding = guess_json_utf(self.content)
        text = self.content.decode(encoding)
        if check and self.status_code not in (0, requests.codes.OK, requests.codes.CREATED):
            raise OsbsResponseException(text, self.status_code)

        try:
            return json.loads(text)
        except ValueError:
            msg = '{}Headers {}\nContent {}'.format('HtttpResponse has corrupt json:\n',
                                                    self.headers, self.content)
            logger.exception(msg)
            raise OsbsResponseException(msg, self.status_code)
