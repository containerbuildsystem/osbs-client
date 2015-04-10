from __future__ import absolute_import, unicode_literals

import pytest
import logging
from .fake_api import openshift
from tests.constants import TEST_BUILD, TEST_LABEL, TEST_LABEL_VALUE


logger = logging.getLogger("osbs.tests")


def test_set_labels_on_build(openshift):
    l = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
    assert l.json() is not None


def test_get_oauth_token(openshift):
    token = openshift.get_oauth_token()
    assert token is not None


def test_list_builds(openshift):
    l = openshift.list_builds()
    assert l is not None
    assert bool(l.json())  # is there at least something
