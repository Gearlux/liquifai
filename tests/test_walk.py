"""Tokenizer + the ONE argv walk shared by dispatch and completion.

Three contracts:

1. ``--`` ends option parsing — every following token is a literal value, so a
   dash-leading positional is representable at last.
2. A value-taking global flag owns the next token (``--level run`` is not the
   ``run`` command) while both tokens still reach ``_parse_globals``.
3. The router and the completion engine run the SAME walk over their two data
   shapes, so a line routes identically for dispatch and for TAB.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from liquifai import LiquifyApp, router
from liquifai.completion import complete, serialize_app
from liquifai.completion.engine import _TreeNav, complete_from_tree
from liquifai.context import set_context
from liquifai.router import _AppNav
from liquifai.walk import Token, literal_texts, option_texts, tokenize, walk_invocation

# ---------------------------------------------------------------------------
# tokenize / Token
# ---------------------------------------------------------------------------


def test_tokenize_marks_everything_after_the_separator_literal() -> None:
    toks = tokenize(["seek", "--", "-5", "--lr"])
    assert [t.text for t in toks] == ["seek", "-5", "--lr"]  # the `--` itself is dropped
    assert [t.literal for t in toks] == [False, True, True]


def test_tokenize_without_separator_marks_nothing_literal() -> None:
    assert all(not t.literal for t in tokenize(["a", "--lr", "1"]))


def test_second_separator_is_an_ordinary_literal_token() -> None:
    """Past the first ``--`` there is no more option syntax, including ``--``."""
    toks = tokenize(["--", "--", "x"])
    assert [t.text for t in toks] == ["--", "x"]
    assert all(t.literal for t in toks)


def test_literal_token_never_stops_a_positional_or_looks_like_a_flag() -> None:
    assert Token("-5", literal=True).stops_positional() is False
    assert Token("-5").stops_positional() is True
    assert Token("-5", literal=True).is_flag_like() is False
    assert Token("-5").is_flag_like() is True


def test_option_and_literal_text_partitions_are_complementary() -> None:
    toks = tokenize(["--lr", "1", "--", "raw"])
    assert option_texts(toks) == ["--lr", "1"]
    assert literal_texts(toks) == ["raw"]


# ---------------------------------------------------------------------------
# Router: `--` makes dash-leading positionals representable
# ---------------------------------------------------------------------------


def _seek_app() -> LiquifyApp:
    app = LiquifyApp("t")

    @app.command(name="seek", positionals=["offset", "path"])
    def seek(offset: str = "", path: str = "") -> None:
        """Seek."""

    return app


def test_dash_leading_positional_needs_the_separator() -> None:
    """Without ``--`` a ``-5`` still (correctly) reads as an option token."""
    inv = router.route(_seek_app(), ["seek", "-5", "/tmp/x"])
    assert inv.positional_values == []
    assert [t.text for t in inv.remaining_tokens] == ["-5", "/tmp/x"]


def test_separator_binds_dash_leading_positionals() -> None:
    inv = router.route(_seek_app(), ["seek", "--", "-5", "/tmp/x"])
    assert inv.positional_values == ["-5", "/tmp/x"]
    assert inv.remaining_tokens == []


def test_separator_protects_a_literal_that_looks_like_a_global_flag() -> None:
    inv = router.route(_seek_app(), ["seek", "--", "--help", "x"])
    assert inv.positional_values == ["--help", "x"]


def test_run_does_not_show_help_for_a_literal_help_token(monkeypatch: Any, capsys: Any) -> None:
    """`app seek -- --help` runs the command; it does not print help."""
    app = LiquifyApp("t")
    seen: Dict[str, Any] = {}

    @app.command(name="seek", positionals=["offset"])
    def seek(offset: str = "") -> None:
        """Seek."""
        seen["offset"] = offset

    monkeypatch.setattr(sys, "argv", ["t", "seek", "--", "--help"])
    set_context(None)
    app.run()
    assert seen["offset"] == "--help"
    assert "Global Options" not in capsys.readouterr().out


def test_unbound_literals_are_warned_not_silently_dropped(monkeypatch: Any) -> None:
    app = LiquifyApp("t")
    warnings: List[str] = []

    @app.command(name="seek", positionals=["offset"])
    def seek(offset: str = "") -> None:
        """Seek."""

    monkeypatch.setattr(sys, "argv", ["t", "seek", "--", "-5", "extra1", "extra2"])
    set_context(None)
    app.run()
    assert app.context is not None
    monkeypatch.setattr(app.context.logger, "warning", warnings.append)
    app._warn_unbound_literals(app._route(["seek", "--", "-5", "extra1", "extra2"]))
    assert any("after `--`" in w and "extra1" in w for w in warnings)


# ---------------------------------------------------------------------------
# Value-taking global flags own their value (both walkers)
# ---------------------------------------------------------------------------


def test_global_value_flag_value_is_not_mistaken_for_a_command() -> None:
    """`--level seek` is a flag+value pair, not the `seek` command."""
    inv = router.route(_seek_app(), ["--level", "seek"])
    assert inv.target_func is None
    # Both tokens survive for _parse_globals to consume.
    assert [t.text for t in inv.remaining_tokens] == ["--level", "seek"]


def test_global_value_flag_before_a_real_command_still_routes() -> None:
    inv = router.route(_seek_app(), ["--level", "DEBUG", "seek", "a", "b"])
    assert inv.positional_values == ["a", "b"]


# ---------------------------------------------------------------------------
# The two Nav adapters agree — the anti-drift pin
# ---------------------------------------------------------------------------


def _nav_pair(app: LiquifyApp) -> Tuple[Any, Any]:
    return _AppNav(app), _TreeNav(serialize_app(app))


def test_app_nav_and_tree_nav_answer_identically() -> None:
    app = LiquifyApp("t")
    sub = LiquifyApp("group")

    @sub.command(name="inner", positionals=["a"])
    def inner(a: str = "") -> None:
        """Inner."""

    @app.script_command(name="run")
    def run() -> None:
        """Run."""

    app.add_app(sub, "group", aliases=["g"])

    app_nav, tree_nav = _nav_pair(app)
    for nav in (app_nav, tree_nav):
        assert nav.has_command("run")
        assert nav.is_script_command("run")
        assert not nav.has_command("nope")
        assert nav.sub_app("nope") is None
        child = nav.sub_app("g")  # alias resolves on both
        assert child is not None
        assert child.positionals("inner") == ["a"]


def test_both_walkers_reach_the_same_command_and_positionals() -> None:
    app = LiquifyApp("t")

    @app.command(name="seek", positionals=["offset", "path"])
    def seek(offset: str = "", path: str = "") -> None:
        """Seek."""

    argv = ["seek", "--", "-5", "/tmp/x"]
    routed = router.route(app, argv)
    walked = walk_invocation(tokenize(argv), _TreeNav(serialize_app(app)), lambda _t: None)

    assert walked.cmd_name == "seek"
    assert walked.positional_values == routed.positional_values == ["-5", "/tmp/x"]


# ---------------------------------------------------------------------------
# Completion resolves a promoted config through confluid's search tiers
# ---------------------------------------------------------------------------


def _config_app() -> LiquifyApp:
    app = LiquifyApp("demo")

    @app.script_command(name="run")
    def run(threshold: float = 0.5) -> None:
        """Run."""

    return app


@pytest.fixture()
def _isolated_config_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    """A sandboxed CWD with a ``./config/`` tier and no leak from the real HOME."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg-dirs"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "demo.yaml").write_text("learning_rate: 0.1\nhidden_units: 32\n")
    return tmp_path


def test_completion_offers_yaml_keys_for_a_config_in_the_config_tier(_isolated_config_dir: Path) -> None:
    """The config-present branch used to be dead for every tier except CWD.

    ``demo run demo`` resolves to ``./config/demo.yaml`` for dispatch, so TAB
    must offer that YAML's override keys too — not just the signature flags.
    """
    app = _config_app()
    cands = complete(app, ["demo", "run", "demo", ""], 3)
    assert "--learning_rate" in cands
    assert "--hidden_units" in cands
    assert "--threshold" in cands  # the signature flag is still there


def test_completion_and_dispatch_consume_the_same_promoted_config(_isolated_config_dir: Path) -> None:
    import confluid

    confluid.set_app_name("demo")
    app = _config_app()
    inv = router.route(app, ["run", "demo"])
    assert inv.config_path is not None
    assert inv.config_path.name == "demo.yaml"

    walked = walk_invocation(tokenize(["run", "demo"]), _TreeNav(serialize_app(app)), lambda t: Path(t + ".yaml"))
    assert walked.consumed_config is True


def test_completion_falls_back_to_signature_flags_when_no_config_resolves(_isolated_config_dir: Path) -> None:
    """An unresolvable token must not fabricate override keys."""
    cands = complete_from_tree(serialize_app(_config_app()), ["demo", "run", "nosuch", ""], 3)
    assert "--threshold" in cands
    assert not any(c.startswith("--learning_rate") for c in cands)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
