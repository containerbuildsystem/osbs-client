"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


abstraction on top of http api calls

use pycurl (can handle chunked response properly)

chunked implementation for pycurl taken from:
  http://stackoverflow.com/a/21809888/909579
"""

from __future__ import print_function, absolute_import, unicode_literals

import re
import sys
import json
import time
import codecs
import logging
from io import BytesIO

import pycurl

from osbs.exceptions import OsbsException, OsbsNetworkException, OsbsResponseException

try:
    # py2
    import httplib
except ImportError:
    # py3
    import http.client as httplib


logger = logging.getLogger(__name__)

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
                        # old pycurl: E_OPERATION_TIMEOUTED, new pycurl: E_OPERATION_TIMEDOUT
                        getattr(pycurl, "E_OPERATION_TIMEDOUT", "E_OPERATION_TIMEOUTED"),
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


def parse_headers(all_headers):
    # all_headers contains headers from all responses - even without FOLLOWLOCATION there
    # might be multiple sets of headers due to 401 Unauthorized. We only care about the last
    # response.
    try:
        raw_headers = all_headers.split(b"\r\n\r\n")[-2]
    except IndexError:
        logger.warning('Incorrectly terminated http headers')
        raw_headers = all_headers

    logger.debug("raw headers: " + repr(raw_headers))

    # http://stackoverflow.com/questions/24728088/python-parse-http-response-string/24729316#24729316
    class FakeSocket(object):
        def __init__(self, response_str):
            self._file = BytesIO(response_str)

        def makefile(self, *args, **kwargs):
            return self._file

    response = httplib.HTTPResponse(FakeSocket(raw_headers))
    response.begin()
    header_list = [(k.lower(), v) for (k, v) in response.getheaders()]
    return dict(header_list)


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
                # joining at once is much faster than doing += in a loop
                all_chunks = list(s.iter_chunks())
                content = ''.join(all_chunks)
                return HttpResponse(s.status_code, s.headers, content)
        except pycurl.error as ex:
            code = ex.args[0]
            try:
                message = ex.args[1]
            except IndexError:
                # happened on rhel 6
                message = ""
            if code in PYCURL_NETWORK_CODES:
                raise OsbsNetworkException(url, message, code, *ex.args[2:],
                                           cause=ex,
                                           traceback=sys.exc_info()[2])

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
        self.response_buffer = BytesIO()
        self.headers_buffer = BytesIO()
        self.response_decoder = None

        self.url = url
        headers = headers or {}
        method = method.lower()

        self.c = pycurl.Curl()
        self.curl_multi = pycurl.CurlMulti()

        if method == 'post':
            self.c.setopt(pycurl.POST, 1)
            headers["Expect"] = ""  # openshift can't handle Expect
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
        self.c.setopt(pycurl.WRITEFUNCTION, self.response_buffer.write)
        self.c.setopt(pycurl.HEADERFUNCTION, self.headers_buffer.write)
        self.c.setopt(pycurl.DEBUGFUNCTION, self._curl_debug)
        self.c.setopt(pycurl.SSL_VERIFYPEER, 1 if verify_ssl else 0)
        self.c.setopt(pycurl.SSL_VERIFYHOST, 2 if verify_ssl else 0)
        if ca:
            logger.info("Setting CAINFO to %r", ca)
            self.c.setopt(pycurl.CAINFO, ca)

        self.c.setopt(pycurl.VERBOSE, 1 if verbose else 0)
        if username and password:
            username = username.encode('utf-8')
            password = password.encode('utf-8')
            self.c.setopt(pycurl.USERPWD, username + b":" + password)

        if client_cert and client_key:
            self.c.setopt(pycurl.SSLCERTTYPE, "PEM")
            self.c.setopt(pycurl.SSLKEYTYPE, "PEM")
            self.c.setopt(pycurl.SSLCERT, client_cert)
            self.c.setopt(pycurl.SSLKEY, client_key)

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

        if headers:
            header_list = []
            for header_key, header_value in headers.items():
                header_list.append(str("%s: %s" % (header_key, header_value)))
            self.c.setopt(pycurl.HTTPHEADER, header_list)

        self.curl_multi.add_handle(self.c)

        # Send request and read all headers. We have all headers once we receive some data or once
        # the response ends.
        # NOTE: HTTP response in chunked encoding can contain additional headers ("trailers") in the
        # last chunk. This is not handled here.
        while not (self.finished or self._any_data_received()):
            self._select()
            self._perform()

        self.headers = parse_headers(self.headers_buffer.getvalue())
        self.status_code = self.c.getinfo(pycurl.HTTP_CODE)
        self.response_decoder = codecs.getincrementaldecoder(self.encoding)()

    def _perform(self):
        while True:
            ret, num_handles = self.curl_multi.perform()
            if ret != pycurl.E_CALL_MULTI_PERFORM:
                # see curl_multi_perform manpage
                break

        num_q, _, err_list = self.curl_multi.info_read()
        if num_q != 0:
            logger.warning("CurlMulti.info_read() has %s remaining messages", num_q)

        if err_list:
            err_obj = err_list[0]

            # For anything but the connection being closed, raise
            if err_obj[1] != pycurl.E_PARTIAL_FILE:
                raise OsbsNetworkException(self.url, err_obj[2], err_obj[1])

        self.finished = (num_handles == 0)

    def _select(self):
        sel = self.curl_multi.select(SELECT_TIMEOUT)
        if sel == -1:
            raise OsbsException("CurlMulti.select() timed out")
        elif sel == 0:
            # sel==0 means curl_multi_fdset returned -1
            # manual page suggests sleeping >100ms when this happens:(
            time.sleep(0.1)

    def _any_data_received(self):
        return self.response_buffer.tell() != 0

    def _get_received_data(self):
        result = self.response_buffer.getvalue()
        self.response_buffer.truncate(0)
        self.response_buffer.seek(0)
        return self.response_decoder.decode(result, final=self.finished)

    def iter_chunks(self):
        while True:
            self._perform()
            if self._any_data_received():
                yield self._get_received_data()
            if self.finished:
                break
            self._select()

        logger.debug("end of the stream")
        self.close()

    def iter_lines(self):
        chunks = self.iter_chunks()
        return self._split_lines_from_chunks(chunks)

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

    @staticmethod
    def _curl_debug(debug_type, debug_msg):
        try:
            logger_name = {
                pycurl.INFOTYPE_TEXT: 'curl',
                pycurl.INFOTYPE_HEADER_IN: 'in',
                pycurl.INFOTYPE_HEADER_OUT: 'out'
            }[debug_type]
        except KeyError:
            return

        curl_logger = logging.getLogger(__name__ + '.' + logger_name)
        for line in debug_msg.splitlines():
            if not line:
                continue
            curl_logger.debug(line)

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

    def close(self):
        if not self.closed:
            logger.debug("cleaning up")
            self.curl_multi.remove_handle(self.c)
            self.c.close()
            self.curl_multi.close()
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
        if check and self.status_code not in (0, httplib.OK, httplib.CREATED):
            raise OsbsResponseException(self.content, self.status_code)

        return json.loads(self.content)
