"""The deprecated ``liquifai.core`` re-export aliases (removal: v1.0).

The consolidation split moved these helpers into ``di`` / ``overrides`` /
``grammar``, but external callers imported them from ``liquifai.core`` under
underscore-prefixed names. They still resolve — served by a PEP-562 module
``__getattr__`` so that ACCESS is observable — and each access emits a
``DeprecationWarning`` naming the exact replacement import.

Three contracts are pinned:

1. every alias still returns the right object,
2. every alias warns, with actionable text,
3. nothing inside liquifai (source OR tests) still uses one — so the eventual
   deletion is a pure external-consumer migration, not an internal refactor.
"""

import warnings
from pathlib import Path
from typing import Any, Tuple

import pytest

import liquifai.core as core
from liquifai import di, grammar, overrides

ALIASES = sorted(core._DEPRECATED_ALIASES)

#: alias -> the object it must resolve to.
EXPECTED = {
    "_confluid_active_context": di.confluid_active_context,
    "_deep_flow": di.deep_flow,
    "_parse_override_args": overrides.parse_override_args,
    "_merge_overrides_into_fluids": overrides.merge_overrides_into_fluids,
    "_delete_dotted_key": overrides.delete_dotted_key,
    "_expand_strings": overrides.expand_strings,
    "_stops_positional": grammar.stops_positional,
    "_looks_like_arg": grammar.looks_like_arg,
    "_looks_like_key": grammar.looks_like_key,
}


def _access(name: str) -> Tuple[Any, "warnings.WarningMessage"]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = getattr(core, name)
    assert len(caught) == 1, f"{name} should emit exactly one warning, got {len(caught)}"
    return value, caught[0]


def test_the_deprecated_surface_is_exactly_the_nine_known_aliases() -> None:
    """A new alias must be a deliberate decision, not an accident."""
    assert set(ALIASES) == set(EXPECTED)


@pytest.mark.parametrize("name", ALIASES)
def test_alias_still_resolves_to_the_owning_module_object(name: str) -> None:
    value, _ = _access(name)
    assert value is EXPECTED[name]


@pytest.mark.parametrize("name", ALIASES)
def test_alias_access_warns_with_the_replacement_import(name: str) -> None:
    _, warning = _access(name)
    assert issubclass(warning.category, DeprecationWarning)
    message = str(warning.message)
    module_name, public_name = core._DEPRECATED_ALIASES[name]
    assert "v1.0" in message
    assert f"from {module_name} import {public_name}" in message


def test_unknown_attribute_still_raises_attribute_error() -> None:
    """The PEP-562 hook must not swallow genuine typos."""
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        core.nope  # type: ignore[attr-defined]


def test_liquifai_itself_no_longer_uses_any_alias() -> None:
    """Internal code and tests import from the owning modules.

    This is what makes the v1.0 deletion a pure external migration: if it fails,
    something inside the package regressed onto the deprecated surface (and
    would start emitting warnings during ordinary runs).
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in list((root / "liquifai").rglob("*.py")) + list((root / "tests").rglob("*.py")):
        if path.name in ("core.py", "test_deprecated_aliases.py"):
            continue  # the definition site and this file name them legitimately
        text = path.read_text()
        for alias in ALIASES:
            if f"core import {alias}" in text or f"core.{alias}" in text:
                offenders.append(f"{path.relative_to(root)}: {alias}")
    assert not offenders, "internal code must import from the owning module:\n  " + "\n  ".join(offenders)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
