"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import json
import pytest
from osbs.build.build_response import BuildResponse
from osbs.exceptions import OsbsException


class TestBuildResponse(object):
    def test_get_koji_build_id(self):
        koji_build_id = '123'
        build_response = BuildResponse({
            'metadata': {
                'labels': {
                    'koji-build-id': koji_build_id,
                 },
            },
        })
        assert build_response.get_koji_build_id() == koji_build_id

    def test_build_cancel(self):
        build_response = BuildResponse({
            'status': {
                'phase': 'Running'
            }
        })
        assert not build_response.cancelled
        build_response.cancelled = True
        assert build_response.cancelled
        assert 'cancelled' in build_response.json['status']
        assert build_response.json['status']['cancelled']
        build_response.cancelled = False
        assert not build_response.cancelled
        assert 'cancelled' in build_response.json['status']
        assert not build_response.json['status'].get('cancelled')
        assert not build_response.is_cancelled()

    def test_state_checkers(self):
        build_response = BuildResponse({
            'status': {
                'phase': 'Complete'
            }
        })

        build_response.status = 'complete'
        assert build_response.is_finished()
        assert build_response.is_succeeded()
        assert not build_response.is_failed()
        assert not build_response.is_cancelled()
        assert not build_response.is_running()
        assert not build_response.is_pending()
        assert not build_response.is_in_progress()

        build_response.status = 'failed'
        assert build_response.is_failed()
        assert build_response.is_finished()
        assert not build_response.is_succeeded()
        assert not build_response.is_cancelled()
        assert not build_response.is_running()
        assert not build_response.is_pending()
        assert not build_response.is_in_progress()

        build_response.status = 'cancelled'
        assert build_response.is_cancelled()
        assert build_response.is_failed()
        assert build_response.is_finished()
        assert not build_response.is_succeeded()
        assert not build_response.is_running()
        assert not build_response.is_pending()
        assert not build_response.is_in_progress()

        build_response.status = 'running'
        assert build_response.is_running()
        assert build_response.is_in_progress()
        assert not build_response.is_cancelled()
        assert not build_response.is_failed()
        assert not build_response.is_finished()
        assert not build_response.is_succeeded()
        assert not build_response.is_pending()

        build_response.status = 'pending'
        assert build_response.is_pending()
        assert build_response.is_in_progress()
        assert not build_response.is_running()
        assert not build_response.is_cancelled()
        assert not build_response.is_failed()
        assert not build_response.is_finished()
        assert not build_response.is_succeeded()

    @pytest.mark.parametrize(('osbs', 'pod', 'plugin', 'message', 'expected_error_message'), [
        (True, False, 'dockerbuild', None, 'Error in plugin dockerbuild'),
        (False, False, 'dockerbuild', None, 'Error in plugin dockerbuild'),
        (True, False, 'foo', 'bar', 'Error in plugin foo: bar'),
        (True, {'reason': 'Container cannot run', 'exitCode': 1}, None, None,
         "Error in pod: {'reason': 'Container cannot run', 'exitCode': 1}"),
        (True, False, None, None, None),
        (False, False, None, None,
         "Error in pod: OSBS unavailable; Pod related errors cannot be retrieved"),
    ])
    def test_error_message(self, osbs, pod, plugin, message, expected_error_message):
        class MockOsbsPod:
            def __init__(self, error):
                self.error = error

            def get_pod_for_build(self, build_id):
                return self

            def get_failure_reason(self):
                if self.error:
                    return self.error
                raise OsbsException

        if pod or osbs:
            mock_pod = MockOsbsPod(pod)
        else:
            mock_pod = None
        if plugin:
            plugins_metadata = json.dumps({
                'errors': {
                    plugin: message,
                },
            })
            build = {
                'metadata': {
                    'annotations': {
                        'plugins-metadata': plugins_metadata,
                    },
                },
            }
        else:
            build = {
                'metadata': {
                    'annotations': {},
                },
            }

        build_response = BuildResponse(build, mock_pod)

        assert build_response.get_error_message() == expected_error_message
