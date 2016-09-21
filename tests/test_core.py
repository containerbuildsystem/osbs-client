"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from flexmock import flexmock
from textwrap import dedent
import six
import time
import json

from osbs.http import HttpResponse
from osbs.constants import (BUILD_FINISHED_STATES, BUILD_RUNNING_STATES,
                            BUILD_CANCELLED_STATE)
from osbs.exceptions import (OsbsResponseException, OsbsNetworkException,
                             OsbsException)
from osbs.core import check_response

from tests.constants import (TEST_BUILD, TEST_CANCELLED_BUILD, TEST_LABEL,
                             TEST_LABEL_VALUE, TEST_BUILD_CONFIG)
from tests.fake_api import openshift
import pytest

try:
    # py2
    import httplib
except ImportError:
    # py3
    import http.client as httplib


class Response(object):
    def __init__(self, status_code, content=None, iterable=None):
        self.status_code = status_code
        self.iterable = iterable
        if content is not None:
            self.content = content

    def iter_lines(self):
        for line in self.iterable:
            yield line


class TestCheckResponse(object):
    @pytest.mark.parametrize('content', [None, 'OK'])
    @pytest.mark.parametrize('status_code', [httplib.OK, httplib.CREATED])
    def test_check_response_ok(self, status_code, content):
        response = Response(status_code, content=content)
        check_response(response)

    def test_check_response_bad_stream(self, caplog):
        iterable = ['iter', 'lines']
        status_code = httplib.CONFLICT
        response = Response(status_code, iterable=iterable)
        with pytest.raises(OsbsResponseException):
            check_response(response)

        logged = [l.getMessage() for l in caplog.records()]
        assert len(logged) == 1
        assert logged[0] == '[{code}] {message}'.format(code=status_code,
                                                        message='iterlines')

    def test_check_response_bad_nostream(self, caplog):
        status_code = httplib.CONFLICT
        content = 'content'
        response = Response(status_code, content=content)
        with pytest.raises(OsbsResponseException):
            check_response(response)

        logged = [l.getMessage() for l in caplog.records()]
        assert len(logged) == 1
        assert logged[0] == '[{code}] {message}'.format(code=status_code,
                                                        message=content)


class TestOpenshift(object):
    def test_set_labels_on_build(self, openshift):
        l = openshift.set_labels_on_build(TEST_BUILD, {TEST_LABEL: TEST_LABEL_VALUE})
        assert l.json() is not None


    def test_stream_logs(self, openshift):
        response = flexmock(status_code=httplib.OK)
        (response
            .should_receive('iter_lines')
            .and_return(["{'stream': 'foo\n'}"])
            .and_raise(StopIteration))

        (flexmock(openshift)
            .should_receive('_get')
             # First: timeout in response after 100s
            .and_raise(httplib.IncompleteRead(''))
             # Next: return a real response
            .and_return(response))

        (flexmock(time)
            .should_receive('time')
            .and_return(0)
            .and_return(100))

        logs = openshift.stream_logs(TEST_BUILD)
        assert len([log for log in logs]) == 1

    def test_list_builds(self, openshift):
        l = openshift.list_builds()
        assert l is not None
        assert bool(l.json())  # is there at least something

    def test_list_pods(self, openshift):
        response = openshift.list_pods(label="openshift.io/build.name=%s" %
                                       TEST_BUILD)
        assert isinstance(response, HttpResponse)

    def test_get_oauth_token(self, openshift):
        token = openshift.get_oauth_token()
        assert token is not None

    def test_get_user(self, openshift):
        l = openshift.get_user()
        assert l.json() is not None

    def test_watch_build(self, openshift):
        response = openshift.wait_for_build_to_finish(TEST_BUILD)
        status_lower = response["status"]["phase"].lower()
        assert response["metadata"]["name"] == TEST_BUILD
        assert status_lower in BUILD_FINISHED_STATES
        assert isinstance(TEST_BUILD, six.text_type)
        assert isinstance(status_lower, six.text_type)

    def test_create_build(self, openshift):
        response = openshift.create_build({})
        assert response is not None
        assert response.json()["metadata"]["name"] == TEST_BUILD
        assert response.json()["status"]["phase"].lower() in BUILD_FINISHED_STATES

    def test_cancel_build(self, openshift):
        response = openshift.cancel_build(TEST_CANCELLED_BUILD)
        assert response is not None
        assert response.json()["metadata"]["name"] == TEST_CANCELLED_BUILD
        assert response.json()["status"]["phase"].lower() in BUILD_CANCELLED_STATE

    def test_get_build_config(self, openshift):
        mock_response = {"spam": "maps"}
        build_config_name = 'some-build-config-name'
        expected_url = openshift._build_url("buildconfigs/%s/" % build_config_name)
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(200, {}, json.dumps(mock_response))))
        response = openshift.get_build_config(build_config_name)
        assert response['spam'] == 'maps'

    def test_get_missing_build_config(self, openshift):
        build_config_name = 'some-build-config-name'
        expected_url = openshift._build_url("buildconfigs/%s/" % build_config_name)
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(404, {}, '')))
        with pytest.raises(OsbsResponseException):
            openshift.get_build_config(build_config_name)

    def test_get_build_config_by_labels(self, openshift):
        mock_response = {"items": [{"spam": "maps"}]}
        build_config_name = 'some-build-config-name'
        label_selectors = (
            ('label-1', 'value-1'),
            ('label-2', 'value-2'),
        )
        expected_url = openshift._build_url(
            "buildconfigs/?labelSelector=label-1%3Dvalue-1%2Clabel-2%3Dvalue-2")
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(200, {}, json.dumps(mock_response))))
        response = openshift.get_build_config_by_labels(label_selectors)
        assert response['spam'] == 'maps'

    def test_get_missing_build_config_by_labels(self, openshift):
        mock_response = {"items": []}
        build_config_name = 'some-build-config-name'
        label_selectors = (
            ('label-1', 'value-1'),
            ('label-2', 'value-2'),
        )
        expected_url = openshift._build_url(
            "buildconfigs/?labelSelector=label-1%3Dvalue-1%2Clabel-2%3Dvalue-2")
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(200, {}, json.dumps(mock_response))))

        with pytest.raises(OsbsException) as exc:
            openshift.get_build_config_by_labels(label_selectors)
        assert str(exc.value).startswith('Build config not found')

    def test_get_multiple_build_config_by_labels(self, openshift):
        mock_response = {"items": [{"spam": "maps"}, {"eggs": "sgge"}]}
        build_config_name = 'some-build-config-name'
        label_selectors = (
            ('label-1', 'value-1'),
            ('label-2', 'value-2'),
        )
        expected_url = openshift._build_url(
            "buildconfigs/?labelSelector=label-1%3Dvalue-1%2Clabel-2%3Dvalue-2")
        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(200, {}, json.dumps(mock_response))))

        with pytest.raises(OsbsException) as exc:
            openshift.get_build_config_by_labels(label_selectors)
        assert str(exc.value).startswith('More than one build config found')

    def test_put_image_stream_tag(self, openshift):
        tag_name = 'spam'
        tag_id = 'maps:' + tag_name
        mock_data = {
          'kind': 'ImageStreamTag',
          'apiVersion': 'v1',
          'tag': {
            'name': tag_name
          }
        }

        expected_url = openshift._build_url('imagestreamtags/' + tag_id)
        (flexmock(openshift)
            .should_receive("_put")
            .with_args(expected_url, data=json.dumps(mock_data),
                       headers={"Content-Type": "application/json"})
            .once()
            .and_return(HttpResponse(200, {}, json.dumps(mock_data))))

        openshift.put_image_stream_tag(tag_id, mock_data)

    def _make_tag_template(self):
        # TODO: Just read from inputs folder
        return json.loads(dedent('''\
            {
              "kind": "ImageStreamTag",
              "apiVersion": "v1",
              "metadata": {
                "name": "{{IMAGE_STREAM_ID}}:{{TAG_ID}}"
              },
              "tag": {
                "name": "{{TAG_ID}}",
                "from": {
                  "kind": "DockerImage",
                  "name": "{{REPOSITORY}}:{{TAG_ID}}"
                },
                "importPolicy": {}
              }
            }
        '''))

    @pytest.mark.parametrize('existing_scheduled', (True, False, None))
    @pytest.mark.parametrize('existing_insecure', (True, False, None))
    @pytest.mark.parametrize('expected_scheduled', (True, False))
    @pytest.mark.parametrize(('s_annotations', 'expected_insecure'), (
        ({'openshift.io/image.insecureRepository': 'true'}, True),
        ({'openshift.io/image.insecureRepository': 'false'}, False),
        ({}, False),
        (None, False),
    ))
    @pytest.mark.parametrize('status_code', (200, 404, 500))
    def test_ensure_image_stream_tag(self,
                                     existing_scheduled,
                                     existing_insecure,
                                     expected_scheduled,
                                     s_annotations,
                                     expected_insecure,
                                     status_code,
                                     openshift):
        stream_name = 'spam'
        stream_repo = 'some.registry.com/spam'
        stream = {
            'metadata': {'name': stream_name},
            'spec': {'dockerImageRepository': stream_repo}
        }
        if s_annotations is not None:
            stream['metadata']['annotations'] = s_annotations

        tag_name = 'maps'
        tag_id = '{0}:{1}'.format(stream_name, tag_name)

        expected_url = openshift._build_url('imagestreamtags/' +
                                            tag_id)

        def verify_image_stream_tag(*args, **kwargs):
            data = json.loads(kwargs['data'])

            assert (bool(data['tag']['importPolicy'].get('insecure')) ==
                    expected_insecure)
            assert (bool(data['tag']['importPolicy'].get('scheduled')) ==
                    expected_scheduled)

            # Also verify new image stream tags are created properly.
            if status_code == 404:
                assert data['metadata']['name'] == tag_id
                assert data['tag']['name'] == tag_name
                assert (data['tag']['from']['name'] ==
                        '{0}:{1}'.format(stream_repo, tag_name))

            return HttpResponse(200, {}, json.dumps('{}'))

        expected_change = False
        expected_error = status_code == 500

        mock_response = '{}'

        if status_code == 200:
            existing_image_stream_tag = {'tag': {'importPolicy': {}}}

            if existing_insecure is not None:
                existing_image_stream_tag['tag']['importPolicy']['insecure'] = \
                    existing_insecure

            if existing_scheduled is not None:
                existing_image_stream_tag['tag']['importPolicy']['scheduled'] = \
                    existing_scheduled

            mock_response = json.dumps(existing_image_stream_tag)

            if expected_insecure != bool(existing_insecure) or \
               expected_scheduled != bool(existing_scheduled):
                    expected_change = True

        elif status_code == 404:
            expected_change = True

        (flexmock(openshift)
            .should_receive("_get")
            .with_args(expected_url)
            .once()
            .and_return(HttpResponse(status_code, {}, mock_response)))

        if expected_change:
            (flexmock(openshift)
                .should_receive("_put")
                .with_args(expected_url, data=str,
                           headers={"Content-Type": "application/json"})
                .replace_with(verify_image_stream_tag)
                .once())

        if expected_error:
            with pytest.raises(OsbsResponseException):
                openshift.ensure_image_stream_tag(
                    stream, tag_name, self._make_tag_template(), expected_scheduled)

        else:
            assert (openshift.ensure_image_stream_tag(
                        stream,
                        tag_name,
                        self._make_tag_template(),
                        expected_scheduled) == expected_change)
