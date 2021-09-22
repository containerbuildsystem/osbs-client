#!/usr/bin/env python
# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""
Copyright (c) 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import argparse
import copy
import logging
import os
import sys
import json
import re
import subprocess
import tempfile
from textwrap import dedent

from osbs import set_logging
from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.constants import DEFAULT_CONFIGURATION_FILE
from tests.constants import (TEST_BUILD, TEST_ORCHESTRATOR_BUILD, TEST_CANCELLED_BUILD)
from osbs.exceptions import OsbsException
from osbs.cli.capture import setup_json_capture


OS_VERSION = "3.9.41"
ORCH_BUILD_LOG = dedent("""2017-06-23 17:18:41,791 platform:- - atomic_reactor.foo - DEBUG - this is from the orchestrator build
2017-06-23 17:18:41,791 platform:x86_64 - atomic_reactor.foo - INFO - 2017-06-23 17:18:41,400 platform:- atomic_reactor.foo -  DEBUG - this is from a worker build
2017-06-23 05:04:51,334 platform:x86_64 - atomic_reactor.plugins.orchestrate_build - INFO - "ContainersPaused": 0,
2017-06-23 17:18:41,791 - I really like bacon
""")
BASE_BUILD_LOG = u"   líne 1   \n".encode('utf-8')
MOCK_CONFIG_MAP = {
    "apiVersion": "v1",
    "data": {
        "special.how": "\"very\"",
        "special.type": "{\"quark\": \"charm\"}",
        "config.yaml": "version:1",
        "config.yml": "version:2",
        "config.ymlll": "{\"version\":3}",
        "config.json": "{\"version\":4}"
    },
    "kind": "ConfigMap",
    "metadata": {
        "creationTimestamp": "2017-06-14T16:49:47Z",
        "name": "special-config",
        "namespace": "osbs-qa01",
        "resourceVersion": "117005745",
        "selfLink": "/api/v1/namespaces/osbs-qa01/configmaps/special-config",
        "uid": "797c8350-5121-11e7-9ce0-0efeb5d2209c"
    }
}

DEFAULT_DIR = "tests/mock_jsons"
DEFAULT_IMAGESTREAM_SERVER = "quay.io"
DEFAULT_IMAGESTREAM_FILE = "prometheus/prometheus"
DEFAULT_IMAGESTREAM_TAGS = ['latest', 'master', 'v2.13.0']


def canonize_data(build_data, build_name=TEST_BUILD, build_status=None):
    build_data["status"]["config"] = {
        "kind": "BuildConfig",
        "name": build_name,
        "namespace": "default",
    }
    build_data["metadata"]["name"] = build_name
    if build_status:
        build_data["status"]["phase"] = build_status
        try:
            del build_data["status"]["logSnippet"]
            del build_data["status"]["message"]
        except KeyError:
            pass
    return build_data


def find_or_make_dir(dir_name):
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)


class MockCreator(object):
    def __init__(self):
        parser = argparse.ArgumentParser(description="osbs test harness mock JSON creator")

        parser.add_argument("user", action='store',
                            help="name of user to use for Basic Authentication in OSBS")
        parser.add_argument("--config", action='store', metavar="PATH",
                            help="path to configuration file", default=DEFAULT_CONFIGURATION_FILE)
        parser.add_argument("--instance", "-i", action='store', metavar="SECTION_NAME",
                            help="section within config for requested instance",
                            default="stage")
        parser.add_argument("--password", action='store',
                            help="password to use for Basic Authentication in OSBS")
        parser.add_argument("--mock-dir", metavar="DIR", action="store", default=DEFAULT_DIR,
                            help="mock JSON responses are stored in DIR")
        parser.add_argument("--imagestream", metavar="IMAGESTREAM", action="store",
                            default=DEFAULT_IMAGESTREAM_FILE,
                            help="Image name for image stream import. Defaults to " +
                            DEFAULT_IMAGESTREAM_FILE)
        parser.add_argument("--image_server", metavar="IMAGESERVER", action="store",
                            default=DEFAULT_IMAGESTREAM_SERVER,
                            help="Server for image stream import. Defaults to " +
                            DEFAULT_IMAGESTREAM_SERVER)
        parser.add_argument("--image_tags", metavar="IMAGETAGS", action="store", nargs=3,
                            default=DEFAULT_IMAGESTREAM_TAGS,
                            help="Image stream tags as 3 space separated values.")
        parser.add_argument("--os-version", metavar="OS_VER", action="store", default=OS_VERSION,
                            help="OpenShift version of the mock JSONs")

        args = parser.parse_args()
        self.user = args.user
        self.password = args.password

        mock_path = args.mock_dir
        self.mock_dir = "/".join([mock_path, args.os_version])
        find_or_make_dir(self.mock_dir)

        self.capture_dir = tempfile.mkdtemp()

        args.git_url = "https://github.com/TomasTomecek/docker-hello-world.git"
        args.git_branch = "master"
        args.git_commit = "HEAD"

        os_conf = Configuration(conf_file=args.config, conf_section=args.instance, cli_args=args)
        build_conf = Configuration(conf_file=args.config, conf_section=args.instance,
                                   cli_args=args)

        set_logging(level=logging.INFO)

        self.osbs = OSBS(os_conf, build_conf)
        setup_json_capture(self.osbs, os_conf, self.capture_dir)

        self.imagestream_file = args.imagestream
        self.imagestream_server = args.image_server
        self.imagestream_tags = args.image_tags
        self.rh_pattern = re.template("redhat.com")
        self.ex_pattern = "(\S+\.)*redhat.com"  # noqa:W605

    def clean_data(self, out_data):
        if isinstance(out_data, dict):
            cleaned_data = {}
            for key, data in out_data.items():
                cleaned_data[key] = self.clean_data(data)
            return cleaned_data
        elif isinstance(out_data, list):
            cleaned_data = []
            for data in out_data:
                cleaned_data.append(self.clean_data(data))
            return cleaned_data
        elif isinstance(out_data, str):
            if re.search(self.rh_pattern, out_data):
                return re.sub(self.ex_pattern, "example.com", out_data)
            else:
                return out_data
        else:
            return out_data

    def comp_write(self, out_name, out_data):
        cleaned_data = self.clean_data(out_data)
        out_path = "/".join([self.mock_dir, out_name])
        with open(out_path, "w") as outf:
            try:
                json.dump(cleaned_data, outf, indent=4)
            except (ValueError, TypeError):
                outf.write(json.dumps(cleaned_data, indent=4))

    def create_mock_builds_list(self):
        kwargs = {}
        # get build list
        self.osbs.list_builds(**kwargs)
        # find 'get-namespaces_osbs-stage_builds_-000.json' file and parse it into
        # 'builds_list.json', 'builds_list_empty.json', 'builds_list_one.json'
        all_builds = "get-namespaces_osbs-stage_builds_-000.json"
        all_builds_path = "/".join([self.capture_dir, all_builds])
        with open(all_builds_path, "r") as infile:
            builds_data = json.load(infile)
            builds_items = copy.copy(builds_data["items"])
            builds_data["items"] = []
            self.comp_write("builds_list_empty.json", builds_data)
            if not builds_items:
                return
            builds_data["items"].append(builds_items[0])
            self.comp_write("builds_list_one.json", builds_data)
            if len(builds_items) < 2:
                return
            builds_data["items"].append(builds_items[1])
            self.comp_write("builds_list.json", builds_data)
        os.remove(all_builds_path)

    def create_pods_list(self, build_id):
        self.osbs.get_pod_for_build(build_id)
        pods_pre = "get-namespaces_osbs-stage_pods_?labelSelector=openshift.io%2Fbuild.name%3D"
        for i in range(0, 4):
            try:
                pods_fname = pods_pre + build_id + "-00{}.json".format(i)
                pods_inpath = "/".join([self.capture_dir, pods_fname])
                os.stat(pods_inpath)
                break
            except OSError:
                continue

        with open(pods_inpath, "r") as infile:
            pods_data = json.load(infile)
            image = "buildroot:latest"
            pods_items = pods_data["items"] or []
            for pod in pods_items:
                pod_containers = pod["status"]["containerStatuses"] or []
                for container in pod_containers:
                    container["imageID"] = "docker-pullable://" + image
                    container["image"] = image
            self.comp_write("pods.json", pods_data)
        os.remove(pods_inpath)

    def create_mock_get_user(self):
        self.osbs.get_user()
        user_list = "get-users_~_-000.json"
        user_list_path = "/".join([self.capture_dir, user_list])
        with open(user_list_path, "r") as infile:
            user_data = json.load(infile)
            user_data["groups"] = []
            user_data["identities"] = None
            if "fullName" in user_data:
                del user_data["fullName"]
            user_data["metadata"]["name"] = "test"
            user_data["metadata"]["selfLink"] = "/apis/user.openshift.io/v1/users/test"
            self.comp_write("get_user.json", user_data)
        os.remove(user_list_path)

    def create_a_mock_build(self, func, build_name, out_tag, build_args):
        try:
            build = func(**build_args)
            build_id = build.get_build_name()
            build = self.osbs.wait_for_build_to_get_scheduled(build_id)
            self.osbs.watch_builds()
            build = self.osbs.wait_for_build_to_finish(build_id)
            self.osbs.get_build_logs(build_id)
        except (subprocess.CalledProcessError, OsbsException):
            pass

        watch_data = canonize_data(copy.deepcopy(build.json), build_name, "Complete")
        watch_obj = {
            "object": watch_data,
            "type": "MODIFIED"
        }
        out_name = "watch_build_test-" + out_tag + "build-123.json"
        self.comp_write(out_name, watch_obj)

        build_fname = "get-namespaces_osbs-stage_builds_" + build_id + "_-000.json"
        build_path = "/".join([self.capture_dir, build_fname])
        with open(build_path, "r") as infile:
            build_data = canonize_data(json.load(infile), build_name, "Complete")
            out_name = "build_test-" + out_tag + "build-123.json"
            self.comp_write(out_name, build_data)
        os.remove(build_path)
        return out_name

    def create_mock_build(self):
        build_kwargs = {
            'git_uri': self.osbs.build_conf.get_git_uri(),
            'git_ref': self.osbs.build_conf.get_git_ref(),
            'git_branch': self.osbs.build_conf.get_git_branch(),
            'user': self.osbs.build_conf.get_user(),
            'release': TEST_BUILD,
            'platform': "x86_64",
            'scratch': True,
        }

        build_name = self.create_a_mock_build(self.osbs.create_worker_build, TEST_BUILD, "",
                                              build_kwargs)
        build_path = "/".join([self.mock_dir, build_name])
        if os.stat(build_path):
            with open(build_path, "r") as infile:
                build_data = json.load(infile)
                build_data["kind"] = "BuildConfig"
                self.comp_write("created_build_config_test-build-config-123.json", build_data)

        del build_kwargs['platform']

        self.create_a_mock_build(self.osbs.create_orchestrator_build, TEST_ORCHESTRATOR_BUILD,
                                 "orchestrator-", build_kwargs)

    def create_mock_build_other(self):
        build_kwargs = {
            'git_uri': self.osbs.build_conf.get_git_uri(),
            'git_ref': self.osbs.build_conf.get_git_ref(),
            'git_branch': self.osbs.build_conf.get_git_branch(),
            'user': self.osbs.build_conf.get_user(),
            'release': TEST_BUILD,
            'platform': "x86_64",
            'scratch': True,
        }
        build_id = ""
        try:
            build = self.osbs.create_worker_build(**build_kwargs)
            build_id = build.get_build_name()
            self.osbs.wait_for_build_to_get_scheduled(build_id)
            self.create_pods_list(build_id)
            self.osbs.cancel_build(build_id)
            self.osbs.wait_for_build_to_finish(build_id)
            self.osbs.get_build_logs(build_id)
        except OsbsException:
            self.create_pods_list(build_id)

        instant_fname = "post-namespaces_osbs-stage_builds_-000.json"
        instant_path = "/".join([self.capture_dir, instant_fname])
        with open(instant_path, "r") as infile:
            instant_data = canonize_data(json.load(infile))
            self.comp_write("instantiated_test-build-config-123.json", instant_data)
        os.remove(instant_path)

        cancel_args = [
            {"suffix": "_-000-001.json", "version": "get", "phase": None},
            {"suffix": "_-000-000.json", "version": "put", "phase": "Cancelled"},
        ]
        for data in cancel_args:
            build_fname = "get-watch_namespaces_osbs-stage_builds_" + build_id + data["suffix"]
            build_path = "/".join([self.capture_dir, build_fname])
            with open(build_path, "r") as infile:
                cancel_obj = json.load(infile)
                cancel_data = canonize_data(copy.deepcopy(cancel_obj["object"]),
                                            TEST_CANCELLED_BUILD, data["phase"])
                self.comp_write("build_test-build-cancel-123_" + data["version"] + ".json",
                                cancel_data)
            os.remove(build_path)

    def create_mock_static_files(self):
        # these aren't JSON, so just write them out
        out_path = "/".join([self.mock_dir, "build_test-orchestrator-build-123_logs.txt"])
        with open(out_path, "w") as outf:
            outf.write(ORCH_BUILD_LOG)
        out_path = "/".join([self.mock_dir, "build_test-build-123_logs.txt"])
        with open(out_path, "wb") as outf:
            outf.write(BASE_BUILD_LOG)

        self.comp_write("create_config_map.json", MOCK_CONFIG_MAP)


def main():
    mock_builder = MockCreator()

    mock_builder.create_mock_builds_list()
    mock_builder.create_mock_get_user()
    mock_builder.create_mock_build_other()
    mock_builder.create_mock_build()
    mock_builder.create_mock_static_files()
    return 0


if __name__ == '__main__':
    sys.exit(main())
