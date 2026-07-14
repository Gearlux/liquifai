"""Pins :mod:`liquifai.grammar` — the single source of truth for the CLI grammar.

The parser (``core._parse_globals``), completion (``completion.GLOBAL_FLAGS``
et al.), and ``--help`` all DERIVE from :data:`liquifai.grammar.GLOBAL_FLAG_SPECS`.
These tests pin the derivations and — most importantly — that help output can
never again drift from the declared flag vocabulary (pre-consolidation,
``--log-dir`` / ``--docs`` / ``--refresh-completions`` were parsed and completed
but missing from ``--help``).
"""

from pathlib import Path
from typing import Any

import pytest

from liquifai import grammar
from liquifai.grammar import (
    GLOBAL_FLAG_SPECS,
    GLOBAL_FLAGS,
    GLOBAL_VALUE_FLAGS,
    PATH_VALUE_FLAGS,
    SHELL_VALUE_FLAGS,
    flag_display,
    looks_like_arg,
    looks_like_key,
    stops_positional,
)

# ---------------------------------------------------------------------------
# Derived flag sets
# ---------------------------------------------------------------------------


def test_global_flags_contains_every_visible_spelling() -> None:
    for spec in GLOBAL_FLAG_SPECS:
        for flag in spec.flags:
            if spec.hidden:
                assert flag not in GLOBAL_FLAGS
            else:
                assert flag in GLOBAL_FLAGS


def test_hidden_flags_excluded_from_all_derived_sets() -> None:
    assert "--refresh-completion-value" not in GLOBAL_FLAGS
    assert "--refresh-completion-value" not in GLOBAL_VALUE_FLAGS


def test_path_and_shell_value_flags() -> None:
    assert PATH_VALUE_FLAGS == {"--config", "-c", "--log-dir"}
    assert SHELL_VALUE_FLAGS == {"--install-completion", "--show-completion"}


def test_global_value_flags_are_exactly_the_visible_value_takers() -> None:
    expected = {f for s in GLOBAL_FLAG_SPECS if not s.hidden and s.takes_value for f in s.flags}
    assert GLOBAL_VALUE_FLAGS == expected
    # Boolean flags never appear.
    assert "--debug" not in GLOBAL_VALUE_FLAGS
    assert "--help" not in GLOBAL_VALUE_FLAGS


def test_completion_module_reuses_grammar_sets() -> None:
    """completion.py must re-export the grammar sets, not restate them."""
    from liquifai import completion as comp

    assert comp.GLOBAL_FLAGS is GLOBAL_FLAGS
    assert comp.GLOBAL_VALUE_FLAGS is GLOBAL_VALUE_FLAGS
    assert comp.PATH_VALUE_FLAGS is PATH_VALUE_FLAGS
    assert comp.SHELL_VALUE_FLAGS is SHELL_VALUE_FLAGS


def test_flag_display_orders_short_form_first() -> None:
    config_spec = next(s for s in GLOBAL_FLAG_SPECS if s.dest == "config_path")
    assert flag_display(config_spec) == "-c, --config PATH"
    level_spec = next(s for s in GLOBAL_FLAG_SPECS if s.dest == "log_level")
    assert flag_display(level_spec) == "--level LEVEL"


# ---------------------------------------------------------------------------
# Token classifiers (moved verbatim from core.py — behavior pinned here)
# ---------------------------------------------------------------------------


def test_stops_positional() -> None:
    assert stops_positional("")
    assert stops_positional("--flag")
    assert stops_positional("-c")
    assert stops_positional("+add=1")
    assert stops_positional("~del")
    assert stops_positional("key=value")
    assert not stops_positional("plain-token")
    assert not stops_positional("file.yaml")


def test_looks_like_arg() -> None:
    assert looks_like_arg("--flag")
    assert looks_like_arg("+add=1")
    assert looks_like_arg("~del")
    assert not looks_like_arg("")
    assert not looks_like_arg("value")
    assert not looks_like_arg("-single-dash")  # only --/+/~ start a new option


def test_looks_like_key() -> None:
    assert looks_like_key("trainer.max_epochs")
    assert looks_like_key("lr")
    assert not looks_like_key("http://x?a")
    assert not looks_like_key("a/b")
    assert not looks_like_key("1leading-digit")


# ---------------------------------------------------------------------------
# Anti-drift pins: parser and help both cover the whole declared vocabulary.
# ---------------------------------------------------------------------------


def test_parse_globals_consumes_every_bootstrap_flag() -> None:
    from liquifai import LiquifyApp

    app = LiquifyApp(name="grammar-app")
    argv = [
        "--config",
        "cfg.yaml",
        "--scope",
        "a,b",
        "--debug",
        "--level",
        "TRACE",
        "--console-level",
        "INFO",
        "--file-level",
        "DEBUG",
        "--log-dir",
        "/tmp/logs",
        "leftover",
    ]
    config_path, scopes, debug, log_overrides, remaining = app._parse_globals(argv)
    assert config_path == Path("cfg.yaml")
    assert scopes == ["a", "b"]
    assert debug is True
    assert log_overrides == {
        "log_level": "TRACE",
        "console_level": "INFO",
        "file_level": "DEBUG",
        "log_dir": Path("/tmp/logs"),
    }
    assert remaining == ["leftover"]


def test_parse_globals_short_forms() -> None:
    from liquifai import LiquifyApp

    app = LiquifyApp(name="grammar-app")
    config_path, scopes, debug, _, remaining = app._parse_globals(["-c", "x.yaml", "-s", "fast", "-d"])
    assert config_path == Path("x.yaml")
    assert scopes == ["fast"]
    assert debug is True
    assert remaining == []


def test_help_lists_every_visible_global_flag(capsys: Any) -> None:
    """THE drift pin: every visible declared flag appears in help output, and
    hidden flags never do. Pre-consolidation, --log-dir / --docs /
    --refresh-completions were accepted by the CLI but absent from --help."""
    from liquifai import LiquifyApp

    app = LiquifyApp(name="grammar-app", description="drift pin")
    app._show_help(app)
    out = capsys.readouterr().out

    for spec in GLOBAL_FLAG_SPECS:
        primary = spec.flags[0]
        if spec.hidden:
            assert primary not in out
        else:
            assert primary in out, f"visible flag {primary} missing from --help output"


def test_grammar_module_is_stdlib_only() -> None:
    """The fast path imports grammar — it must never pull in confluid/loggair/rich."""
    import importlib
    import sys

    for heavy in ("confluid", "loggair", "rich"):
        assert not any(
            m == heavy for m in getattr(importlib.import_module("liquifai.grammar"), "__dict__", {})
        ), f"grammar.py must not import {heavy}"
    # Source-level check: no heavy import statements at all.
    source = Path(grammar.__file__).read_text()
    for heavy in ("import confluid", "import loggair", "import rich", "from confluid", "from loggair", "from rich"):
        assert heavy not in source
    assert "liquifai.grammar" in sys.modules


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
