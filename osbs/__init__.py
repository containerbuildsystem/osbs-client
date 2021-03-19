"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging

from osbs.constants import USER_WARNING_LEVEL, USER_WARNING_LEVEL_NAME
from osbs.utils import user_warning_log_handler
from osbs.version import __version__  # noqa


def set_logging(name="osbs", level=logging.DEBUG):
    # add new level to loggers
    logging.addLevelName(USER_WARNING_LEVEL, USER_WARNING_LEVEL_NAME)
    logging.Logger.user_warning = user_warning_log_handler

    # create logger
    logger = logging.getLogger(name)
    logger.handlers = []
    logger.setLevel(level)

    # create console handler and set level to debug
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    # create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # add formatter to ch
    ch.setFormatter(formatter)

    # add ch to logger
    logger.addHandler(ch)


set_logging(level=logging.WARNING)  # override this however you want
