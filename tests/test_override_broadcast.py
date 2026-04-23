"""Override-matcher tests: `_merge_overrides_into_fluids` and friends.

Pins the rule: a CLI override `--key value` applies to every Fluid in the
tree whose target class

* already has `key` in its kwargs (covers the post-construction pattern
  like ``Enable.visualize``), OR
* has `key` in its ``__init__`` signature (covers defaults-only kwargs
  like ``RFUAVSource.max_packs``), OR
* is ``@configurable`` and has `key` as a public class-level attribute
  (covers @property setters and public class attrs Confluid setattr's).

Other Fluids are left alone — no typo broadcasting.
"""

from typing import Optional

import pytest
from confluid import configurable
from confluid.fluid import Class

from liquifai.core import _accepted_override_keys, _merge_overrides_into_fluids


@configurable
class _WithDefaultKwarg:
    """RFUAVSource-shaped: has a kwarg with a default that isn't in YAML by default."""

    def __init__(self, root: str = "", max_packs: Optional[int] = None) -> None:
        self.root = root
        self.max_packs = max_packs


@configurable
class _WithProperty:
    """Configurable with a settable @property that's not a __init__ param."""

    def __init__(self, x: int = 0) -> None:
        self.x = x
        self._threshold = 10

    @property
    def threshold(self) -> int:
        return self._threshold

    @threshold.setter
    def threshold(self, v: int) -> None:
        self._threshold = v


@configurable
class _WithReadOnlyProperty:
    def __init__(self, x: int = 0) -> None:
        self.x = x

    @property
    def computed(self) -> int:
        return self.x * 2


class _NotConfigurable:
    def __init__(self, a: int = 1) -> None:
        self.a = a


def test_accepted_keys_for_configurable_includes_ctor_and_public_attrs() -> None:
    keys = _accepted_override_keys(_WithProperty)
    assert "x" in keys  # ctor param
    assert "threshold" in keys  # settable @property


def test_accepted_keys_skips_readonly_property() -> None:
    keys = _accepted_override_keys(_WithReadOnlyProperty)
    assert "x" in keys
    assert "computed" not in keys  # read-only @property is skipped


def test_accepted_keys_for_non_configurable_is_ctor_only() -> None:
    keys = _accepted_override_keys(_NotConfigurable)
    assert keys == {"a"}


def test_merge_applies_ctor_kwarg_even_when_missing_from_yaml() -> None:
    """Override for `max_packs` must land even though YAML doesn't set it."""
    fluid = Class(_WithDefaultKwarg, root="/data")
    _merge_overrides_into_fluids({"src": fluid}, {"max_packs": 1})
    assert fluid.kwargs.get("max_packs") == 1


def test_merge_applies_property_kwarg_for_configurable() -> None:
    fluid = Class(_WithProperty, x=0)
    _merge_overrides_into_fluids({"obj": fluid}, {"threshold": 42})
    assert fluid.kwargs.get("threshold") == 42


def test_merge_skips_unknown_kwarg_on_non_configurable() -> None:
    fluid = Class(_NotConfigurable, a=1)
    _merge_overrides_into_fluids({"obj": fluid}, {"typo": 99})
    assert "typo" not in fluid.kwargs


def test_merge_preserves_existing_kwarg_override_path() -> None:
    """The legacy "already in kwargs" path still wins — post-construction toggles stay overridable."""
    fluid = Class(_WithDefaultKwarg, root="/data", max_packs=7)
    _merge_overrides_into_fluids({"src": fluid}, {"max_packs": 1})
    assert fluid.kwargs["max_packs"] == 1


def test_dotted_override_targets_instance_by_name() -> None:
    """`--overlay.visualize true` lands only on the Fluid whose `name: overlay`."""
    overlay = Class(_WithDefaultKwarg, root="/a", name="overlay")
    ls = Class(_WithDefaultKwarg, root="/b", name="labelstudio")
    _merge_overrides_into_fluids(
        {"o": overlay, "l": ls},
        {"overlay.max_packs": 1},
    )
    assert overlay.kwargs.get("max_packs") == 1
    # labelstudio unaffected.
    assert "max_packs" not in ls.kwargs


def test_flat_override_still_broadcasts_to_named_instances() -> None:
    """Plain `--max_packs 1` continues to broadcast (legacy behaviour preserved)."""
    a = Class(_WithDefaultKwarg, root="/a", name="overlay")
    b = Class(_WithDefaultKwarg, root="/b", name="labelstudio")
    _merge_overrides_into_fluids(
        {"a": a, "b": b},
        {"max_packs": 5},
    )
    assert a.kwargs["max_packs"] == 5
    assert b.kwargs["max_packs"] == 5


def test_dotted_override_ignored_when_head_doesnt_match_name() -> None:
    """Unknown names don't fall back to broadcast — avoid surprise matches."""
    fluid = Class(_WithDefaultKwarg, root="/a", name="overlay")
    _merge_overrides_into_fluids(
        {"o": fluid},
        {"wrong_name.max_packs": 99},
    )
    # The dotted head "wrong_name" doesn't match "overlay" — the tail is NOT
    # applied flatly either, because the user intended a targeted override.
    assert "max_packs" not in fluid.kwargs


def test_dotted_override_on_unnamed_fluid_is_noop() -> None:
    """Without a YAML `name`, dotted keys can't target the instance."""
    fluid = Class(_WithDefaultKwarg, root="/a")  # no name
    _merge_overrides_into_fluids(
        {"o": fluid},
        {"overlay.max_packs": 1},
    )
    assert "max_packs" not in fluid.kwargs


def test_merge_broadcasts_to_nested_fluids() -> None:
    inner = Class(_WithDefaultKwarg, root="/inner")
    outer = Class(_WithDefaultKwarg, root="/outer", sub=inner)
    _merge_overrides_into_fluids({"s": outer}, {"max_packs": 5})
    # Both Fluids had `max_packs` in their ctor → both get the override.
    assert outer.kwargs["max_packs"] == 5
    assert inner.kwargs["max_packs"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
