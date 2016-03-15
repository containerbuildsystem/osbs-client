"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import sys

PY3 = sys.version_info[0] >= 3

BUILD_JSON_STORE = "/usr/share/osbs/"
DEFAULT_GIT_REF = "master"
DEFAULT_BUILD_IMAGE = "buildroot:latest"
DEFAULT_CONFIGURATION_FILE = "/etc/osbs.conf"
DEFAULT_CONFIGURATION_SECTION = "default"
GENERAL_CONFIGURATION_SECTION = "general"
POD_FINISHED_STATES = ["failed", "succeeded"]
POD_FAILED_STATES = ["failed"]
POD_SUCCEEDED_STATES = ["succeeded"]
POD_RUNNING_STATES = ["pending", "running"]
# https://github.com/GoogleCloudPlatform/kubernetes/blob/master/pkg/api/types.go
# type PodPhase string
# fixme: what about "unknown" state?
BUILD_CANCELLED_STATE = "cancelled"
BUILD_FINISHED_STATES = ["failed", "complete", "error", BUILD_CANCELLED_STATE]
BUILD_FAILED_STATES = ["failed", "error", "cancelled"]  # meaning no image produced
BUILD_SUCCEEDED_STATES = ["complete"]
BUILD_PENDING_STATES = ["pending", "new"]
BUILD_RUNNING_STATES = ["running"]

# Watch response types
WATCH_ADDED = 'added'
WATCH_DELETED = 'deleted'
WATCH_MODIFIED = 'modified'
WATCH_ERROR = 'error'

# https://github.com/openshift/origin/blob/master/pkg/build/api/types.go
# type BuildStatus string
DEFAULT_NAMESPACE = "default"

PROD_BUILD_TYPE = "prod"
PROD_WITHOUT_KOJI_BUILD_TYPE = "prod-without-koji"
PROD_WITH_SECRET_BUILD_TYPE = "prod-with-secret"
SIMPLE_BUILD_TYPE = "simple"

# How to authenticate from within a pod
SERVICEACCOUNT_SECRET = "/var/run/secrets/kubernetes.io/serviceaccount"
SERVICEACCOUNT_TOKEN = "token"
SERVICEACCOUNT_CACRT = "ca.crt"

# Where will secrets be mounted?
SECRETS_PATH = "/var/run/secrets/atomic-reactor"

# Backup/restore
BACKUP_RESOURCES = ('buildconfigs', 'imagestreams', 'builds',)

CLI_LIST_BUILDS_DEFAULT_COLS = ["name", "status", "image"]
