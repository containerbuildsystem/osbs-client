"""
Copyright (c) 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

import json


class JsonMatcher(object):
    """Match python object to json string"""

    def __init__(self, expected):
        self.expected = expected

    def __eq__(self, json_str):
        # Assert to provide a more meaningful error
        assert self.expected == json.loads(json_str)
        return self.expected == json.loads(json_str)

    __hash__ = None     # py2 compatibility
