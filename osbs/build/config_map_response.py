"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import json
import yaml

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

    def is_yaml(self, name):
        if name.rsplit('.', 1)[-1] in ('yaml', 'yml'):
            return True
        return False

    def get_data(self):
        """
        Find the data stored in the config_map

        :return: dict, the json of the data data that was passed into the ConfigMap on creation
        """
        data = graceful_chain_get(self.json, "data")
        if data is None:
            return {}

        data_dict = {}
        for key in data:
            if self.is_yaml(key):
                data_dict[key] = yaml.load(data[key])
            else:
                data_dict[key] = json.loads(data[key])

        return data_dict

    def get_data_by_key(self, name):
        """
        Find the object stored by a JSON string at key 'name'

        :return: str or dict, the json of the str or dict stored in the ConfigMap at that location
        """
        data = graceful_chain_get(self.json, "data")

        if data is None or name not in data:
            return {}

        if self.is_yaml(name):
            return yaml.load(data[name]) or {}
        return json.loads(data[name])
