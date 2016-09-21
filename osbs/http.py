"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


abstraction on top of http api calls
"""

from __future__ import print_function, absolute_import, unicode_literals

import sys
import json
import logging

from osbs.exceptions import OsbsException, OsbsNetworkException, OsbsResponseException

import requests
try:
    from requests_kerberos import HTTPKerberosAuth
except ImportError:
    HTTPKerberosAuth = None

logger = logging.getLogger(__name__)


def decoded_json(iterable):
    """Return str objects for each JSON string in iterable

    See https://tools.ietf.org/html/rfc4627#section-3
    """
    for line in iterable:
        if isinstance(line, str):
            yield line
        else:
            yield line.decode(requests.utils.guess_json_utf(line))


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
                content = s.req.text
                return HttpResponse(s.status_code, s.headers, content)
        except requests.exceptions.HTTPError as ex:
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
                 client_cert=None, client_key=None, verbose=False):
        self.finished = False  # have we read all data?
        self.closed = False    # have we destroyed curl resources?

        self.status_code = 0
        self.headers = None

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
        self.req = requests.request(method, url, **args)

        self.headers = self.req.headers
        self.status_code = self.req.status_code

    def _get_received_data(self):
        return self.req.text

    def iter_chunks(self):
        return self.req.iter_content(None)

    def iter_lines(self):
        return self.req.iter_lines(decode_unicode=True)

    def close(self):
        if not self.closed:
            logger.debug("cleaning up")
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
        if check and self.status_code not in (0, requests.codes.OK, requests.codes.CREATED):
            raise OsbsResponseException(self.content, self.status_code)

        return json.loads(self.content)
