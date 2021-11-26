"""
Copyright (c) 2021 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import json
import os
from textwrap import dedent

from flexmock import flexmock

from osbs.cli.main import print_output
from osbs.tekton import PipelineRun


def test_print_output(tmpdir, capsys):
    """Test print_output function

    Tests:
      * if STDOUT is correct
      * if JSON exported metadata are correct
    """
    ppln_run = flexmock(PipelineRun(flexmock(), 'test_ppln'))
    (ppln_run
     .should_receive('get_info')
     .and_return(
        {
            'metadata': {
                'annotations': {
                    # annotations are JSON
                    'repositories': """{
                        "primary": ["test1"],
                        "floating": ["test2a", "test2b"],
                        "unique": ["test3"]
                    }""",
                }
            }
        }
     ))
    ppln_run.should_receive('has_succeeded').and_return(True)
    ppln_run.should_receive('status_reason').and_return('complete')
    (ppln_run
     .should_receive('get_logs')
     .and_return([
        '2021-11-25 23:17:49,886 platform:- - atomic_reactor.inner - INFO - YOLO 1',
        '2021-11-25 23:17:50,000 platform:- - smth - USER_WARNING - {"message": "user warning"}',
        '2021-11-25 23:17:59,123 platform:- - atomic_reactor.inner - INFO - YOLO 2',
     ]))

    export_metadata_file = os.path.join(tmpdir, 'metadata.json')
    print_output(ppln_run, export_metadata_file=export_metadata_file)

    captured = capsys.readouterr()
    expected_stdout = dedent("""\
        Pipeline run created (test_ppln), watching logs (feel free to interrupt)
        '2021-11-25 23:17:49,886 platform:- - atomic_reactor.inner - INFO - YOLO 1'
        '2021-11-25 23:17:59,123 platform:- - atomic_reactor.inner - INFO - YOLO 2'

        pipeline run test_ppln is complete
        primary repositories:
        \ttest1
        floating repositories:
        \ttest2a
        \ttest2b
        unique repositories:
        \ttest3

        user warnings:
        \tuser warning
        """)
    assert captured.out == expected_stdout

    expected_metadata = {
        'pipeline_run': {
            'name': 'test_ppln',
            'status': 'complete'
        },
        'results': {
            'error_msg': '',
            'repositories': {
                'floating': ['test2a', 'test2b'],
                'primary': ['test1'],
                'unique': ['test3']},
            'user_warnings': ['user warning']
        },
    }

    with open(export_metadata_file, 'r') as f:
        metadata = json.load(f)
    assert metadata == expected_metadata


def test_print_output_failure(tmpdir, capsys):
    """Test print_output function when build failed

    Tests:
      * if STDOUT is correct when build failed
      * if JSON exported metadata are correct
    """
    ppln_run = flexmock(PipelineRun(flexmock(), 'test_ppln'))
    ppln_run.should_receive('has_succeeded').and_return(False)
    ppln_run.should_receive('status_reason').and_return('failed')
    ppln_run.should_receive('get_error_message').and_return('Build failed ...')
    (ppln_run
     .should_receive('get_logs')
     .and_return([
        '2021-11-25 23:17:49,886 platform:- - atomic_reactor.inner - INFO - YOLO 1',
        '2021-11-25 23:17:50,000 platform:- - smth - USER_WARNING - {"message": "user warning"}',
        '2021-11-25 23:17:59,123 platform:- - atomic_reactor.inner - ERROR - YOLO 2',
     ]))

    export_metadata_file = os.path.join(tmpdir, 'metadata.json')
    print_output(ppln_run, export_metadata_file=export_metadata_file)

    captured = capsys.readouterr()
    expected_stdout = dedent("""\
        Pipeline run created (test_ppln), watching logs (feel free to interrupt)
        '2021-11-25 23:17:49,886 platform:- - atomic_reactor.inner - INFO - YOLO 1'
        '2021-11-25 23:17:59,123 platform:- - atomic_reactor.inner - ERROR - YOLO 2'

        pipeline run test_ppln is failed

        user warnings:
        \tuser warning

        Build failed ...
        """)
    assert captured.out == expected_stdout

    expected_metadata = {
        'pipeline_run': {
            'name': 'test_ppln',
            'status': 'failed'
        },
        'results': {
            'error_msg': 'Build failed ...',
            'repositories': {},
            'user_warnings': ['user warning']
        },
    }

    with open(export_metadata_file, 'r') as f:
        metadata = json.load(f)
    assert metadata == expected_metadata
