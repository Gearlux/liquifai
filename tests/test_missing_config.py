"""A script command handed a config file that does not exist says so, in one line, and runs nothing.

The first non-flag token after a ``script_command`` is its config when it resolves to an existing file. A token that
NAMES a config file — a ``.yaml`` / ``.yml`` suffix, or a ``pkg://<distribution>/<path>`` reference — but resolves to
none used to fall through to the override parser, which warned ``Ignoring unrecognized CLI token …`` and ran the
command with no config. It is refused instead: ``Error: Configuration file not found: <token>``, exit 1. A token that
names no file (no suffix, no ``pkg://``) still falls through, as before.
"""

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

import confluid
import pytest

from liquifai import LiquifyApp, host
from liquifai.context import set_context


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    set_context(None)  # type: ignore[arg-type]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    saved = confluid.get_app_name()
    yield tmp_path
    confluid.set_app_name(saved)


def _app() -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    app = LiquifyApp(name="test-app")
    runs: List[Dict[str, Any]] = []

    @app.script_command()
    def process() -> None:
        config = app.context.config_data if app.context else None
        runs.append({k: v for k, v in (config or {}).items() if k != host.NAMESPACE})

    return app, runs


def _run(app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["test-app", *args])
    with pytest.raises(SystemExit) as stopped:
        app.run()
    return int(stopped.value.code or 0)


@pytest.mark.parametrize("token", ["missing.yaml", "configs/missing.yml", "/abs/where/nr.yaml"])
def test_a_config_file_that_does_not_exist_is_refused_and_nothing_runs(
    token: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, runs = _app()
    assert _run(app, monkeypatch, "process", token, "lr=0.1") == 1
    assert runs == []
    captured = capsys.readouterr()
    said = captured.out + captured.err  # the `Error:` line is the console's (stdout), a warning loggair's (stderr)
    assert f"Error: Configuration file not found: {token}" in said
    assert "Ignoring unrecognized CLI token" not in said


def test_a_package_reference_that_names_no_installed_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, runs = _app()
    assert _run(app, monkeypatch, "process", "pkg://not-installed/config/a.yaml") == 1
    assert runs == []
    captured = capsys.readouterr()
    assert "Error: Configuration file not found: pkg://not-installed/config/a.yaml" in captured.out + captured.err


def test_a_package_reference_to_an_installed_file_is_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pkg://<distribution>/<path>`` (confluid) names a config an installed distribution ships."""
    site = tmp_path / "venv" / "lib" / "python3" / "site-packages"
    info = site / "demo-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n")
    checkout = tmp_path / "checkout"
    (checkout / "config").mkdir(parents=True)
    (checkout / "config" / "a.yaml").write_text("val: 7\n")
    (info / "direct_url.json").write_text(json.dumps({"url": checkout.as_uri(), "dir_info": {"editable": True}}))
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()

    app, runs = _app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "pkg://demo/config/a.yaml"])
    app.run()
    assert runs == [{"val": 7}]


def test_a_token_that_names_no_file_still_falls_through_to_the_overrides(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No suffix and no ``pkg://``: not a file name — the override parser's warning, the command still runs."""
    app, runs = _app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "demo"])
    app.run()
    assert runs == [{}]
    assert "Ignoring unrecognized CLI token 'demo'" in capsys.readouterr().err


def test_an_existing_config_is_still_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "exp.yaml").write_text("val: 1\n")
    app, runs = _app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "exp.yaml"])
    app.run()
    assert runs == [{"val": 1}]
