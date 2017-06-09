"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import pytest
import sys

from textwrap import dedent
from osbs.cli.main import (str_on_2_unicode_on_3, check_required_args,
                           check_unwanted_args, make_worker_builds_str,
                           make_digests_str)


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


class TestGetBuild(object):

    @pytest.mark.parametrize(('worker_builds', 'expected_str'), (
        ({}, ''),
        (
            {'x86_64': {
                'build': {
                    'build-name': 'spam-build-name',
                    'cluster-url': 'spam-cluster-url',
                    'namespace': 'spam-namespace',
                },
                'digests': []
            }},
            dedent("""\
                x86_64 WORKER BUILD

                spam-build-name on spam-cluster-url (spam-namespace)

                x86_64 V2 DIGESTS

                (empty)""")
        ),
        (
            {'x86_64': {
                'build': {
                    'build-name': 'spam-build-name',
                    'cluster-url': 'spam-cluster-url',
                    'namespace': 'spam-namespace',
                },
                'digests': [{
                    'registry': 'spam-registry',
                    'repository': 'spam-repository',
                    'tag': 'spam-tag',
                    'digest': 'spam-digest',
                }]
            }},
            dedent("""\
                x86_64 WORKER BUILD

                spam-build-name on spam-cluster-url (spam-namespace)

                x86_64 V2 DIGESTS

                spam-registry/spam-repository:spam-tag spam-digest""")
        ),
    ))
    def test_make_worker_builds_str(self, worker_builds, expected_str):
        assert make_worker_builds_str(worker_builds) == expected_str

    @pytest.mark.parametrize(('digests', 'expected_str'), (
        ([], '(empty)'),
        ([{}], '(invalid value)'),
        (None, '(unset)'),
        (
            [
                {
                    'registry': 'spam-registry',
                    'repository': 'spam-repository',
                    'tag': 'spam-tag',
                    'digest': 'spam-digest',
                },
            ],
            dedent("""\
                spam-registry/spam-repository:spam-tag spam-digest""")
        ),
        (
            [
                {
                    'registry': 'spam-registry',
                    'repository': 'spam-repository',
                    'tag': 'spam-tag',
                    'digest': 'spam-digest',
                },
                {
                    'registry': 'eggs-registry',
                    'repository': 'eggs-repository',
                    'tag': 'eggs-tag',
                    'digest': 'eggs-digest',
                },
            ],
            dedent("""\
                spam-registry/spam-repository:spam-tag spam-digest
                eggs-registry/eggs-repository:eggs-tag eggs-digest""")
        ),
    ))
    def test_make_digests_str(self, digests, expected_str):
        assert make_digests_str(digests) == expected_str
