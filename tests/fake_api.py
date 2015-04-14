from __future__ import absolute_import, unicode_literals, print_function

import json
import os
import re
import pytest
import inspect
import logging
from osbs.core import Openshift
from osbs.http import Response

try:
    # py2
    import urlparse
except ImportError:
    # py3
    import urllib.parse as urlparse


def process_authorize(content):
    match = re.findall(b"[Ll]ocation: (.+)", content)
    headers = {
        "location": match[0],
    }
    logger.debug("headers: %s", headers)
    return {
        "headers": headers
    }


DEFINITION = {
    "/osapi/v1beta1/builds": {
        "get": {
            "file": "builds_list.json",
        },
    },
    "/osapi/v1beta1/builds/test-build-123": {
        "get": {
            "file": "build_test-build-123.json",
        },
        "put": {
            "file": "build_test-build-123.json",
        }
    },
    "/oauth/authorize": {
        "get": {
            "file": "authorize.txt",
            "custom_callback": process_authorize,
        }
    },
}


logger = logging.getLogger("osbs.tests")


def response(status_code=200, content='', headers=None):
    res = Response(content=content)
    res.status_code = status_code
    res._headers = headers or {}
    return res


class Connection(object):
    def __init__(self, version="0.4.1"):
        self.version = version
        self.response_mapping = ResponseMapping(version)

    def _request(self, url, method, *args, **kwargs):
        parsed_url = urlparse.urlparse(url)
        # fragment = parsed_url.fragment
        # parsed_fragment = urlparse.parse_qs(fragment)
        url_path = parsed_url.path
        logger.info("URL path is '%s'", url_path)
        kwargs = self.response_mapping.response_mapping(url_path, method)
        return response(**kwargs)

    def get(self, url, *args, **kwargs):
        return self._request(url, "get", *args, **kwargs)

    def post(self, url, *args, **kwargs):
        return self._request(url, "post", *args, **kwargs)

    def put(self, url, *args, **kwargs):
        return self._request(url, "put", *args, **kwargs)


@pytest.fixture
def openshift():
    os_inst = Openshift("/osapi/v1beta1/", "/oauth/authorize", "")
    os_inst._con = Connection()
    return os_inst


class ResponseMapping(object):
    def __init__(self, version):
        self.version = version

    def get_response_content(self, file_name):
        this_file = inspect.getfile(ResponseMapping)
        this_dir = os.path.dirname(this_file)
        json_path = os.path.join(this_dir, "mock_jsons", self.version, file_name)
        with open(json_path, "r") as fd:
            return fd.read().encode("utf-8")

    def response_mapping(self, url_path, method):
        global DEFINITION
        file_name = DEFINITION[url_path][method]["file"]
        custom_callback = DEFINITION[url_path][method].get("custom_callback", None)
        content = self.get_response_content(file_name)
        if custom_callback:
            return custom_callback(content)
        else:
            return {"content": content}

