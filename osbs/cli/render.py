"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import sys
import logging
from subprocess import CalledProcessError
try:
    from subprocess import check_output
except ImportError:
    from osbs.utils import backported_check_output as check_output


logger = logging.getLogger(__name__)


def get_terminal_size():
    """
    get size of console: rows x columns

    :return: tuple, (int, int)
    """
    try:
        rows, columns = check_output(['stty', 'size']).split()
    except CalledProcessError:
        # not attached to terminal
        logger.info("not attached to terminal")
        return 0, 0
    logger.debug("console size is %s %s", rows, columns)
    return int(rows), int(columns)


class TableFormatter(object):
    def __init__(self, table):
        """
        util functions for printing a table

        [
            {
                "key": "value",
                "another_key": "some_value"
                "k": "v"
            },
            {
                "...": "...",
                ...
            }
            ...
        ]

        :param table: list of dicts, table to print
        """
        self.table = table
        self.header = table[0]
        self.data = table[1:]
        self._terminal_width = None

    def _longest_val_in_column(self, col):
        try:
            # +2 is for implicit separator
            return max([len(x[col]) for x in self.table if x[col]]) + 2
        except KeyError:
            logger.error("there is no column %r", col)
            raise

    @property
    def terminal_width(self):
        if self._terminal_width is None:
            self._terminal_width = get_terminal_size()[1]
        return self._terminal_width


class TablePrinter(TableFormatter):
    """
    Print one specific instance of a table
    """
    def __init__(self, table, col_list):
        super(TablePrinter, self).__init__(table)
        self.col_list = col_list

        self._init()
        self._count_sizes()

    def _init(self):
        self.col_count = len(self.col_list)
        # list of lengths of longest entries in columns
        self.col_longest = self.slice_col_lengths()
        self.data_length = sum(self.col_longest.values())

        if self.terminal_width > 0:
            # free space is space which should be equeally distributed for all columns
            # self.terminal_width -- terminal is our canvas
            #  - self.data_length -- substract length of content (the actual data)
            #  - self.col_count + 1 -- table lines are not part of free space, their width is
            #                          (number of columns - 1)
            self.total_free_space = (self.terminal_width - self.data_length) - self.col_count + 1
            if self.total_free_space <= 0:
                self.total_free_space = None
            else:
                self.default_column_space = self.total_free_space / self.col_count
                self.default_column_space_remainder = self.total_free_space % self.col_count
        else:
            self.total_free_space = None

    def _count_sizes(self):
        """
        count everything

        <><---terminal-width-----------><>

        <> HEADER  | HEADER2  | HEADER3 <>
        <>---------+----------+---------<>

        :return:
        """
        format_list = []
        header_sepa_format_list = []
        # actual widths of columns
        self.col_widths = {}

        for col in self.col_list:
            col_length = self.col_longest[col]
            col_width = col_length + self._separate()
            format_list.append(" {%s:%d} " % (col, col_width - 2))
            header_sepa_format_list.append("{%s:%d}" % (col, col_width))
            self.col_widths[col] = col_width

        self.format_str = "|".join(format_list)

        self.header_format_str = "+".join(header_sepa_format_list)
        self.header_data = {}
        for k in self.col_widths:
            self.header_data[k] = "-" * self.col_widths[k]

    def slice_col_lengths(self):
        response = {}
        for col in self.col_list:
            response[col] = self._longest_val_in_column(col)
        return response

    def _separate(self):
        """
        get a width of separator for current column

        :return: int
        """
        if self.total_free_space is None:
            return 0
        else:
            sepa = self.default_column_space
            # we need to distribute remainders
            if self.default_column_space_remainder > 0:
                sepa += 1
                self.default_column_space_remainder -= 1
            logger.debug("total: %d, remainder: %d, sepa: %d", self.total_free_space,
                         self.default_column_space_remainder, sepa)
            return sepa

    def render(self):
        """

        :return: None
        """
        print(self.format_str.format(**self.header), file=sys.stderr)
        print(self.header_format_str.format(**self.header_data), file=sys.stderr)
        for row in self.data:
            print(self.format_str.format(**row))
