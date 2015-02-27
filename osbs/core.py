from __future__ import print_function, unicode_literals, absolute_import
import copy

import time
import json
import datetime
import logging
from urlparse import urljoin
from .http import get_http_session


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
        while bl.status_code == 500:
            bl = self.con.get(redir_url, allow_redirects=False)
            time.sleep(3)
        buildlogs_url = self._build_k8s_url(
            "containerLogs/default/build-%s/custom-build?follow=%d" % (
                build_id, 1 if follow else 0))
        response = self.con.get(buildlogs_url, stream=True, headers={'Connection': 'close'})
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
        return self.con.get(url)


class OSBS(object):

    def __init__(self, openshift):
        self.os = openshift

    def create_and_start_plain_build(self, build):
        """
        create a build object from provided build in openshift,
        this will also start the build right away

        :param build:
        :return:
        """
        response = self.os.create_build(json.dumps(build.build_json))
        build.response = response
        return build

    def create_build_config(self, build):
        """

        :param build:
        :return:
        """
        build_config = copy.deepcopy(build.build_json)
        build_config['kind'] = "BuildConfig"
        response = self.os.create_build_config(json.dumps(build_config))
        build.response = response
        return build

    def start_build(self, build_config_id):
        """

        :param build:
        :return:
        """
        response = self.os.get_build_config(build_config_id)
        build_json = response.json()
        build_json["kind"] = "Build"
        response = self.os.create_build(json.dumps(build_json))
        return response
