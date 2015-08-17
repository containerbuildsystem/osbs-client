import pytest

from osbs.utils import (deep_update,
                        get_imagestreamtag_from_image,
                        git_repo_humanish_part_from_uri)


def test_deep_update():
    x = {'a': 'A', 'b': {'b1': 'B1', 'b2': 'B2'}}
    y = {'b': {'b1': 'newB1', 'b3': 'B3'}, 'c': 'C'}
    deep_update(x, y)
    assert x == {'a': 'A', 'b': {'b1': 'newB1', 'b2': 'B2', 'b3': 'B3'}, 'c': 'C'}


@pytest.mark.parametrize(('uri', 'humanish'), [
    ('http://git.example.com/git/repo.git/', 'repo'),
    ('http://git.example.com/git/repo.git', 'repo'),
    ('http://git.example.com/git/repo/.git', 'repo'),
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
