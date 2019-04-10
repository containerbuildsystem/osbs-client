"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

import logging
from osbs import set_logging
set_logging(name="osbs.tests", level=logging.DEBUG)
set_logging(name="osbs", level=logging.DEBUG)
