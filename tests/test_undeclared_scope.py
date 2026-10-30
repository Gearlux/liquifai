"""A bare ``--scope NAME`` the config does not declare is refused before anything runs.

``tool run cfg.yaml --scope vla`` (a typo of ``val``) used to run as if no scope were given — the default, train
split — and said nothing. A typed bare scope is now checked against the config's scopes (its includes too) and
refused naming the file and the scopes it does declare, as a ``config_files=`` command's typed scope already is.
A keyed ``--scope dim=value`` whose dimension the config never declares stays an inert no-op: a CLI passes a
dimension to every config (``--framework`` becomes ``--scope framework=<name>``) while a config grows into it.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from liquifai import LiquifyApp
from liquifai.exceptions import UnknownScopeError

CONFIG = "size: 1\nquick: !scope:quick\n  size: 2\nkeyed: !scope:task=a\n  size: 3\n"


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.yaml").write_text("deep: !scope:deep\n  size: 4\n")
    path = tmp_path / "cfg.yaml"
    path.write_text(CONFIG + "include: base.yaml\n")  # last: its scope's size wins
    return path


def _run(config: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> List[Dict[str, Any]]:
    app = LiquifyApp(name="probe")
    calls: List[Dict[str, Any]] = []

    @app.script_command("run", default=True)
    def run(size: int = 0) -> None:
        """Run."""
        calls.append({"size": size})

    monkeypatch.setattr(sys, "argv", ["probe", "run", str(config), *argv])
    app.run()
    return calls


def _refused(config: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, *argv: str) -> str:
    with pytest.raises(SystemExit) as stopped:
        _run(config, monkeypatch, *argv)
    assert stopped.value.code == 1
    return " ".join(capsys.readouterr().out.split())


@pytest.mark.parametrize(
    "argv, size",
    [
        (["--scope", "quick"], 2),
        (["--scope", "deep"], 4),  # declared in an included file
        (["--scope", "task=a"], 3),
        (["--task", "a"], 3),  # the dimension flag
        ([], 1),
    ],
)
def test_a_declared_scope_runs(config: Path, monkeypatch: pytest.MonkeyPatch, argv: List[str], size: int) -> None:
    assert _run(config, monkeypatch, *argv) == [{"size": size}]


@pytest.mark.parametrize("argv", [["--scope", "quik"], ["--scope", "quick,quik"], ["-s", "quik"]])
def test_an_undeclared_bare_scope_is_refused_naming_the_file_and_its_scopes(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, argv: List[str]
) -> None:
    out = _refused(config, monkeypatch, capsys, *argv)
    assert "Error: cfg.yaml has no scope 'quik' (its scopes: deep, quick, task=a)" in out


def test_an_undeclared_keyed_dimension_stays_inert(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(config, monkeypatch, "--scope", "framework=keras") == [{"size": 1}]


def test_a_bare_name_of_a_keyed_dimension_keeps_confluids_own_refusal(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    out = _refused(config, monkeypatch, capsys, "--scope", "task")
    assert "task is a KEYED dimension" in out


def test_with_debug_the_refusal_propagates(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnknownScopeError, match="has no scope 'quik'"):
        _run(config, monkeypatch, "--scope", "quik", "--debug")


def test_a_command_without_a_config_checks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = LiquifyApp(name="probe")
    calls: List[str] = []

    @app.command("show")
    def show() -> None:
        """Show."""
        calls.append("ran")

    monkeypatch.setattr(sys, "argv", ["probe", "show", "--scope", "anything"])
    app.run()
    assert calls == ["ran"]
