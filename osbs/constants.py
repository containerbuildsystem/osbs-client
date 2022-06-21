"""
Copyright (c) 2015-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import re
import sys

PY3 = sys.version_info[0] >= 3

DEFAULT_GIT_REF = "master"
DEFAULT_CONFIGURATION_FILE = "/etc/osbs.conf"
DEFAULT_CONF_BINARY_SECTION = "default_binary"
DEFAULT_CONF_SOURCE_SECTION = "default_source"
PRUN_TEMPLATE_USER_PARAMS = "user-params"
PRUN_TEMPLATE_REACTOR_CONFIG_WS = "ws-reactor-config-map"
PRUN_TEMPLATE_BUILD_DIR_WS = "ws-build-dir"
PRUN_TEMPLATE_CONTEXT_DIR_WS = "ws-context-dir"
GENERAL_CONFIGURATION_SECTION = "general"

# https://github.com/openshift/origin/blob/master/pkg/build/api/types.go
# type BuildStatus string
DEFAULT_NAMESPACE = "default"

# How to authenticate from within a pod
SERVICEACCOUNT_SECRET = "/var/run/secrets/kubernetes.io/serviceaccount"
SERVICEACCOUNT_TOKEN = "token"
SERVICEACCOUNT_CACRT = "ca.crt"

# number of digits used for unique image tags
RAND_DIGITS = 5

# Logging format used in Atomic Reactor
ATOMIC_REACTOR_LOGGING_FMT = \
    '%(asctime)s platform:%(arch)s - %(name)s - %(levelname)s - %(message)s'

# Numeric representation of user warnings loggers' level
# User warning level is logically between INFO (20) and WARNING (30) levels
USER_WARNING_LEVEL = 25
USER_WARNING_LEVEL_NAME = 'USER_WARNING'

ADDITIONAL_TAGS_FILE = 'additional-tags'
REPO_CONTAINER_CONFIG = 'container.yaml'
REPO_CONTAINER_CONFIG_POSSIBLE_TYPOS = {'container.yml'}
REPO_CONTENT_SETS_FILE = 'content_sets.yml'
REPO_CONTENT_SETS_FILE_POSSIBLE_TYPOS = {'content_sets.yaml',
                                         'content-sets.yml',
                                         'content-sets.yaml'}

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

# number of retries on openshift not found
OS_NOT_FOUND_MAX_RETRIES = 6

# number of seconds to wait, before retrying on openshift not found
OS_NOT_FOUND_MAX_WAIT = 1

ISOLATED_RELEASE_FORMAT = re.compile(r'^\d+\.\d+(\..+)?$')
RELEASE_LABEL_FORMAT = re.compile(r"""^\d+             # First character must be a digit
                                      ([._]?           # allow separators between groups
                                      [a-zA-Z0-9]+)*$  # last characters must be alphanumeric
                                   """, re.X)
VERSION_LABEL_FORBIDDEN_CHARS = ['-']

# number of retries for git clone operations
GIT_MAX_RETRIES = 3

# backoff factor for git clone operations - exponential backoff in seconds
GIT_BACKOFF_FACTOR = 60

# number of deepen operations to attempt if a requested commit is not
# in the shallow depth of the original clone
GIT_FETCH_RETRY = 9

USER_PARAMS_KIND_IMAGE_BUILDS = 'build_user_params'
USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS = 'source_containers_user_params'
