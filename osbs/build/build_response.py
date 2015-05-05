"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from osbs.constants import BUILD_FINISHED_STATES, BUILD_RUNNING_STATES, \
    BUILD_SUCCEEDED_STATES, BUILD_FAILED_STATES


class BuildResponse(object):
    """ class which wraps json from http response from OpenShift """

    def __init__(self, request):
        """
        :param request: http.Request
        """
        self.request = request
        self._json = None
        self._status = None
        self._build_id = None

    @property
    def json(self):
        if self._json is None:
            self._json = self.request.json()
        return self._json

    @property
    def status(self):
        if self._status is None:
            self._status = self.json['status'].lower()
        return self._status

    @property
    def build_id(self):
        if self._build_id is None:
            self._build_id = self.json['metadata']['name']
        return self._build_id

    def is_finished(self):
        return self.status in BUILD_FINISHED_STATES

    def is_failed(self):
        return self.status in BUILD_FAILED_STATES

    def is_succeeded(self):
        return self.status in BUILD_SUCCEEDED_STATES

    def is_running(self):
        return self.status in BUILD_RUNNING_STATES
