"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import six

from osbs.constants import BUILD_FINISHED_STATES

from tests.constants import TEST_BUILD, TEST_LABEL, TEST_LABEL_VALUE
from tests.fake_api import openshift


class TestOpenshift(object):
    def test_set_labels_on_build(self, openshift):
        l = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
        assert l.json() is not None

    def test_list_builds(self, openshift):
        l = openshift.list_builds()
        assert l is not None
        assert bool(l.json())  # is there at least something

    def test_get_oauth_token(self, openshift):
        token = openshift.get_oauth_token()
        assert token is not None

    def test_get_user(self, openshift):
        l = openshift.get_user()
        assert l.json() is not None

    def test_watch_build(self, openshift):
        response = openshift.wait_for_build_to_finish(TEST_BUILD)
        status_lower = response["status"]["phase"].lower()
        assert response["metadata"]["name"] == TEST_BUILD
        assert status_lower in BUILD_FINISHED_STATES
        assert isinstance(TEST_BUILD, six.text_type)
        assert isinstance(status_lower, six.text_type)

    def test_create_build(self, openshift):
        response = openshift.create_build({})
        assert response is not None
        assert response.json()["metadata"]["name"] == TEST_BUILD
        assert response.json()["status"]["phase"].lower() in BUILD_FINISHED_STATES
