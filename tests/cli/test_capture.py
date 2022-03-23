"""
Copyright (c) 2015, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

import json
import os
import responses
import yaml
from tempfile import NamedTemporaryFile
from textwrap import dedent

from osbs.cli.capture import setup_json_capture
from osbs.tekton import PipelineRun
from osbs.conf import Configuration
from osbs.api import OSBS
from tests.constants import TEST_PIPELINE_RUN_TEMPLATE, TEST_OCP_NAMESPACE

PIPELINE_RUN_NAME = 'source-container-x-x-default'
OPENSHIFT_URL = 'https://openshift.testing'
PIPELINE_RUN_URL = f'{OPENSHIFT_URL}/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}' # noqa E501
PIPELINE_RUN_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}' # noqa E501
PIPELINE_WATCH_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/watch/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}/' # noqa E501
TASK_RUN_NAME = 'test-task-run-1'
POD_NAME = 'test-pod'
PIPELINE_RUN_JSON = {"metadata": {"name": "name"},
                     "status": {"conditions": [{"reason": "Running", "status": "Unknown"}]}}
PIPELINE_RUN_WATCH_JSON = {"type": "ADDED", "object": PIPELINE_RUN_JSON}

with open(TEST_PIPELINE_RUN_TEMPLATE) as f:
    yaml_data = f.read()
PIPELINE_RUN_DATA = yaml.safe_load(yaml_data)


def osbs_for_capture(tmpdir):
    kwargs = {'openshift_url': OPENSHIFT_URL, 'namespace': TEST_OCP_NAMESPACE}

    with NamedTemporaryFile(mode="wt") as fp:
        config = dedent("""\
            [default]
            openshift_url = {openshift_url}
            use_auth = false
            namespace = {namespace}
            """)

        fp.write(config.format(**kwargs))
        fp.flush()
        dummy_config = Configuration(fp.name, conf_section='default')
        osbs = OSBS(dummy_config)

    setup_json_capture(osbs, osbs.os_conf, str(tmpdir))
    return osbs


def pipeline_run(osbs_for_capture):
    return PipelineRun(os=osbs_for_capture.os, pipeline_run_name=PIPELINE_RUN_NAME,
                       pipeline_run_data=PIPELINE_RUN_DATA)


@responses.activate
def test_json_capture_no_watch(tmpdir):
    osbs = osbs_for_capture(tmpdir)
    prun = pipeline_run(osbs)

    for visit in ["000", "001"]:
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)

        prun.get_info()
        filename = "get-tekton.dev_v1beta1_namespaces_{n}_pipelineruns_{p}-{v}.json"

        path = os.path.join(str(tmpdir), filename.format(n=TEST_OCP_NAMESPACE,
                                                         v=visit, p=PIPELINE_RUN_NAME))
        assert os.access(path, os.R_OK)
        with open(path) as fp:
            obj = json.load(fp)

        assert obj


@responses.activate
def test_json_capture_watch(tmpdir):
    osbs = osbs_for_capture(tmpdir)
    prun = pipeline_run(osbs)

    responses.add(
        responses.GET,
        PIPELINE_WATCH_URL,
        json=PIPELINE_RUN_WATCH_JSON,
    )
    responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
    prun.wait_for_start()

    watch_filename = "get-tekton.dev_v1beta1_watch_namespaces_{n}_pipelineruns_{p}_-000-000.json"
    get_filename = "get-tekton.dev_v1beta1_namespaces_{n}_pipelineruns_{p}-000.json"

    watch_path = os.path.join(str(tmpdir), watch_filename.format(n=TEST_OCP_NAMESPACE,
                                                                 p=PIPELINE_RUN_NAME))
    get_path = os.path.join(str(tmpdir), get_filename.format(n=TEST_OCP_NAMESPACE,
                                                             p=PIPELINE_RUN_NAME))
    for path in (watch_path, get_path):
        assert os.access(path, os.R_OK)
        with open(path) as fp:
            obj = json.load(fp)

        assert obj
