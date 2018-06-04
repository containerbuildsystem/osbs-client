"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals

import os

HERE = os.path.dirname(__file__)
INPUTS_PATH = os.path.join(HERE, '..', 'inputs')

TEST_BUILD = "test-build-123"
TEST_ORCHESTRATOR_BUILD = "test-orchestrator-build-123"
TEST_CANCELLED_BUILD = "test-build-cancel-123"
TEST_BUILD_CONFIG = "path-master-?????"
TEST_SCRATCH_BUILD_NAME = "scratch-?????-??????????????"
TEST_ISOLATED_BUILD_NAME = "isolated-?????-??????????????"
TEST_IMAGESTREAM = "test_imagestream"
TEST_IMAGESTREAM_NO_TAGS = "test_imagestream_no_tags"
TEST_IMAGESTREAM_WITH_ANNOTATION = "test_imagestream_with_annotation"
TEST_GIT_URI = "git://hostname/path"
# centos doesn't like urlquote(TEST_GIT_URI, safe='') so do it by hand
TEST_GIT_URI_HTML = "git%3A%2F%2Fhostname%2Fpath"
TEST_GIT_URI_HUMAN_NAME = "path"
TEST_GIT_REF = "0123456789012345678901234567890123456789"
TEST_GIT_BRANCH = "master"
TEST_USER = "user"
TEST_COMPONENT = "component"
TEST_VERSION = "1.0"
TEST_TARGET = "target"
TEST_ARCH = "x86_64"
TEST_BUILD_POD = "build-test-build-123"
TEST_LABEL = "test-label"
TEST_LABEL_VALUE = "sample-value"
TEST_KOJI_TASK_ID = 12345
TEST_FILESYSTEM_KOJI_TASK_ID = 67890

TEST_FLATPAK_BASE_IMAGE = 'registry.fedoraproject.org/fedora:latest'

TEST_BUILD_JSON = {
    "metadata": {
        "name": "{{NAME}}"
    },
    "kind": "BuildConfig",
    "apiVersion": "v1",
    "spec": {
        "triggers": [
            {
                "type": "ImageChange",
                "imageChange": {
                    "from": {
                        "kind": "ImageStreamTag",
                        "name": "{{BASE_IMAGE_STREAM}}"
                    }
                }
            }
        ],
        "source": {
            "type": "Git",
            "git": {
                "uri": "{{GIT_URI}}"
            }
        },
        "strategy": {
            "type": "Custom",
            "customStrategy": {
                "from": {
                    "kind": "ImageStreamTag",
                    "name": "buildroot:latest"
                },
                "exposeDockerSocket": True,
                "env": [{
                    "name": "ATOMIC_REACTOR_PLUGINS",
                    "value": "TBD"
                }]
            }
        },
        "output": {
            "to": {
                "kind": "DockerImage",
                "name": "{{REGISTRY_URI}}/{{OUTPUT_IMAGE_TAG}}"
            }
        }
    }
}

TEST_INNER_DOCK_JSON = {
    "prebuild_plugins": [
        {
            "name": "change_from_in_dockerfile"
        },
        {
            "args": {
                "key1": {
                    "a": "1",
                    "b": "2"
                },
                "key2": "b"
            },
            "name": "a_plugin"
        },
    ],
    "postbuild_plugins": [
        {
            "args": {
                "image_id": "BUILT_IMAGE_ID"
            },
            "name": "all_rpm_packages"
        },
    ]
}
