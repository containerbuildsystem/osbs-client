"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import osbs.cli.render
from osbs.cli.render import TablePrinter, get_terminal_size

from flexmock import flexmock


LONGEST_VAL1 = "l" * 10
LONGEST_VAL2 = "l" * 100
LONGEST_VAL3 = "l" * 80

SAMPLE_DATA = [
    {"x": "H" * 8, "y": "H" * 20, "z": "H" * 4},
    {"x": LONGEST_VAL1, "y": LONGEST_VAL2, "z": "a"},
    {"x": "zxc", "y": "asdqwe", "z": LONGEST_VAL3},
]


def test_get_terminal_size():
    result = get_terminal_size()
    assert isinstance(result, tuple)
    assert isinstance(result[0], int) or result[0] is None
    assert isinstance(result[1], int) or result[1] is None


def test_get_longest_val_in_col():
    p = TablePrinter(SAMPLE_DATA, ["x", "y"])
    assert p._longest_val_in_column("x") == len(LONGEST_VAL1) + 2
    assert p._longest_val_in_column("y") == len(LONGEST_VAL2) + 2


def test_get_longest_col_vals():
    p = TablePrinter(SAMPLE_DATA, ["x", "y", "z"])
    response = p.get_all_longest_col_lengths()
    assert response["x"] == len(LONGEST_VAL1) + 2
    assert response["y"] == len(LONGEST_VAL2) + 2
    assert response["z"] == len(LONGEST_VAL3) + 2


def test_print_table():
    p = TablePrinter(SAMPLE_DATA, ["x", "y", "z"])
    p.render()


def test_print_table_with_mocked_terminal(capsys):
    (flexmock(osbs.cli.render)
        .should_receive('get_terminal_size')
        .and_return(25, 80)
        .once())
    short_data = [{'x': 'Header1', 'y': 'Header2', 'z': 'Header3'},
                  {'x': 'x' * 8, 'y': 'y' * 20, 'z': 'z' * 4}]
    p = TablePrinter(short_data, ["x", "y", "z"])
    p.render()
    out, err = capsys.readouterr()
    expected_header = """
 Header1               | Header2                          | Header3             
-----------------------+----------------------------------+---------------------
""".lstrip('\n')
    expected_data = """
 xxxxxxxx              | yyyyyyyyyyyyyyyyyyyy             | zzzz                
""".lstrip('\n')

    assert err == expected_header
    assert out == expected_data
