"""Config-path resolution through confluid's search tiers (./config/, XDG).

Liquifai resolves promoted script-command tokens and ``--config`` values via
``confluid.resolve_config_path`` and namespaces the XDG lookup by setting the
app name at ``run()`` start. Env vars are isolated per test so a developer's
real ``~/.config`` never leaks in.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Iterator

import confluid
import pytest

from liquifai import LiquifyApp
from liquifai.context import set_context


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fresh context, sandboxed CWD/XDG env, and app-name restore."""
    set_context(None)  # type: ignore[arg-type]
    xdg_home = tmp_path / "xdg"
    xdg_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    saved = confluid.get_app_name()
    yield xdg_home
    confluid.set_app_name(saved)


def _capture_app() -> "tuple[LiquifyApp, Dict[str, Any]]":
    app = LiquifyApp(name="test-app")
    seen: Dict[str, Any] = {}

    @app.script_command()
    def process() -> None:
        seen["config"] = app.context.config_data if app.context else None
        seen["path"] = app.context.config_path if app.context else None

    return app, seen


def test_promoted_token_found_in_cwd_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "myexp.yaml").write_text("val: 1")

    app, seen = _capture_app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "myexp"])
    app.run()

    assert seen["config"] == {"val": 1}
    assert seen["path"] == tmp_path / "config" / "myexp.yaml"


def test_promoted_token_found_under_xdg_app_dir(_isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = _isolated / "test-app"
    app_dir.mkdir()
    (app_dir / "myexp.yaml").write_text("val: 2")

    app, seen = _capture_app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "myexp"])
    app.run()

    assert seen["config"] == {"val": 2}
    assert seen["path"] == app_dir / "myexp.yaml"
    # run() namespaced the lookup with the app's own name.
    assert confluid.get_app_name() == "test-app"


def test_config_flag_resolves_via_xdg(_isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = _isolated / "test-app"
    app_dir.mkdir()
    (app_dir / "flagged.yaml").write_text("val: 3")

    app, seen = _capture_app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "--config", "flagged.yaml"])
    app.run()

    assert seen["config"] == {"val": 3}
    assert seen["path"] == app_dir / "flagged.yaml"


def test_local_config_still_wins_over_xdg(tmp_path: Path, _isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = _isolated / "test-app"
    app_dir.mkdir()
    (app_dir / "myexp.yaml").write_text("val: 4")
    (tmp_path / "myexp.yaml").write_text("val: 5")

    app, seen = _capture_app()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "myexp"])
    app.run()

    assert seen["config"] == {"val": 5}
    assert seen["path"] == tmp_path / "myexp.yaml"
