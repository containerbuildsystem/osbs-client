import responses
import pytest
import yaml
from copy import deepcopy

from osbs.tekton import Openshift, PipelineRun, TaskRun, Pod
from tests.constants import TEST_PIPELINE_RUN_TEMPLATE, TEST_OCP_NAMESPACE

PIPELINE_NAME = 'source-container-0-1'
PIPELINE_RUN_NAME = 'source-container-x-x-default'
TASK_RUN_NAME = 'test-task-run-1'
PIPELINE_RUN_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}' # noqa E501
PIPELINE_WATCH_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/watch/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns/{PIPELINE_RUN_NAME}/' # noqa E501
TASK_RUN_URL = f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/taskruns/{TASK_RUN_NAME}' # noqa E501
TASK_RUN_WATCH_URL = f"https://openshift.testing/apis/tekton.dev/v1beta1/watch/namespaces/{TEST_OCP_NAMESPACE}/taskruns/{TASK_RUN_NAME}/" # noqa E501

POD_NAME = 'test-pod'
CONTAINERS = ['step-hello', 'step-wait', 'step-bye']
POD_URL = f'https://openshift.testing/api/v1/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME}'
POD_WATCH_URL = f"https://openshift.testing/api/v1/watch/namespaces/{TEST_OCP_NAMESPACE}/pods/{POD_NAME}/" # noqa E501

EXPECTED_LOGS = {
    'step-hello': 'Hello World\n',
    'step-wait': '',
    'step-bye': 'Bye World\n'
}

PIPELINE_RUN_JSON = {
    "apiVersion": "tekton.dev/v1beta1",
    "kind": "PipelineRun",
    "status": {
        "conditions": [
            {
                "reason": "Running",
                "status": "Unknown",
            }
        ],
        "pipelineSpec": {
            "tasks": [
                {"name": "short-sleep", "taskRef": {"kind": "Task", "name": "short-sleep"}}
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

PIPELINE_RUN_WATCH_JSON = {
    "type": "ADDED",
    "object": PIPELINE_RUN_JSON
}

TASK_RUN_WATCH_JSON = {
    "type": "ADDED",
    "object": TASK_RUN_JSON
}

POD_WATCH_JSON = {
    "type": "ADDED",
    "object": POD_JSON
}

with open(TEST_PIPELINE_RUN_TEMPLATE) as f:
    yaml_data = f.read()
PIPELINE_RUN_DATA = yaml.safe_load(yaml_data)


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


@pytest.fixture(scope='function')
def expected_request_body_pipeline_run():
    return PIPELINE_RUN_DATA


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
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container], stream=True)

        logs = [line for line in pod.get_logs(wait=True, follow=True)]

        assert len(responses.calls) == 5
        assert logs == ['Hello World', 'Bye World']


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
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container], stream=True)

        logs = [line for line in task_run.get_logs(follow=True, wait=True)]
        assert logs == ['Hello World', 'Bye World']


class TestPipelineRun():

    @responses.activate
    def test_start_pipeline(self, pipeline_run, expected_request_body_pipeline_run):
        responses.add(
            responses.POST,
            f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns', # noqa E501
            match=[responses.json_params_matcher(expected_request_body_pipeline_run)],
        )

        pipeline_run.start_pipeline_run()
        assert len(responses.calls) == 1

    @responses.activate
    def test_cancel_pipeline(self, pipeline_run, expected_request_body_pipeline_run):
        expected_request_body_pipeline_run['spec']['status'] = 'PipelineRunCancelled'
        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.json_params_matcher(expected_request_body_pipeline_run)],
        )

        pipeline_run.cancel_pipeline_run()
        assert len(responses.calls) == 1

    @responses.activate
    def test_update_labels(self, pipeline_run, expected_request_body_pipeline_run):
        labels = {'label_key': 'label_value'}
        expected_request_body_pipeline_run['metadata']['namespace'] = TEST_OCP_NAMESPACE
        expected_request_body_pipeline_run['metadata']['labels'] = labels
        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.json_params_matcher(expected_request_body_pipeline_run)],
        )
        pipeline_run.update_labels(labels)
        assert len(responses.calls) == 1

    @responses.activate
    def test_update_annotations(self, pipeline_run, expected_request_body_pipeline_run):
        annotations = {'annotation_key': 'annotation_value'}
        expected_request_body_pipeline_run['metadata']['namespace'] = TEST_OCP_NAMESPACE
        expected_request_body_pipeline_run['metadata']['annotations'] = annotations
        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.json_params_matcher(expected_request_body_pipeline_run)],
        )
        pipeline_run.update_annotations(annotations)
        assert len(responses.calls) == 1

    @responses.activate
    def test_get_info(self, pipeline_run):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
        resp = pipeline_run.get_info()

        assert len(responses.calls) == 1
        assert resp == PIPELINE_RUN_JSON

    @responses.activate
    @pytest.mark.parametrize(('reason', 'succeeded'), [
        ('Running', False),
        ('Succeeded', True),
    ])
    def test_status(self, pipeline_run, reason, succeeded):
        run_json = deepcopy(PIPELINE_RUN_JSON)
        run_json['status']['conditions'][0]['reason'] = reason
        responses.add(responses.GET, PIPELINE_RUN_URL, json=run_json)
        assert succeeded == pipeline_run.has_succeeded()
        assert pipeline_run.status_reason == reason

    @responses.activate
    def test_wait_for_start(self, pipeline_run):
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
    def test_wait_for_taskruns(self, pipeline_run):
        responses.add(
            responses.GET,
            PIPELINE_WATCH_URL,
            json=PIPELINE_RUN_WATCH_JSON,
        )
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
        task_runs = [task_run for task_run in pipeline_run.wait_for_taskruns()]

        assert len(responses.calls) == 2
        assert task_runs == [TASK_RUN_NAME]

    @responses.activate
    def test_get_logs(self, pipeline_run):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        for container in CONTAINERS:
            url = f"{POD_URL}/log?container={container}"
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container])
        logs = pipeline_run.get_logs()

        assert len(responses.calls) == 5
        assert logs == {TASK_RUN_NAME: EXPECTED_LOGS}

    @responses.activate
    def test_get_logs_stream(self, pipeline_run):
        responses.add(
            responses.GET,
            PIPELINE_WATCH_URL,
            json=PIPELINE_RUN_WATCH_JSON,
        )
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
        responses.add(
            responses.GET,
            PIPELINE_WATCH_URL,
            json=PIPELINE_RUN_WATCH_JSON,
        )
        responses.add(responses.GET, PIPELINE_RUN_URL, json=PIPELINE_RUN_JSON)
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
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container], stream=True)
        logs = [line for line in pipeline_run.get_logs(follow=True, wait=True)]

        assert len(responses.calls) == 11
        assert logs == ['Hello World', 'Bye World']
