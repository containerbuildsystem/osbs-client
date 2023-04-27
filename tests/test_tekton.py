"""
Copyright (c) 2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import json
import re
import time
import responses
import pytest
import yaml
from copy import deepcopy
from flexmock import flexmock

from osbs.tekton import (Openshift, PipelineRun, TaskRun, Pod, API_VERSION, WAIT_RETRY_SECS,
                         WAIT_RETRY, get_sorted_task_runs)
from osbs.exceptions import OsbsException
from tests.constants import TEST_PIPELINE_RUN_TEMPLATE, TEST_OCP_NAMESPACE

PIPELINE_NAME = 'source-container-0-1'
PIPELINE_RUN_NAME = 'source-default'
TASK_RUN_NAME = 'test-task-run-1'
TASK_RUN_NAME2 = 'test-task-run-2'
TASK_RUN_NAME3 = 'test-task-run-3'
PIPELINE_RUN_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}' # noqa E501
PIPELINE_WATCH_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/watch/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}/' # noqa E501
TASK_RUN_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/taskruns/{TASK_RUN_NAME}' # noqa E501
TASK_RUN_URL2 = f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/taskruns/{TASK_RUN_NAME2}' # noqa E501
TASK_RUN_WATCH_URL = f"https://openshift.testing/apis/tekton.dev/v1beta1/watch/namespaces/{TEST_OCP_NAMESPACE}/taskruns/{TASK_RUN_NAME}/" # noqa E501
TASK_RUN_WATCH_URL2 = f"https://openshift.testing/apis/tekton.dev/v1beta1/watch/namespaces/{TEST_OCP_NAMESPACE}/taskruns/{TASK_RUN_NAME2}/" # noqa E501

POD_NAME = 'test-pod'
POD_NAME2 = 'test-pod2'
POD_NAME3 = 'test-pod3'
CONTAINERS = ['step-hello', 'step-wait', 'step-bye']
CONTAINERS2 = ['step2-hello', 'step2-wait', 'step2-bye']
CONTAINERS3 = ['step3-hello', 'step3-wait', 'step3-bye']
POD_URL = f'https://openshift.testing/api/v1/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME}'
POD_URL2 = f'https://openshift.testing/api/v1/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME2}'
POD_URL3 = f'https://openshift.testing/api/v1/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME3}'
POD_WATCH_URL = f"https://openshift.testing/api/v1/watch/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME}/" # noqa E501
POD_WATCH_URL2 = f"https://openshift.testing/api/v1/watch/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME2}/" # noqa E501

EXPECTED_LOGS = {
    'step-hello': 'Hello World\n',
    'step-wait': '',
    'step-bye': 'Bye World\n',
}
EXPECTED_LOGS2 = {
    'step2-hello': '2Hello World\n',
    'step2-wait': '2',
    'step2-bye': '2Bye World\n'
}
EXPECTED_LOGS3 = {
    'step3-hello': '3Hello World\n',
    'step3-wait': '3',
    'step3-bye': '3Bye World\n'
}

PIPELINE_RUN_JSON = {
    "apiVersion": "tekton.dev/v1beta1",
    "kind": "PipelineRun",
    "metadata": {},
    "status": {
        "conditions": [
            {
                "reason": "Running",
                "status": "Unknown",
            }
        ],
        "pipelineSpec": {
            "tasks": [
                {"name": "short-sleep", "taskRef": {"kind": "Task", "name": "short-sleep"}},
                {"name": "short2-sleep", "taskRef": {"kind": "Task", "name": "short2-sleep"}}
            ]
        },
        "taskRuns": {
            TASK_RUN_NAME: {
                "pipelineTaskName": "short-sleep",
                "status": {
                    "conditions": [
                        {
                            "reason": "Running",
                            "status": "Unknown",
                        }
                    ],
                    "podName": POD_NAME,
                    "steps": [
                        {
                            "container": "step-hello",
                        },
                        {
                            "container": "step-wait",
                        },
                        {
                            "container": "step-bye",
                        },
                    ],
                    "startTime": "2022-04-26T15:58:42Z"
                },
            },
            TASK_RUN_NAME2: {
                "pipelineTaskName": "short2-sleep",
                "status": {
                    "conditions": [
                        {
                            "reason": "Running",
                            "status": "Unknown",
                        }
                    ],
                    "podName": POD_NAME2,
                    "steps": [
                        {
                            "container": "step2-hello",
                        },
                        {
                            "container": "step2-wait",
                        },
                        {
                            "container": "step2-bye",
                        },
                    ],
                    "startTime": "2022-04-26T14:58:42Z"
                },
            }
        },
    },
}

TASK_RUN_JSON = {
    "apiVersion": "tekton.dev/v1beta1",
    "kind": "TaskRun",
    "status": {
        "conditions": [
            {
                "reason": "Running",
                "status": "Unknown",
            }
        ],
        "podName": POD_NAME,
        "steps": [
            {
                "container": "step-hello",
            },
            {
                "container": "step-wait",
            },
            {
                "container": "step-bye",
            },
        ],
    },
}
TASK_RUN_JSON2 = {
    "apiVersion": "tekton.dev/v1beta1",
    "kind": "TaskRun",
    "status": {
        "conditions": [
            {
                "reason": "Running",
                "status": "Unknown",
            }
        ],
        "podName": POD_NAME2,
        "steps": [
            {
                "container": "step2-hello",
            },
            {
                "container": "step2-wait",
            },
            {
                "container": "step2-bye",
            },
        ],
    },
}
TASK_RUN_JSON3 = {
    "apiVersion": "tekton.dev/v1beta1",
    "kind": "TaskRun",
    "status": {
        "conditions": [
            {
                "reason": "Running",
                "status": "Unknown",
            }
        ],
        "podName": POD_NAME3,
        "steps": [
            {
                "container": "step3-hello",
            },
            {
                "container": "step3-wait",
            },
            {
                "container": "step3-bye",
            },
        ],
    },
}


POD_JSON = {
    "kind": "Pod",
    "apiVersion": "v1",
    "metadata": {
        "name": POD_NAME,
        "namespace": TEST_OCP_NAMESPACE,
    },
    "status": {
        "phase": "Running",
    },
}
POD_JSON2 = {
    "kind": "Pod",
    "apiVersion": "v1",
    "metadata": {
        "name": POD_NAME2,
        "namespace": TEST_OCP_NAMESPACE,
    },
    "status": {
        "phase": "Running",
    },
}
POD_JSON3 = {
    "kind": "Pod",
    "apiVersion": "v1",
    "metadata": {
        "name": POD_NAME3,
        "namespace": TEST_OCP_NAMESPACE,
    },
    "status": {
        "phase": "Running",
    },
}

PIPELINE_RUN_WATCH_JSON = {
    "type": "ADDED",
    "object": PIPELINE_RUN_JSON
}

TASK_RUN_WATCH_JSON = {
    "type": "ADDED",
    "object": TASK_RUN_JSON
}
TASK_RUN_WATCH_JSON2 = {
    "type": "ADDED",
    "object": TASK_RUN_JSON2
}

POD_WATCH_JSON = {
    "type": "ADDED",
    "object": POD_JSON
}
POD_WATCH_JSON2 = {
    "type": "ADDED",
    "object": POD_JSON2
}

with open(TEST_PIPELINE_RUN_TEMPLATE) as f:
    yaml_data = f.read()
PIPELINE_RUN_DATA = yaml.safe_load(yaml_data)

PIPELINE_MINIMAL_DATA = {
    "apiVersion": API_VERSION,
    "kind": "PipelineRun",
    "metadata": {"name": PIPELINE_RUN_NAME},
    "spec": {},
}


@pytest.fixture(scope='module')
def openshift():
    return Openshift(openshift_api_url="https://openshift.testing/",
                     openshift_oauth_url="https://openshift.testing/oauth/authorize",
                     namespace=TEST_OCP_NAMESPACE)


@pytest.fixture(scope='module')
def pipeline_run(openshift):
    return PipelineRun(os=openshift, pipeline_run_name=PIPELINE_RUN_NAME,
                       pipeline_run_data=PIPELINE_RUN_DATA)


@pytest.fixture(scope='module')
def task_run(openshift):
    return TaskRun(os=openshift, task_run_name=TASK_RUN_NAME)


@pytest.fixture(scope='module')
def pod(openshift):
    return Pod(os=openshift, pod_name=POD_NAME, containers=CONTAINERS)


class TestPod():

    @responses.activate
    def test_get_info(self, pod):
        responses.add(responses.GET, POD_URL, json=POD_JSON)
        resp = pod.get_info()

        assert len(responses.calls) == 1
        assert resp == POD_JSON

    @responses.activate
    def test_wait_for_start(self, pod):
        responses.add(
            responses.GET,
            POD_WATCH_URL,
            json=POD_WATCH_JSON,
        )
        responses.add(responses.GET, POD_URL,
                      json=POD_JSON)
        resp = pod.wait_for_start()

        assert len(responses.calls) == 2
        assert resp == POD_JSON

    @responses.activate
    @pytest.mark.parametrize(('get_info_json', 'calls'), [
        (POD_JSON, 2),
        ({}, 1),
    ])
    def test_wait_for_start_removed(self, pod, get_info_json, calls):
        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            yield {}
            yield {}

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)
        responses.add(responses.GET, POD_URL, json=get_info_json)
        resp = pod.wait_for_start()

        assert len(responses.calls) == calls
        assert resp is None

    @responses.activate
    def test_get_logs_no_containers_specified(self, openshift):
        url = f"{POD_URL}/log"
        responses.add(responses.GET, url, "log message")
        pod = Pod(os=openshift, pod_name=POD_NAME)
        logs = pod.get_logs()

        assert len(responses.calls) == 1
        assert logs == "log message"

    @responses.activate
    def test_get_logs(self, pod):
        for container in CONTAINERS:
            url = f"{POD_URL}/log?container={container}"
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container])
        logs = pod.get_logs()

        assert len(responses.calls) == 3
        assert logs == EXPECTED_LOGS

    @responses.activate
    def test_get_logs_stream(self, pod):
        responses.add(
            responses.GET,
            POD_WATCH_URL,
            json=POD_WATCH_JSON,
        )
        responses.add(responses.GET, POD_URL, json=POD_JSON)
        for container in CONTAINERS:
            url = f"{POD_URL}/log?follow=True&container={container}"
            responses.add(
                responses.GET,
                url,
                body=EXPECTED_LOGS[container],
                match=[responses.matchers.request_kwargs_matcher({"stream": True})],
            )

        logs = [line for line in pod.get_logs(wait=True, follow=True)]

        assert len(responses.calls) == 5
        assert logs == ['Hello World', 'Bye World']

    @responses.activate
    def test_get_logs_stream_removed(self, pod):
        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            yield {}

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)
        responses.add(responses.GET, POD_URL, json={})

        logs = [line for line in pod.get_logs(wait=True, follow=True)]

        assert len(responses.calls) == 2
        assert logs == []


class TestTaskRun():

    @responses.activate
    def test_get_info(self, task_run):
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        resp = task_run.get_info()

        assert len(responses.calls) == 1
        assert resp == TASK_RUN_JSON

    @responses.activate
    def test_wait_for_start(self, task_run):
        responses.add(
            responses.GET,
            TASK_RUN_WATCH_URL,
            json=TASK_RUN_WATCH_JSON,
        )
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        resp = task_run.wait_for_start()

        assert len(responses.calls) == 2
        assert resp == TASK_RUN_JSON

    @responses.activate
    @pytest.mark.parametrize(('get_info_json', 'calls'), [
        (TASK_RUN_JSON, 2),
        ({}, 1),
    ])
    def test_wait_for_start_removed(self, task_run, get_info_json, calls):
        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            yield {}
            yield {}

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)
        responses.add(responses.GET, TASK_RUN_URL, json=get_info_json)
        resp = task_run.wait_for_start()

        assert len(responses.calls) == calls
        assert resp is None

    @responses.activate
    def test_get_logs(self, task_run):
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        for container in CONTAINERS:
            url = f"{POD_URL}/log?container={container}"
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container])
        logs = task_run.get_logs()

        assert len(responses.calls) == 4
        assert logs == EXPECTED_LOGS

    @responses.activate
    def test_get_logs_wait(self, task_run):
        responses.add(
            responses.GET,
            TASK_RUN_WATCH_URL,
            json=TASK_RUN_WATCH_JSON,
        )
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        responses.add(responses.GET, POD_WATCH_URL,
                      json=POD_WATCH_JSON)
        responses.add(responses.GET, POD_URL,
                      json=POD_JSON)
        for container in CONTAINERS:
            url = f"{POD_URL}/log?follow=True&container={container}"
            responses.add(
                responses.GET,
                url,
                body=EXPECTED_LOGS[container],
                match=[responses.matchers.request_kwargs_matcher({"stream": True})],
            )

        logs = [line for line in task_run.get_logs(follow=True, wait=True)]
        assert logs == ['Hello World', 'Bye World']

    @responses.activate
    def test_get_logs_wait_removed(self, task_run):
        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            yield {}

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)

        responses.add(responses.GET, TASK_RUN_URL, json={})
        assert task_run.get_logs(follow=True, wait=True) is None


class TestPipelineRun():

    @responses.activate
    @pytest.mark.parametrize('run_name_in_input', [PIPELINE_RUN_NAME, 'wrong'])
    @pytest.mark.parametrize('input_data', [deepcopy(PIPELINE_RUN_DATA), None])
    def test_start_pipeline(self, openshift,
                            run_name_in_input, input_data):

        new_input_data = deepcopy(input_data)

        if new_input_data:
            new_input_data['metadata']['name'] = run_name_in_input

        p_run = PipelineRun(os=openshift, pipeline_run_name=PIPELINE_RUN_NAME,
                            pipeline_run_data=new_input_data)

        responses.add(
            responses.POST,
            f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns', # noqa E501
            json={},
        )

        if new_input_data:
            if run_name_in_input == PIPELINE_RUN_NAME:
                p_run.start_pipeline_run()
                assert len(responses.calls) == 1
                req_body = json.loads(responses.calls[0].request.body)
                if new_input_data:
                    assert req_body['metadata']['name'] == run_name_in_input
            else:
                msg = f"Pipeline run name provided '{PIPELINE_RUN_NAME}' is different " \
                      f"than in input data '{run_name_in_input}'"
                with pytest.raises(OsbsException, match=msg):
                    p_run.start_pipeline_run()
                assert len(responses.calls) == 0
        else:
            match_exception = "No input data provided for pipeline run to start"
            with pytest.raises(OsbsException, match=match_exception):
                p_run.start_pipeline_run()
            assert len(responses.calls) == 0

    @responses.activate
    def test_remove_pipeline(self, openshift):
        p_run = PipelineRun(os=openshift, pipeline_run_name=PIPELINE_RUN_NAME)
        response_json = {'kind': 'Status',
                         'apiVersion': 'v1',
                         'metadata': {},
                         'status': 'Success',
                         'details': {'name': PIPELINE_RUN_NAME, 'group': 'tekton.dev',
                                     'kind': 'pipelineruns',
                                     'uid': '16a9ad64-89f1-4612-baec-ded3e8a6df26'}
                         }
        responses.add(
            responses.DELETE,
            f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/'
            f'{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}',
            json=response_json
        )
        result_response = p_run.remove_pipeline_run()
        assert len(responses.calls) == 1
        assert result_response == response_json

    @responses.activate
    @pytest.mark.parametrize(('get_json', 'status', 'raises'), [
        (deepcopy(PIPELINE_RUN_JSON), 200, False),
        ({}, 404, True),
        ({}, 500, True),
    ])
    def test_cancel_pipeline(self, caplog, pipeline_run, get_json, status, raises):
        exp_request_body_pipeline_run = deepcopy(PIPELINE_MINIMAL_DATA)
        exp_request_body_pipeline_run['spec']['status'] = 'CancelledRunFinally'

        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.matchers.json_params_matcher(exp_request_body_pipeline_run)],
            json=get_json,
            status=status,
        )

        if raises:
            exc_msg = None
            log_msg = None
            if status == 404:
                exc_msg = f"Pipeline run '{PIPELINE_RUN_NAME}' can't be canceled, " \
                          f"because it doesn't exist"
            elif status != 200:
                log_msg = f"cancel pipeline run '{PIPELINE_RUN_NAME}' " \
                          f"failed with : [{status}] {get_json}"

            with pytest.raises(OsbsException) as exc:
                pipeline_run.cancel_pipeline_run()

            if exc_msg:
                assert exc_msg == str(exc.value)
            if log_msg:
                assert log_msg in caplog.text
        else:
            pipeline_run.cancel_pipeline_run()

        assert len(responses.calls) == 1

    @responses.activate
    @pytest.mark.parametrize('get_json', [PIPELINE_RUN_JSON, {}, None])  # noqa
    def test_get_info(self, pipeline_run, get_json):
        if get_json is not None:
            responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)
        else:
            responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json, status=404)

        resp = pipeline_run.get_info()

        assert len(responses.calls) == 1
        assert resp == get_json

    @responses.activate
    @pytest.mark.parametrize(('get_json', 'error_lines'), [
        # no data
        ({}, 'pipeline run removed;'),

        # no taskRuns in status and no plugins-metadata
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'pipeline err message1'}]},
          'metadata': {}},
         "pipeline err message1;"),

        # taskRuns in status, without steps
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'pipeline err message1'}],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-build-task1',
                             'status': {'conditions': [{'reason': 'reason2',
                                                        'message': 'message2'}],
                                        "startTime": "2022-04-26T15:58:42Z"}
                         }
                     }},
          'metadata': {}},
         "Error in binary-build-task1: message2;\n"),

        # taskRuns in status, with steps and failed without terminated key
        ({'status': {'conditions': [{'reason': 'reason1'}],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-build-task1',
                             'status': {
                                 'conditions': [{'reason': 'reason2', 'message': 'message2'}],
                                 'steps': [
                                     {'name': 'step_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'step_ko'},
                                 ],
                                 "startTime": "2022-04-26T15:58:42Z"
                             }
                         }
                     }},
          'metadata': {}},
         "Error in binary-build-task1: message2;\n"),

        # taskRuns in status, with steps, and no annotations in binary exit task
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-build-task1',
                             'status': {
                                 'conditions': [{'reason': 'reason2', 'message': 'message2'}],
                                 'steps': [
                                     {'name': 'step_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'step_ko',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'binary error'}])}}
                                 ],
                                 "startTime": "2022-04-26T15:58:42Z"
                             }
                         },
                         'task2': {
                             'pipelineTaskName': 'binary-build-pretask',
                             'status': {
                                 'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                                 'steps': [
                                     {'name': 'prestep_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'prestep_ko',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'pre1 error'}])}},
                                     {'name': 'prestep_ko2',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'pre2 error'}])}},
                                 ],
                                 "startTime": "2022-04-26T14:58:42Z"
                             }
                         },
                         'task3': {
                             'pipelineTaskName': 'binary-container-exit',
                             'status': {
                                 'conditions': [{'reason': 'Succeeded'}],
                                 'startTime': '2022-04-26T14:58:42Z',
                                 'taskResults': [{'name': 'not_annotations',
                                                  'value': '{"plugins-metadata": {"errors": '
                                                           '{"plugin1": "error1", '
                                                           '"plugin2": "error2"}}}'}]
                             }
                         }
                     }},
          'metadata': {}},
         "Error in binary-build-pretask: pre1 error;\n"
         "Error in binary-build-pretask: pre2 error;\n"
         "Error in binary-build-task1: binary error;\n"),

        # taskRuns in status, with steps, and annotations in binary exit task
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-build-task1',
                             'status': {
                                 'conditions': [{'reason': 'reason2', 'message': 'message2'}],
                                 'steps': [
                                     {'name': 'step_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'step_ko',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'binary error'}])}}
                                 ],
                                 "startTime": "2022-04-26T15:58:42Z"
                             }
                         },
                         'task2': {
                             'pipelineTaskName': 'binary-build-pretask',
                             'status': {
                                 'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                                 'steps': [
                                     {'name': 'prestep_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'prestep_ko',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'pre1 error'}])}},
                                     {'name': 'prestep_ko2',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'pre2 error'}])}},
                                 ],
                                 "startTime": "2022-04-26T14:58:42Z"
                             }
                         },
                         'task3': {
                             'pipelineTaskName': 'binary-container-exit',
                             'status': {
                                 'conditions': [{'reason': 'Succeeded'}],
                                 'startTime': '2022-04-26T14:58:42Z',
                                 'taskResults': [{'name': 'annotations',
                                                  'value': '{"plugins-metadata": {"errors": '
                                                           '{"plugin1": "error1", '
                                                           '"plugin2": "error2"}}}'}]
                             }
                         }
                     }},
          'metadata': {}},
         "Error in plugin plugin1: error1;\nError in plugin plugin2: error2;\n"
         "Error in binary-build-pretask: pre1 error;\n"
         "Error in binary-build-pretask: pre2 error;\n"
         "Error in binary-build-task1: binary error;\n"),

        # taskRuns in status, with steps, and annotations in source exit task
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-build-task1',
                             'status': {
                                 'conditions': [{'reason': 'reason2', 'message': 'message2'}],
                                 'steps': [
                                     {'name': 'step_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'step_ko',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'binary error'}])}}
                                 ],
                                 "startTime": "2022-04-26T15:58:42Z"
                             }
                         },
                         'task2': {
                             'pipelineTaskName': 'binary-build-pretask',
                             'status': {
                                 'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                                 'steps': [
                                     {'name': 'prestep_ok',
                                      'terminated': {'exitCode': 0}},
                                     {'name': 'prestep_ko',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'pre1 error'}])}},
                                     {'name': 'prestep_ko2',
                                      'terminated': {
                                          'exitCode': 1,
                                          'message': json.dumps([{'key': 'task_result',
                                                                  'value': 'pre2 error'}])}},
                                 ],
                                 "startTime": "2022-04-26T14:58:42Z"
                             }
                         },
                         'task3': {
                             'pipelineTaskName': 'source-container-exit',
                             'status': {
                                 'conditions': [{'reason': 'Succeeded'}],
                                 'startTime': '2022-04-26T14:58:42Z',
                                 'taskResults': [{'name': 'annotations',
                                                  'value': '{"plugins-metadata": {"errors": '
                                                           '{"plugin1": "error1", '
                                                           '"plugin2": "error2"}}}'}]
                             }
                         }
                     }},
          'metadata': {}},
         "Error in plugin plugin1: error1;\nError in plugin plugin2: error2;\n"
         "Error in binary-build-pretask: pre1 error;\n"
         "Error in binary-build-pretask: pre2 error;\n"
         "Error in binary-build-task1: binary error;\n"),
    ])  # noqa
    def test_get_error_message(self, pipeline_run, get_json, error_lines):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)

        resp = pipeline_run.get_error_message()

        assert len(responses.calls) == (2 if get_json else 1)
        assert resp == error_lines

    @responses.activate
    @pytest.mark.parametrize(('get_json', 'platforms'), [
        # no data
        ({}, None),

        # no taskRuns in status
        ({'status': {'conditions': []}},
         None),

        # taskRuns in status, no prebuild task
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-build-task1',
                             'status': {'startTime': '2022-04-26T15:58:42Z'}
                         }
                     }}},
         None),

        # taskRuns in status, prebuild task, not completed
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-container-prebuild',
                             'status': {'startTime': '2022-04-26T15:58:42Z',
                                        'conditions': [{'reason': 'Running'}]}
                         }
                     }}},
         None),

        # taskRuns in status, prebuild task, completed, no taskResults
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-container-prebuild',
                             'status': {'startTime': '2022-04-26T15:58:42Z',
                                        'conditions': [{'reason': 'Succeeded'}]}
                         }
                     }}},
         None),

        # taskRuns in status, prebuild task, completed, taskResults empty
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-container-prebuild',
                             'status': {'startTime': '2022-04-26T15:58:42Z',
                                        'conditions': [{'reason': 'Succeeded'}],
                                        'taskResults': []},
                         }
                     }}},
         None),

        # taskRuns in status, prebuild task, completed, taskResults but not platforms
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-container-prebuild',
                             'status': {'startTime': '2022-04-26T15:58:42Z',
                                        'conditions': [{'reason': 'Succeeded'}],
                                        'taskResults': [{'name': 'some_result',
                                                         'value': 'some_value'}]},
                         }
                     }}},
         None),

        # taskRuns in status, prebuild task, completed, taskResults with empty platforms
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-container-prebuild',
                             'status': {'startTime': '2022-04-26T15:58:42Z',
                                        'conditions': [{'reason': 'Succeeded'}],
                                        'taskResults': [{'name': 'platforms_result',
                                                         'value': []}]},
                         }
                     }}},
         []),

        # taskRuns in status, prebuild task, completed, taskResults with some platforms
        ({'status': {'conditions': [],
                     'taskRuns': {
                         'task1': {
                             'pipelineTaskName': 'binary-container-prebuild',
                             'status': {'startTime': '2022-04-26T15:58:42Z',
                                        'conditions': [{'reason': 'Succeeded'}],
                                        'taskResults': [{'name': 'platforms_result',
                                                         'value': ["x86_64", "ppc64le"]}]},
                         }
                     }}},
         ["x86_64", "ppc64le"]),
    ])  # noqa
    def test_get_final_platforms(self, pipeline_run, get_json, platforms):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)

        assert pipeline_run.get_final_platforms() == platforms

    @responses.activate
    @pytest.mark.parametrize(('get_json', 'reason', 'succeeded'), [
        (deepcopy(PIPELINE_RUN_JSON), 'Running', False),
        (deepcopy(PIPELINE_RUN_JSON), 'Succeeded', True),
        ({}, None, False),
    ])
    def test_status(self, pipeline_run, get_json, reason, succeeded):
        if get_json:
            get_json['status']['conditions'][0]['reason'] = reason
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)
        assert succeeded == pipeline_run.has_succeeded()
        assert pipeline_run.status_reason == reason

    @pytest.mark.parametrize(
        'any_failed, any_canceled, task_run_states',
        [
            (False, False, None),
            (False, False, []),
            # all task runs are successful or still running
            (
                False,
                False,
                [
                    ("clone", ("True", "Succeeded", "2022-05-27T08:07:27Z")),
                    ("binary-container-prebuild", ("Unknown", "Running", None)),
                ],
            ),
            # a task run failed
            (
                True,
                False,
                [
                    ("clone", ("True", "Succeeded", "2022-05-27T08:07:27Z")),
                    ("binary-container-prebuild", ("False", "Failed", "2022-05-27T08:07:50Z")),
                    ("binary-container-exit", ("True", "Succeeded", "2022-05-27T08:08:07Z")),
                ],
            ),
            # a task run was cancelled
            (
                False,
                True,
                [
                    ("clone", ("True", "Succeeded", "2022-05-27T08:07:27Z")),
                    ("binary-container-prebuild", ("True", "Succeeded", "2022-05-27T08:07:50Z")),
                    (
                        "binary-container-build-x86-64",
                        ("False", "TaskRunCancelled", "2022-05-27T08:00:08Z"),
                    )
                ],
            ),
            # one task run failed and another was cancelled
            (
                True,
                True,
                [
                    ("clone", ("True", "Succeeded", "2022-05-27T08:07:27Z")),
                    ("binary-container-prebuild", ("True", "Succeeded", "2022-05-27T08:07:50Z")),
                    (
                        "binary-container-build-x86-64",
                        ("False", "Failed", "2022-05-27T08:00:08Z"),
                    ),
                    (
                        "binary-container-build-ppc64le",
                        ("False", "TaskRunCancelled", "2022-05-27T08:00:15Z"),
                    ),
                ],
            ),
            # a task run encountered an error but might still succeed (considered not failed)
            (
                False,
                False,
                [("clone", ("False", "Failed, will retry", None))],
            ),
            # a task run is currently getting cancelled (considered cancelled)
            (
                False,
                True,
                [("clone", ("Unknown", "TaskRunCancelled", None))],
            ),
        ],
    )
    @responses.activate
    def test_any_task_failed_or_cancelled(
        self, pipeline_run, task_run_states, any_failed, any_canceled, caplog
    ):
        ppr_json = deepcopy(PIPELINE_RUN_JSON)

        if task_run_states is not None:
            ppr_json['status']['taskRuns'] = {
                task_name: {
                    "pipelineTaskName": task_name,
                    "status": {
                        "completionTime": completion_time,
                        "conditions": [
                            {"status": status, "reason": reason}
                        ],
                    },
                }
                for task_name, (status, reason, completion_time) in task_run_states
            }
        else:
            ppr_json['status'].pop('taskRuns', None)

        responses.add(responses.GET, PIPELINE_RUN_URL, json=ppr_json)

        assert pipeline_run.any_task_failed() == any_failed
        assert pipeline_run.any_task_was_cancelled() == any_canceled

        failed_re = re.compile(
            r"Found failed task: name=.*; status=False; reason=Failed; completionTime=2022.*"
        )
        cancelled_re = re.compile(
            r"Found cancelled task: name=.*; status=(False|Unknown); "
            r"reason=TaskRunCancelled; completionTime=.*"
        )

        if any_failed:
            assert failed_re.search(caplog.text) is not None

        if any_canceled:
            assert cancelled_re.search(caplog.text) is not None

    @responses.activate
    @pytest.mark.parametrize(
        'get_json, expect_results',
        [
            ({}, {}),
            ({'status': {}}, {}),
            (
                {"status": {"pipelineResults": [{'name': 'foo', 'value': '1234'},
                                                {'name': 'exclude', 'value': 'null'}]}},
                {'foo': 1234},
            ),
            (
                {"status": {"pipelineResults": [{'name': 'foo', 'value': '1234'},
                                                {'name': 'bar', 'value': 'string'}]}},
                {'foo': 1234, 'bar': 'string'},
            ),
            (
                {"status": {"pipelineResults": [{'name': 'foo', 'value': '1234'},
                                                {'name': 'barlist', 'value': ['string', 5]}]}},
                {'foo': 1234, 'barlist': ['string', 5]},
            ),
            (
                {"status": {"pipelineResults": [{'name': 'foo', 'value': '1234'},
                                                {'name': 'barlist', 'value': '["string", 5]'}]}},
                {'foo': 1234, 'barlist': ['string', 5]},
            ),
            (
                {
                    "status": {
                        "pipelineResults": [
                            {'name': 'x', 'value': '{"key": "value"}'}, {'name': 'y', 'value': '2'},
                        ],
                    },
                },
                {'x': {'key': 'value'}, 'y': 2},
            ),
        ],
    )
    def test_pipeline_results(self, pipeline_run, get_json, expect_results):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)
        assert pipeline_run.pipeline_results == expect_results

    @responses.activate
    @pytest.mark.parametrize(('status', 'reason', 'sleep_times'), [
        (None, None, 0),
        ('True', 'Succeeded', 0),
        ('False', 'Failed', 0),
        ('Unknown', 'PipelineRunCancelled', 0),
        ('Unknown', 'Running', WAIT_RETRY),
    ])
    def test_wait_for_finish(self, pipeline_run, status, reason, sleep_times):
        get_json = deepcopy(PIPELINE_RUN_JSON)
        get_json['status']['conditions'][0]['reason'] = reason
        get_json['status']['conditions'][0]['status'] = status
        if status is None and reason is None:
            get_json = {}
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)

        (flexmock(time)
            .should_receive('sleep')
            .with_args(WAIT_RETRY_SECS)
            .times(sleep_times)
            .and_return(None))

        pipeline_run.wait_for_finish()

    @responses.activate
    def test_wait_for_start(self, pipeline_run):
        flexmock(time).should_receive('sleep')
        responses.add(
            responses.GET,
            PIPELINE_WATCH_URL,
            json=PIPELINE_RUN_WATCH_JSON,
        )
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
        resp = pipeline_run.wait_for_start()

        assert len(responses.calls) == 2
        assert resp == PIPELINE_RUN_JSON

    @responses.activate
    @pytest.mark.parametrize(('get_info_json', 'calls'), [
        (PIPELINE_RUN_JSON, 2),
        ({}, 1),
    ])
    def test_wait_for_start_removed(self, pipeline_run, get_info_json, calls):
        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            yield {}
            yield {}

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_info_json)
        resp = pipeline_run.wait_for_start()

        assert len(responses.calls) == calls
        assert resp is None

    def test_wait_for_taskruns(self, pipeline_run):
        flexmock(time).should_receive('sleep')
        count = 0
        completed_pipeline = deepcopy(PIPELINE_RUN_JSON)
        completed_pipeline['status']['conditions'][0]['status'] = 'True'
        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            nonlocal count
            if count == 1:
                yield completed_pipeline
            else:
                count += 1
                yield PIPELINE_RUN_JSON

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)
        task_runs = [task_run for task_run in pipeline_run.wait_for_taskruns()]

        assert task_runs == [[
            (PIPELINE_RUN_JSON['status']['taskRuns'][TASK_RUN_NAME]['pipelineTaskName'],
             TASK_RUN_NAME),
            (PIPELINE_RUN_JSON['status']['taskRuns'][TASK_RUN_NAME2]['pipelineTaskName'],
             TASK_RUN_NAME2)]]

    @responses.activate
    def test_wait_for_taskruns_removed(self, pipeline_run):
        flexmock(time).should_receive('sleep')

        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            yield {}

        flexmock(Openshift).should_receive('watch_resource').replace_with(custom_watch)
        responses.add(responses.GET, PIPELINE_RUN_URL, json={})
        task_runs = [task_run for task_run in pipeline_run.wait_for_taskruns()]

        assert task_runs == []

    @responses.activate
    @pytest.mark.parametrize(('get_json', 'empty_logs'), [
        (PIPELINE_RUN_JSON, False),
        ({}, True),
    ])
    def test_get_logs(self, pipeline_run, get_json, empty_logs):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        responses.add(responses.GET, TASK_RUN_URL2, json=TASK_RUN_JSON2)
        for container in CONTAINERS:
            url = f"{POD_URL}/log?container={container}"
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container])
        for container in CONTAINERS2:
            url = f"{POD_URL2}/log?container={container}"
            responses.add(responses.GET, url, body=EXPECTED_LOGS2[container])
        logs = pipeline_run.get_logs()

        if empty_logs:
            assert len(responses.calls) == 1
            assert logs is None
        else:
            # pipeline = 1
            # tasks = 2
            # 3 steps per task = 6
            assert len(responses.calls) == 9
            assert logs == {get_json['status']['taskRuns'][TASK_RUN_NAME2]['pipelineTaskName']:
                            EXPECTED_LOGS2,
                            get_json['status']['taskRuns'][TASK_RUN_NAME]['pipelineTaskName']:
                            EXPECTED_LOGS}

    @responses.activate
    def test_get_logs_stream(self, pipeline_run):
        responses.add(
            responses.GET,
            PIPELINE_WATCH_URL,
            json=PIPELINE_RUN_WATCH_JSON,
        )
        flexmock(time).should_receive('sleep')
        second_set_tasks_pipeline = deepcopy(PIPELINE_RUN_JSON)
        task_run_3 = {TASK_RUN_NAME3: {
                "pipelineTaskName": "short3-sleep",
                "status": {
                    "conditions": [
                        {
                            "reason": "Running",
                            "status": "Unknown",
                        }
                    ],
                    "podName": POD_NAME3,
                    "steps": [
                        {
                            "container": "step3-hello",
                        },
                        {
                            "container": "step3-wait",
                        },
                        {
                            "container": "step3-bye",
                        },
                    ],
                    "startTime": "2022-08-29T14:58:42Z"
                },
            }}
        second_set_tasks_pipeline['status']['taskRuns'] = task_run_3
        completed_pipeline = deepcopy(PIPELINE_RUN_JSON)
        completed_pipeline['status']['conditions'][0]['status'] = 'True'

        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            if resource_type == 'pipelineruns':
                # send pipeline finished response once all logs are collected
                yield PIPELINE_RUN_JSON
                yield second_set_tasks_pipeline
                yield completed_pipeline
            elif resource_type == 'taskruns':
                if resource_name == TASK_RUN_NAME:
                    yield TASK_RUN_JSON
                elif resource_name == TASK_RUN_NAME2:
                    yield TASK_RUN_JSON2
                else:
                    yield TASK_RUN_JSON3
            elif resource_type == 'pods':
                if resource_name == POD_NAME:
                    yield POD_JSON
                elif resource_name == POD_NAME2:
                    yield POD_JSON2
                else:
                    yield POD_JSON3

        (flexmock(Openshift)
         .should_receive('watch_resource')
         .replace_with(custom_watch))

        for container in CONTAINERS:
            url = f"{POD_URL}/log?follow=True&container={container}"
            responses.add(
                responses.GET,
                url,
                body=EXPECTED_LOGS[container],
                match=[responses.matchers.request_kwargs_matcher({"stream": True})]
            )
        for container in CONTAINERS2:
            url = f"{POD_URL2}/log?follow=True&container={container}"
            responses.add(
                responses.GET,
                url,
                body=EXPECTED_LOGS2[container],
                match=[responses.matchers.request_kwargs_matcher({"stream": True})]
            )
        for container in CONTAINERS3:
            url = f"{POD_URL3}/log?follow=True&container={container}"
            responses.add(
                responses.GET,
                url,
                body=EXPECTED_LOGS3[container],
                match=[responses.matchers.request_kwargs_matcher({"stream": True})]
            )
        logs = [line for line in pipeline_run.get_logs(follow=True, wait=True)]

        assert logs == [(PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME]['pipelineTaskName'],
                         'Hello World'),
                        (PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME2]['pipelineTaskName'],
                         '2Hello World'),
                        (PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME]['pipelineTaskName'],
                         'Bye World'),
                        (PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME2]['pipelineTaskName'],
                         '2'),
                        (PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME2]['pipelineTaskName'],
                         '2Bye World'),
                        (task_run_3[TASK_RUN_NAME3]['pipelineTaskName'], '3Hello World'),
                        (task_run_3[TASK_RUN_NAME3]['pipelineTaskName'], '3'),
                        (task_run_3[TASK_RUN_NAME3]['pipelineTaskName'], '3Bye World')]

    @responses.activate
    def test_get_logs_stream_removed(self, pipeline_run):
        responses.add(
            responses.GET,
            PIPELINE_WATCH_URL,
            json=PIPELINE_RUN_WATCH_JSON,
        )

        def custom_watch(api_path, api_version, resource_type, resource_name,
                         **request_args):
            if resource_type == 'pipelineruns':
                yield PIPELINE_RUN_JSON
            elif resource_type == 'taskruns':
                if resource_name == TASK_RUN_NAME:
                    yield TASK_RUN_JSON
                # watcher for second task failed
                elif resource_name == TASK_RUN_NAME2:
                    yield {}

            elif resource_type == 'pods':
                yield POD_JSON

        # second task removed
        responses.add(responses.GET, TASK_RUN_URL2, json={})

        (flexmock(Openshift)
         .should_receive('watch_resource')
         .replace_with(custom_watch))

        for container in CONTAINERS:
            url = f"{POD_URL}/log?follow=True&container={container}"
            responses.add(
                responses.GET,
                url,
                body=EXPECTED_LOGS[container],
                match=[responses.matchers.request_kwargs_matcher({"stream": True})]
            )
        logs = [line for line in pipeline_run.get_logs(follow=True, wait=True)]

        assert logs == [(PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME]['pipelineTaskName'],
                         'Hello World'),
                        (PIPELINE_RUN_JSON['status']['taskRuns']
                         [TASK_RUN_NAME]['pipelineTaskName'],
                         'Bye World')]


def test_get_sorted_task_runs():
    task_runs = {
        "TaskRunSecond": {"status": {"startTime": "2022-09-07T18:23:51Z"}},
        "TaskRunMissingKey": {"status": {}},
        "TaskRunThird": {"status": {"startTime": "2022-09-07T18:24:51Z"}},
        "TaskRunFirst": {"status": {"startTime": "2022-09-07T18:22:51Z"}},
        "TaskRunMissingKeyAlso": {"status": {}},
    }
    expected_sorted = [
        ("TaskRunFirst", {"status": {"startTime": "2022-09-07T18:22:51Z"}}),
        ("TaskRunSecond", {"status": {"startTime": "2022-09-07T18:23:51Z"}}),
        ("TaskRunThird", {"status": {"startTime": "2022-09-07T18:24:51Z"}}),
        ("TaskRunMissingKey", {"status": {}}),
        ("TaskRunMissingKeyAlso", {"status": {}}),
    ]

    actual_sorted = get_sorted_task_runs(task_runs)
    assert actual_sorted == expected_sorted
