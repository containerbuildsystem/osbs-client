import pytest

from osbs.utils import deep_update, get_imagestream_name_from_image


def test_deep_update():
    x = {'a': 'A', 'b': {'b1': 'B1', 'b2': 'B2'}}
    y = {'b': {'b1': 'newB1', 'b3': 'B3'}, 'c': 'C'}
    deep_update(x, y)
    assert x == {'a': 'A', 'b': {'b1': 'newB1', 'b2': 'B2', 'b3': 'B3'}, 'c': 'C'}


@pytest.mark.parametrize(('img', 'expected'), [
    ('fedora23', 'fedora23'),
    ('fedora23:sometag', 'fedora23'),
    ('fedora23/python', 'fedora23-python'),
    ('fedora23/python:sometag', 'fedora23-python'),
    ('docker.io/fedora23', 'fedora23'),
    ('docker.io/fedora23/python', 'fedora23-python'),
    ('docker.io/fedora23/python:sometag', 'fedora23-python'),
])
def test_get_imagestream_name_from_image(img, expected):
    assert get_imagestream_name_from_image(img) == expected
