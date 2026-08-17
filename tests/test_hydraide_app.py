"""``hydraide`` — confluid's preprocessor as a LiquifyApp (``liquifai/hydraide.py``).

Confluid ships the FUNCTIONS (``confluid.hydraide.emit`` / ``check``); the command lives here.
These tests pin the command surface only — what the emitted document CONTAINS is confluid's
suite's job (``tests/test_hydraide.py`` there): the app resolves the promoted config with the
scopes the framework parsed, writes where told, and follows the failure contract.
"""

import sys
from pathlib import Path
from typing import Iterator

import confluid
import pytest
import yaml

from liquifai.context import set_context
from liquifai.hydraide import app

BASE = """\
model:
  _target_: HModel
  hidden: 32
lr: 0.1
optimizer:
  _target_: HAdam
  _partial_: true
"""

EXPERIMENT = """\
include: base.yaml
model.hidden: 64
torch_only:
  _scope_: {framework: torch}
  lr: 0.3
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fresh context, sandboxed CWD/XDG env, and app-name restore — the pattern the other suites use."""
    set_context(None)  # type: ignore[arg-type]
    xdg_home = tmp_path / "xdg"
    xdg_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "base.yaml").write_text(BASE)
    (tmp_path / "exp.yaml").write_text(EXPERIMENT)
    saved = confluid.get_app_name()
    yield xdg_home
    confluid.set_app_name(saved)


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run the app in-process; the exit code (0 when ``run()`` returns normally)."""
    monkeypatch.setattr(sys, "argv", ["hydraide", *argv])
    try:
        app.run()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_emit_prints_the_resolved_document_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "emit", "exp.yaml") == 0
    doc = yaml.safe_load(capsys.readouterr().out)
    assert doc["model"] == {"_target_": "HModel", "hidden": 64, "lr": 0.1}  # the dotted override, the bare key
    assert "include" not in doc and "torch_only" not in doc


def test_emit_honours_the_frameworks_scope_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--scope`` is liquifai's global flag; the app hands the parsed scopes to confluid."""
    assert _run(monkeypatch, "emit", "exp.yaml", "--scope", "framework=torch") == 0
    doc = yaml.safe_load(capsys.readouterr().out)
    assert doc["model"]["lr"] == 0.3 and doc["lr"] == 0.3


def test_emit_honours_the_configs_own_dimension_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dimension the config declares is a flag for free (``--framework torch``) — the framework's
    dimension-flag binding, nothing hydraide-specific."""
    assert _run(monkeypatch, "emit", "exp.yaml", "--framework", "torch") == 0
    assert yaml.safe_load(capsys.readouterr().out)["model"]["lr"] == 0.3


def test_emit_writes_the_file_named_by_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _run(monkeypatch, "emit", "exp.yaml", "--output", "resolved.yaml") == 0
    doc = yaml.safe_load((tmp_path / "resolved.yaml").read_text())
    assert doc["model"]["hidden"] == 64
    assert doc["optimizer"] == {"_target_": "HAdam", "_partial_": True, "lr": 0.1}


def test_check_passes_on_a_file_that_is_its_own_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, "emit", "exp.yaml", "--output", "resolved.yaml") == 0
    assert _run(monkeypatch, "check", "resolved.yaml") == 0


def test_check_exits_1_with_a_diff_on_an_unresolved_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "check", "exp.yaml") == 1
    out = capsys.readouterr().out
    assert "---" in out and "+++" in out, "a unified diff is the report"


def test_a_located_config_error_follows_the_failure_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A malformed document is a ConfluidError: ONE clean line naming file:line:col, exit 1,
    no traceback — the framework's contract, not a second error path in the app."""
    (tmp_path / "bad.yaml").write_text("m: {_target_: HModel, _partial_: sometimes}\n")
    assert _run(monkeypatch, "emit", "bad.yaml") == 1
    captured = capsys.readouterr()
    assert "_partial_ must be true or false" in captured.out + captured.err
    assert "bad.yaml" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_no_config_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(monkeypatch, "emit") == 1
    assert "hydraide emit <config.yaml>" in capsys.readouterr().out


def test_the_console_script_is_declared_here_not_in_confluid() -> None:
    here = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert 'hydraide = "liquifai.hydraide:main"' in here
    confluid_pyproject = Path(confluid.__file__).resolve().parents[1] / "pyproject.toml"
    if confluid_pyproject.exists():  # a workspace checkout; a wheel install has no pyproject to read
        assert "confluid.hydraide:main" not in confluid_pyproject.read_text()
