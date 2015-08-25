"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import pytest

from osbs.utils import (deep_update,
                        get_imagestreamtag_from_image,
                        git_repo_humanish_part_from_uri,
                        get_time_from_rfc3399)


def test_deep_update():
    x = {'a': 'A', 'b': {'b1': 'B1', 'b2': 'B2'}}
    y = {'b': {'b1': 'newB1', 'b3': 'B3'}, 'c': 'C'}
    deep_update(x, y)
    assert x == {'a': 'A', 'b': {'b1': 'newB1', 'b2': 'B2', 'b3': 'B3'}, 'c': 'C'}


@pytest.mark.parametrize(('uri', 'humanish'), [
    ('http://git.example.com/git/repo.git/', 'repo'),
    ('http://git.example.com/git/repo.git', 'repo'),
    ('http://git.example.com/git/repo/.git', 'repo'),
    ('git://hostname/path', 'path'),
])
def test_git_repo_humanish_part_from_uri(uri, humanish):
    assert git_repo_humanish_part_from_uri(uri) == humanish


@pytest.mark.parametrize(('img', 'expected'), [
    ('fedora23', 'fedora23'),
    ('fedora23:sometag', 'fedora23:sometag'),
    ('fedora23/python', 'fedora23-python'),
    ('fedora23/python:sometag', 'fedora23-python:sometag'),
    ('docker.io/fedora23', 'fedora23'),
    ('docker.io/fedora23/python', 'fedora23-python'),
    ('docker.io/fedora23/python:sometag', 'fedora23-python:sometag'),
])
def test_get_imagestreamtag_from_image(img, expected):
    assert get_imagestreamtag_from_image(img) == expected


@pytest.mark.parametrize(('rfc3399', 'seconds'), [
    ('2015-08-24T10:41:00Z', 1440412860.0),
])
def test_get_time_from_rfc3399_valid(rfc3399, seconds):
    assert get_time_from_rfc3399(rfc3399) == seconds


@pytest.mark.parametrize('rfc3399', [
    ('just completely invalid'),

    # The implementation doesn't know enough about RFC 3399 to
    # distinguish between invalid and unsupported
    ('2015-08-24T10:41:00.1Z'),
])
def test_get_time_from_rfc3399_invalid(rfc3399):
    with pytest.raises(RuntimeError):
        get_time_from_rfc3399(rfc3399)
