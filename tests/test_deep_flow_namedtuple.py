"""Regression test: ``_deep_flow`` must rebuild NamedTuples positionally.

Repro for the bug surfaced by the navigaitor end-to-end smoke run, where a
``dataflux.sample.Sample`` (a NamedTuple of ``(input, target, metadata)``)
stored in a source's ``__dict__`` got rebuilt as
``Sample(input=[input, target, metadata], target=None, metadata={})`` —
i.e. the entire triplet was wrapped into the ``input`` field. The cause
was ``type(value)(out)`` passing a single iterable to a NamedTuple
constructor that expects positional args.
"""

from typing import Any, List, NamedTuple

from liquifai.core import _deep_flow


class _Sample(NamedTuple):
    """Mirrors dataflux.sample.Sample's NamedTuple shape."""

    input: Any
    target: Any = None
    metadata: dict = {}


class _Holder:
    """Live instance whose ``__dict__`` carries a list of NamedTuples."""

    def __init__(self, items: List[_Sample]) -> None:
        self.items = items


def test_deep_flow_preserves_namedtuple_field_layout() -> None:
    """A NamedTuple inside a list inside an instance's __dict__ must NOT be
    wrapped — its field assignments must round-trip identically."""
    items = [
        _Sample(input="A", target=1, metadata={"i": 0}),
        _Sample(input="B", target=2, metadata={"i": 1}),
    ]
    holder = _Holder(items)
    _deep_flow(holder)

    # Each Sample's fields must be unchanged.
    for i, expected in enumerate(items):
        actual = holder.items[i]
        assert isinstance(actual, _Sample)
        assert actual.input == expected.input, f"item[{i}].input was rewrapped: {actual!r}"
        assert actual.target == expected.target
        assert actual.metadata == expected.metadata


def test_deep_flow_namedtuple_directly() -> None:
    """Calling _deep_flow on a NamedTuple value directly must be a no-op."""
    sample = _Sample(input="X", target=42, metadata={"k": "v"})
    result = _deep_flow(sample)
    assert isinstance(result, _Sample)
    assert result.input == "X"
    assert result.target == 42
    assert result.metadata == {"k": "v"}


def test_deep_flow_regular_tuple_still_rebuilt_via_iterable() -> None:
    """Plain tuples (non-NamedTuple) keep the iterable-constructor path."""
    plain = (1, 2, 3)
    result = _deep_flow(plain)
    assert isinstance(result, tuple) and not hasattr(type(result), "_fields")
    assert result == (1, 2, 3)


def test_deep_flow_list_remains_a_list() -> None:
    """Lists round-trip as lists with each element flowed."""
    raw = [1, 2, 3]
    result = _deep_flow(raw)
    assert isinstance(result, list)
    assert result == [1, 2, 3]
