"""
Copyright (c) 2015, 2019, 2021 Red Hat, Inc
All rights reserved.
This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import json
import time
import logging
import base64
import os
import requests
import copy


from osbs.exceptions import OsbsResponseException, OsbsAuthException, OsbsException
from osbs.constants import (DEFAULT_NAMESPACE, SERVICEACCOUNT_SECRET, SERVICEACCOUNT_TOKEN,
                            SERVICEACCOUNT_CACRT)
from osbs.osbs_http import HttpSession
from osbs.kerberos_ccache import kerberos_ccache_init
from osbs.utils import retry_on_conflict
from urllib.parse import urljoin, urlencode, urlparse, parse_qs
from requests.utils import guess_json_utf

logger = logging.getLogger(__name__)

# Retry each connection attempt after 5 seconds, for a maximum of 10 times
WATCH_RETRY_SECS = 5
WATCH_RETRY = 10
MAX_BAD_RESPONSES = WATCH_RETRY // 3
# Give up after 12 hours
WAIT_RETRY_HOURS = 12
WAIT_RETRY = WAIT_RETRY_HOURS * 3600 // (WATCH_RETRY_SECS * WATCH_RETRY)

API_VERSION = "tekton.dev/v1beta1"


def check_response(response, log_level=logging.ERROR):
    if response.status_code not in (
            requests.status_codes.codes.ok,
            requests.status_codes.codes.created,
    ):
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = b''.join(response.iter_lines())

        logger.log(log_level, "[%d] %s", response.status_code, content)
        raise OsbsResponseException(message=content, status_code=response.status_code)


class Openshift(object):
    def __init__(self, openshift_api_url, openshift_oauth_url,
                 k8s_api_url=None,
                 verbose=False, username=None, password=None, use_kerberos=False,
                 kerberos_keytab=None, kerberos_principal=None, kerberos_ccache=None,
                 client_cert=None, client_key=None, verify_ssl=True, use_auth=None,
                 token=None, namespace=DEFAULT_NAMESPACE):
        self.os_api_url = openshift_api_url
        self.k8s_api_url = k8s_api_url
        self._os_oauth_url = openshift_oauth_url
        self.namespace = namespace
        self.verbose = verbose
        self.verify_ssl = verify_ssl
        self._con = HttpSession(verbose=self.verbose)
        self.retries_enabled = True

        # auth stuff
        self.use_kerberos = use_kerberos
        self.username = username
        self.password = password
        self.client_cert = client_cert
        self.client_key = client_key
        self.kerberos_keytab = kerberos_keytab
        self.kerberos_principal = kerberos_principal
        self.kerberos_ccache = kerberos_ccache
        self.token = token

        self.ca = None
        auth_credentials_provided = bool(use_kerberos or
                                         token or
                                         (username and password))
        if use_auth is None:
            self.use_auth = auth_credentials_provided
            if not self.use_auth:
                # Are we running inside a pod? If so, we will have a
                # token available which can be used for authentication
                self.use_auth = self.can_use_serviceaccount_token()
        else:
            self.use_auth = use_auth
            if not auth_credentials_provided:
                # We've been told to use authentication but no
                # credentials have been given. See if we're running
                # inside a pod, and if so use the provided token.
                self.can_use_serviceaccount_token()

    def can_use_serviceaccount_token(self):
        try:
            with open(os.path.join(SERVICEACCOUNT_SECRET,
                                   SERVICEACCOUNT_TOKEN),
                      mode='rt') as tfp:
                self.token = tfp.read().rstrip()

            ca = os.path.join(SERVICEACCOUNT_SECRET,
                              SERVICEACCOUNT_CACRT)
            if os.access(ca, os.R_OK):
                self.ca = ca
        except IOError:
            # No token available
            return False
        else:
            # We can authenticate using the supplied token
            logger.info("Using service account's auth token")
            return True

    @property
    def os_oauth_url(self):
        return self._os_oauth_url

    def _build_k8s_url(self, url, _prepend_namespace=True, **query):
        if _prepend_namespace:
            url = "namespaces/%s/%s" % (self.namespace, url)
        if query:
            url += ("?" + urlencode(query))
        return urljoin(self.k8s_api_url, url)

    def build_url(self, api_path, api_version, url, _prepend_namespace=True, **query):
        if _prepend_namespace:
            url = "namespaces/%s/%s" % (self.namespace, url)
        if query:
            url += ("?" + urlencode(query))
        url = f"{api_path}/{api_version}/{url}"
        return urljoin(self.os_api_url, url)

    def _request_args(self, with_auth=True, **kwargs):
        headers = kwargs.pop("headers", {})
        if with_auth and self.use_auth:
            if self.token is None:
                self.get_oauth_token()
            if self.token:
                headers["Authorization"] = "Bearer %s" % self.token
            else:
                raise OsbsAuthException("Please check your credentials. "
                                        "Token was not retrieved successfully.")

        # Use the client certificate both for the OAuth request and OpenShift
        # API requests. Certificate auth can be used as an alternative to
        # OAuth, however a scenario where they are used to get OAuth token is
        # also possible. Certificate is not sent when server does not request it.
        if self.client_cert or self.client_key:
            if self.client_cert and self.client_key:
                kwargs["client_cert"] = self.client_cert
                kwargs["client_key"] = self.client_key
            else:
                raise OsbsAuthException("You need to provide both client certificate and key.")

        # Do we have a ca.crt? If so, use it
        if self.verify_ssl and self.ca is not None:
            kwargs["ca"] = self.ca

        return headers, kwargs

    def post(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.post(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def get(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.get(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def put(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.put(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def patch(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.patch(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def delete(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.delete(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def get_oauth_token(self):
        url = self.os_oauth_url + "?response_type=token&client_id=openshift-challenging-client"
        if self.use_auth:
            if self.username and self.password:
                logger.debug("using basic authentication")
                r = self.get(
                    url,
                    with_auth=False,
                    allow_redirects=False,
                    username=self.username,
                    password=self.password,
                )
            elif self.use_kerberos:
                logger.debug("using kerberos authentication")

                if self.kerberos_keytab:
                    if not self.kerberos_principal:
                        raise OsbsAuthException("You need to provide kerberos principal along "
                                                "with the keytab path.")
                    kerberos_ccache_init(self.kerberos_principal, self.kerberos_keytab,
                                         ccache_file=self.kerberos_ccache)

                r = self.get(url, with_auth=False, allow_redirects=False, kerberos_auth=True)
            else:
                logger.debug("using identity authentication")
                r = self.get(url, with_auth=False, allow_redirects=False)
        else:
            logger.debug("getting token without any authentication (fingers crossed)")
            r = self.get(url, with_auth=False, allow_redirects=False)

        try:
            redir_url = r.headers['location']
        except KeyError:
            logger.error("[%s] 'Location' header is missing in response, cannot retrieve token",
                         r.status_code)
            return ""
        parsed_url = urlparse(redir_url)
        fragment = parsed_url.fragment
        parsed_fragment = parse_qs(fragment)
        self.token = parsed_fragment['access_token'][0]
        return self.token

    def get_serviceaccount_tokens(self, username="~"):
        result = {}

        url = self._build_k8s_url("serviceaccounts/%s/" % username, _prepend_namespace=True)
        response = self.get(url)
        check_response(response)
        sa_json = response.json()
        if not sa_json:
            return {}

        if 'secrets' not in sa_json.keys():
            logger.debug("No secrets found for service account %s", username)
            return {}

        secrets = sa_json['secrets']

        for secret in secrets:
            if 'name' not in secret.keys():
                logger.debug("Malformed secret info: missing 'name' key in %r",
                             secret)
                continue
            secret_name = secret['name']
            if 'token' not in secret_name:
                logger.debug("Secret %s is not a token", secret_name)
                continue

            url = self._build_k8s_url("secrets/%s/" % secret_name, _prepend_namespace=True)
            response = self.get(url)
            check_response(response)

            secret_json = response.json()
            if not secret_json:
                continue
            if 'data' not in secret_json.keys():
                logger.debug("Malformed secret info: missing 'data' key in %r",
                             json)
                continue

            secret_data = secret_json['data']
            if 'token' not in secret_data.keys():
                logger.debug("Malformed secret data: missing 'token' key in %r",
                             secret_data)
                continue

            token = secret_data['token']

            # Token needs to be base64-decoded
            result[secret_name] = base64.b64decode(token)

        return result

    def watch_resource(self, api_path, api_version, resource_type, resource_name,
                       **request_args):
        """
        Watch for changes in openshift object and return it's json representation
        after each update to the object
        """
        def log_and_sleep():
            logger.debug("connection closed, reconnecting in %ds", WATCH_RETRY_SECS)
            time.sleep(WATCH_RETRY_SECS)

        watch_path = f"watch/namespaces/{self.namespace}/{resource_type}/{resource_name}/"
        watch_url = self.build_url(
            api_path, api_version, watch_path, _prepend_namespace=False, **request_args
        )
        get_url = self.build_url(api_path, api_version,
                                 f"{resource_type}/{resource_name}")

        bad_responses = 0
        for _ in range(WATCH_RETRY):
            logger.debug("watching for updates for %s, %s", resource_type, resource_name)
            try:
                response = self.get(watch_url, stream=True,
                                    headers={'Connection': 'close'})
                check_response(response)
            # we're already retrying, so there's no need to panic just because of a bad response
            except OsbsResponseException as exc:
                bad_responses += 1
                if bad_responses > MAX_BAD_RESPONSES:
                    raise exc
                else:
                    # check_response() already logged the message, so just report that we're
                    # sleeping and retry
                    log_and_sleep()
                    continue

            for line in response.iter_lines():
                encoding = guess_json_utf(line)
                try:
                    j = json.loads(line.decode(encoding))
                except ValueError:
                    logger.error("Cannot decode watch event: %s", line)
                    continue
                if 'object' not in j:
                    logger.error("Watch event has no 'object': %s", j)
                    continue
                if 'type' not in j:
                    logger.error("Watch event has no 'type': %s", j)
                    continue

                # Avoid races. We've already asked the server to tell us
                # about changes to the object, but now ask for a fresh
                # copy of the object as well. This is to catch the
                # situation where the object changed before the call to
                # this method, or in between retries in this method.
                logger.debug("retrieving fresh version of object %s", resource_name)
                fresh_response = self.get(get_url)
                check_response(fresh_response)
                yield fresh_response.json()

            log_and_sleep()


class PipelineRun():
    def __init__(self, os, pipeline_run_name, pipeline_run_data=None):
        self.os = os
        self.pipeline_run_name = pipeline_run_name
        self.api_path = 'apis'
        self.api_version = API_VERSION
        self.input_data = pipeline_run_data
        self._pipeline_run_url = None
        self.minimal_data = {
            "apiVersion": API_VERSION,
            "kind": "PipelineRun",
            "metadata": {"name": self.pipeline_run_name},
            "spec": {},
        }

    @property
    def data(self):
        # always get fresh info
        return self.get_info()

    @property
    def pipeline_run_url(self):
        if self._pipeline_run_url is None:
            self._pipeline_run_url = self.os.build_url(
                self.api_path,
                self.api_version,
                f"pipelineruns/{self.pipeline_run_name}"
            )
        return self._pipeline_run_url

    def start_pipeline_run(self):
        if not self.input_data:
            raise OsbsException("No input data provided for pipeline run to start")

        run_name = self.input_data.get('metadata', {}).get('name')

        if run_name != self.pipeline_run_name:
            msg = f"Pipeline run name provided '{self.pipeline_run_name}' is different " \
                  f"than in input data '{run_name}'"
            raise OsbsException(msg)

        url = self.os.build_url(
            self.api_path,
            self.api_version,
            "pipelineruns"
        )
        response = self.os.post(
            url,
            data=json.dumps(self.input_data),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        return response.json()

    def _check_response(self, response, cmd):
        try:
            run_json = response.json()
        except OsbsResponseException as ex:
            if ex.status_code == 404:
                run_json = None
            else:
                logger.error("%s failed with : [%d] %s", cmd, ex.status_code, ex)
                raise

        return run_json

    @retry_on_conflict
    def cancel_pipeline_run(self):
        data = copy.deepcopy(self.minimal_data)
        data['spec']['status'] = 'PipelineRunCancelled'

        response = self.os.patch(
            self.pipeline_run_url,
            data=json.dumps(data),
            headers={
                "Content-Type": "application/merge-patch+json",
                "Accept": "application/json",
            },
        )

        msg = f"cancel pipeline run '{self.pipeline_run_name}'"
        exc_msg = f"Pipeline run '{self.pipeline_run_name}' can't be canceled, " \
                  f"because it doesn't exist"
        response_json = self._check_response(response, msg)
        if not response_json:
            raise OsbsException(exc_msg)
        return response_json

    @retry_on_conflict
    def update_labels(self, labels):
        data = copy.deepcopy(self.minimal_data)
        data['metadata']['labels'] = labels

        response = self.os.patch(
            self.pipeline_run_url,
            data=json.dumps(data),
            headers={
                "Content-Type": "application/merge-patch+json",
                "Accept": "application/json",
            },
        )

        msg = f"update labels on pipeline run '{self.pipeline_run_name}'"
        exc_msg = f"Can't update labels on pipeline run '{self.pipeline_run_name}', " \
                  f"because it doesn't exist"
        response_json = self._check_response(response, msg)
        if not response_json:
            raise OsbsException(exc_msg)
        return response_json

    @retry_on_conflict
    def update_annotations(self, annotations):
        data = copy.deepcopy(self.minimal_data)
        data['metadata']['annotations'] = annotations

        response = self.os.patch(
            self.pipeline_run_url,
            data=json.dumps(data),
            headers={
                "Content-Type": "application/merge-patch+json",
                "Accept": "application/json",
            },
        )

        msg = f"update annotations on pipeline run '{self.pipeline_run_name}'"
        exc_msg = f"Can't update annotations on pipeline run '{self.pipeline_run_name}', " \
                  f"because it doesn't exist"
        response_json = self._check_response(response, msg)
        if not response_json:
            raise OsbsException(exc_msg)
        return response_json

    def get_info(self, wait=False):
        if wait:
            self.wait_for_start()
        response = self.os.get(self.pipeline_run_url)

        return self._check_response(response, 'get_info')

    def get_error_message(self):
        data = self.data

        if not data:
            return None

        annotations = data['metadata']['annotations']

        plugins_metadata = annotations.get('plugins-metadata')
        plugin_errors = None

        if plugins_metadata:
            metadata_dict = json.loads(plugins_metadata)
            plugin_errors = metadata_dict.get('errors')

        err_message = ""

        if plugin_errors:
            for plugin, error in plugin_errors.items():
                err_message += f"Error in plugin {plugin}: {error}\n"

        err_message += "\npipeline run errors:\n"

        task_runs_status = data['status'].get('taskRuns', {})

        for task_name, stats in task_runs_status.items():
            if stats['status']['conditions'][0]['reason'] == 'Succeeded':
                continue

            err_message += f"pipeline task '{task_name}' failed:\n"

            if 'steps' in stats['status']:
                for step in stats['status']['steps']:
                    exit_code = step['terminated']['exitCode']
                    if exit_code == 0:
                        continue

                    reason = step['terminated']['reason']
                    err_message += f"task step '{step['name']}' failed with exit " \
                                   f"code: {exit_code} " \
                                   f"and reason: '{reason}'"
            else:
                task_condition = stats['status']['conditions'][0]
                err_message += f"task run '{task_name}' failed with reason:" \
                               f" '{task_condition['reason']}' and message:" \
                               f" '{task_condition['message']}'"

        if not task_runs_status:
            pipeline_run_condition = data['status']['conditions'][0]
            err_message += f"pipeline run {self.pipeline_run_name} failed with reason:" \
                           f" '{pipeline_run_condition['reason']}' and message:" \
                           f" '{pipeline_run_condition['message']}'"

        return err_message

    def has_succeeded(self):
        status_reason = self.status_reason
        logger.info("Pipeline run info: '%s'", self.data)
        return status_reason == 'Succeeded'

    def has_not_finished(self):
        return self.status_status == 'Unknown' and self.status_reason != 'PipelineRunCancelled'

    def was_cancelled(self):
        return self.status_reason == 'PipelineRunCancelled'

    @property
    def annotations(self):
        data = self.data

        if not data:
            return None
        return data['metadata']['annotations']

    @property
    def labels(self):
        data = self.data

        if not data:
            return None
        return data['metadata']['labels']

    @property
    def status_reason(self):
        data = self.data

        if not data:
            return None
        return data['status']['conditions'][0]['reason']

    @property
    def status_status(self):
        data = self.data

        if not data:
            return None
        return data['status']['conditions'][0]['status']

    def wait_for_start(self):
        """
        https://tekton.dev/docs/pipelines/pipelineruns/#monitoring-execution-status
        """
        logger.info("Waiting for pipeline run '%s' to start", self.pipeline_run_name)
        for pipeline_run in self.os.watch_resource(
                self.api_path,
                self.api_version,
                resource_type="pipelineruns",
                resource_name=self.pipeline_run_name,
        ):
            try:
                status = pipeline_run['status']['conditions'][0]['status']
                reason = pipeline_run['status']['conditions'][0]['reason']
            except KeyError:
                logger.error("pipeline run does not have any status")
                continue
            # pipeline run finished succesfully or failed
            if status in ['True', 'False']:
                return pipeline_run
            elif status == 'Unknown' and reason == 'Running':
                return pipeline_run
            else:
                # (Unknown, Started), (Unknown, PipelineRunCancelled)
                logger.debug("Waiting for pipeline run, current status %s, reason %s",
                             status, reason)

    def wait_for_taskruns(self):
        """
        This generator method watches new task runs in a pipeline run
        and yields newly started task runs.
        The reason we have to watch for changes is that at the start, the pipeline run
        does not have information about all of its task runs, especially when there are multiple
        sequential tasks.
        """
        watched_task_runs = []
        for pipeline_run in self.os.watch_resource(
                self.api_path,
                self.api_version,
                resource_type="pipelineruns",
                resource_name=self.pipeline_run_name,
        ):
            try:
                task_runs = pipeline_run['status']['taskRuns']
            except KeyError:
                logger.error("pipeline run does not have any task runs")
                continue
            for task_run in task_runs:
                if task_run not in watched_task_runs:
                    watched_task_runs.append(task_run)
                    yield task_run
            # all task runs accounted for
            if len(pipeline_run['status']['pipelineSpec']['tasks']) == len(task_runs):
                return

    def _get_logs(self):
        logs = {}
        pipeline_run = self.data

        if not pipeline_run:
            return None

        task_runs = pipeline_run['status']['taskRuns']
        for task_run in task_runs:
            logs[task_run] = TaskRun(os=self.os, task_run_name=task_run).get_logs()
        return logs

    def _get_logs_stream(self):
        self.wait_for_start()
        for task_run in self.wait_for_taskruns():
            yield from TaskRun(os=self.os, task_run_name=task_run).get_logs(
                follow=True, wait=True)

    def get_logs(self, follow=False, wait=False):
        if wait or follow:
            return self._get_logs_stream()
        else:
            return self._get_logs()


class TaskRun():
    def __init__(self, os, task_run_name):
        self.os = os
        self.task_run_name = task_run_name
        self.api_path = 'apis'
        self.api_version = API_VERSION

    def get_info(self, wait=False):
        if wait:
            self.wait_for_start()

        url = self.os.build_url(
            self.api_path,
            self.api_version,
            f"taskruns/{self.task_run_name}"
        )
        r = self.os.get(url)
        return r.json()

    def get_logs(self, follow=False, wait=False):
        if follow or wait:
            task_run = self.wait_for_start()
        else:
            task_run = self.get_info()

        pod_name = task_run['status']['podName']
        containers = [step['container'] for step in task_run['status']['steps']]
        pod = Pod(os=self.os, pod_name=pod_name, containers=containers)
        return pod.get_logs(follow=follow, wait=wait)

    def wait_for_start(self):
        """
        https://tekton.dev/docs/pipelines/taskruns/#monitoring-execution-status
        """
        logger.info("Waiting for task run '%s' to start", self.task_run_name)
        for task_run in self.os.watch_resource(
                self.api_path,
                self.api_version,
                resource_type="taskruns",
                resource_name=self.task_run_name,
        ):
            try:
                status = task_run['status']['conditions'][0]['status']
                reason = task_run['status']['conditions'][0]['reason']
            except KeyError:
                logger.error("Task run does not have any status")
                continue
            # task run finished succesfully or failed
            if status in ['True', 'False']:
                return task_run
            elif status == 'Unknown' and reason == 'Running':
                return task_run
            else:
                # (Unknown, Started), (Unknown, Pending), (Unknown, TaskRunCancelled)
                logger.debug("Waiting for task run, current status: %s, reason %s", status, reason)


class Pod():
    def __init__(self, os, pod_name, containers=None):
        self.os = os
        self.pod_name = pod_name
        self.containers = containers
        self.api_version = 'v1'
        self.api_path = 'api'

    def get_info(self, wait=False):
        if wait:
            self.wait_for_start()
        url = self.os.build_url(
            self.api_path,
            self.api_version,
            f"pods/{self.pod_name}"
        )
        r = self.os.get(url)
        return r.json()

    def _get_logs_no_container(self):
        url = self.os.build_url(
            self.api_path,
            self.api_version,
            f"pods/{self.pod_name}/log"
        )
        r = self.os.get(url)
        check_response(r)
        return r.content.decode('utf-8')

    def _get_logs(self):
        logs = {}
        for container in self.containers:
            kwargs = {'container': container}
            logger.debug("Getting log for container %s", container)
            url = self.os.build_url(
                self.api_path,
                self.api_version,
                f"pods/{self.pod_name}/log",
                **kwargs
            )
            r = self.os.get(url)
            check_response(r)
            logs[container] = r.content.decode('utf-8')
        return logs

    def _get_logs_stream(self):
        self.wait_for_start()
        for container in self.containers:
            yield from self._stream_logs(container)

    def get_logs(self, follow=False, wait=False):
        if follow or wait:
            return self._get_logs_stream()
        if self.containers:
            return self._get_logs()
        else:
            return self._get_logs_no_container()

    def _stream_logs(self, container):
        kwargs = {'follow': True}
        if container:
            kwargs['container'] = container

        # If connection is closed within this many seconds, give up:
        min_idle_timeout = 60

        # Stream logs, but be careful of the connection closing
        # due to idle timeout. In that case, try again until the
        # call returns more quickly than a reasonable timeout
        # would be set to.
        while True:
            connected = time.time()
            url = self.os.build_url(
                self.api_path,
                self.api_version,
                f"pods/{self.pod_name}/log",
                **kwargs
            )
            try:
                logger.debug('Streaming logs for container %s', container)
                response = self.os.get(url, stream=True,
                                       headers={'Connection': 'close'})
                check_response(response)

                for line in response.iter_lines():
                    connected = time.time()
                    yield line.decode('utf-8')
            # NOTE1: If self.get causes ChunkedEncodingError, ConnectionError,
            # or IncompleteRead to be raised, they'll be wrapped in
            # OsbsNetworkException or OsbsException
            # NOTE2: If iter_lines causes ChunkedEncodingError
            # or IncompleteRead to be raised, it'll simply be silenced.
            # NOTE3: An exception may be raised from
            # check_response(). In this case, exception will be
            # wrapped in OsbsException or OsbsNetworkException,
            # inspect cause to detect ConnectionError.
            except OsbsException as exc:
                if not isinstance(exc.cause, requests.ConnectionError):
                    raise
            except requests.exceptions.ConnectionError:
                pass

            idle = time.time() - connected
            logger.debug("connection closed after %ds", idle)
            if idle < min_idle_timeout:
                # Finish output
                return

            since = int(idle - 1)
            logger.debug("fetching logs starting from %ds ago", since)
            kwargs['sinceSeconds'] = since

    def wait_for_start(self):
        logger.info("Waiting for pod to start '%s'", self.pod_name)
        for pod in self.os.watch_resource(
                self.api_path, self.api_version, resource_type="pods", resource_name=self.pod_name
        ):
            try:
                status = pod['status']['phase']
            except KeyError:
                logger.error("Pod does not have any status")
                continue
            if status in ['Running', 'Succeeded', 'Failed']:
                return pod
            else:
                # unknown or pending
                logger.debug("Waiting for pod, current state: %s", status)
