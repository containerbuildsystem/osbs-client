"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals

import os

HERE = os.path.dirname(__file__)
FILES = os.path.join(HERE, 'files')

TEST_PIPELINE_RUN_TEMPLATE = os.path.join(FILES, 'example-source-pipepiline-run.yaml')
TEST_PIPELINE_REPLACEMENTS_TEMPLATE = os.path.join(FILES, 'pipeline_template_replacement_test.yaml')
TEST_OCP_NAMESPACE = 'test-namespace'
TEST_OCP_URL = 'http://test/'
TEST_BUILD = "test-build-123"
TEST_ORCHESTRATOR_BUILD = "test-orchestrator-build-123"
TEST_CANCELLED_BUILD = "test-build-cancel-123"
TEST_BUILD_CONFIG = "path-master-?????"
TEST_SCRATCH_BUILD_NAME = "scratch-?????-??????????????"
TEST_ISOLATED_BUILD_NAME = "isolated-?????-??????????????"
TEST_GIT_URI = "git://hostname/path"
TEST_GIT_URI_SANITIZED = "githostnamepath"
TEST_GIT_URI_HUMAN_NAME = "path"
TEST_GIT_REF = "0123456789012345678901234567890123456789"
TEST_GIT_BRANCH = "master"
TEST_USER = "user"
TEST_COMPONENT = "component"
TEST_KOJI_NAME = "test-build"
TEST_VERSION = "1.0"
TEST_KOJI_RELEASE = "300"
TEST_TARGET = "target"
TEST_ARCH = "x86_64"
TEST_BUILD_POD = "build-test-build-123"
TEST_LABEL = "test-label"
TEST_LABEL_VALUE = "sample-value"
TEST_KOJI_TASK_ID = 12345
TEST_KOJI_BUILD_ID = 1234567
TEST_KOJI_BUILD_NVR = TEST_KOJI_NAME + "-" + TEST_VERSION + "-" + TEST_KOJI_RELEASE
TEST_DOCKERFILE_GIT = "https://github.com/TomasTomecek/docker-hello-world.git"
TEST_DOCKERFILE_SHA1 = "6e592f1420efcd331cd28b360a7e02f669caf540"
TEST_DOCKERFILE_INIT_SHA1 = "04523782eeb1e6c960b12f2f6fc887aa7cf76290"
TEST_DOCKERFILE_BRANCH = "error-build"
TEST_REMOTE_SOURCE_REQUEST_ID = 12345
TEST_REMOTE_SOURCE_ICM_URL = ('http://cachito.example.com/api/v1/requests/{}/content-manifest'
                              .format(TEST_REMOTE_SOURCE_REQUEST_ID))
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
                "env": []
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
