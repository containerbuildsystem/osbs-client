import responses
import pytest
import yaml
from copy import deepcopy

from osbs.tekton import Openshift, PipelineRun, TaskRun, Pod, API_VERSION
from osbs.exceptions import OsbsException
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
    @pytest.mark.parametrize('run_name_in_input', [PIPELINE_RUN_NAME, 'wrong'])
    @pytest.mark.parametrize('input_data', [deepcopy(PIPELINE_RUN_DATA), None])
    def test_start_pipeline(self, openshift, expected_request_body_pipeline_run,
                            run_name_in_input, input_data):

        if input_data:
            input_data['metadata']['name'] = run_name_in_input

        p_run = PipelineRun(os=openshift, pipeline_run_name=PIPELINE_RUN_NAME,
                            pipeline_run_data=input_data)

        responses.add(
            responses.POST,
            f'https://openshift.testing/apis/tekton.dev/v1beta1/namespaces/{TEST_OCP_NAMESPACE}/pipelineruns', # noqa E501
            match=[responses.json_params_matcher(expected_request_body_pipeline_run)],
            json={},
        )

        if input_data:
            if run_name_in_input == PIPELINE_RUN_NAME:
                p_run.start_pipeline_run()
                assert len(responses.calls) == 1
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
    @pytest.mark.parametrize(('get_json', 'status', 'raises'), [
        (deepcopy(PIPELINE_RUN_JSON), 200, False),
        ({}, 404, True),
        ({}, 500, True),
    ])
    def test_cancel_pipeline(self, caplog, pipeline_run, get_json, status, raises):
        exp_request_body_pipeline_run = deepcopy(PIPELINE_MINIMAL_DATA)
        exp_request_body_pipeline_run['spec']['status'] = 'PipelineRunCancelled'

        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.json_params_matcher(exp_request_body_pipeline_run)],
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
    @pytest.mark.parametrize(('get_json', 'status', 'raises'), [
        (deepcopy(PIPELINE_RUN_JSON), 200, False),
        ({}, 404, True),
        ({}, 500, True),
    ])
    def test_update_labels(self, caplog, pipeline_run, get_json, status, raises):
        labels = {'label_key': 'label_value'}
        exp_request_body_pipeline_run = deepcopy(PIPELINE_MINIMAL_DATA)
        exp_request_body_pipeline_run['metadata']['labels'] = labels

        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.json_params_matcher(exp_request_body_pipeline_run)],
            json=get_json,
            status=status,
        )

        if raises:
            exc_msg = None
            log_msg = None
            if status == 404:
                exc_msg = f"Can't update labels on pipeline run " \
                          f"'{PIPELINE_RUN_NAME}', because it doesn't exist"
            elif status != 200:
                log_msg = f"update labels on pipeline run '{PIPELINE_RUN_NAME}' " \
                          f"failed with : [{status}] {get_json}"

            with pytest.raises(OsbsException) as exc:
                pipeline_run.update_labels(labels)

            if exc_msg:
                assert exc_msg == str(exc.value)
            if log_msg:
                assert log_msg in caplog.text
        else:
            pipeline_run.update_labels(labels)

        assert len(responses.calls) == 1

    @responses.activate
    @pytest.mark.parametrize(('get_json', 'status', 'raises'), [
        (deepcopy(PIPELINE_RUN_JSON), 200, False),
        ({}, 404, True),
        ({}, 500, True),
    ])
    def test_update_annotations(self, caplog, pipeline_run, get_json, status, raises):
        annotations = {'annotation_key': 'annotation_value'}
        exp_request_body_pipeline_run = deepcopy(PIPELINE_MINIMAL_DATA)
        exp_request_body_pipeline_run['metadata']['annotations'] = annotations

        responses.add(
            responses.PATCH,
            PIPELINE_RUN_URL,
            match=[responses.json_params_matcher(exp_request_body_pipeline_run)],
            json=get_json,
            status=status,
        )

        if raises:
            exc_msg = None
            log_msg = None
            if status == 404:
                exc_msg = f"Can't update annotations on pipeline run " \
                          f"'{PIPELINE_RUN_NAME}', because it doesn't exist"
            elif status != 200:
                log_msg = f"update annotations on pipeline run '{PIPELINE_RUN_NAME}' " \
                          f"failed with : [{status}] {get_json}"

            with pytest.raises(OsbsException) as exc:
                pipeline_run.update_annotations(annotations)

            if exc_msg:
                assert exc_msg == str(exc.value)
            if log_msg:
                assert log_msg in caplog.text
        else:
            pipeline_run.update_annotations(annotations)

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
        ({}, None),

        # no taskRuns in status and no plugins-metadata
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}]},
          'metadata': {'annotations': {}}},
         "\npipeline run errors:\npipeline "
         "run source-container-x-x-default failed with reason: 'reason1' and message: 'message1'"),

        # no taskRuns in status
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}]},
          'metadata': {'annotations': {'plugins-metadata': '{"errors": {"plugin1": "error1",'
                                                           '"plugin2": "error2"}}'}}},
         "Error in plugin plugin1: error1\nError in plugin plugin2: error2\n\npipeline run errors:"
         "\npipeline run source-container-x-x-default failed with reason: 'reason1' and message: "
         "'message1'"),

        # taskRuns in status, without steps
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                     'taskRuns': {'task1': {'status': {'conditions': [{'reason': 'reason2',
                                                                       'message': 'message2'}]}}}},
          'metadata': {'annotations': {'plugins-metadata': '{"errors": {"plugin1": "error1",'
                                                           '"plugin2": "error2"}}'}}},
         "Error in plugin plugin1: error1\nError in plugin plugin2: error2\n\npipeline run errors:"
         "\npipeline task 'task1' failed:\ntask run 'task1' failed with reason: 'reason2' "
         "and message: 'message2'"),

        # taskRuns in status, with steps
        ({'status': {'conditions': [{'reason': 'reason1', 'message': 'message1'}],
                     'taskRuns': {
                         'task1': {'status': {'conditions': [
                                               {'reason': 'reason2', 'message': 'message2'}],
                                              'steps': [
                                                  {'name': 'step_ok',
                                                   'terminated': {'exitCode': 0}},
                                                  {'name': 'step_ko',
                                                   'terminated': {'exitCode': 1,
                                                                  'reason': 'step_reason'}}]}}}},
          'metadata': {'annotations': {'plugins-metadata': '{"errors": {"plugin1": "error1",'
                                                           '"plugin2": "error2"}}'}}},
         "Error in plugin plugin1: error1\nError in plugin plugin2: error2\n\npipeline run errors:"
         "\npipeline task 'task1' failed:\ntask step 'step_ko' failed with exit code: 1 "
         "and reason: 'step_reason'"),
    ])  # noqa
    def test_get_error_message(self, pipeline_run, get_json, error_lines):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)

        resp = pipeline_run.get_error_message()

        assert len(responses.calls) == 1
        assert resp == error_lines

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
    @pytest.mark.parametrize(('get_json', 'empty_logs'), [
        (PIPELINE_RUN_JSON, False),
        ({}, True),
    ])
    def test_get_logs(self, pipeline_run, get_json, empty_logs):
        responses.add(responses.GET, PIPELINE_RUN_URL, json=get_json)
        responses.add(responses.GET, TASK_RUN_URL, json=TASK_RUN_JSON)
        for container in CONTAINERS:
            url = f"{POD_URL}/log?container={container}"
            responses.add(responses.GET, url, body=EXPECTED_LOGS[container])
        logs = pipeline_run.get_logs()

        if empty_logs:
            assert len(responses.calls) == 1
            assert logs is None
        else:
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
