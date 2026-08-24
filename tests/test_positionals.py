"""Positional-argument support on ``LiquifyApp.command`` / ``script_command``.

Positionals are declared via ``@command(positionals=[...])`` and bound, in
order, to the command function's parameters as verbatim strings. Consumption
stops at the first flag-like (``-``/``+``/``~``) or ``key=value`` token, so the
positional form, the equals form, and trailing flags all interoperate.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Tuple

from liquifai import LiquifyApp
from liquifai.context import set_context


def _make_app(positionals: Any = None) -> Tuple[LiquifyApp, Dict[str, Any]]:
    """Build a one-command app capturing the DI-resolved kwargs."""
    app = LiquifyApp(name="t")
    captured: Dict[str, Any] = {}

    @app.command(name="cmd", positionals=positionals)
    def cmd(name: str = "", version: str = "", path: str = "") -> None:
        captured.update(name=name, version=version, path=path)

    return app, captured


def _run(app: LiquifyApp, argv: list[str], monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["t", *argv])
    set_context(None)  # type: ignore[arg-type]
    app.run()


def test_positionals_bind_in_order(monkeypatch: Any) -> None:
    app, captured = _make_app(positionals=["name", "version"])
    _run(app, ["cmd", "foo", "1.0"], monkeypatch)
    assert captured["name"] == "foo"
    # Crucially bound verbatim as a string — NOT coerced to float 1.0.
    assert captured["version"] == "1.0"
    assert isinstance(captured["version"], str)


def test_version_like_value_is_not_numerically_coerced(monkeypatch: Any) -> None:
    app, captured = _make_app(positionals=["name", "version"])
    _run(app, ["cmd", "ds", "1.10"], monkeypatch)
    # "1.10" must survive intact (float coercion would lose the trailing zero).
    assert captured["version"] == "1.10"


def test_partial_positionals_fall_back_to_defaults(monkeypatch: Any) -> None:
    app, captured = _make_app(positionals=["name", "version"])
    _run(app, ["cmd", "foo"], monkeypatch)
    assert captured["name"] == "foo"
    assert captured["version"] == ""  # default


def test_positionals_then_trailing_flags(monkeypatch: Any) -> None:
    app, captured = _make_app(positionals=["name", "version"])
    _run(app, ["cmd", "foo", "1.0", "--path", "/tmp/x"], monkeypatch)
    assert captured["name"] == "foo"
    assert captured["version"] == "1.0"
    assert captured["path"] == "/tmp/x"


def test_equals_form_stops_positional_consumption(monkeypatch: Any) -> None:
    # `cmd name=foo` should route through the override path (key=value), not be
    # swallowed as the positional value "name=foo".
    app, captured = _make_app(positionals=["name"])
    _run(app, ["cmd", "name=foo"], monkeypatch)
    assert captured["name"] == "foo"


def test_flag_stops_positional_consumption(monkeypatch: Any) -> None:
    app, captured = _make_app(positionals=["name"])
    _run(app, ["cmd", "--name", "bar"], monkeypatch)
    assert captured["name"] == "bar"


def test_explicit_flag_wins_over_positional(monkeypatch: Any) -> None:
    # Mixed input is user error, but explicit --flag should win deterministically.
    app, captured = _make_app(positionals=["name", "version"])
    _run(app, ["cmd", "foo", "--name", "bar"], monkeypatch)
    assert captured["name"] == "bar"
    assert captured["version"] == ""


def test_no_positionals_declared_is_backward_compatible(monkeypatch: Any) -> None:
    # A bare token with no declared positionals is dropped (legacy behaviour);
    # the command still runs with defaults.
    app, captured = _make_app(positionals=None)
    _run(app, ["cmd", "stray"], monkeypatch)
    assert captured["name"] == ""
    assert captured["version"] == ""


def test_help_renders_positionals(monkeypatch: Any, capsys: Any) -> None:
    app, _ = _make_app(positionals=["name", "version"])
    _run(app, ["cmd", "--help"], monkeypatch)
    out = capsys.readouterr().out
    assert "<name>" in out
    assert "<version>" in out


def test_script_command_binds_positionals_after_config(tmp_path: Path, monkeypatch: Any) -> None:
    config_file = tmp_path / "cfg.yaml"
    config_file.write_text("val: 1\n")

    app = LiquifyApp(name="t")
    captured: Dict[str, Any] = {}

    @app.script_command(name="run", positionals=["name"])
    def run_cmd(name: str = "") -> None:
        captured["name"] = name

    # config path is consumed by the promotion peek first, then "extra" binds
    # to the `name` positional.
    _run(app, ["run", str(config_file), "extra"], monkeypatch)
    assert captured["name"] == "extra"


def test_sub_app_command_positionals(monkeypatch: Any) -> None:
    parent = LiquifyApp(name="t")
    sub = LiquifyApp(name="dataset")
    parent.add_app(sub, "dataset")
    parent.add_app(sub, "ds")  # alias
    captured: Dict[str, Any] = {}

    @sub.command(name="download", positionals=["name", "version"])
    def download(name: str = "", version: str = "") -> None:
        captured.update(name=name, version=version)

    _run(parent, ["ds", "download", "mydata", "2.0"], monkeypatch)
    assert captured == {"name": "mydata", "version": "2.0"}


# ---------------------------------------------------------------------------
# The default command's positionals bind without its name on the line
# ---------------------------------------------------------------------------


def _default_app() -> Tuple[LiquifyApp, Dict[str, Any]]:
    app = LiquifyApp(name="t")
    captured: Dict[str, Any] = {}

    @app.command(name="ws", default=True, positionals=["workspace"])
    def ws(workspace: str = "") -> None:
        captured.update(workspace=workspace)

    @app.command(name="other")
    def other() -> None:
        captured.update(other=True)

    return app, captured


def test_default_command_positional_binds_without_a_name_token(monkeypatch: Any) -> None:
    app, captured = _default_app()
    _run(app, ["w.yaml"], monkeypatch)
    assert captured == {"workspace": "w.yaml"}


def test_default_command_positional_reaches_the_command_even_when_it_looks_like_a_typo(monkeypatch: Any) -> None:
    # The command decides what to do with it (a clear "file not found") — the token is
    # no longer silently ignored with a warning.
    app, captured = _default_app()
    _run(app, ["nope"], monkeypatch)
    assert captured == {"workspace": "nope"}


def test_default_command_flag_form_still_binds(monkeypatch: Any) -> None:
    app, captured = _default_app()
    _run(app, ["--workspace", "w.yaml"], monkeypatch)
    assert captured == {"workspace": "w.yaml"}


def test_explicit_command_still_wins_over_the_default_positional(monkeypatch: Any) -> None:
    app, captured = _default_app()
    _run(app, ["other", "w.yaml"], monkeypatch)
    assert captured == {"other": True}


def test_default_command_without_positionals_is_unchanged(monkeypatch: Any) -> None:
    app = LiquifyApp(name="t")
    ran: Dict[str, Any] = {}

    @app.command(name="serve", default=True)
    def serve() -> None:
        ran["serve"] = True

    _run(app, ["stray"], monkeypatch)  # runs on defaults; the token is reported by the override parser as before
    assert ran == {"serve": True}
