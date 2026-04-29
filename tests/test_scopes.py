"""Direct unit tests for ``liquifai.scopes.resolve_scopes``.

Confirms in-place position-preserving unwrap, hierarchy, aliases, negative
scopes, scope-metadata cleanup, and that inactive scope blocks pass through
unchanged (liquifai cannot tell whether a top-level dict-valued key is a
scope or domain data, so it does not strip).
"""

import pytest

from liquifai.scopes import resolve_scopes


def test_no_active_scopes_strips_metadata_only() -> None:
    config = {"val": 1, "scope_aliases": {"d": "debug"}, "scopes": ["debug"]}
    out = resolve_scopes(config, [])
    assert out == {"val": 1}


def test_basic_active_scope_unwraps_in_place() -> None:
    config = {"val": 1, "debug": {"val": 10}, "prod": {"val": 100}}
    out = resolve_scopes(config, ["debug"])
    # debug splices in; prod left alone (we do not guess that prod is a scope)
    assert out["val"] == 10
    assert out["prod"] == {"val": 100}
    assert "debug" not in out


def test_inactive_scope_blocks_pass_through() -> None:
    """``prod:`` is preserved when only ``debug`` is active. Liquifai cannot
    distinguish a scope wrapper from domain data, so it keeps the wrapper."""
    config = {"val": 1, "debug": {"val": 10}, "prod": {"val": 100}}
    out = resolve_scopes(config, ["debug"])
    assert "prod" in out
    assert out["prod"] == {"val": 100}


def test_unwrap_preserves_position() -> None:
    """The unwrapped scope's contents replace the wrapper at its slot, not at the end."""
    config = {"a": 1, "debug": {"b": 2}, "c": 3}
    out = resolve_scopes(config, ["debug"])
    assert list(out.items()) == [("a", 1), ("b", 2), ("c", 3)]


def test_unwrap_collision_keeps_original_position() -> None:
    """When the unwrapped value's key already exists at top, Python dict
    re-assignment keeps the original slot. The unwrapped VALUE wins."""
    config = {"val": 1, "debug": {"val": 10}}
    out = resolve_scopes(config, ["debug"])
    assert out == {"val": 10}
    assert list(out.items()) == [("val", 10)]


def test_hierarchical_scopes_apply_in_order() -> None:
    config = {"val": 1, "prod": {"val": 100}, "prod.gpu": {"gpu": True}}
    out = resolve_scopes(config, ["prod.gpu"])
    assert out["val"] == 100
    assert out["gpu"] is True
    assert "prod" not in out
    assert "prod.gpu" not in out


def test_scope_aliases() -> None:
    config = {
        "scope_aliases": {"dev": ["debug", "local"]},
        "debug": {"lr": 0.1},
        "local": {"db": "sqlite"},
    }
    out = resolve_scopes(config, ["dev"])
    assert out["lr"] == 0.1
    assert out["db"] == "sqlite"
    assert "scope_aliases" not in out


def test_negative_scope_applies_when_target_inactive() -> None:
    config = {"lr": 0.001, "not debug": {"lr": 0.0001}, "debug": {"lr": 0.1}}
    # Without debug: 'not debug' applies in place
    out = resolve_scopes(config, [])
    assert out["lr"] == 0.0001
    assert "not debug" not in out
    # debug block left alone (inactive scope = pass-through)
    assert out["debug"] == {"lr": 0.1}


def test_negative_scope_dropped_when_target_active() -> None:
    config = {"lr": 0.001, "not debug": {"lr": 0.0001}, "debug": {"lr": 0.1}}
    out = resolve_scopes(config, ["debug"])
    # debug splices, then 'not debug' is dropped because debug is active
    assert out["lr"] == 0.1
    assert "not debug" not in out
    assert "debug" not in out


def test_scope_cleanup_strips_metadata_keys_only() -> None:
    """Verify scope metadata is gone but inactive scope blocks are kept."""
    config = {
        "val": 1,
        "scope_aliases": {"d": "debug"},
        "debug": {"val": 2},
        "prod": {"val": 3},
        "not debug": {"val": 4},
    }
    out = resolve_scopes(config, ["debug"])
    assert out["val"] == 2
    assert out["prod"] == {"val": 3}  # inactive scope blocks pass through
    assert "debug" not in out
    assert "not debug" not in out
    assert "scope_aliases" not in out


def test_circular_alias_raises() -> None:
    config = {"scope_aliases": {"a": "b", "b": "a"}}
    with pytest.raises(ValueError, match="Circular scope alias"):
        resolve_scopes(config, ["a"])


def test_alias_chain_resolves_to_terminal_scopes() -> None:
    config = {"scope_aliases": {"a": ["b"]}, "b": {"x": 1}}
    out = resolve_scopes(config, ["a"])
    assert out["x"] == 1
    assert "b" not in out


def test_dict_value_is_replaced_wholesale() -> None:
    """Per spec: dict-valued unwrap REPLACES, no deep-merge."""
    config = {"obj": {"a": 1, "b": 2}, "debug": {"obj": {"c": 3}}}
    out = resolve_scopes(config, ["debug"])
    # debug spliced — obj's value is replaced, not merged
    assert out["obj"] == {"c": 3}
