from __future__ import print_function, unicode_literals, absolute_import
import httplib
import logging

import time
from urlparse import urljoin
from .http import get_http_session


logger = logging.getLogger(__name__)


class OpenshiftException(Exception):
    """ OpenShift didn't respond with OK (200) status """

    def __init__(self, status_code, *args, **kwargs):
        super(OpenshiftException, self).__init__(*args, **kwargs)
        self.status_code = status_code


def check_response(response):
    if response.status_code != httplib.OK:
        raise OpenshiftException(response.status_code)


# TODO: error handling: create function which handles errors in response object
class Openshift(object):

    def __init__(self, openshift_url, kubelet_base, verbose=False):
        self.os_base = openshift_url
        self.kubelet_base = kubelet_base
        self.verbose = verbose
        self.con = get_http_session(verbose=self.verbose)

    def _build_url(self, url):
        return urljoin(self.os_base, url)

    def _build_k8s_url(self, url):
        return urljoin(self.kubelet_base, url)

    def create_build(self, build_json):
        """
        :return:
        """
        url = self._build_url("builds/")
        return self.con.post(url, data=build_json,
                             headers={"Content-Type": "application/json"})

    def get_build_config(self, build_config_id):
        url = self._build_url("buildConfigs/%s/" % build_config_id)
        response = self.con.get(url)
        build_config = response.json()
        return build_config

    def create_build_config(self, build_config_json):
        """
        :return:
        """
        url = self._build_url("buildConfigs/")
        return self.con.post(url, data=build_config_json,
                             headers={"Content-Type": "application/json"})

    def start_build(self, build_config_id):
        """
        :return:
        """
        build_config = self.get_build_config(build_config_id)
        assert build_config["Kind"] == "BuildConfig"
        build_config["Kind"] = "Build"
        return self.create_build(build_config)

    def logs(self, build_id, follow=False):
        """

        :param follow:
        :return:
        """
        redir_url = self._build_url("redirect/buildLogs/%s" % build_id)
        bl = self.con.get(redir_url, allow_redirects=False)
        attempts = 10
        while bl.status_code not in (httplib.OK, httplib.TEMPORARY_REDIRECT, httplib.MOVED_PERMANENTLY):
            bl = self.con.get(redir_url, allow_redirects=False)
            time.sleep(5)  # 50 seconds got to be enough
            if attempts <= 0:
                break
            attempts -= 1
        buildlogs_url = self._build_k8s_url(
            "containerLogs/default/build-%s/custom-build?follow=%d" % (
                build_id, 1 if follow else 0))
        time.sleep(15)  # container ***STILL*** may not be ready
        response = self.con.get(buildlogs_url, stream=follow, headers={'Connection': 'close'})
        return response.iter_lines()

    def list_builds(self):
        """

        :return:
        """
        url = self._build_url("builds/")
        return self.con.get(url)

    def get_build(self, build_id):
        """

        :return:
        """
        url = self._build_url("builds/%s/" % build_id)
        response = self.con.get(url)
        check_response(response)
        return response

