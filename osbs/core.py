"""
Copyright (c) 2015, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import, division
import json
import os
import time
import base64

import logging
from osbs.kerberos_ccache import kerberos_ccache_init
from osbs.build.build_response import BuildResponse
from osbs.constants import (DEFAULT_NAMESPACE, BUILD_FINISHED_STATES, BUILD_RUNNING_STATES,
                            SERVICEACCOUNT_SECRET, SERVICEACCOUNT_TOKEN,
                            SERVICEACCOUNT_CACRT)
from osbs.exceptions import (OsbsResponseException, OsbsException,
                             OsbsWatchBuildNotFound, OsbsAuthException)
from osbs.utils import retry_on_conflict, retry_on_not_found

import requests
from requests.utils import guess_json_utf

from six.moves import http_client
from six.moves.urllib.parse import urljoin, urlencode, urlparse, parse_qs

from osbs.osbs_http import HttpSession


logger = logging.getLogger(__name__)


# Retry each connection attempt after 30 seconds, for a maximum of 10 times
WATCH_RETRY_SECS = 30
WATCH_RETRY = 10
MAX_BAD_RESPONSES = WATCH_RETRY // 3
# Give up after 12 hours
WAIT_RETRY_HOURS = 12
WAIT_RETRY = WAIT_RETRY_HOURS * 3600 // (WATCH_RETRY_SECS * WATCH_RETRY)

OCP_BUILD_API_V1 = "build.openshift.io/v1"
OCP_IMAGE_API_V1 = "image.openshift.io/v1"
OCP_USER_API_V1 = "user.openshift.io/v1"

OCP_RESOURCE_API_VERSION_MAP = {
    'builds': OCP_BUILD_API_V1,
    'imagestreams': OCP_IMAGE_API_V1,
}


def check_response(response, log_level=logging.ERROR):
    if response.status_code not in (http_client.OK, http_client.CREATED):
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

    def _build_url(self, api_version, url, _prepend_namespace=True, **query):
        if _prepend_namespace:
            url = "namespaces/%s/%s" % (self.namespace, url)
        if query:
            url += ("?" + urlencode(query))
        url = "{}/{}".format(api_version, url)
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
        url = self._build_url(
            OCP_USER_API_V1,
            "users/%s/" % username,
            _prepend_namespace=False
        )
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
        url = self._build_url(OCP_BUILD_API_V1, "builds/")
        logger.debug(build_json)
        return self._post(url, data=json.dumps(build_json),
                          headers={"Content-Type": "application/json"})

    def cancel_build(self, build_id):
        response = self.get_build(build_id)
        br = BuildResponse(response.json())
        br.cancelled = True
        url = self._build_url(OCP_BUILD_API_V1, "builds/%s/" % build_id)
        return self._put(url, data=json.dumps(br.json),
                         headers={"Content-Type": "application/json"})

    def list_pods(self, label=None):
        kwargs = {}
        if label is not None:
            kwargs['labelSelector'] = label
        url = self._build_k8s_url("pods/", **kwargs)
        return self._get(url)

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
        while True:
            connected = time.time()
            buildlogs_url = self._build_url(
                OCP_BUILD_API_V1,
                "builds/%s/log/" % build_id,
                **kwargs
            )
            try:
                response = self._get(buildlogs_url, stream=1,
                                     headers={'Connection': 'close'})
                check_response(response)

                for line in response.iter_lines():
                    connected = time.time()
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

        buildlogs_url = self._build_url(
            OCP_BUILD_API_V1,
            "builds/%s/log/" % build_id
        )
        response = self._get(buildlogs_url, headers={'Connection': 'close'})
        check_response(response)
        return response.content

    def list_builds(self, koji_task_id=None, field_selector=None, labels=None):
        """
        List builds matching criteria

        :param koji_task_id: str, only list builds for Koji Task ID
        :param field_selector: str, field selector for query
        :return: HttpResponse
        """
        query = {}
        selector = '{key}={value}'

        label = {}
        if labels is not None:
            label.update(labels)

        if koji_task_id is not None:
            label['koji-task-id'] = str(koji_task_id)

        if label:
            query['labelSelector'] = ','.join([selector.format(key=key,
                                                               value=value)
                                               for key, value in label.items()])

        if field_selector is not None:
            query['fieldSelector'] = field_selector
        url = self._build_url(
            OCP_BUILD_API_V1,
            "builds/",
            **query
        )
        return self._get(url)

    def get_build(self, build_id):
        """

        :return:
        """
        url = self._build_url(
            OCP_BUILD_API_V1,
            "builds/%s/" % build_id
        )
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
        """
        Generator function which yields tuples of (change_type, object)
        where:

        - change_type is one of:
          - 'modified', the object was modified
          - 'deleted', the object was deleted
          - None, a fresh version of the object was retrieved using
            GET (only when resource_name is provided)

        - object is the latest version of the object
        """
        def log_and_sleep():
            logger.debug("connection closed, reconnecting in %ds", WATCH_RETRY_SECS)
            time.sleep(WATCH_RETRY_SECS)

        watch_path = "watch/namespaces/%s/%s/" % (self.namespace, resource_type)
        if resource_name is not None:
            watch_path += "%s/" % resource_name
        api_ver = OCP_RESOURCE_API_VERSION_MAP[resource_type]
        watch_url = self._build_url(
            api_ver, watch_path, _prepend_namespace=False, **request_args
        )

        get_url = None
        if resource_name is not None:
            get_url = self._build_url(api_ver,
                                      "%s/%s" % (resource_type,
                                                 resource_name))

        bad_responses = 0
        for _ in range(WATCH_RETRY):
            logger.debug("watching for updates")
            try:
                response = self._get(watch_url, stream=True,
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

            encoding = None

            # Avoid races. We've already asked the server to tell us
            # about changes to the object, but now ask for a fresh
            # copy of the object as well. This is to catch the
            # situation where the object changed before the call to
            # this method, or in between retries in this method.
            if get_url is not None:
                logger.debug("retrieving fresh version of object")
                fresh_response = self._get(get_url)
                check_response(fresh_response)
                yield None, fresh_response.json()

            for line in response.iter_lines():
                logger.debug('%r', line)

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
        api_ver = OCP_RESOURCE_API_VERSION_MAP[collection]
        url = self._build_url(api_ver, "%s/%s" % (collection, name))
        response = self._get(url)
        logger.debug("before modification: %r", response.content)
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
        url = self._build_url(
            OCP_IMAGE_API_V1,
            "imagestreamtags/%s" % tag_id
        )
        response = self._get(url)
        check_response(response, log_level=logging.DEBUG)
        return response

    @retry_on_not_found
    def get_image_stream_tag_with_retry(self, tag_id):
        url = self._build_url(
            OCP_IMAGE_API_V1,
            "imagestreamtags/%s" % tag_id
        )
        response = self._get(url)
        check_response(response, log_level=logging.DEBUG)
        return response

    def get_image_stream(self, stream_id):
        url = self._build_url(
            OCP_IMAGE_API_V1,
            "imagestreams/%s" % stream_id
        )
        response = self._get(url)
        check_response(response, log_level=logging.DEBUG)
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
    o = Openshift(openshift_api_url="https://localhost:8443/apis/",
                  openshift_oauth_url="https://localhost:8443/oauth/authorize",
                  verbose=True)
    print(o.get_oauth_token())
