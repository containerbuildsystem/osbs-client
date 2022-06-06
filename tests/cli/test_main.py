"""
Copyright (c) 2021 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import json
import os
import time
from textwrap import dedent

from flexmock import flexmock
import pytest

from osbs.cli.main import print_output
from osbs.tekton import PipelineRun


def test_print_output(tmpdir, capsys):
    """Test print_output function

    Tests:
      * if STDOUT is correct
      * if JSON exported metadata are correct
    """
    test_metadata = {
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
    flexmock(time).should_receive('sleep').and_return(None)
    ppln_run = flexmock(PipelineRun(flexmock(), 'test_ppln'))
    (ppln_run
     .should_receive('get_info')
     .and_return(test_metadata))
    ppln_run.should_receive('has_succeeded').and_return(True)
    ppln_run.should_receive('status_reason').and_return('complete')
    ppln_run.should_receive('has_not_finished').and_return(False)

    log_entries = [
        '2021-11-25 23:17:49,886 platform:- - atomic_reactor.inner - INFO - YOLO 1',
        '2021-11-25 23:17:50,000 platform:- - smth - USER_WARNING - {"message": "user warning"}',
        '2021-11-25 23:17:59,123 platform:- - atomic_reactor.inner - INFO - YOLO 2',
     ]

    def get_logs():
        task_run_name = "some-task-run"
        for log in log_entries:
            yield task_run_name, log

    ppln_run.should_receive('get_logs').and_return(get_logs())

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
            'status': 'complete',
            'info': test_metadata,
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


@pytest.mark.parametrize('get_logs_failed', [True, False])
@pytest.mark.parametrize('build_not_finished', [True, False])
def test_print_output_failure(tmpdir, capsys, get_logs_failed, build_not_finished):
    """Test print_output function when build failed

    Tests:
      * if STDOUT is correct when build failed
      * if JSON exported metadata are correct
    """
    flexmock(time).should_receive('sleep').and_return(None)
    ppln_run = flexmock(PipelineRun(flexmock(), 'test_ppln'))
    ppln_run.should_receive('has_succeeded').and_return(False)
    ppln_run.should_receive('status_reason').and_return('failed')
    ppln_run.should_receive('get_error_message').and_return('Build failed ...')

    log_entries = [
        '2021-11-25 23:17:49,886 platform:- - atomic_reactor.inner - INFO - YOLO 1',
        '2021-11-25 23:17:50,000 platform:- - smth - USER_WARNING - {"message": "user warning"}',
        '2021-11-25 23:17:59,123 platform:- - atomic_reactor.inner - ERROR - YOLO 2',
     ]

    def get_logs():
        task_run_name = "some-task-run"
        for log in log_entries:
            yield task_run_name, log
        if get_logs_failed:
            raise Exception("error reading logs")

    ppln_run.should_receive('get_logs').and_return(get_logs())
    ppln_run.should_receive('has_not_finished').and_return(build_not_finished)

    if get_logs_failed and build_not_finished:
        ppln_run.should_receive('cancel_pipeline_run').once()
    else:
        ppln_run.should_receive('cancel_pipeline_run').never()

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
            'status': 'failed',
            'info': {},
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
