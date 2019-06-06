"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import, division
import json
import os
import numbers
import time
import base64

import logging
from osbs.kerberos_ccache import kerberos_ccache_init
from osbs.build.build_response import BuildResponse
from osbs.constants import (DEFAULT_NAMESPACE, BUILD_FINISHED_STATES, BUILD_RUNNING_STATES,
                            WATCH_MODIFIED, WATCH_DELETED,
                            SERVICEACCOUNT_SECRET, SERVICEACCOUNT_TOKEN,
                            SERVICEACCOUNT_CACRT, ANNOTATION_SOURCE_REPO,
                            ANNOTATION_INSECURE_REPO)
from osbs.exceptions import (OsbsResponseException, OsbsException,
                             OsbsWatchBuildNotFound, OsbsAuthException,
                             ImportImageFailed, ImportImageFailedServerError)
from osbs.utils import graceful_chain_get, retry_on_conflict, retry_on_exception

import requests
from requests.utils import guess_json_utf

from six.moves import http_client
from six.moves.urllib.parse import urljoin, urlencode, urlparse, parse_qs

from .http import HttpSession


logger = logging.getLogger(__name__)


# Retry each connection attempt after 30 seconds, for a maximum of 10 times
WATCH_RETRY_SECS = 30
WATCH_RETRY = 10
MAX_BAD_RESPONSES = WATCH_RETRY // 3
# Give up after 12 hours
WAIT_RETRY_HOURS = 12
WAIT_RETRY = WAIT_RETRY_HOURS * 3600 // (WATCH_RETRY_SECS * WATCH_RETRY)


def check_response(response, log_level=logging.ERROR):
    if response.status_code not in (http_client.OK, http_client.CREATED):
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = b''.join(response.iter_lines())

        logger.log(log_level, "[%d] %s", response.status_code, content)
        raise OsbsResponseException(message=content, status_code=response.status_code)


# TODO: error handling: create function which handles errors in response object
class Openshift(object):
    def __init__(self, openshift_api_url, openshift_api_version, openshift_oauth_url,
                 k8s_api_url=None,
                 verbose=False, username=None, password=None, use_kerberos=False,
                 kerberos_keytab=None, kerberos_principal=None, kerberos_ccache=None,
                 client_cert=None, client_key=None, verify_ssl=True, use_auth=None,
                 token=None, namespace=DEFAULT_NAMESPACE):
        self.os_api_url = openshift_api_url
        self.k8s_api_url = k8s_api_url
        self._os_api_version = openshift_api_version
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

    def _build_url(self, url, _prepend_namespace=True, **query):
        if _prepend_namespace:
            url = "namespaces/%s/%s" % (self.namespace, url)
        if query:
            url += ("?" + urlencode(query))
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

    def _post(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.post(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def _get(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.get(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def _put(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.put(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def _delete(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.delete(
            url, headers=headers, verify_ssl=self.verify_ssl,
            retries_enabled=self.retries_enabled, **kwargs)

    def get_oauth_token(self):
        url = self.os_oauth_url + "?response_type=token&client_id=openshift-challenging-client"
        if self.use_auth:
            if self.username and self.password:
                logger.debug("using basic authentication")
                r = self._get(url, with_auth=False, allow_redirects=False,
                              username=self.username, password=self.password)
            elif self.use_kerberos:
                logger.debug("using kerberos authentication")

                if self.kerberos_keytab:
                    if not self.kerberos_principal:
                        raise OsbsAuthException("You need to provide kerberos principal along "
                                                "with the keytab path.")
                    kerberos_ccache_init(self.kerberos_principal, self.kerberos_keytab,
                                         ccache_file=self.kerberos_ccache)

                r = self._get(url, with_auth=False, allow_redirects=False, kerberos_auth=True)
            else:
                logger.debug("using identity authentication")
                r = self._get(url, with_auth=False, allow_redirects=False)
        else:
            logger.debug("getting token without any authentication (fingers crossed)")
            r = self._get(url, with_auth=False, allow_redirects=False)

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

    def get_user(self, username="~"):
        """
        get info about user (if no user specified, use the one initiating request)

        :param username: str, name of user to get info about, default="~"
        :return: dict
        """
        url = self._build_url("users/%s/" % username, _prepend_namespace=False)
        response = self._get(url)
        check_response(response)
        return response

    def get_serviceaccount_tokens(self, username="~"):
        result = {}

        url = self._build_k8s_url("serviceaccounts/%s/" % username, _prepend_namespace=True)
        response = self._get(url)
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
            response = self._get(url)
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

    def create_build(self, build_json):
        """
        :return:
        """
        url = self._build_url("builds/")
        logger.debug(build_json)
        return self._post(url, data=json.dumps(build_json),
                          headers={"Content-Type": "application/json"})

    def cancel_build(self, build_id):
        response = self.get_build(build_id)
        br = BuildResponse(response.json())
        br.cancelled = True
        url = self._build_url("builds/%s/" % build_id)
        return self._put(url, data=json.dumps(br.json),
                         headers={"Content-Type": "application/json"})

    def list_pods(self, label=None):
        kwargs = {}
        if label is not None:
            kwargs['labelSelector'] = label
        url = self._build_k8s_url("pods/", **kwargs)
        return self._get(url)

    def get_build_config(self, build_config_id):
        url = self._build_url("buildconfigs/%s/" % build_config_id)
        response = self._get(url)
        build_config = response.json()
        return build_config

    def get_all_build_configs_by_labels(self, label_selectors):
        """
        Returns all builds matching a given set of label selectors. It is up to the
        calling function to filter the results.
        """
        labels = ['%s=%s' % (field, value) for field, value in label_selectors]
        labels = ','.join(labels)
        url = self._build_url("buildconfigs/", labelSelector=labels)
        return self._get(url).json()['items']

    def get_build_config_by_labels(self, label_selectors):
        """
        Returns a build config matching the given label
        selectors. This method will raise OsbsException
        if not exactly one build config is found.
        """
        items = self.get_all_build_configs_by_labels(label_selectors)

        if not items:
            raise OsbsException(
                "Build config not found for labels: %r" %
                (label_selectors, ))
        if len(items) > 1:
            raise OsbsException(
                "More than one build config found for labels: %r" %
                (label_selectors, ))

        return items[0]

    def get_build_config_by_labels_filtered(self, label_selectors, filter_key, filter_value):
        """
        Returns a build config matching the given label selectors, filtering against
        another predetermined value. This method will raise OsbsException
        if not exactly one build config is found after filtering.
        """
        items = self.get_all_build_configs_by_labels(label_selectors)

        if filter_value is not None:
            build_configs = []
            for build_config in items:
                match_value = graceful_chain_get(build_config, *filter_key.split('.'))
                if filter_value == match_value:
                    build_configs.append(build_config)
            items = build_configs

        if not items:
            raise OsbsException(
                "Build config not found for labels: %r" %
                (label_selectors, ))
        if len(items) > 1:
            raise OsbsException(
                "More than one build config found for labels: %r" %
                (label_selectors, ))
        return items[0]

    def create_build_config(self, build_config_json):
        """
        :return:
        """
        url = self._build_url("buildconfigs/")
        return self._post(url, data=build_config_json,
                          headers={"Content-Type": "application/json"})

    def update_build_config(self, build_config_id, build_config_json):
        url = self._build_url("buildconfigs/%s" % build_config_id)
        response = self._put(url, data=build_config_json,
                             headers={"Content-Type": "application/json"})
        check_response(response)
        return response

    def instantiate_build_config(self, build_config_id):
        url = self._build_url("buildconfigs/%s/instantiate" % build_config_id)
        data = json.dumps({
            "kind": "BuildRequest",
            "apiVersion": self._os_api_version,
            "metadata": {
                "name": build_config_id,
            },
        })
        return self._post(url, data=data,
                          headers={"Content-Type": "application/json"})

    def start_build(self, build_config_id):
        """
        :return:
        """
        return self.instantiate_build_config(build_config_id)

    def wait_for_new_build_config_instance(self, build_config_id, prev_version):
        logger.info("waiting for build config %s to get instantiated", build_config_id)
        for changetype, obj in self.watch_resource("buildconfigs", build_config_id):
            if changetype == WATCH_MODIFIED:
                version = graceful_chain_get(obj, 'status', 'lastVersion')
                if not isinstance(version, numbers.Integral):
                    logger.error("BuildConfig %s has unexpected lastVersion: %s", build_config_id,
                                 version)
                    continue

                if version > prev_version:
                    return "%s-%s" % (build_config_id, version)

            if changetype == WATCH_DELETED:
                logger.error("BuildConfig deleted while waiting for new build instance")
                break

        raise OsbsResponseException("New BuildConfig instance not found",
                                    http_client.NOT_FOUND)

    def stream_logs(self, build_id):
        """
        stream logs from build

        :param build_id: str
        :return: iterator
        """
        kwargs = {'follow': 1}

        # If connection is closed within this many seconds, give up:
        min_idle_timeout = 60

        # Stream logs, but be careful of the connection closing
        # due to idle timeout. In that case, try again until the
        # call returns more quickly than a reasonable timeout
        # would be set to.
        last_activity = time.time()
        while True:
            buildlogs_url = self._build_url("builds/%s/log/" % build_id,
                                            **kwargs)
            try:
                response = self._get(buildlogs_url, stream=1,
                                     headers={'Connection': 'close'})
                check_response(response)

                for line in response.iter_lines():
                    last_activity = time.time()
                    yield line
            # NOTE1: If self._get causes ChunkedEncodingError, ConnectionError,
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

            idle = time.time() - last_activity
            logger.debug("connection closed after %ds", idle)
            if idle < min_idle_timeout:
                # Finish output
                return

            since = int(idle - 1)
            logger.debug("fetching logs starting from %ds ago", since)
            kwargs['sinceSeconds'] = since

    def logs(self, build_id, follow=False, build_json=None, wait_if_missing=False):
        """
        provide logs from build

        :param build_id: str
        :param follow: bool, fetch logs as they come?
        :param build_json: dict, to save one get-build query
        :param wait_if_missing: bool, if build doesn't exist, wait
        :return: None, str or iterator
        """
        # does build exist?
        try:
            build_json = build_json or self.get_build(build_id).json()
        except OsbsResponseException as ex:
            if ex.status_code == 404:
                if not wait_if_missing:
                    raise OsbsException("Build '%s' doesn't exist." % build_id)
            else:
                raise

        if follow or wait_if_missing:
            build_json = self.wait_for_build_to_get_scheduled(build_id)

        br = BuildResponse(build_json)

        # When build is in new or pending state, openshift responds with 500
        if br.is_pending():
            return

        if follow:
            return self.stream_logs(build_id)

        buildlogs_url = self._build_url("builds/%s/log/" % build_id)
        response = self._get(buildlogs_url, headers={'Connection': 'close'})
        check_response(response)
        return response.content

    def list_builds(self, build_config_id=None, koji_task_id=None,
                    field_selector=None, labels=None):
        """
        List builds matching criteria

        :param build_config_id: str, only list builds created from BuildConfig
        :param koji_task_id: str, only list builds for Koji Task ID
        :param field_selector: str, field selector for query
        :return: HttpResponse
        """
        query = {}
        selector = '{key}={value}'

        label = {}
        if labels is not None:
            label.update(labels)

        if build_config_id is not None:
            label['buildconfig'] = build_config_id

        if koji_task_id is not None:
            label['koji-task-id'] = str(koji_task_id)

        if label:
            query['labelSelector'] = ','.join([selector.format(key=key,
                                                               value=value)
                                               for key, value in label.items()])

        if field_selector is not None:
            query['fieldSelector'] = field_selector
        url = self._build_url("builds/", **query)
        return self._get(url)

    def get_build(self, build_id):
        """

        :return:
        """
        url = self._build_url("builds/%s/" % build_id)
        response = self._get(url)
        check_response(response)
        return response

    def list_resource_quotas(self):
        url = self._build_k8s_url("resourcequotas/")
        response = self._get(url)
        check_response(response)
        return response

    def get_resource_quota(self, quota_name):
        url = self._build_k8s_url("resourcequotas/%s" % quota_name)
        response = self._get(url)
        check_response(response)
        return response

    def create_resource_quota(self, name, quota_json):
        """
        Prevent builds being scheduled and wait for running builds to finish.

        :return:
        """

        url = self._build_k8s_url("resourcequotas/")
        response = self._post(url, data=json.dumps(quota_json),
                              headers={"Content-Type": "application/json"})
        if response.status_code == http_client.CONFLICT:
            url = self._build_k8s_url("resourcequotas/%s" % name)
            response = self._put(url, data=json.dumps(quota_json),
                                 headers={"Content-Type": "application/json"})

        check_response(response)
        return response

    def delete_resource_quota(self, name):
        url = self._build_k8s_url("resourcequotas/%s" % name)
        response = self._delete(url)
        if response.status_code != http_client.NOT_FOUND:
            check_response(response)

        return response

    def watch_resource(self, resource_type, resource_name=None, **request_args):
        def log_and_sleep():
            logger.debug("connection closed, reconnecting in %ds", WATCH_RETRY_SECS)
            time.sleep(WATCH_RETRY_SECS)

        path = "watch/namespaces/%s/%s/" % (self.namespace, resource_type)
        if resource_name is not None:
            path += "%s/" % resource_name
        url = self._build_url(path, _prepend_namespace=False, **request_args)

        bad_responses = 0
        for _ in range(WATCH_RETRY):
            try:
                response = self._get(url, stream=True, headers={'Connection': 'close'})
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

            encoding = None
            for line in response.iter_lines():
                logger.debug(line)

                if not encoding:
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

                yield (j['type'].lower(), j['object'])

            log_and_sleep()

    def wait(self, build_id, states):
        """
        :param build_id: wait for build to finish

        :return:
        """
        logger.info("watching build '%s'", build_id)
        for changetype, obj in self.watch_resource("builds", build_id):
            try:
                obj_name = obj["metadata"]["name"]
            except KeyError:
                logger.error("'object' doesn't have any name")
                continue
            try:
                obj_status = obj["status"]["phase"]
            except KeyError:
                logger.error("'object' doesn't have any status")
                continue
            else:
                obj_status_lower = obj_status.lower()
            logger.info("object has changed: '%s', status: '%s', name: '%s'",
                        changetype, obj_status, obj_name)
            if obj_name == build_id:
                logger.info("matching build found")
                logger.debug("is %s in %s?", repr(obj_status_lower), states)
                if obj_status_lower in states:
                    logger.debug("Yes, build is in the state I'm waiting for.")
                    return obj
                else:
                    logger.debug("No, build is not in the state I'm "
                                 "waiting for.")
            else:
                logger.info("The build %r isn't me %r", obj_name, build_id)

        # I'm not sure how we can end up here since there are two possible scenarios:
        #   1. our object was found and we are returning in the loop
        #   2. our object was not found and we keep waiting (in the loop)
        # Therefore, let's raise here
        logger.warning("build '%s' was not found during wait", build_id)
        raise OsbsWatchBuildNotFound("build '%s' was not found and response stream ended" %
                                     build_id)

    def wait_for_build_to_finish(self, build_id):
        for retry in range(WAIT_RETRY):
            try:
                build_response = self.wait(build_id, BUILD_FINISHED_STATES)
                return build_response
            except OsbsWatchBuildNotFound:
                # this is woraround for https://github.com/openshift/origin/issues/2348
                logger.warning("I'm going to wait again. Retry #%d.", retry)
                continue
        raise OsbsException("Failed to wait for a build: %s" % build_id)

    def wait_for_build_to_get_scheduled(self, build_id):
        for _ in range(WAIT_RETRY):
            try:
                build_response = self.wait(build_id, BUILD_FINISHED_STATES + BUILD_RUNNING_STATES)
                return build_response
            except OsbsWatchBuildNotFound:
                continue
        raise OsbsException('Failed to schedule a build in {} attempts: {}'.format(WAIT_RETRY,
                                                                                   build_id))

    @staticmethod
    def _update_metadata_things(metadata, things, values):
        metadata.setdefault(things, {})
        metadata[things].update(values)

    @staticmethod
    def _replace_metadata_things(metadata, things, values):
        metadata[things] = values

    @retry_on_conflict
    def adjust_attributes_on_object(self, collection, name, things, values, how):
        """
        adjust labels or annotations on object

        labels have to match RE: (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])? and
        have at most 63 chars

        :param collection: str, object collection e.g. 'builds'
        :param name: str, name of object
        :param things: str, 'labels' or 'annotations'
        :param values: dict, values to set
        :param how: callable, how to adjust the values e.g.
                    self._replace_metadata_things
        :return:
        """
        url = self._build_url("%s/%s" % (collection, name))
        response = self._get(url)
        logger.debug("before modification: %s", response.content)
        build_json = response.json()
        how(build_json['metadata'], things, values)
        response = self._put(url, data=json.dumps(build_json), use_json=True)
        check_response(response)
        return response

    def update_labels_on_build(self, build_id, labels):
        return self.adjust_attributes_on_object('builds', build_id,
                                                'labels', labels,
                                                self._update_metadata_things)

    def set_labels_on_build(self, build_id, labels):
        return self.adjust_attributes_on_object('builds', build_id,
                                                'labels', labels,
                                                self._replace_metadata_things)

    def update_labels_on_build_config(self, build_config_id, labels):
        return self.adjust_attributes_on_object('buildconfigs', build_config_id,
                                                'labels', labels,
                                                self._update_metadata_things)

    def set_labels_on_build_config(self, build_config_id, labels):
        return self.adjust_attributes_on_object('buildconfigs', build_config_id,
                                                'labels', labels,
                                                self._replace_metadata_things)

    def update_annotations_on_build(self, build_id, annotations):
        """
        set annotations on build object

        :param build_id: str, id of build
        :param annotations: dict, annotations to set
        :return:
        """
        return self.adjust_attributes_on_object('builds', build_id,
                                                'annotations', annotations,
                                                self._update_metadata_things)

    def set_annotations_on_build(self, build_id, annotations):
        return self.adjust_attributes_on_object('builds', build_id,
                                                'annotations', annotations,
                                                self._replace_metadata_things)

    def get_image_stream_tag(self, tag_id):
        url = self._build_url("imagestreamtags/%s" % tag_id)
        response = self._get(url)
        check_response(response, log_level=logging.DEBUG)
        return response

    def put_image_stream_tag(self, tag_id, tag):
        url = self._build_url("imagestreamtags/%s" % tag_id)
        response = self._put(url, data=json.dumps(tag),
                             headers={"Content-Type": "application/json"})
        check_response(response)
        return response

    @retry_on_conflict
    def ensure_image_stream_tag(self, stream, tag_name, tag_template,
                                scheduled=False, repository=None, insecure=False):
        stream_id = stream['metadata']['name']

        if not repository:
            insecure = (stream['metadata'].get('annotations', {})
                        .get(ANNOTATION_INSECURE_REPO) == 'true')

            repository = stream['metadata'].get('annotations', {}).get(ANNOTATION_SOURCE_REPO)
            # ImageStream may not have been updated with new annotation, fallback
            # to fetching repo from dockerImageRepository
            if not repository:
                repository = stream['spec']['dockerImageRepository']

        tag_id = '{}:{}'.format(stream_id, tag_name)

        changed = False
        try:
            tag = self.get_image_stream_tag(tag_id).json()
            logger.debug('image stream tag found: %s', tag_id)
        except OsbsResponseException as exc:
            if exc.status_code != 404:
                raise

            logger.debug('image stream tag NOT found: %s', tag_id)

            tag = tag_template
            tag['metadata']['name'] = tag_id
            tag['tag']['name'] = tag_name
            tag['tag']['from']['name'] = '{}:{}'.format(repository, tag_name)
            changed = True

        if insecure != tag['tag']['importPolicy'].get('insecure', False):
            tag['tag']['importPolicy']['insecure'] = insecure
            logger.debug('setting importPolicy.insecure to: %s', insecure)
            changed = True

        if scheduled != tag['tag']['importPolicy'].get('scheduled', False):
            tag['tag']['importPolicy']['scheduled'] = scheduled
            logger.debug('setting importPolicy.scheduled to: %s', scheduled)
            changed = True

        if changed:
            logger.debug('modifying image stream tag: %s', tag_id)
            self.put_image_stream_tag(tag_id, tag)

        return changed

    def get_image_stream(self, stream_id):
        url = self._build_url("imagestreams/%s" % stream_id)
        response = self._get(url)
        check_response(response, log_level=logging.DEBUG)
        return response

    def create_image_stream(self, stream_json):
        url = self._build_url("imagestreams/")
        response = self._post(url, data=stream_json,
                              headers={"Content-Type": "application/json"})
        check_response(response)
        return response

    def update_image_stream(self, stream_id, stream_json):
        url = self._build_url("imagestreams/%s" % stream_id)
        response = self._put(url, data=json.dumps(stream_json),
                             use_json=True)
        check_response(response)
        return response

    @retry_on_conflict
    @retry_on_exception(ImportImageFailedServerError)
    def import_image(self, name, stream_import, tags=None):
        """
        Import image tags from a Docker registry into an ImageStream

        :return: bool, whether tags were imported
        """

        # Get the JSON for the ImageStream
        imagestream_json = self.get_image_stream(name).json()
        logger.debug("imagestream: %r", imagestream_json)

        if 'dockerImageRepository' in imagestream_json.get('spec', {}):
            logger.debug("Removing 'dockerImageRepository' from ImageStream %s", name)
            source_repo = imagestream_json['spec'].pop('dockerImageRepository')
            imagestream_json['metadata']['annotations'][ANNOTATION_SOURCE_REPO] = source_repo
            imagestream_json = self.update_image_stream(name, imagestream_json).json()

        # Note the tags before import
        oldtags = imagestream_json.get('status', {}).get('tags', [])
        logger.debug("tags before import: %r", oldtags)

        stream_import['metadata']['name'] = name
        stream_import['spec']['images'] = []
        tags_set = set(tags) if tags else set()
        for tag in imagestream_json.get('spec', {}).get('tags', []):
            if tags_set and tag['name'] not in tags_set:
                continue

            image_import = {
                'from': tag['from'],
                'to': {'name': tag['name']},
                'importPolicy': tag.get('importPolicy'),
                'referencePolicy': tag.get('referencePolicy'),
            }
            stream_import['spec']['images'].append(image_import)

        if not stream_import['spec']['images']:
            logger.debug('No tags to import')
            return False

        import_url = self._build_url("imagestreamimports/")
        import_response = self._post(import_url, data=json.dumps(stream_import),
                                     use_json=True)
        self._check_import_image_response(import_response)

        new_tags = [
            image['tag']
            for image in import_response.json().get('status', {}).get('images', [])]
        logger.debug("tags after import: %r", new_tags)

        return True

    @retry_on_conflict
    @retry_on_exception(ImportImageFailedServerError)
    def import_image_tags(self, name, stream_import, tags, repository, insecure):
        """
        Import image tags from a Docker registry into an ImageStream

        :return: bool, whether tags were imported
        """

        # Get the JSON for the ImageStream
        imagestream_json = self.get_image_stream(name).json()
        logger.debug("imagestream: %r", imagestream_json)
        changed = False

        # existence of dockerImageRepository is limiting how many tags are updated
        if 'dockerImageRepository' in imagestream_json.get('spec', {}):
            logger.debug("Removing 'dockerImageRepository' from ImageStream %s", name)
            imagestream_json['spec'].pop('dockerImageRepository')
            changed = True
        all_annotations = imagestream_json.get('metadata', {}).get('annotations', {})
        # remove annotations about registry, since method will get them as arguments
        for annotation in ANNOTATION_SOURCE_REPO, ANNOTATION_INSECURE_REPO:
            if annotation in all_annotations:
                imagestream_json['metadata']['annotations'].pop(annotation)
                changed = True

        if changed:
            imagestream_json = self.update_image_stream(name, imagestream_json).json()

        # Note the tags before import
        oldtags = imagestream_json.get('status', {}).get('tags', [])
        logger.debug("tags before import: %r", oldtags)

        stream_import['metadata']['name'] = name
        stream_import['spec']['images'] = []
        tags_set = set(tags) if tags else set()

        if not tags_set:
            logger.debug('No tags to import')
            return False

        for tag in tags_set:
            image_import = {
                'from': {"kind": "DockerImage",
                         "name": '{}:{}'.format(repository, tag)},
                'to': {'name': tag},
                'importPolicy': {'insecure': insecure},
                # referencePolicy will default to "type: source"
                # so we don't have to explicitly set it
            }
            stream_import['spec']['images'].append(image_import)

        import_url = self._build_url("imagestreamimports/")
        import_response = self._post(import_url, data=json.dumps(stream_import),
                                     use_json=True)
        self._check_import_image_response(import_response)

        new_tags = [
            image['tag']
            for image in import_response.json().get('status', {}).get('images', [])]
        logger.debug("tags after import: %r", new_tags)

        return True

    def _check_import_image_response(self, import_response):
        check_response(import_response)

        failed_images = []
        failed_images_server_error = False

        for image in import_response.json()['status']['images']:
            if image['status']['status'] == 'Success':
                continue
            logger.error('Error importing image %s: %r', image['tag'], image)
            failed_images.append(image)
            if image['status']['code'] == requests.codes.server_error:
                failed_images_server_error = True

        if failed_images:
            error_msg = 'Failed to import {} image(s)'.format(len(failed_images))
            logger.error(error_msg)
            if failed_images_server_error:
                raise ImportImageFailedServerError(error_msg)
            else:
                raise ImportImageFailed(error_msg)

    def dump_resource(self, resource_type):
        url = self._build_url("%s" % resource_type)
        response = self._get(url)
        check_response(response)
        return response

    def restore_resource(self, resource_type, resource):
        url = self._build_url("%s" % resource_type)
        response = self._post(url, data=json.dumps(resource),
                              headers={"Content-Type": "application/json"})
        check_response(response)
        return response

    def create_config_map(self, config_data):
        url = self._build_k8s_url("configmaps/")
        response = self._post(url, data=json.dumps(config_data))
        check_response(response)
        return response

    def get_config_map(self, config_name):
        url = self._build_k8s_url("configmaps/%s" % config_name)
        response = self._get(url)
        check_response(response)
        return response

    def delete_config_map(self, config_name):
        url = self._build_k8s_url("configmaps/%s" % config_name)
        response = self._delete(url, data='{}')
        check_response(response)
        return response


if __name__ == '__main__':
    o = Openshift(openshift_api_url="https://localhost:8443/oapi/v1/",
                  openshift_api_version="v1",
                  openshift_oauth_url="https://localhost:8443/oauth/authorize",
                  verbose=True)
    print(o.get_oauth_token())
