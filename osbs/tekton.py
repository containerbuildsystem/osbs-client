"""
Copyright (c) 2015-2022 Red Hat, Inc
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
from typing import Dict, Tuple, Callable, Any


from osbs.exceptions import OsbsResponseException, OsbsAuthException, OsbsException
from osbs.constants import (DEFAULT_NAMESPACE, SERVICEACCOUNT_SECRET, SERVICEACCOUNT_TOKEN,
                            SERVICEACCOUNT_CACRT)
from osbs.osbs_http import HttpSession
from osbs.kerberos_ccache import kerberos_ccache_init
from osbs.utils import retry_on_conflict
from urllib.parse import urljoin, urlencode, urlparse, parse_qs
from requests.utils import guess_json_utf

logger = logging.getLogger(__name__)

# Retry each connection attempt after 5 seconds, for a maximum of 20 times
WATCH_RETRY_SECS = 5
WATCH_RETRY = 20
MAX_BAD_RESPONSES = 20

# Retry waiting for pipeline to finish for 5 seconds, for a maximum of 5 hours
WAIT_RETRY_SECS = 5
WAIT_RETRY_HOURS = 5
WAIT_RETRY = (WAIT_RETRY_HOURS * 3600) // WAIT_RETRY_SECS

API_VERSION = "tekton.dev/v1beta1"


def check_response(response, log_level=logging.INFO):
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


def check_response_json(response, cmd):
    try:
        run_json = response.json()
    except OsbsResponseException as ex:
        if ex.status_code == 404:
            run_json = None
        else:
            logger.info("%s failed with : [%d] %s", cmd, ex.status_code, ex)
            raise

    return run_json


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
            logger.debug("Connection closed, reconnecting in %ds", WATCH_RETRY_SECS)
            time.sleep(WATCH_RETRY_SECS)

        watch_path = f"watch/namespaces/{self.namespace}/{resource_type}/{resource_name}/"
        watch_url = self.build_url(
            api_path, api_version, watch_path, _prepend_namespace=False, **request_args
        )
        get_url = self.build_url(api_path, api_version,
                                 f"{resource_type}/{resource_name}")

        bad_responses = 0
        for _ in range(WATCH_RETRY):
            logger.debug("Watching for updates for %s, %s", resource_type, resource_name)
            try:
                response = self.get(watch_url, stream=True,
                                    headers={'Connection': 'close'})
                check_response(response)

                for line in response.iter_lines():
                    encoding = guess_json_utf(line)
                    try:
                        j = json.loads(line.decode(encoding))
                    except ValueError:
                        logger.warning("Cannot decode watch event: %s", line)
                        continue
                    if 'object' not in j:
                        logger.warning("Watch event has no 'object': %s", j)
                        continue
                    if 'type' not in j:
                        logger.warning("Watch event has no 'type': %s", j)
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

            # we're already retrying, so there's no need to panic just because of a bad response
            except OsbsResponseException as exc:
                bad_responses += 1
                if bad_responses > MAX_BAD_RESPONSES:
                    raise exc

            except OsbsException as exc:
                if (not isinstance(exc.cause, requests.ConnectionError) and
                        not isinstance(exc.cause, requests.Timeout)):
                    raise
            except requests.exceptions.ConnectionError:
                # resource might have been already removed, so yield None
                # and check if resource still exists
                logger.debug("Got Connection exception while watching resource %s", resource_name)
                yield {}
            except requests.exceptions.Timeout:
                # resource might have been already removed, so yield None
                # and check if resource still exists
                logger.debug("Got Timeout exception while watching resource %s", resource_name)
                yield {}

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

    def remove_pipeline_run(self):
        url = self.os.build_url(
            self.api_path,
            self.api_version,
            f"pipelineruns/{self.pipeline_run_name}"
        )
        response = self.os.delete(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        return response.json()

    @retry_on_conflict
    def cancel_pipeline_run(self):
        data = copy.deepcopy(self.minimal_data)
        data['spec']['status'] = 'CancelledRunFinally'

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
        response_json = check_response_json(response, msg)
        if not response_json:
            raise OsbsException(exc_msg)
        return response_json

    def get_info(self, wait=False):
        if wait:
            self.wait_for_start()
        response = self.os.get(self.pipeline_run_url)

        return check_response_json(response, 'get_info')

    def get_task_results(self):
        data = self.data
        task_results = {}

        if not data:
            return task_results

        for task_run in self.child_references:
            task_info = TaskRun(os=self.os, task_run_name=task_run['name']).get_info()

            task_name = task_info['metadata']['labels']['tekton.dev/pipelineTask']
            results = {}

            if 'taskResults' not in task_info['status']:
                continue

            for result in task_info['status']['taskResults']:
                results[result['name']] = result['value']

            task_results[task_name] = results

        return task_results

    def get_error_message(self):
        data = self.data

        if not data:
            return "pipeline run removed;"

        plugin_errors = None
        annotations_str = None
        task_results = self.get_task_results()

        for task_name in ('binary-container-exit', 'source-container-exit'):
            if task_name not in task_results:
                continue

            if 'annotations' in task_results[task_name]:
                annotations_str = task_results[task_name]['annotations']
                break

        if annotations_str:
            plugins_metadata = json.loads(annotations_str).get('plugins-metadata')

            if plugins_metadata:
                plugin_errors = plugins_metadata.get('errors')

        err_message = ""

        if plugin_errors:
            for plugin, error in plugin_errors.items():
                err_message += f"Error in plugin {plugin}: {error};\n"

        pipeline_error = data['status']['conditions'][0].get('message')

        for task_run in self.child_references:
            task_info = TaskRun(os=self.os, task_run_name=task_run['name']).get_info()

            task_name = task_info['metadata']['labels']['tekton.dev/pipelineTask']
            got_task_error = False
            if task_info['status']['conditions'][0]['reason'] in ['Succeeded', 'None']:
                # tekton: "None" reason means skipped task; yes string
                continue

            if 'steps' in task_info['status']:
                for step in task_info['status']['steps']:
                    if 'terminated' in step:
                        exit_code = step['terminated']['exitCode']
                        if exit_code == 0:
                            continue

                        if 'message' in step['terminated']:
                            try:
                                message_json = json.loads(step['terminated']['message'])
                                for message in message_json:
                                    if message['key'] == 'task_result':
                                        err_message += f"Error in {task_name}: " \
                                                       f"{message['value']};\n"
                                got_task_error = True
                                continue
                            except Exception as e:
                                logger.info("failed to get error message: %s", repr(e))
                                continue

            if not got_task_error:
                err_message += f"Error in {task_name}: " \
                               f"{task_info['status']['conditions'][0]['message']};\n"

        if not err_message:
            if pipeline_error:
                err_message = f"{pipeline_error};"
            else:
                err_message = "pipeline run failed;"

        return err_message

    def get_final_platforms(self):
        data = self.data

        if not data:
            return None

        task_results = self.get_task_results()

        if 'binary-container-prebuild' not in task_results:
            return None

        if 'platforms_result' in task_results['binary-container-prebuild']:
            platforms = json.loads(task_results['binary-container-prebuild']['platforms_result'])
            return platforms['platforms']

        return None

    def has_succeeded(self):
        status_reason = self.status_reason
        logger.info("Pipeline run info: '%s'", self.data)
        # tekton: completed means succeeded with a skipped task
        return status_reason in ['Succeeded', 'Completed']

    def has_not_finished(self):
        data = self.data
        if not data:
            logger.info("Pipeline run removed '%s'", self.pipeline_run_name)
            return False

        return self.status_status == 'Unknown' and self.status_reason != 'PipelineRunCancelled'

    def was_cancelled(self):
        return self.status_reason == 'PipelineRunCancelled'

    def any_task_failed(self) -> bool:
        """
        Check if any taskRun failed.

        See table in https://tekton.dev/docs/pipelines/taskruns/#monitoring-execution-status
        """
        return self._any_task_run_in_state(
            'failed',
            lambda status, reason, has_completion_time: (
                status == 'False' and reason != 'TaskRunCancelled' and has_completion_time
            ),
        )

    def any_task_was_cancelled(self) -> bool:
        """
        Check if any taskRun was cancelled or is currently getting cancelled.

        See table in https://tekton.dev/docs/pipelines/taskruns/#monitoring-execution-status
        """
        return self._any_task_run_in_state(
            'cancelled',
            lambda status, reason, has_completion_time: reason == 'TaskRunCancelled',
        )

    def _any_task_run_in_state(
        self, state_name: str, match_state: Callable[[str, str, bool], bool]
    ) -> bool:

        def matches_state(task_run: Dict[str, Any]) -> bool:
            task_run_status = task_run['status']
            task_name = task_run['metadata']['labels']['tekton.dev/pipelineTask']

            if 'conditions' not in task_run_status:
                logger.debug('conditions are missing from status in task %s : %s',
                             task_name, task_run_status)
                return False

            status = task_run_status['conditions'][0]['status']
            reason = task_run_status['conditions'][0]['reason']
            completion_time = task_run_status.get('completionTime')

            if match_state(status, reason, completion_time is not None):
                logger.debug(
                    'Found %s task: name=%s; status=%s; reason=%s; completionTime=%s',
                    state_name, task_name, status, reason, completion_time,
                )
                return True

            return False

        task_runs = []
        for task_run in self.child_references:
            task_info = TaskRun(os=self.os, task_run_name=task_run['name']).get_info()
            task_runs.append(task_info)

        return any(matches_state(tr) for tr in task_runs)

    def wait_for_finish(self):
        """
        use this method after reading logs finished, to ensure that pipeline run finished,
        as pipeline run status doesn't change immediately when logs finished
        """
        for _ in range(WAIT_RETRY):
            if self.has_not_finished():
                logger.info("Waiting for pipeline run '%s' to finish, sleep for %ss",
                            self.pipeline_run_name, WAIT_RETRY_SECS)
                time.sleep(WAIT_RETRY_SECS)
            else:
                logger.info("Pipeline run '%s' finished", self.pipeline_run_name)
                break

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

    @property
    def child_references(self):
        data = self.data

        if not data:
            return []

        child_references = data['status'].get('childReferences', [])

        return [child for child in child_references if child['kind'] == 'TaskRun']

    @property
    def pipeline_results(self) -> Dict[str, any]:
        """
        Fetch the pipelineResults for this build.

        Converts the results array to a dict of {name: <JSON-decoded value>} and filters out
        results with null values.
        """
        data = self.data
        if not data:
            return {}

        def load_result(result: Dict[str, str]) -> Tuple[str, Any]:
            name = result['name']
            raw_value = result['value']
            try:
                value = json.loads(raw_value)
            # TypeError is returned when value is list
            except (json.JSONDecodeError, TypeError):
                logger.info("pipeline result '%s' is not json '%s'", name, raw_value)
                value = raw_value
            return name, value

        pipeline_results = data['status'].get('pipelineResults', [])

        return {
            name: value for name, value in map(load_result, pipeline_results) if value is not None
        }

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
            # failed because connection or timeout and pipeline was removed
            if not pipeline_run and not self.data:
                logger.info("Pipeline run '%s' does not exist", self.pipeline_run_name)
                return

            try:
                status = pipeline_run['status']['conditions'][0]['status']
                reason = pipeline_run['status']['conditions'][0]['reason']
            except KeyError:
                logger.debug(
                    "Pipeline run '%s' does not have any status yet",
                    self.pipeline_run_name)
                continue
            # pipeline run finished successfully or failed, or is still running
            if status in ['True', 'False'] or (status == 'Unknown' and reason == 'Running'):
                logger.info("Pipeline run '%s' started", self.pipeline_run_name)
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
        watched_task_runs = set()
        for pipeline_run in self.os.watch_resource(
                self.api_path,
                self.api_version,
                resource_type="pipelineruns",
                resource_name=self.pipeline_run_name,
        ):
            # failed because connection or timeout and pipeline was removed
            if not pipeline_run and not self.data:
                logger.info("Pipeline run '%s' does not exist", self.pipeline_run_name)
                return []

            try:
                child_references = self.data['status']['childReferences']
            except KeyError:
                logger.debug(
                    "Pipeline run '%s' does not have any task runs yet",
                    self.pipeline_run_name)
                continue
            current_task_runs = []

            for task_run in child_references:
                if task_run['kind'] != 'TaskRun':
                    continue
                task_run_name = task_run['name']
                task_info = TaskRun(os=self.os, task_run_name=task_run_name).get_info()
                task_name = task_info['metadata']['labels']['tekton.dev/pipelineTask']

                if task_run_name not in watched_task_runs:
                    watched_task_runs.add(task_run_name)
                    current_task_runs.append((task_name, task_run_name))

            yield current_task_runs

            try:
                status = pipeline_run['status']['conditions'][0]['status']
            except KeyError:
                logger.warning("Pipeline run '%s' does not have any status", self.pipeline_run_name)
                return
            # pipeline run finished successfully or failed
            if status in ['True', 'False']:
                return

    def _get_logs(self):
        logs = {}
        pipeline_run = self.data

        if not pipeline_run:
            return None

        for task_run in self.child_references:

            task_run_object = TaskRun(os=self.os, task_run_name=task_run['name'])
            task_info = task_run_object.get_info()
            pipeline_task_name = task_info['metadata']['labels']['tekton.dev/pipelineTask']

            logs[pipeline_task_name] = task_run_object.get_logs()

        return logs

    def _get_logs_stream(self):
        self.wait_for_start()
        streaming_task_runs = {}
        for task_runs in self.wait_for_taskruns():
            for pipeline_task_name, task_run_name in task_runs:
                streaming_task_runs[pipeline_task_name] = (TaskRun(os=self.os,
                                                                   task_run_name=task_run_name)
                                                           .get_logs(follow=True, wait=True))
            while streaming_task_runs:
                tasks = list(streaming_task_runs.items())
                for pipeline_task_name, task_run in tasks:
                    try:
                        if not task_run:
                            del streaming_task_runs[pipeline_task_name]
                            continue

                        yield pipeline_task_name, next(task_run)
                    except StopIteration:
                        del streaming_task_runs[pipeline_task_name]

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
        response = self.os.get(url)
        return check_response_json(response, 'get_info')

    def get_logs(self, follow=False, wait=False):
        if follow or wait:
            task_run = self.wait_for_start()
        else:
            task_run = self.get_info()

        if not task_run and not self.get_info():
            return

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
            # failed because connection or timeout and task was removed
            if not task_run and not self.get_info():
                logger.info("Task run '%s' does not exist", self.task_run_name)
                return

            try:
                status = task_run['status']['conditions'][0]['status']
                reason = task_run['status']['conditions'][0]['reason']
            except KeyError:
                logger.debug("Task run '%s' does not have any status yet", self.task_run_name)
                continue
            # task run finished successfully or failed
            if status in ['True', 'False'] or (status == 'Unknown' and reason == 'Running'):
                logger.info("Task run '%s' started", self.task_run_name)
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
        response = self.os.get(url)
        return check_response_json(response, 'get_info')

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
        pod = self.wait_for_start()

        if not pod and not self.get_info():
            return

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
                if (not isinstance(exc.cause, requests.ConnectionError) and
                        not isinstance(exc.cause, requests.Timeout)):
                    raise
            except requests.exceptions.ConnectionError:
                pass
            except requests.exceptions.Timeout:
                pass

            idle = time.time() - connected
            logger.debug("Connection closed after %ds", idle)
            if idle < min_idle_timeout:
                # Finish output
                return

            since = int(idle - 1)
            logger.debug("Fetching logs starting from %ds ago", since)
            kwargs['sinceSeconds'] = since

    def wait_for_start(self):
        logger.info("Waiting for pod to start '%s'", self.pod_name)
        for pod in self.os.watch_resource(
                self.api_path, self.api_version, resource_type="pods", resource_name=self.pod_name
        ):
            # failed because connection or timeout and pod was removed
            if not pod and not self.get_info():
                logger.info("Pod '%s' does not exist", self.pod_name)
                return

            try:
                status = pod['status']['phase']
            except KeyError:
                logger.debug("Pod '%s' does not have any status yet", self.pod_name)
                continue
            if status in ['Running', 'Succeeded', 'Failed']:
                logger.info("Pod '%s' started", self.pod_name)
                return pod
            else:
                # unknown or pending
                logger.debug("Waiting for pod, current state: %s", status)
