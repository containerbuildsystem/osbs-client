"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging

from osbs.utils import graceful_chain_get


logger = logging.getLogger(__name__)


class PodResponse(object):
    """
    Wrapper for JSON describing build pod
    """

    def __init__(self, pod):
        """
        :param request: http.Request
        """
        self._json = pod

    @property
    def json(self):
        return self._json

    def get_container_image_ids(self):
        """
        Find the image IDs the containers use.

        :return: dict, image tag to docker ID
        """

        statuses = graceful_chain_get(self.json, "status", "containerStatuses")
        if statuses is None:
            return {}

        def remove_prefix(image_id, prefix):
            if image_id.startswith(prefix):
                return image_id[len(prefix):]

            return image_id

        return dict([(status['image'], remove_prefix(status['imageID'],
                                                     'docker://'))
                     for status in statuses])
