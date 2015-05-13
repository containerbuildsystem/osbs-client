"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


abstraction on top of http api calls

use pycurl (can handle chunked response properly), fallback to requests

chunked implementation for pycurl taken from:
  http://stackoverflow.com/a/21809888/909579
"""

from __future__ import print_function, absolute_import, unicode_literals

import re
import json
import logging
import email.parser
from osbs.exceptions import OsbsException, OsbsNetworkException

try:
    # py2
    import httplib
    from urllib2 import HTTPError
    from StringIO import StringIO as BytesIO
except ImportError:
    # py3
    import http.client as httplib
    from urllib.error import HTTPError
    from io import BytesIO


logger = logging.getLogger(__name__)

# requests_imported = False
# pycurl_imported = False

# prefered http lib is pycurl, since it understands chunked responses and kerberos
import pycurl
pycurl_imported = True

# FIXME: fix compat abstraction between requests and pycurl
#        so core doesn't have to care about chosen http lib

# try:
#     import pycurl
# except ImportError:
#     try:
#         import requests
#     except ImportError:
#         print("Neither requests, nor pycurl are available.")
#         sys.exit(1)
#     else:
#         requests_imported = True
# else:
#     pycurl_imported = True


SELECT_TIMEOUT = 9999
PYCURL_NETWORK_CODES = [pycurl.E_BAD_CONTENT_ENCODING,
                        pycurl.E_BAD_DOWNLOAD_RESUME,
                        pycurl.E_CONV_FAILED,
                        pycurl.E_CONV_REQD,
                        pycurl.E_COULDNT_CONNECT,
                        pycurl.E_COULDNT_RESOLVE_HOST,
                        pycurl.E_COULDNT_RESOLVE_PROXY,
                        pycurl.E_FILESIZE_EXCEEDED,
                        pycurl.E_HTTP_POST_ERROR,
                        pycurl.E_HTTP_RANGE_ERROR,
                        pycurl.E_HTTP_RETURNED_ERROR,
                        pycurl.E_LOGIN_DENIED,
                        getattr(pycurl, "E_OPERATION_TIMEDOUT", None),  # not in el7 pycurl
                        pycurl.E_PARTIAL_FILE,
                        pycurl.E_READ_ERROR,
                        pycurl.E_RECV_ERROR,
                        pycurl.E_REMOTE_FILE_NOT_FOUND,
                        pycurl.E_SEND_ERROR,
                        pycurl.E_SSL_CACERT,
                        pycurl.E_SSL_CERTPROBLEM,
                        pycurl.E_SSL_CIPHER,
                        pycurl.E_SSL_CONNECT_ERROR,
                        pycurl.E_SSL_PEER_CERTIFICATE,
                        pycurl.E_SSL_SHUTDOWN_FAILED,
                        pycurl.E_TOO_MANY_REDIRECTS,
                        pycurl.E_UNSUPPORTED_PROTOCOL,
                        pycurl.E_WRITE_ERROR]

PYCURL_NETWORK_CODES = [x for x in PYCURL_NETWORK_CODES if x is not None]


class Response(object):
    """ let's mock Response object of requests """

    def __init__(self, status_code=0, content=b'', curl=None, raw_headers=None):
        self.status_code = status_code
        self.content = content
        self.curl = curl
        self.curl_multi = getattr(self.curl, "curl_multi", None)
        self.response_buffer = getattr(self.curl, "response", None)
        self._raw_headers = raw_headers
        self._headers = None

    @property
    def raw_headers(self):
        """
        Headers of request (str)
        """
        return self._raw_headers

    @raw_headers.setter
    def raw_headers(self, raw_headers):
        self._raw_headers = raw_headers

    @property
    def headers(self):
        if self._headers is None:
            logger.debug("raw headers: " + repr(self.raw_headers))
            headers_buffer = BytesIO(self.raw_headers)
            try:
                # py 2
                # seekable has to be 0, otherwise it won't parse anything
                m = httplib.HTTPMessage(headers_buffer, seekable=0)
                m.readheaders()
                self._headers = m.dict
            except TypeError as ex:
                # py 3
                if ex.args[0] == "__init__() got an unexpected keyword argument 'seekable'":
                    parser = email.parser.Parser()
                    m = parser.parsestr(self.raw_headers.decode('iso-8859-1'))
                    self._headers = dict(m.items())
                else:
                    raise
        return self._headers

    @property
    def encoding(self):
        encoding = None
        if 'content-type' in self.headers:
            content_type = self.headers['content-type'].lower()
            match = re.search(r'charset=(\S+)', content_type)
            if match:
                encoding = match.group(1)
        if encoding is None:
            encoding = 'utf-8'  # assume utf-8

        return encoding

    def json(self, check=True):
        if check:
            self._check_status_code()
        return json.loads(self.content.decode(self.encoding))

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
        if self.status_code not in (0, httplib.OK, httplib.CREATED):
            if self.curl:
                url = getattr(self.curl, "url", None)
            else:
                url = None
            raise HTTPError(url, self.status_code, None, None, None)

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
            sel = self.curl_multi.select(SELECT_TIMEOUT)
            if sel == -1:
                raise RuntimeError("error during select")
        self._check_status_code()
        self._check_curl_errors()
        self.close_multi()

    def _perform_on_curl(self):
        while True:
            ret, num_handles = self.curl_multi.perform()
            if ret != pycurl.E_CALL_MULTI_PERFORM:
                break
        return num_handles

    def iter_lines(self):
        try:
            chunks = self._iter_chunks()
            return self._split_lines_from_chunks(chunks)
        except pycurl.error as ex:
            code = ex.args[0]
            message = ex.args[1]
            if code in PYCURL_NETWORK_CODES:
                raise OsbsNetworkException("<?>", message, code, *ex.args[2:])

            raise OsbsException(repr(ex))
        except HTTPError as ex:
            raise OsbsNetworkException(ex.geturl(), ex.message, ex.code)
        except Exception as ex:
            raise OsbsException(repr(ex))

    @staticmethod
    def _split_lines_from_chunks(chunks):
        # same behaviour as requests' Response.iter_lines(...)

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

    def close_multi(self):
        logger.debug("closing curl multi: %s", id(self.curl_multi))
        self.curl_multi.remove_handle(self.curl)
        self.curl_multi.close()


class PycurlAdapter(object):
    """
    curl will cache http session
    """

    def __init__(self, verbose=None):
        self._c = None
        self.url = None
        self.response = BytesIO()
        self.response_headers = BytesIO()
        self.verbose = verbose

    @property
    def c(self):
        if self._c is None:
            self._c = pycurl.Curl()
        return self._c

    def request(self, url, method, data=None, kerberos_auth=False,
                allow_redirects=True, verify_ssl=True, use_json=False,
                headers=None, stream=False, username=None, password=None):
        self.c.reset()
        self.url = url
        headers = headers or {}
        method = method.lower()

        if method == 'post':
            self.c.setopt(pycurl.POST, 1)
        elif method == 'get':
            self.c.setopt(pycurl.HTTPGET, 1)
        elif method == 'put':
            # self.c.setopt(pycurl.PUT, 1)
            self.c.setopt(pycurl.CUSTOMREQUEST, b"PUT")
            headers["Expect"] = ""
        elif method == 'delete':
            self.c.setopt(pycurl.CUSTOMREQUEST, b"DELETE")
        else:
            raise RuntimeError("Unsupported method '%s' for curl call!" % method)

        self.c.setopt(pycurl.COOKIEFILE, b'')
        self.c.setopt(pycurl.URL, str(url))
        self.c.setopt(pycurl.WRITEFUNCTION, self.response.write)
        self.c.setopt(pycurl.HEADERFUNCTION, self.response_headers.write)
        self.c.setopt(pycurl.SSL_VERIFYPEER, 1 if verify_ssl else 0)
        self.c.setopt(pycurl.SSL_VERIFYHOST, 2 if verify_ssl else 0)
        self.c.setopt(pycurl.VERBOSE, 1 if self.verbose else 0)
        if username and password:
            self.c.setopt(pycurl.USERPWD, b"%s:%s" % (username, password))

        if data:
            # curl sets the method to post if one sets any POSTFIELDS (even '')
            self.c.setopt(pycurl.POSTFIELDS, data)

        if use_json:
            headers['Content-Type'] = b'application/json'

        if allow_redirects:
            self.c.setopt(pycurl.FOLLOWLOCATION, 1)

        if kerberos_auth:
            self.c.setopt(pycurl.HTTPAUTH, pycurl.HTTPAUTH_GSSNEGOTIATE)
            self.c.setopt(pycurl.USERPWD, b':')

        if stream:
            headers['Cache-Control'] = b'no-cache'
            #self.curl.setopt(pycurl.CONNECTTIMEOUT, 5)

        if headers:
            header_list = []
            for header_key, header_value in headers.items():
                header_list.append(str("%s: %s" % (header_key, header_value)))
            self.c.setopt(pycurl.HTTPHEADER, header_list)

        response = Response()
        if stream:
            curl_multi = pycurl.CurlMulti()
            curl_multi.add_handle(self.c)
            while response.status_code == 0:
                sel = curl_multi.select(SELECT_TIMEOUT)  # returns number
                if sel == -1:
                    raise OsbsException("error during select")
                ret, _ = curl_multi.perform()
                if ret == pycurl.E_CALL_MULTI_PERFORM:
                    raise OsbsNetworkException(url,
                                               "error during doing curl_multi",
                                               ret)
                response.status_code = self.c.getinfo(pycurl.HTTP_CODE)
            response.content = self.response.getvalue()

            # self.response_headers contains headers from all responses - even
            # without FOLLOWLOCATION there might be multiple sets of headers
            # due to 401 Unauthorized. We only care about the last response.
            allheaders = self.response_headers.getvalue().decode(errors='replace')
            response.raw_headers = allheaders.split("\r\n\r\n")[-2]

            response.curl = self.c
            response.curl_multi = curl_multi
            response.response_buffer = self.response
        else:
            self.c.perform()
            response.status_code = self.c.getinfo(pycurl.HTTP_CODE)
            response.content = self.response.getvalue()

            # self.response_headers contains headers from all responses - even
            # without FOLLOWLOCATION there might be multiple sets of headers
            # due to 401 Unauthorized. We only care about the last response.
            allheaders = self.response_headers.getvalue().decode(errors='replace')
            try:
                response.raw_headers = allheaders.split("\r\n\r\n")[-2]
            except IndexError:
                logger.warning('Incorrectly terminated http headers')
                response.raw_headers = allheaders

            # clear buffer
            self.response.truncate(0)
            self.response_headers.truncate(0)
        return response

    def _do_request(self, url, method, **kwargs):
        try:
            return self.request(url, method, **kwargs)
        except pycurl.error as ex:
            code = ex.args[0]
            message = ex.args[1]
            if code in PYCURL_NETWORK_CODES:
                raise OsbsNetworkException(url, message, code, *ex.args[2:])

            raise OsbsException(repr(ex))
        except HTTPError as ex:
            raise OsbsNetworkException(ex.geturl(), ex.message, ex.code)
        except Exception as ex:
            raise OsbsException(repr(ex))

    def get(self, url, **kwargs):
        return self._do_request(url, "get", **kwargs)

    def post(self, url, **kwargs):
        return self._do_request(url, "post", **kwargs)

    def put(self, url, **kwargs):
        return self._do_request(url, "put", **kwargs)

    def delete(self, url, **kwargs):
        return self._do_request(url, "delete", **kwargs)


def get_http_session(verbose=None):
    if pycurl_imported:
        return PycurlAdapter(verbose=verbose)
    #elif requests_imported:
    #    return requests.Session()
    else:
        raise OsbsException("no http library imported")
