"""
Copyright (c) 2015, 2016, 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import re
import sys

PY3 = sys.version_info[0] >= 3

BUILD_JSON_STORE = "/usr/share/osbs/"
DEFAULT_GIT_REF = "master"
DEFAULT_CONFIGURATION_FILE = "/etc/osbs.conf"
DEFAULT_CONFIGURATION_SECTION = "default"
WORKER_OUTER_TEMPLATE = "worker.json"
ORCHESTRATOR_OUTER_TEMPLATE = "orchestrator.json"
ORCHESTRATOR_SOURCES_OUTER_TEMPLATE = "orchestrator_sources.json"
WORKER_INNER_TEMPLATE = "worker_inner:{arrangement_version}.json"
ORCHESTRATOR_INNER_TEMPLATE = "orchestrator_inner:{arrangement_version}.json"
ORCHESTRATOR_SOURCES_INNER_TEMPLATE = "orchestrator_sources_inner:{arrangement_version}.json"
DEFAULT_ARRANGEMENT_VERSION = 6  # this should be the highest-numbered version
REACTOR_CONFIG_ARRANGEMENT_VERSION = 6
WORKER_CUSTOMIZE_CONF = "worker_customize.json"
ORCHESTRATOR_CUSTOMIZE_CONF = "orchestrator_customize.json"
DEFAULT_OUTER_TEMPLATE = WORKER_OUTER_TEMPLATE
DEFAULT_SOURCES_OUTER_TEMPLATE = ORCHESTRATOR_SOURCES_OUTER_TEMPLATE
DEFAULT_INNER_TEMPLATE = "worker_inner:6.json"
DEFAULT_CUSTOMIZE_CONF = WORKER_CUSTOMIZE_CONF
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

# How to authenticate from within a pod
SERVICEACCOUNT_SECRET = "/var/run/secrets/kubernetes.io/serviceaccount"
SERVICEACCOUNT_TOKEN = "token"
SERVICEACCOUNT_CACRT = "ca.crt"

# Where will secrets be mounted?
SECRETS_PATH = "/var/run/secrets/atomic-reactor"

# Backup/restore
BACKUP_RESOURCES = ('buildconfigs', 'imagestreams',)

CLI_LIST_BUILDS_DEFAULT_COLS = ["name", "status", "image"]
CLI_WATCH_BUILDS_DEFAULT_COLS = ["changetype", "status", "created", "name"]

# number of digits used for unique image tags
RAND_DIGITS = 5

# Logging format used in Atomic Reactor
ATOMIC_REACTOR_LOGGING_FMT = \
    '%(asctime)s platform:%(arch)s - %(name)s - %(levelname)s - %(message)s'

REPO_CONFIG_FILE = '.osbs-repo-config'
ADDITIONAL_TAGS_FILE = 'additional-tags'
REPO_CONTAINER_CONFIG = 'container.yaml'

# number of retries for http requests
HTTP_MAX_RETRIES = 8

# how many seconds should request wait for in case non-critical error has occurred
HTTP_BACKOFF_FACTOR = 4

# Statuses which should trigger automatic retry
HTTP_RETRIES_STATUS_FORCELIST = [408, 500, 502, 503, 504]

# HTTP methods that we should retry on
HTTP_RETRIES_METHODS_WHITELIST = ['GET', 'PUT', 'POST', 'DELETE']

# requests timeout in seconds
HTTP_REQUEST_TIMEOUT = 600

# number of retries on openshift conflict
OS_CONFLICT_MAX_RETRIES = 8

# number of seconds to wait, before retrying on openshift conflict
OS_CONFLICT_WAIT = 5

BUILD_TYPE_ORCHESTRATOR = "orchestrator"
BUILD_TYPE_WORKER = "worker"

ISOLATED_RELEASE_FORMAT = re.compile(r'^\d+\.\d+(\..+)?$')
RELEASE_LABEL_FORMAT = re.compile(r"""^\d+             # First character must be a digit
                                      ([._]?           # allow separators between groups
                                      [a-zA-Z0-9]+)*$  # last characters must be alphanumeric
                                   """, re.X)

ANNOTATION_SOURCE_REPO = 'osbs/source_repo'
ANNOTATION_INSECURE_REPO = 'openshift.io/image.insecureRepository'

# optional key path for filtering existing build config results
FILTER_KEY = 'spec.source.git.uri'

# number of retries for git clone operations
GIT_MAX_RETRIES = 3

# backoff factor for git clone operations - exponential backoff in seconds
GIT_BACKOFF_FACTOR = 60

# number of deepen operations to attempt if a requested commit is not
# in the shallow depth of the original clone
GIT_FETCH_RETRY = 9

# completion deadlin in hours
WORKER_MAX_RUNTIME = 3
ORCHESTRATOR_MAX_RUNTIME = 4
