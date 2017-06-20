"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import json
import pytest
from osbs.build.build_response import BuildResponse


class TestBuildResponse(object):
    def test_get_logs(self):
        msg = "This is an error message"
        error = json.dumps({
            'errorDetail': {
                'code': 1,
                'message': msg,
                'error': msg,
            },
        })
        build_response = BuildResponse({
            'metadata': {
                'annotations': {
                    'logs': error,
                },
            },
        })

        assert msg in build_response.get_logs()

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

    @pytest.mark.parametrize(('plugin', 'message', 'expected_error_message'), [
        ('dockerbuild', None, 'Error in plugin dockerbuild'),
        ('foo', 'bar', 'Error in plugin foo: bar'),
        (None, None, None)
    ])
    def test_error_message(self, plugin, message, expected_error_message):
        plugins_metadata = json.dumps({
            'errors': {
                plugin: message,
            },
        })
        if not plugin:
            plugins_metadata = ''
        build_response = BuildResponse({
            'metadata': {
                'annotations': {
                    'plugins-metadata': plugins_metadata
                }
            }
        })
        assert build_response.get_error_message() == expected_error_message
