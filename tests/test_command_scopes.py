"""``--scope`` activates a scope of the APP's own config, on every command; and help text keeps its brackets."""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from liquifai import LiquifyApp


def _run(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


def _help(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> str:
    monkeypatch.setenv("COLUMNS", "200")
    _run(app, argv, monkeypatch)
    return str(capsys.readouterr().out)


def test_a_scope_activates_on_the_apps_own_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    own = tmp_path / "own.yaml"
    own.write_text("port: 1\nonnx: !scope:onnx\n  port: 2\n")
    app = LiquifyApp(name="probe")
    calls: List[Dict[str, Any]] = []

    @app.command()
    def plain(port: int = 0) -> None:
        """A command reading the app's config."""
        calls.append({"port": port})

    _run(app, ["plain", "-c", str(own), "--scope", "onnx"], monkeypatch)
    assert calls[-1] == {"port": 2}


def test_help_text_keeps_square_brackets(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """Help text is printed as plain text: ``[key=value, ...]`` once read as a style tag and vanished."""
    app = LiquifyApp(name="probe", description="Runs [stage] steps.")

    @app.command(positionals=["option"])
    def train(option: str = "", settings: Optional[List[str]] = None) -> None:
        """Train [one] option.

        Args:
            option: The option ([a] or [b]).
            settings: Extra arguments, a list ("[epochs=1, batch=2]").
        """

    out = _help(app, ["train", "--help"], monkeypatch, capsys)
    assert "Runs [stage] steps." in out
    assert "Train [one] option." in out
    assert "The option ([a] or [b])." in out
    assert 'a list ("[epochs=1, batch=2]")' in out
    docs = _help(app, ["train", "--docs"], monkeypatch, capsys)
    assert 'a list ("[epochs=1, batch=2]")' in docs
    index = _help(app, ["--help"], monkeypatch, capsys)
    assert "Train [one] option." in index
