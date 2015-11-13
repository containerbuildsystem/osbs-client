"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import
import json
import os

import logging
from osbs.kerberos_ccache import kerberos_ccache_init
from osbs.build.build_response import BuildResponse
from osbs.constants import DEFAULT_NAMESPACE, BUILD_FINISHED_STATES, BUILD_RUNNING_STATES, BUILD_CANCELLED_STATE
from osbs.constants import WATCH_MODIFIED, WATCH_DELETED, WATCH_ERROR
from osbs.constants import (SERVICEACCOUNT_SECRET, SERVICEACCOUNT_TOKEN,
                            SERVICEACCOUNT_CACRT)
from osbs.exceptions import (OsbsResponseException, OsbsException,
                             OsbsWatchBuildNotFound, OsbsAuthException)

try:
    # py2
    import httplib
    import urlparse
    from urllib import urlencode
except ImportError:
    # py3
    import http.client as httplib
    import urllib.parse as urlparse
    from urllib.parse import urlencode

from .http import HttpSession


logger = logging.getLogger(__name__)


def check_response(response):
    if response.status_code not in (httplib.OK, httplib.CREATED):
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = ''.join(response.iter_lines())

        logger.error("[%d] %s", response.status_code, content)
        raise OsbsResponseException(message=content, status_code=response.status_code)


# TODO: error handling: create function which handles errors in response object
class Openshift(object):
    def __init__(self, openshift_api_url, openshift_api_version, openshift_oauth_url,
                 k8s_api_url=None,
                 verbose=False, username=None, password=None, use_kerberos=False,
                 kerberos_keytab=None, kerberos_principal=None, kerberos_ccache=None,
                 client_cert=None, client_key=None, verify_ssl=True, use_auth=None):
        self.os_api_url = openshift_api_url
        self.k8s_api_url = k8s_api_url
        self._os_api_version = openshift_api_version
        self._os_oauth_url = openshift_oauth_url
        self.verbose = verbose
        self.verify_ssl = verify_ssl
        self._con = HttpSession(verbose=self.verbose)

        # auth stuff
        self.use_kerberos = use_kerberos
        self.username = username
        self.password = password
        self.client_cert = client_cert
        self.client_key = client_key
        self.kerberos_keytab = kerberos_keytab
        self.kerberos_principal = kerberos_principal
        self.kerberos_ccache = kerberos_ccache
        self.token = None
        self.ca = None
        auth_credentials_provided = bool(use_kerberos or
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

    def _build_k8s_url(self, url, **query):
        if query:
            url += ("?" + urlencode(query))
        return urlparse.urljoin(self.k8s_api_url, url)

    def _build_url(self, url, **query):
        if query:
            url += ("?" + urlencode(query))
        return urlparse.urljoin(self.os_api_url, url)

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
        return self._con.post(url, headers=headers, verify_ssl=self.verify_ssl, **kwargs)

    def _get(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.get(url, headers=headers, verify_ssl=self.verify_ssl, **kwargs)

    def _put(self, url, with_auth=True, **kwargs):
        headers, kwargs = self._request_args(with_auth, **kwargs)
        return self._con.put(url, headers=headers, verify_ssl=self.verify_ssl, **kwargs)

    def _delete(self, url, with_auth=True, **kwargs):
        headers, hwargs = self._request_args(with_auth, **kwargs)
        return self._con.delete(url, headers=headers, verify_ssl=self.verify_ssl,
                                **kwargs)

    def get_oauth_token(self):
        url = self.os_oauth_url + "?response_type=token&client_id=openshift-challenging-client"
        if self.use_auth:
            if self.username and self.password:
                logger.info("using basic authentication")
                r = self._get(url, with_auth=False, allow_redirects=False,
                              username=self.username, password=self.password)
            elif self.use_kerberos:
                logger.info("using kerberos authentication")

                if self.kerberos_keytab:
                    if not self.kerberos_principal:
                        raise OsbsAuthException("You need to provide kerberos principal along "
                                                "with the keytab path.")
                    kerberos_ccache_init(self.kerberos_principal, self.kerberos_keytab,
                                         ccache_file=self.kerberos_ccache)

                r = self._get(url, with_auth=False, allow_redirects=False, kerberos_auth=True)
            else:
                logger.info("using identity authentication")
                r = self._get(url, with_auth=False, allow_redirects=False)
        else:
            logger.info("getting token without any authentication (fingers crossed)")
            r = self._get(url, with_auth=False, allow_redirects=False)

        try:
            redir_url = r.headers['location']
        except KeyError:
            logger.error("[%s] 'Location' header is missing in response, cannot retrieve token", r.status_code)
            return ""
        parsed_url = urlparse.urlparse(redir_url)
        fragment = parsed_url.fragment
        logger.debug("fragment is '%s'", fragment)
        parsed_fragment = urlparse.parse_qs(fragment)
        self.token = parsed_fragment[b'access_token'][0]
        return self.token

    def get_user(self, username="~"):
        """
        get info about user (if no user specified, use the one initiating request)

        :param username: str, name of user to get info about, default="~"
        :return: dict
        """
        url = self._build_url("users/%s/" % username)
        response = self._get(url)
        check_response(response)
        return response

    def create_build(self, build_json, namespace=DEFAULT_NAMESPACE):
        """
        :return:
        """
        url = self._build_url("namespaces/%s/builds/" % namespace)
        logger.debug(build_json)
        return self._post(url, data=build_json,
                          headers={"Content-Type": "application/json"})

    def cancel_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        response = self.get_build(build_id, namespace=namespace)
        br = BuildResponse(response)
        br.status = BUILD_CANCELLED_STATE
        url = self._build_url("namespaces/%s/builds/%s/" % (namespace, build_id))
        return self._put(url, data=json.dumps(br.json),
                         headers={"Content-Type": "application/json"})

    def list_pods(self, label=None, namespace=DEFAULT_NAMESPACE):
        kwargs = {}
        if label is not None:
            kwargs['labelSelector'] = label
        url = self._build_k8s_url("namespaces/%s/pods/" % namespace, **kwargs)
        return self._get(url)

    def get_build_config(self, build_config_id, namespace=DEFAULT_NAMESPACE):
        url = self._build_url("namespaces/%s/buildconfigs/%s/" % (namespace, build_config_id))
        response = self._get(url)
        build_config = response.json()
        return build_config

    def create_build_config(self, build_config_json, namespace=DEFAULT_NAMESPACE):
        """
        :return:
        """
        url = self._build_url("namespaces/%s/buildconfigs/" % namespace)
        return self._post(url, data=build_config_json,
                          headers={"Content-Type": "application/json"})

    def update_build_config(self, build_config_id, build_config_json, namespace=DEFAULT_NAMESPACE):
        url = self._build_url("namespaces/%s/buildconfigs/%s" % (namespace, build_config_id))
        response = self._put(url, data=build_config_json,
                             headers={"Content-Type": "application/json"})
        check_response(response)
        return response

    def instantiate_build_config(self, build_config_id, namespace=DEFAULT_NAMESPACE):
        url = self._build_url("namespaces/%s/buildconfigs/%s/instantiate" %
                              (namespace, build_config_id))
        data = json.dumps({
            "kind": "BuildRequest",
            "apiVersion": self._os_api_version,
            "metadata": {
                "name": build_config_id,
            },
        })
        return self._post(url, data=data,
                          headers={"Content-Type": "application/json"})

    def start_build(self, build_config_id, namespace=DEFAULT_NAMESPACE):
        """
        :return:
        """
        return self.instantiate_build_config(build_config_id, namespace=namespace)

    def logs(self, build_id, follow=False, build_json=None, wait_if_missing=False,
             namespace=DEFAULT_NAMESPACE):
        """
        provide logs from build

        :param build_id: str
        :param follow: bool, fetch logs as they come?
        :param build_json: dict, to save one get-build query
        :param wait_if_missing: bool, if build doesn't exist, wait
        :param namespace: str
        :return: None, str or iterator
        """
        # does build exist?
        try:
            build_json = build_json or self.get_build(build_id, namespace=namespace).json()
        except OsbsResponseException as ex:
            if ex.status_code == 404:
                if not wait_if_missing:
                    raise OsbsException("Build '%s' doesn't exist." % build_id)
            else:
                raise

        if follow or wait_if_missing:
            build_json = self.wait_for_build_to_get_scheduled(build_id, namespace=namespace)

        br = BuildResponse(None, build_json=build_json)

        # When build is in new or pending state, openshift responds with 500
        if br.is_pending():
            return

        buildlogs_url = self._build_url("namespaces/%s/builds/%s/log/" % (namespace, build_id),
                                        follow=(1 if follow else 0))
        response = self._get(buildlogs_url, stream=follow, headers={'Connection': 'close'})
        check_response(response)

        if follow:
            return response.iter_lines()
        return response.content

    def list_builds(self, build_config_id=None, namespace=DEFAULT_NAMESPACE):
        """

        :return:
        """
        query = {}
        if build_config_id is not None:
            query['labelSelector'] = '%s=%s' % ('buildconfig', build_config_id)
        url = self._build_url("namespaces/%s/builds/" % namespace, **query)
        return self._get(url)

    def get_build(self, build_id, namespace=DEFAULT_NAMESPACE):
        """

        :return:
        """
        url = self._build_url("namespaces/%s/builds/%s/" % (namespace, build_id))
        response = self._get(url)
        check_response(response)
        return response

    def create_resource_quota(self, name, quota_json,
                              namespace=DEFAULT_NAMESPACE):
        """
        Prevent builds being scheduled and wait for running builds to finish.

        :return:
        """

        url = self._build_k8s_url("namespaces/%s/resourcequotas/" % namespace)
        response = self._post(url, data=json.dumps(quota_json),
                              headers={"Content-Type": "application/json"})
        if response.status_code == httplib.CONFLICT:
            url = self._build_k8s_url("namespaces/%s/resourcequotas/%s" %
                                      (namespace, name))
            response = self._put(url, data=json.dumps(quota_json),
                                 headers={"Content-Type": "application/json"})

        check_response(response)
        return response

    def delete_resource_quota(self, name, namespace=DEFAULT_NAMESPACE):
        url = self._build_k8s_url("namespaces/%s/resourcequotas/%s" %
                                  (namespace, name))
        response = self._delete(url)
        if response.status_code != httplib.NOT_FOUND:
            check_response(response)

        return response

    def wait(self, build_id, states, namespace=DEFAULT_NAMESPACE):
        """
        :param build_id: wait for build to finish

        :return:
        """
        logger.info("watching build '%s'", build_id)
        url = self._build_url("watch/namespaces/%s/builds/%s/" % (namespace, build_id))
        with self._get(url, stream=True, headers={'Connection': 'close'}) as response:
            check_response(response)
            for line in response.iter_lines():
                j = json.loads(line)
                logger.debug(line)
                obj = j.get("object", None)
                if obj is None:
                    logger.error("'object' is None")
                    continue
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
                            j['type'], obj_status, obj_name)
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
        logger.error("build '%s' was not found during wait", build_id)
        check_response(response)
        raise OsbsWatchBuildNotFound("build '%s' was not found and response stream ended" % build_id)

    def wait_for_build_to_finish(self, build_id, namespace=DEFAULT_NAMESPACE):
        for retry in range(1, 10):
            try:
                build_response = self.wait(build_id, BUILD_FINISHED_STATES,
                                           namespace)
                return build_response
            except OsbsWatchBuildNotFound:
                # this is woraround for https://github.com/openshift/origin/issues/2348
                logger.error("I'm going to wait again. Retry #%d.", retry)
                continue
        raise OsbsException("Failed to wait for a build: %s" % build_id)

    def wait_for_build_to_get_scheduled(self, build_id, namespace=DEFAULT_NAMESPACE):
        build_response = self.wait(build_id, BUILD_FINISHED_STATES + BUILD_RUNNING_STATES,
                                   namespace)
        return build_response

    @staticmethod
    def _update_metadata_things(metadata, things, values):
        metadata.setdefault(things, {})
        metadata[things].update(values)

    @staticmethod
    def _replace_metadata_things(metadata, things, values):
        metadata[things] = values

    def adjust_attributes_on_object(self, collection, name, things, values,
                                    how, namespace=DEFAULT_NAMESPACE):
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
        :param namespace: str
        :return:
        """
        url = self._build_url("namespaces/%s/%s/%s" % (namespace, collection,
                                                       name))
        build_json = self._get(url).json()
        how(build_json['metadata'], things, values)
        response = self._put(url, data=json.dumps(build_json), use_json=True)
        check_response(response)
        return response

    def update_labels_on_build(self, build_id, labels,
                               namespace=DEFAULT_NAMESPACE):
        return self.adjust_attributes_on_object('builds', build_id,
                                                'labels', labels,
                                                self._update_metadata_things,
                                                namespace=namespace)

    def set_labels_on_build(self, build_id, labels,
                            namespace=DEFAULT_NAMESPACE):
        return self.adjust_attributes_on_object('builds', build_id,
                                                'labels', labels,
                                                self._replace_metadata_things,
                                                namespace=namespace)

    def update_labels_on_build_config(self, build_config_id, labels,
                                      namespace=DEFAULT_NAMESPACE):
        return self.adjust_attributes_on_object('buildconfigs', build_config_id,
                                                'labels', labels,
                                                self._update_metadata_things,
                                                namespace=namespace)

    def set_labels_on_build_config(self, build_config_id, labels,
                                   namespace=DEFAULT_NAMESPACE):
        return self.adjust_attributes_on_object('buildconfigs', build_config_id,
                                                'labels', labels,
                                                self._replace_metadata_things,
                                                namespace=namespace)

    def update_annotations_on_build(self, build_id, annotations,
                                    namespace=DEFAULT_NAMESPACE):
        """
        set annotations on build object

        :param build_id: str, id of build
        :param annotations: dict, annotations to set
        :param namespace: str
        :return:
        """
        return self.adjust_attributes_on_object('builds', build_id,
                                                'annotations', annotations,
                                                self._update_metadata_things,
                                                namespace=namespace)

    def set_annotations_on_build(self, build_id, annotations,
                                 namespace=DEFAULT_NAMESPACE):
        return self.adjust_attributes_on_object('builds', build_id,
                                                'annotations', annotations,
                                                self._replace_metadata_things,
                                                namespace=namespace)

    def get_image_stream(self, stream_id, namespace=DEFAULT_NAMESPACE):
        url = self._build_url("namespaces/%s/imagestreams/%s" % (namespace, stream_id))
        response = self._get(url)
        check_response(response)
        return response

    def create_image_stream(self, stream_json, namespace=DEFAULT_NAMESPACE):
        url = self._build_url("namespaces/%s/imagestreams/" % namespace)
        response = self._post(url, data=stream_json,
                              headers={"Content-Type": "application/json"})
        return response

    def import_image(self, name, namespace=DEFAULT_NAMESPACE):
        """
        Import image tags from a Docker registry into an ImageStream
        """

        # Get the JSON for the ImageStream
        url = self._build_url("namespaces/%s/imagestreams/%s" % (namespace,
                                                                 name))
        imagestream_json = self._get(url).json()
        logger.debug("imagestream: %r", imagestream_json)
        spec = imagestream_json.get('spec', {})
        if 'dockerImageRepository' not in spec:
            raise OsbsException('No dockerImageRepository for image import')

        # Mark it as needing import
        imagestream_json['metadata'].setdefault('annotations', {})
        check_annotation = "openshift.io/image.dockerRepositoryCheck"
        imagestream_json['metadata']['annotations'][check_annotation] = ''
        response = self._put(url, data=json.dumps(imagestream_json),
                             use_json=True)
        check_response(response)

        # Watch for it to be updated
        resource_version = imagestream_json['metadata']['resourceVersion']
        url = self._build_url("watch/namespaces/%s/imagestreams/%s/" % (namespace, name),
                              resourceVersion=resource_version)
        with self._get(url, stream=True, headers={'Connection': 'close'}) as response:
            check_response(response)
            for line in response.iter_lines():
                j = json.loads(line)
                logger.debug(line)
                if 'object' not in j:
                    logger.error("no 'object'")
                    continue

                if 'type' not in j:
                    logger.error("no 'type'")
                    continue

                changetype = j['type']
                logger.info("Change type: %r", changetype)
                changetype = changetype.lower()
                if changetype == WATCH_DELETED:
                    logger.info("Watched ImageStream was deleted")
                    break

                if changetype == WATCH_ERROR:
                    logger.error("Error watching ImageStream")
                    break

                if changetype == WATCH_MODIFIED:
                    logger.info("ImageStream modified")
                    obj = j['object']
                    metadata = obj.get('metadata', {})
                    annotations = metadata.get('annotations', {})
                    logger.info("ImageStream annotations: %r", annotations)
                    if annotations.get(check_annotation, False):
                        logger.info("ImageStream updated")
                        break


if __name__ == '__main__':
    o = Openshift(openshift_api_url="https://localhost:8443/oapi/v1/",
                  openshift_api_version="v1",
                  openshift_oauth_url="https://localhost:8443/oauth/authorize",
                  verbose=True)
    print(o.get_oauth_token())
