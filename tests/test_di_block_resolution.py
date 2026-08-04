"""Pins DI config-block resolution (:func:`liquifai.di.resolve_kwargs`).

Block lookup is by MEMBERSHIP, not truthiness: a present-but-empty block
(``DiWidget: {}`` or YAML-null ``DiWidget:``) SELECTS that block — the
pre-consolidation ``cfg.get(a) or cfg.get(b) or cfg`` chain falsy-fell-through
an empty block to the param-name block and ultimately splatted the ENTIRE
top-level config into the instance's kwargs.

Two orthogonal mechanisms are pinned apart here:

* **Block selection** (liquifai's job, fixed here): which config sub-dict
  seeds the synthesized Instance's kwargs.
* **Confluid broadcasting** (sanctioned, unchanged): ``materialize(...,
  context=...)`` still broadcasts accept-listed TOP-LEVEL keys into the
  instance regardless of the block — that is how flat configs (the sonair
  pattern) work, and the whole-config fallback below relies on it.
"""

from typing import Any, Dict

import confluid
import pytest

from liquifai.context import LiquifyContext
from liquifai.di import resolve_kwargs
from liquifai.overrides import apply_overrides


@confluid.configurable
class DiWidget:
    def __init__(self, size: int = 3, label: str = "default"):
        self.size = size
        self.label = label


class _StubLogger:
    def debug(self, msg: str) -> None:
        pass

    def trace(self, msg: str) -> None:
        pass


def _context(config: Dict[str, Any]) -> LiquifyContext:
    ctx = LiquifyContext(name="di-test")
    ctx.logger = _StubLogger()
    ctx.config_data = config
    return ctx


def _command(widget: DiWidget) -> None:  # pragma: no cover - signature donor only
    pass


def test_empty_class_block_shields_param_name_block() -> None:
    """``DiWidget: {}`` is a real (empty) block — it must NOT falsy-fall-through
    to the ``widget:`` param-name block like the old ``or`` chain did."""
    ctx = _context({"DiWidget": {}, "widget": {"size": 7}})
    kwargs = resolve_kwargs(ctx, _command)
    assert isinstance(kwargs["widget"], DiWidget)
    assert kwargs["widget"].size == 3  # ctor default — the {} block was honored


def test_yaml_null_block_shields_param_name_block() -> None:
    """A YAML-null block (``DiWidget:`` with no value) means "all defaults"."""
    ctx = _context({"DiWidget": None, "widget": {"size": 7}})
    kwargs = resolve_kwargs(ctx, _command)
    assert kwargs["widget"].size == 3


def test_empty_block_confluid_broadcast_still_applies() -> None:
    """Confluid broadcasting is orthogonal to block selection: an accept-listed
    TOP-LEVEL key (``size``) still reaches the instance through ``materialize``'s
    context broadcast even when the declared block is empty."""
    ctx = _context({"DiWidget": {}, "size": 99})
    kwargs = resolve_kwargs(ctx, _command)
    assert kwargs["widget"].size == 99


def test_class_name_block_is_used() -> None:
    ctx = _context({"DiWidget": {"size": 5}})
    kwargs = resolve_kwargs(ctx, _command)
    assert kwargs["widget"].size == 5


def test_param_name_block_is_used() -> None:
    ctx = _context({"widget": {"size": 7}})
    kwargs = resolve_kwargs(ctx, _command)
    assert kwargs["widget"].size == 7


def test_class_name_takes_precedence_over_param_name() -> None:
    ctx = _context({"DiWidget": {"size": 5}, "widget": {"size": 7}})
    kwargs = resolve_kwargs(ctx, _command)
    assert kwargs["widget"].size == 5


def test_a_cli_override_beats_a_class_name_block() -> None:
    """The block must stay IN the document, or it cannot lose a precedence contest.

    Confluid decides bare-vs-addressed by document position. A class-name block
    copied into the synthesized marker's kwargs has left the document, and a value
    with no position can never be outranked — so ``--size 9`` lost to
    ``DiWidget: {size: 5}`` wherever it sat. `resolve_kwargs` therefore does NOT
    hoist a class-name block: it is confluid's own addressed-block spelling, read
    from the context document where the author wrote it.
    """
    config = apply_overrides({"DiWidget": {"size": 5}}, {"size": 9}, [])
    kwargs = resolve_kwargs(_context(config), _command)
    assert kwargs["widget"].size == 9


def test_a_class_name_block_still_wins_over_an_earlier_bare_key() -> None:
    """The other direction — position decides, not a "CLI/bare always wins" tier.

    Without this the fix above could be "make the bare key win", which is a
    specificity tier by another name and would break every config that overrides
    a global default at one node.
    """
    kwargs = resolve_kwargs(_context({"size": 1, "DiWidget": {"size": 5}}), _command)
    assert kwargs["widget"].size == 5


def test_flat_config_fallback_preserved() -> None:
    """No class-/param-name key at all → the whole top-level config is the
    block (flat-config broadcasting, the sonair pattern)."""
    ctx = _context({"size": 42, "label": "flat"})
    kwargs = resolve_kwargs(ctx, _command)
    assert kwargs["widget"].size == 42
    assert kwargs["widget"].label == "flat"


def test_empty_block_still_resolves_sibling_plain_params() -> None:
    """The block fix only scopes the CONFIGURABLE param — plain params keep
    resolving from the top-level config as before. ``count`` is not a
    ``DiWidget`` attribute, so it can't broadcast into the widget."""

    def cmd(widget: DiWidget, count: int = 0) -> None:  # pragma: no cover
        pass

    ctx = _context({"DiWidget": {}, "widget": {"size": 7}, "count": 9})
    kwargs = resolve_kwargs(ctx, cmd)
    assert kwargs["widget"].size == 3
    assert kwargs["count"] == 9


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
