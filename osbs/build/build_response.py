"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import json
import logging

from osbs.utils import graceful_chain_get, get_time_from_rfc3339
from osbs.constants import BUILD_FINISHED_STATES, BUILD_RUNNING_STATES, \
    BUILD_SUCCEEDED_STATES, BUILD_FAILED_STATES, BUILD_PENDING_STATES, \
    BUILD_CANCELLED_STATE
from osbs.exceptions import OsbsException


logger = logging.getLogger(__name__)


class BuildResponse(object):
    """ class which wraps json from http response from OpenShift """

    def __init__(self, build_json, osbs=None):
        """
        :param build_json: dict from JSON of OpenShift Build object
        :param osbs: object of the creater's OSBS instance
        """
        self.json = build_json
        self._status = None
        self._cancelled = None
        self.osbs = osbs

    @property
    def status(self):
        if self._status is None:
            self._status = self.json['status']['phase'].lower()
        return self._status

    @status.setter
    def status(self, value):
        cap_value = value.capitalize()
        logger.info("changing status from %s to %s", self.status, cap_value)
        self.json['status']['phase'] = cap_value
        self._status = value

    @property
    def cancelled(self):
        if self._cancelled is None:
            self._cancelled = self.json['status'].get('cancelled')
        return self._cancelled

    @cancelled.setter
    def cancelled(self, value):
        self.json['status']['cancelled'] = value
        self._cancelled = value

    def is_finished(self):
        return self.status in BUILD_FINISHED_STATES

    def is_failed(self):
        return self.status in BUILD_FAILED_STATES

    def is_cancelled(self):
        return self.status == BUILD_CANCELLED_STATE

    def is_succeeded(self):
        return self.status in BUILD_SUCCEEDED_STATES

    def is_running(self):
        return self.status in BUILD_RUNNING_STATES

    def is_pending(self):
        return self.status in BUILD_PENDING_STATES

    def is_in_progress(self):
        return self.status not in BUILD_FINISHED_STATES

    def get_build_name(self):
        return graceful_chain_get(self.json, "metadata", "name")

    def get_image_tag(self):
        return graceful_chain_get(self.json, "spec", "output", "to", "name")

    def get_time_created(self):
        return graceful_chain_get(self.json, "metadata", "creationTimestamp")

    def get_time_created_in_seconds(self):
        return get_time_from_rfc3339(self.get_time_created())

    def get_annotations(self):
        return graceful_chain_get(self.json, "metadata", "annotations")

    def get_labels(self):
        return graceful_chain_get(self.json, "metadata", "labels")

    def get_annotations_or_labels(self):
        r = self.get_annotations()
        if r is None:
            r = self.get_labels()
        return r

    def get_dockerfile(self):
        return graceful_chain_get(self.get_annotations_or_labels(), "dockerfile")

    def get_error_message(self):
        """
        Return an error message based on atomic-reactor's metadata
        """
        error_reason = self.get_error_reason()
        if error_reason:
            error_message = error_reason.get('pod') or None
            if error_message:
                return "Error in pod: %s" % error_message
            plugin = error_reason.get('plugin')[0] or None
            error_message = error_reason.get('plugin')[1] or None
            if error_message:
                # Plugin has non-empty error description
                return "Error in plugin %s: %s" % (plugin, error_message)
            else:
                return "Error in plugin %s" % plugin

    def get_error_reason(self):
        str_metadata = graceful_chain_get(self.get_annotations(),
                                          "plugins-metadata")
        if str_metadata:
            try:
                metadata_dict = json.loads(str_metadata)
                plugin, error_message = list(metadata_dict['errors'].items())[0]
                return {'plugin': [plugin, error_message]}
            except (ValueError, KeyError, IndexError):
                pass

        if not self.osbs:
            return {'pod': 'OSBS unavailable; Pod related errors cannot be retrieved'}

        try:
            pod = self.osbs.get_pod_for_build(self.get_build_name())
            return {'pod': pod.get_failure_reason()}
        except OsbsException:
            return None

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

    def get_image_id(self):
        return graceful_chain_get(self.get_annotations_or_labels(), "image-id")

    def get_base_image_id(self):
        return graceful_chain_get(self.get_annotations_or_labels(),
                                  "base-image-id")

    def get_base_image_name(self):
        return graceful_chain_get(self.get_annotations_or_labels(),
                                  "base-image-name")

    def get_digests(self):
        digests_json = graceful_chain_get(self.get_annotations_or_labels(), "digests")
        if digests_json:
            return json.loads(digests_json)

    def get_koji_build_id(self):
        return graceful_chain_get(self.get_labels(), "koji-build-id")
