"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging

from osbs.utils import graceful_chain_get


logger = logging.getLogger(__name__)


class ConfigMapResponse(object):
    """
    Wrapper for JSON describing a ConfigMap
    """

    def __init__(self, config_map):
        """
        :param config_map: dict, data to be stored in the ConfigMap
        """
        self._json = config_map

    @property
    def json(self):
        return self._json

    def get_data(self):
        """
        Find the data stored in the config_map

        :return: dict, the json data that was passed into the ConfigMap on creation
        """
        data = graceful_chain_get(self.json, "data")
        if data is None:
            return {}

        return data
