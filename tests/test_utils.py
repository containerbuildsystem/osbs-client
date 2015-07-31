from osbs.utils import deep_update


def test_deep_update():
    x = {'a': 'A', 'b': {'b1': 'B1', 'b2': 'B2'}}
    y = {'b': {'b1': 'newB1', 'b3': 'B3'}, 'c': 'C'}
    deep_update(x, y)
    assert x == {'a': 'A', 'b': {'b1': 'newB1', 'b2': 'B2', 'b3': 'B3'}, 'c': 'C'}
