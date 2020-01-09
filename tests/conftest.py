"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals, print_function

import os
import re
import pytest
import inspect
import json
import logging
import fnmatch
from osbs.core import Openshift
from osbs.http import HttpResponse
from osbs.conf import Configuration
from osbs.api import OSBS
from osbs.constants import ANNOTATION_SOURCE_REPO, ANNOTATION_INSECURE_REPO
from tests.constants import (TEST_BUILD, TEST_CANCELLED_BUILD, TEST_ORCHESTRATOR_BUILD,
                             TEST_GIT_BRANCH, TEST_BUILD_CONFIG, TEST_GIT_URI_HUMAN_NAME,
                             TEST_KOJI_TASK_ID, TEST_IMAGESTREAM, TEST_IMAGESTREAM_NO_TAGS,
                             TEST_IMAGESTREAM_WITH_ANNOTATION,
                             TEST_IMAGESTREAM_WITHOUT_IMAGEREPOSITORY, TEST_GIT_URI_SANITIZED)
from tempfile import NamedTemporaryFile
from textwrap import dedent

from six.moves import http_client
from six.moves.urllib.parse import urlparse


logger = logging.getLogger("osbs.tests")
API_VER = Configuration.get_k8s_api_version()
APIS_PREFIX = "/apis/"
API_PREFIX = "/api/{v}/".format(v=API_VER)

API_BUILD_V1 = APIS_PREFIX + "build.openshift.io/v1/"
API_IMAGE_V1 = APIS_PREFIX + "image.openshift.io/v1/"
API_USER_V1 = APIS_PREFIX + "user.openshift.io/v1/"


class StreamingResponse(object):
    def __init__(self, status_code=200, content=b'', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def iter_lines(self):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class Connection(object):
    def __init__(self, version="3.9.41"):
        self.version = version
        self.response_mapping = ResponseMapping(version,
                                                lookup=self.get_definition_for)

        # mapping of urls or tuples of urls to responses; use get_definition_for
        # to get values from this dict
        #
        # The files are captured using the command line tool's
        # --capture-dir parameter, and edited as needed.
        self.DEFINITION = {
            (API_BUILD_V1 + "namespaces/default/builds",
             API_BUILD_V1 + "namespaces/default/builds/",
             API_BUILD_V1 + "namespaces/default/builds/?labelSelector=koji-task-id%3D123456789"): {
                "get": {
                    # Contains a list of builds
                    "file": "builds_list.json",
                },
                "post": {
                    # Contains a single build named test-build-123
                    "file": "build_test-build-123.json",
                },
            },

            (API_BUILD_V1 + "namespaces/default/builds/?labelSelector=koji-task-id%3D987654321"): {
                "get": {
                    # no build for this koji id
                    "file": "builds_list_empty.json",
                },
            },

            (API_BUILD_V1 + "namespaces/default/builds/?labelSelector=koji-task-id%3D123459876"): {
                "get": {
                    # one build for this koji id
                    "file": "builds_list_one.json",
                },
            },

            (API_BUILD_V1 + "namespaces/default/builds?fieldSelector=status%3DRunning",
             API_BUILD_V1 + "namespaces/default/builds/?fieldSelector=status%3DRunning"): {
                "get": {
                    # Contains a list of builds
                    "file": "builds_list.json",
                }
            },

            (API_BUILD_V1 + "namespaces/default/builds?fieldSelector=status%21%3DFailed%2C"
                           "status%21%3DComplete%2Cstatus%21%3DError%2Cstatus%21%3DCancelled",
             API_BUILD_V1 + "namespaces/default/builds/?fieldSelector=status%21%3DFailed%2C"
                           "status%21%3DComplete%2Cstatus%21%3DError%2Cstatus%21%3DCancelled"): {
                "get": {
                    # Contains a list of builds
                    "file": "builds_list.json",
                }
            },

            (API_BUILD_V1 + "namespaces/default/builds?fieldSelector=foo%3Doof%2C"
                           "status%21%3DFailed%2Cstatus%21%3DComplete%2Cstatus%21%3DError%2C"
                           "status%21%3DCancelled",
             API_BUILD_V1 + "namespaces/default/builds/?fieldSelector=foo%3Doof%2C"
                           "status%21%3DFailed%2Cstatus%21%3DComplete%2Cstatus%21%3DError%2C"
                           "status%21%3DCancelled"): {
                "get": {
                    # Contains a list of builds
                    "file": "builds_list.json",
                }
            },

            (API_BUILD_V1 + "namespaces/default/builds?fieldSelector=foo%3Doof",
             API_BUILD_V1 + "namespaces/default/builds/?fieldSelector=foo%3Doof"): {
                "get": {
                    # Contains a list of builds
                    "file": "builds_list.json",
                }
            },

            API_BUILD_V1 + "namespaces/default/builds/"
                          "?labelSelector=koji-task-id%3D{task}".format(task=TEST_KOJI_TASK_ID): {
                "get": {
                    # Contains a list of builds
                    "file": "builds_list.json",
                }
            },

            # Some 'builds' requests are with a trailing slash, some without:
            (API_BUILD_V1 + "namespaces/default/builds/%s" % TEST_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/" % TEST_BUILD): {
                 "get": {
                     # Contains a single build in Completed phase
                     # named test-build-123
                     "file": "build_test-build-123.json",
                 },
                 "put": {
                     "file": "build_test-build-123.json",
                 }
            },

            # Some 'builds' requests are with a trailing slash, some without:
            (API_BUILD_V1 + "namespaces/default/builds/%s" % TEST_ORCHESTRATOR_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/" % TEST_ORCHESTRATOR_BUILD): {
                 "get": {
                     # Contains a single build in Completed phase
                     # named test-orchestrator-build-123
                     "file": "build_test-orchestrator-build-123.json",
                 },
                 "put": {
                     "file": "build_test-orchestrator-build-123.json",
                 }
            },

            # Simulate build cancellation
            (API_BUILD_V1 + "namespaces/default/builds/%s" % TEST_CANCELLED_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/" % TEST_CANCELLED_BUILD): {
                 "get": {
                     # Contains a single build in Completed phase
                     # named test-build-123
                     "file": "build_test-build-cancel-123_get.json",
                 },
                 "put": {
                     "file": "build_test-build-cancel-123_put.json",
                 }
            },


            (API_BUILD_V1 + "namespaces/default/builds/%s/log/" % TEST_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/log/?follow=0" % TEST_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/log/?follow=1" % TEST_BUILD): {
                 "get": {
                     # Lines of text
                     "file": "build_test-build-123_logs.txt",
                 },
            },

            (API_BUILD_V1 + "namespaces/default/builds/%s/log/" % TEST_ORCHESTRATOR_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/log/?follow=0" % TEST_ORCHESTRATOR_BUILD,
             API_BUILD_V1 + "namespaces/default/builds/%s/log/?follow=1"
             % TEST_ORCHESTRATOR_BUILD): {
                 "get": {
                     # Lines of text
                     "file": "build_test-orchestrator-build-123_logs.txt",
                 },
            },

            ("/oauth/authorize",
             "/oauth/authorize?client_id=openshift-challenging-client&response_type=token",
             "/oauth/authorize?response_type=token&client_id=openshift-challenging-client"): {
                 "get": {
                     "file": "authorize.txt",
                     "custom_callback": self.process_authorize,
                 }
            },

            API_USER_V1 + "users/~/": {
                "get": {
                    "file": "get_user.json",
                }
            },

            API_BUILD_V1 + "watch/namespaces/default/builds/%s/" % TEST_BUILD: {
                "get": {
                    # Single MODIFIED item, with a Build object in
                    # Completed phase named test-build-123
                    "file": "watch_build_test-build-123.json",
                }
            },

            API_BUILD_V1 + "watch/namespaces/default/builds/%s/" % TEST_ORCHESTRATOR_BUILD: {
                "get": {
                    # Single MODIFIED item, with a Build object in
                    # Completed phase named test-build-123
                    "file": "watch_build_test-orchestrator-build-123.json",
                }
            },

            API_BUILD_V1 + "namespaces/default/buildconfigs/": {
                "post": {
                    # Contains a BuildConfig named test-build-config-123
                    "file": "created_build_config_test-build-config-123.json",
                }
            },

            API_BUILD_V1 + "namespaces/default/buildconfigs/%s/instantiate" % TEST_BUILD_CONFIG: {
                "post": {
                    # A Build named test-build-123 instantiated from a
                    # BuildConfig named test-build-config-123
                    "file": "instantiated_test-build-config-123.json",
                }
            },

            # use both version with ending slash and without it
            (API_BUILD_V1 + "namespaces/default/buildconfigs/%s" % TEST_BUILD_CONFIG,
             API_BUILD_V1 + "namespaces/default/buildconfigs/%s/" % TEST_BUILD_CONFIG,
             ((API_BUILD_V1 + "namespaces/default/buildconfigs/?labelSelector=" +
               "git-repo-name%%3D%s" "%%2C" "git-branch%%3D%s"
               ) % (TEST_GIT_URI_HUMAN_NAME, TEST_GIT_BRANCH)),
             ((API_BUILD_V1 + "namespaces/default/buildconfigs/?labelSelector=" +
               "git-repo-name%%3D%s" "%%2C" "git-branch%%3D%s" "%%2C" "git-full-repo%%3D%s"
               ) % (TEST_GIT_URI_HUMAN_NAME, TEST_GIT_BRANCH, TEST_GIT_URI_SANITIZED)),
             ): {
                 "get": {
                     "custom_callback":
                         self.with_status_code(http_client.NOT_FOUND),
                     # Empty file (no response content as the status is 404
                     "file": None,
                 }
            },

            API_BUILD_V1 + "namespaces/default/builds/?labelSelector=buildconfig%%3D%s" %
            TEST_BUILD_CONFIG: {
                "get": {
                    # Contains a BuildList with Builds labeled with
                    # buildconfig=fedora23-something, none of which
                    # are running
                    "file": "builds_list.json"
                }
            },

            API_IMAGE_V1 + "namespaces/default/imagestreams/%s" %
            TEST_IMAGESTREAM: {
                "get": {
                    # Contains imagestream
                    # with 3 tags
                    "file": "imagestream.json"
                },
                "put": {
                    # Contains imagestream
                    # with 3 tags but with different resourceVersion
                    "file": "imagestream.json",
                    "custom_callback": self.increment_resource_version
                }
            },

            API_IMAGE_V1 + "namespaces/default/imagestreams/%s" %
            TEST_IMAGESTREAM_NO_TAGS: {
                "get": {
                    # Contains imagestream with no tags
                    "file": "imagestream.json",
                    "custom_callback": self.remove_tags
                },
                "put": {
                    # Contains imagestream with no tags
                    "file": "imagestream.json",
                    "custom_callback": self.remove_tags
                }
            },

            API_IMAGE_V1 + "namespaces/default/imagestreams/%s" %
            TEST_IMAGESTREAM_WITH_ANNOTATION: {
                "get": {
                    # Contains imagestream with 3 tags; source repository
                    # is listed in annotation instead of spec.
                    "file": "imagestream.json",
                    "custom_callback": self.move_repo_to_annotation
                },
                "put": {
                    # Contains imagestream with 3 tags; source repository
                    # is listed in annotation instead of spec.
                    "file": "imagestream.json",
                    "custom_callback": self.move_repo_to_annotation
                }
            },

            API_IMAGE_V1 + "namespaces/default/imagestreams/%s" %
            TEST_IMAGESTREAM_WITHOUT_IMAGEREPOSITORY: {
                "get": {
                    # Contains imagestream with 3 tags; source repository
                    # is listed in annotation instead of spec.
                    "file": "imagestream.json",
                    "custom_callback": self.remove_imagerepository
                },
                "put": {
                    # Contains imagestream with 3 tags; source repository
                    # is listed in annotation instead of spec.
                    "file": "imagestream.json",
                    "custom_callback": self.remove_imagerepository
                }
            },

            API_IMAGE_V1 + "namespaces/default/imagestreamimports/": {
                "post": {
                    "file": "imagestreamimport.json",
                }
            },

            API_PREFIX + "namespaces/default/pods/?labelSelector=openshift.io%%2Fbuild.name%%3D%s" %
            TEST_BUILD: {
                "get": {
                    # Contains a list of build pods, just needs not to
                    # be empty
                    "file": "pods.json",
                },
            },

            API_PREFIX + "namespaces/default/pods/?labelSelector=openshift.io%%2Fbuild.name%%3D%s" %
            TEST_ORCHESTRATOR_BUILD: {
                "get": {
                    # Contains a list of build pods, just needs not to
                    # be empty
                    "file": "pods.json",
                },
            },

            API_PREFIX + "namespaces/default/resourcequotas/": {
                # Make the POST fail so we can test PUT
                "post": {
                    "custom_callback": self.with_status_code(http_client.CONFLICT),

                    # Reponse is not really empty but it isn't relevant to
                    # the testing
                    "file": None,
                },
            },

            API_PREFIX + "namespaces/default/resourcequotas/pause": {
                "put": {
                    "file": None,
                },

                "delete": {
                    "file": None,  # not really empty but not relevant
                },
            },

            (API_PREFIX + "namespaces/default/configmaps/",
             API_PREFIX + "namespaces/default/configmaps/special-config"): {
                "post": {
                    # Contains a configMap
                    "file": "create_config_map.json",
                },
                "get": {
                    # Contains a configMap
                    "file": "create_config_map.json",
                },
                "delete": {
                    # doesn't return anything
                    "file": None,
                },
            },

        }

    @staticmethod
    def process_authorize(key, content):
        match = re.findall("[Ll]ocation: (.+)", content.decode("utf-8"))
        headers = {
            "location": match[0],
        }
        logger.debug("headers: %s", headers)
        return {
            "headers": headers
        }

    @staticmethod
    def with_status_code(status_code):
        def custom_func(key, content):
            return {
                "content": content,
                "status_code": status_code,
            }

        return custom_func

    @staticmethod
    def increment_resource_version(key, content):
        content = json.loads(content)
        ver = int(content['metadata']['resourceVersion']) + 1
        content['metadata']['resourceVersion'] = str(ver)
        return {"content": json.dumps(content).encode('utf-8')}

    @staticmethod
    def remove_tags(key, content):
        content = json.loads(content)
        content['spec']['tags'] = []
        return {"content": json.dumps(content).encode('utf-8')}

    @staticmethod
    def remove_imagerepository(key, content):
        content = json.loads(content)
        content['spec'].pop('dockerImageRepository', None)
        content['metadata']['annotations'].pop(ANNOTATION_INSECURE_REPO, None)
        content['spec']['tags'] = []
        return {"content": json.dumps(content).encode('utf-8')}

    @staticmethod
    def move_repo_to_annotation(key, content):
        content = json.loads(content)
        repo = content['spec'].pop('dockerImageRepository', None)
        content['metadata']['annotations'][ANNOTATION_SOURCE_REPO] = repo
        return {"content": json.dumps(content).encode('utf-8')}

    def get_definition_for(self, key):
        """
        Returns key and value associated with given key in DEFINITION dict.

        This means that either key is an actual dict key in DEFINITION or it is member
        of a tuple that serves as a dict key in DEFINITION.
        """
        try:
            # Try a direct look-up
            return key, self.DEFINITION[key]
        except KeyError:
            # Try all the tuples
            for k, v in self.DEFINITION.items():
                if isinstance(k, tuple):
                    for tup in k:
                        if fnmatch.fnmatch(key, tup):
                            return k, v
                else:
                    if fnmatch.fnmatch(key, k):
                        return k, v

            raise ValueError("Can't find '%s' in url mapping definition" % key)

    @staticmethod
    def response(status_code=200, content=b'', headers=None):
        return HttpResponse(status_code, headers or {}, content=content)

    def request(self, url, method, stream=None, *args, **kwargs):
        parsed_url = urlparse(url)
        # fragment = parsed_url.fragment
        # parsed_fragment = urllib.parse_qs(fragment)
        url_path = parsed_url.path
        if parsed_url.query:
            url_path += '?' + parsed_url.query
        logger.info("URL path is '%s'", url_path)
        kwargs = self.response_mapping.response_mapping(url_path, method)
        if stream:
            return StreamingResponse(**kwargs)
        else:
            return self.response(**kwargs)

    def get(self, url, *args, **kwargs):
        return self.request(url, "get", *args, **kwargs)

    def post(self, url, *args, **kwargs):
        return self.request(url, "post", *args, **kwargs)

    def put(self, url, *args, **kwargs):
        return self.request(url, "put", *args, **kwargs)

    def delete(self, url, *args, **kwargs):
        return self.request(url, "delete", *args, **kwargs)


@pytest.fixture(params=["3.9.41"])
def openshift(request):
    os_inst = Openshift(APIS_PREFIX, "/oauth/authorize", k8s_api_url=API_PREFIX)
    os_inst._con = Connection(request.param)
    return os_inst


@pytest.fixture(params=[{'kwargs': None, 'additional_config': None}])
def osbs(request, openshift):
    kwargs = request.param['kwargs'] or {}

    kwargs.setdefault('build_json_dir', 'inputs')
    kwargs.setdefault('additional_general', '')
    with NamedTemporaryFile(mode="wt") as fp:
        config = dedent("""\
            [general]
            build_json_dir = {build_json_dir}
            {additional_general}

            [default]
            openshift_url = /
            flatpak_base_image = registry.fedoraproject.org/fedora:latest
            can_orchestrate = true
            use_auth = false
            build_from = image:buildroot:latest
            """)

        if request.param['additional_config'] is not None:
            config += request.param['additional_config']
            config += '\n'

        fp.write(config.format(**kwargs))
        fp.flush()
        dummy_config = Configuration(fp.name)
        osbs = OSBS(dummy_config, dummy_config)

    osbs.os = openshift
    return osbs


@pytest.fixture
def osbs_cant_orchestrate(openshift):
    with NamedTemporaryFile(mode="wt") as fp:
        fp.write("""
[general]
build_json_dir = {build_json_dir}
[default]
openshift_url = /
use_auth = false
""".format(build_json_dir="inputs"))
        fp.flush()
        dummy_config = Configuration(fp.name)
        osbs = OSBS(dummy_config, dummy_config)

    osbs.os = openshift
    return osbs


@pytest.fixture
def osbs106(openshift):
    with NamedTemporaryFile(mode="wt") as fp:
        fp.write("""
[general]
build_json_dir = {build_json_dir}
openshift_required_version = 1.0.6
[default]
openshift_url = /
build_from = image:buildroot:latest
""".format(build_json_dir="inputs"))
        fp.flush()
        dummy_config = Configuration(fp.name)
        osbs = OSBS(dummy_config, dummy_config)

    osbs.os = openshift
    return osbs


class ResponseMapping(object):
    def __init__(self, version, lookup):
        self.version = version
        self.lookup = lookup

    def get_response_content(self, file_name):
        this_file = inspect.getfile(ResponseMapping)
        this_dir = os.path.dirname(this_file)
        json_path = os.path.join(this_dir, "mock_jsons", self.version, file_name)
        logger.debug("File: %s", json_path)
        with open(json_path, "rb") as fd:
            return fd.read()

    def response_mapping(self, url_path, method):
        key, value_to_use = self.lookup(url_path)
        file_name = value_to_use[method]["file"]
        logger.debug("API response content: %s", file_name)
        custom_callback = value_to_use[method].get("custom_callback", None)
        if file_name is None:
            content = b''
        else:
            content = self.get_response_content(file_name)

        if custom_callback:
            logger.debug("Custom API callback: %s", custom_callback)
            return custom_callback(key, content)
        else:
            return {"content": content}
