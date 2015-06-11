"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import json
import logging

from osbs.utils import graceful_chain_get
from osbs.constants import BUILD_FINISHED_STATES, BUILD_RUNNING_STATES, \
    BUILD_SUCCEEDED_STATES, BUILD_FAILED_STATES, BUILD_PENDING_STATES


logger = logging.getLogger(__name__)


class BuildResponse(object):
    """ class which wraps json from http response from OpenShift """

    def __init__(self, request, build_json=None):
        """
        :param request: http.Request
        :param build_json: dict
        """
        self._json = build_json
        self.request = request
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

    def is_pending(self):
        return self.status in BUILD_PENDING_STATES

    def get_build_name(self):
        return graceful_chain_get(self.json, "metadata", "name")

    def get_image_tag(self):
        return graceful_chain_get(self.json, 'parameters', 'output', 'imageTag')

    def get_annotations_or_labels(self):
        r = graceful_chain_get(self.json, "metadata", "annotations")
        if r is None:
            r = graceful_chain_get(self.json, "metadata", "labels")
        return r

    def get_rpm_packages(self):
        return graceful_chain_get(self.get_annotations_or_labels(), "rpm-packages")

    def get_dockerfile(self):
        return graceful_chain_get(self.get_annotations_or_labels(), "dockerfile")

    def get_logs(self, decode_logs=True):
        """
        :param decode_logs: bool, docker by default output logs in simple json structure:
            { "stream": "line" }
            if this arg is set to True, it decodes logs to human readable form
        :return: str
        """
        logs = graceful_chain_get(self.get_annotations_or_labels(), "logs")
        if not logs:
            logger.error("no logs")
            return ""
        if decode_logs:
            output = []
            for line in logs.split("\n"):
                try:
                    decoded_line = json.loads(line)
                except ValueError:
                    continue
                output += [decoded_line.get("stream", "").strip()]
                error = decoded_line.get("error", "").strip()
                if error:
                    output += [error]
                error_detail = decoded_line.get("errorDetail", "").strip()
                if error_detail:
                    output += [error_detail]
            output += "\n"
            return "\n".join(output)
        else:
            return logs

    def get_commit_id(self):
        return graceful_chain_get(self.get_annotations_or_labels(), "commit_id")

    def get_repositories(self):
        repositories_json = graceful_chain_get(self.get_annotations_or_labels(), "repositories")
        if repositories_json:
            return json.loads(repositories_json)

    def get_tar_metadata(self):
        tar_md_json = graceful_chain_get(self.get_annotations_or_labels(), "tar_metadata")
        if tar_md_json:
            return json.loads(tar_md_json)

    def get_tar_metadata_size(self):
        return graceful_chain_get(self.get_tar_metadata(), "size")

    def get_tar_metadata_md5sum(self):
        return graceful_chain_get(self.get_tar_metadata(), "md5sum")

    def get_tar_metadata_sha256sum(self):
        return graceful_chain_get(self.get_tar_metadata(), "sha256sum")

    def get_tar_metadata_filename(self):
        return graceful_chain_get(self.get_tar_metadata(), "filename")
