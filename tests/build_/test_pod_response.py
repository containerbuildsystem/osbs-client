"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals, absolute_import

from copy import deepcopy
import pytest

from osbs.build.pod_response import PodResponse


class TestPodResponse(object):
    GENERIC_POD_JSON = {
        'apiVersion': 'v1',
        'kind': 'Pod',
        'metadata': {
            'name': 'foo',
        },
        'spec': {
            'containers': [
                {
                    'image': 'foo',
                    'name': 'custom-build',
                },
            ],
        },
        'status': {},
        'phase': 'Succeeded',
    }

    @pytest.mark.parametrize('expect_image_ids,container_statuses', [
        # No containerStatuses
        ({}, None),

        # Empty containerStatuses
        ({}, []),

        # No prefix
        ({'image': 'no-prefix'},
         [{
             'image': 'image',
             'imageID': 'no-prefix',
         }]),

        # Normal case
        ({'image1': 'imageID1', 'image2': 'imageID2'},
         [
             {
                 'image': 'image1',
                 'imageID': 'docker://imageID1',
             },
             {
                 'image': 'image2',
                 'imageID': 'docker://imageID2',
             },
         ]),

        # New normal case
        ({'image3': 'imageID3', 'image4': 'imageID4'},
         [
             {
                 'image': 'image3',
                 'imageID': 'docker-pullable://imageID3',
             },
             {
                 'image': 'image4',
                 'imageID': 'docker-pullable://imageID4',
             },
         ]),
    ])
    def test_container_image_ids(self, expect_image_ids, container_statuses):
        pod_json = deepcopy(self.GENERIC_POD_JSON)
        if container_statuses is not None:
            pod_json['status']['containerStatuses'] = container_statuses

        pod_response = PodResponse(pod_json)
        image_ids = pod_response.get_container_image_ids()
        assert image_ids == expect_image_ids

    @pytest.mark.parametrize('expected_reason,pod_status', [
        # No container statuses but a pod message
        ({'reason': 'too cold'},
         {
             'message': 'too cold',
             'reason': 'too hot',
             'phase': 'Failed',
             'containerStatuses': [],
         }),

        # No non-zero exit code container statuses but a pod reason
        ({'reason': 'too hot'},
         {
             'reason': 'too hot',
             'phase': 'Failed',
             'containerStatuses': [
                 {
                     'state': {
                         'terminated': {
                             'exitCode': 0
                         },
                     },
                 },
             ],
         }),

        # No container statuses, only pod phase available
        ({'reason': 'Failed'}, {'phase': 'Failed'}),

        # Non-zero exit code with message
        (
            {
                'reason': 'Container cannot run',
                'exitCode': 1,
            },
            {
                'message': 'too cold',
                'reason': 'too hot',
                'phase': 'Failed',
                'containerStatuses': [
                    {
                        'state': {
                            'terminated': {
                                # Should ignore this one
                                'exitCode': 0,
                            },
                        },
                    },
                    {
                        'state': {
                            'terminated': {
                                'exitCode': 1,
                                'message': 'Container cannot run',
                                'reason': 'ContainerCannotRun',
                            },
                        },
                    },
                ],
            }
        ),

        # Non-zero exit code with reason
        (
            {
                'reason': 'ContainerCannotRun',
                'exitCode': 1,
            },
            {
                'message': 'too cold',
                'reason': 'too hot',
                'phase': 'Failed',
                'containerStatuses': [
                    {
                        'state': {
                            'terminated': {
                                'exitCode': 1,
                                'reason': 'ContainerCannotRun',
                            },
                        },
                    },
                    {
                        'state': {
                            'terminated': {
                                # Should ignore this one
                                'exitCode': 2,
                                'message': 'on fire',
                                'reason': 'FanFailure',
                            },
                        },
                    },
                ],
            }
        ),

        # Non-zero exit code, no explanation
        (
            {
                'reason': 'Exit code 1',
                'exitCode': 1,
                'containerID': 'docker://abcde',
            },
            {
                'message': 'too cold',
                'reason': 'too hot',
                'phase': 'Failed',
                'containerStatuses': [
                    {
                        'state': {
                            # Should ignore this one
                            'running': {},
                        },
                    },
                    {
                        'state': {
                            'terminated': {
                                'containerID': 'docker://abcde',
                                'exitCode': 1,
                            },
                        },
                    },
                ],
            },
        ),
    ])
    def test_failure_reason(self, expected_reason,
                            pod_status):
        pod_json = deepcopy(self.GENERIC_POD_JSON)
        pod_json['status'].update(pod_status)
        pod_response = PodResponse(pod_json)
        fail_reason = pod_response.get_failure_reason()
        assert fail_reason == expected_reason
