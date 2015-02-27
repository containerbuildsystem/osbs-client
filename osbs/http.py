"""
abstraction on top of http api calls

use requests, fallback to pycurl
"""

from __future__ import print_function, absolute_import, unicode_literals

import sys
import json
import httplib
import urllib2
from cStringIO import StringIO

requests_imported = False
pycurl_imported = False

# prefered http lib is pycurl, since it understands chunked response
try:
    import pycurl
except ImportError:
    try:
        import requests
    except ImportError:
        print("Neither requests, nor pycurl are available.")
        sys.exit(1)
    else:
        requests_imported = True
else:
    pycurl_imported = True


SELECT_TIMEOUT = 9999


class Response(object):
    """ let's mock Response object of requests """

    def __init__(self, status_code=0, content='', curl=None):
        self.status_code = status_code
        self.content = content
        self.curl = curl
        self.curl_multi = getattr(self.curl, "curl_multi", None)
        self.response_buffer = getattr(self.curl, "response", None)

    def json(self):
        return json.loads(self.content)

    def _any_data_received(self):
        return self.response_buffer.tell() != 0

    def _get_received_data(self):
        result = self.response_buffer.getvalue()
        self.response_buffer.truncate(0)
        self.response_buffer.seek(0)
        return result

    def _check_status_code(self):
        if self.status_code == 0:
            self.status_code = self.curl.getinfo(pycurl.HTTP_CODE)
        if self.status_code != 0 and self.status_code != httplib.OK:
            raise urllib2.HTTPError(self.curl.url, self.status_code, None, None, None)

    def _check_curl_errors(self):
        for f in self.curl_multi.info_read()[2]:
            raise pycurl.error(*f[1:])

    def _iter_chunks(self):
        while True:
            remaining = self._perform_on_curl()
            if self._any_data_received():
                self._check_status_code()
                yield self._get_received_data()
            if remaining == 0:
                break
            self.curl_multi.select(SELECT_TIMEOUT)
        self._check_status_code()
        self._check_curl_errors()

    def _perform_on_curl(self):
        while True:
            ret, num_handles = self.curl_multi.perform()
            if ret != pycurl.E_CALL_MULTI_PERFORM:
                break
        return num_handles

    def iter_lines(self):
        chunks = self._iter_chunks()
        return self._split_lines_from_chunks(chunks)

    @staticmethod
    def _split_lines_from_chunks(chunks):
        #same behaviour as requests' Response.iter_lines(...)

        pending = None
        for chunk in chunks:

            if pending is not None:
                chunk = pending + chunk
            lines = chunk.splitlines()

            if lines and lines[-1] and chunk and lines[-1][-1] == chunk[-1]:
                pending = lines.pop()
            else:
                pending = None

            for line in lines:
                yield line

        if pending is not None:
            yield pending


class PycurlAdapter(object):
    """
    curl will cache http session
    """

    def __init__(self, verbose=None):
        self._c = None
        self.url = None
        self.response = StringIO()
        self.verbose = verbose

    @property
    def c(self):
        if self._c is None:
            self._c = pycurl.Curl()
        return self._c

    def request(self, url, method, data=None, kerberos_auth=False,
                allow_redirects=True, verify_ssl=True,
                headers=None, stream=False):
        self.c.reset()
        self.url = url
        method = method.lower()
        if method == 'post':
            print("setting post")
            self.c.setopt(pycurl.POST, 1)
            self.c.setopt(pycurl.HTTPGET, 0)
        elif method == 'get':
            print("setting get")
            self.c.setopt(pycurl.HTTPGET, 1)
            self.c.setopt(pycurl.POST, 0)
        elif method == 'put':
            self.c.setopt(pycurl.PUT, 1)
        elif method == 'delete':
            self.c.setopt(pycurl.CUSTOMREQUEST, "DELETE")
        else:
            raise RuntimeError("Unsupported method '%s' for curl call!" % method)

        self.c.setopt(pycurl.URL, url)
        self.c.setopt(pycurl.COOKIEFILE, '')
        self.c.setopt(pycurl.WRITEFUNCTION, self.response.write)
        self.c.setopt(pycurl.SSL_VERIFYPEER, 1 if verify_ssl else 0)
        self.c.setopt(pycurl.VERBOSE, 1 if self.verbose else 0)

        if data:
            self.c.setopt(pycurl.POSTFIELDS, data)
        #else:
        #    self.c.setopt(pycurl.POSTFIELDS, '')

        if allow_redirects:
            self.c.setopt(pycurl.FOLLOWLOCATION, 1)

        if kerberos_auth:
            self.c.setopt(pycurl.HTTPAUTH, pycurl.HTTPAUTH_GSSNEGOTIATE)
            self.c.setopt(pycurl.USERPWD, ':')

        if stream:
            headers = headers or {}
            headers['Cache-Control'] = 'no-cache'
            #self.curl.setopt(pycurl.CONNECTTIMEOUT, 5)

        if headers:
            header_list = []
            for header_key, header_value in headers.items():
                header_list.append("%s: %s" % (header_key, header_value))
            self.c.setopt(pycurl.HTTPHEADER, header_list)

        response = Response()
        if stream:
            curl_multi = pycurl.CurlMulti()
            curl_multi.add_handle(self.c)
            response.curl = self.c
            response.curl_multi = curl_multi
            response.response_buffer = self.response
        else:
            self.c.perform()
            response.status_code = self.c.getinfo(pycurl.HTTP_CODE)
            response.content = self.response.getvalue()
            # clear buffer
            self.response.truncate(0)
        return response

    def get(self, url, **kwargs):
        return self.request(url, "get", **kwargs)

    def post(self, url, **kwargs):
        return self.request(url, "post", **kwargs)

    def put(self, url, **kwargs):
        return self.request(url, "put", **kwargs)

    def delete(self, url, **kwargs):
        return self.request(url, "delete", **kwargs)


def get_http_session(verbose=None):
    if pycurl_imported:
        return PycurlAdapter(verbose=verbose)
    elif requests_imported:
        return requests.Session()
    else:
        RuntimeError("no http library imported")
