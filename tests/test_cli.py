"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import pytest
import sys

from flexmock import flexmock
from osbs.cli.main import (str_on_2_unicode_on_3, check_required_args,
                           check_unwanted_args)


class TestStrOn2UnicodeOn3(object):
    def test_force_str(self):
        b = b"s"
        if sys.version_info[0] == 3:
            s = "s"
            assert str_on_2_unicode_on_3(s) == s
            assert str_on_2_unicode_on_3(b) == s
        else:
            s = u"s"
            assert str_on_2_unicode_on_3(s) == b
            assert str_on_2_unicode_on_3(b) == b


class TestCheckProvidedArgs(object):

    @pytest.mark.parametrize(('attributes', 'raises'), (
        ({'spam': 'maps'}, False),
        ({'spam': 'maps', 'eggs': 'sgge'}, False),
        ({'unwanted1': 'ha!'}, 'unwanted1'),
        ({'spam': 'maps', 'unwanted1': 'ha!'}, 'unwanted1'),
        ({'unwanted1': 'ha!', 'unwanted2': 'heh'}, 'unwanted1, unwanted2'),
    ))
    def test_check_unwanted_args(self, attributes, raises):
        attributes.setdefault('unwanted1', None)
        attributes.setdefault('unwanted2', None)
        args = type('FakeArgParseNamespace', (object,), attributes)

        if raises:
            with pytest.raises(ValueError) as exc:
                check_unwanted_args(args, ['unwanted1', 'unwanted2'])
            assert 'Unwanted params: {0}'.format(raises) in str(exc)

        else:
            check_unwanted_args(args, ['unwanted1'])
            check_unwanted_args(args, ['unwanted1', 'unwanted2'])

    @pytest.mark.parametrize(('attributes', 'raises'), (
        ({'spam': 'maps'}, 'required1, required2'),
        ({'spam': 'maps', 'eggs': 'sgge'}, 'required1, required2'),
        ({'spam': 'maps', 'required1': 'ha!'}, 'required2'),
        ({'spam': 'maps', 'required1': 'ha!', 'required2': 'heh'}, False),
        ({'required1': 'ha!', 'required2': 'heh'}, False),
    ))
    def test_check_required_args(self, attributes, raises):
        attributes.setdefault('required1', None)
        attributes.setdefault('required2', None)
        args = type('FakeArgParseNamespace', (object,), attributes)

        if raises:
            with pytest.raises(ValueError) as exc:
                check_required_args(args, ['required1', 'required2'])
            assert 'Missing required params: {0}'.format(raises) in str(exc)

        else:
            check_required_args(args, ['required1'])
            check_required_args(args, ['required1', 'required2'])
