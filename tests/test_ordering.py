"""The ordering keys themselves: the property everything else depends on."""

import pytest

from app.blocks.ordering import OrderingError, key_between, keys_between


def test_between_two_keys_sorts_between_them():
    a = key_between(None, None)
    b = key_between(a, None)
    middle = key_between(a, b)
    assert a < middle < b


def test_prepending_sorts_before_everything():
    first = key_between(None, None)
    assert key_between(None, first) < first


def test_repeated_appends_stay_short():
    """The reason for base-62 rather than plain fractions.

    Typing a thousand paragraphs one after another must not produce
    thousand-character keys.
    """
    key = None
    keys = []
    for _ in range(1000):
        key = key_between(key, None)
        keys.append(key)

    assert keys == sorted(keys)
    assert max(len(k) for k in keys) <= 4


def test_repeated_inserts_into_the_same_gap_keep_working():
    """Dragging into the same spot over and over is the pathological case."""
    low, high = key_between(None, None), key_between(key_between(None, None), None)
    previous = high
    for _ in range(200):
        high = key_between(low, high)
        assert low < high < previous
        previous = high


def test_out_of_order_neighbours_append_instead_of_failing():
    """A wonky pair of neighbours must not make a block unsaveable."""
    high, low = "a2", "a1"
    assert key_between(high, low) > high


def test_equal_neighbours_append_instead_of_failing():
    assert key_between("a1", "a1") > "a1"


def test_n_keys_land_in_order_inside_the_gap():
    keys = keys_between("a1", "a2", 5)
    assert len(keys) == 5
    assert keys == sorted(keys)
    assert all("a1" < k < "a2" for k in keys)


def test_zero_keys_is_not_an_error():
    assert keys_between("a1", "a2", 0) == []


def test_a_malformed_key_is_reported_not_swallowed():
    with pytest.raises(OrderingError):
        key_between("this is not a key", None)
