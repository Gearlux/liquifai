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


# ---------------------------------------------------------------------------
# Promotion provenance — WHICH tier supplied the file
# ---------------------------------------------------------------------------
# Promotion is eager: a bare positional token is consumed as a config the moment
# a matching YAML exists in ANY tier. A stale `~/.config/<app>/report.yaml` will
# silently swallow `app process report` that meant `report` as a positional, so
# every promotion is recorded at TRACE and a non-CWD one additionally at DEBUG.
# The log fires from `_prepare`, NOT from the router: routing is phase 1 and
# loggair is only configured in phase 4, so a debug/trace call there is dropped.


class _LevelRecorder:
    """Logger double recording per-level messages (loggair bypasses caplog)."""

    def __init__(self) -> None:
        self.debug_msgs: list[str] = []
        self.trace_msgs: list[str] = []

    def debug(self, msg: str) -> None:
        self.debug_msgs.append(msg)

    def trace(self, msg: str) -> None:
        self.trace_msgs.append(msg)

    def info(self, msg: str) -> None:  # pragma: no cover - noise sink
        pass

    def warning(self, msg: str) -> None:  # pragma: no cover - noise sink
        pass


def _promotion_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> _LevelRecorder:
    """Run `test-app process demo` with demo.yaml in ``config_dir``; return the log."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "demo.yaml").write_text("value: 1\n")

    app, _seen = _capture_app()
    recorder = _LevelRecorder()
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "demo"])
    original_bootstrap = app._bootstrap

    def _bootstrap_then_swap(**kwargs: Any) -> None:
        original_bootstrap(**kwargs)
        assert app.context is not None
        app.context.logger = recorder  # after loggair wiring, before the promotion log

    monkeypatch.setattr(app, "_bootstrap", _bootstrap_then_swap)
    app.run()
    return recorder


def test_cwd_promotion_is_recorded_at_trace_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = _promotion_log(tmp_path, monkeypatch, tmp_path)
    assert any("Promoted token 'demo'" in m for m in log.trace_msgs)
    # The ordinary case is not surprising — no DEBUG escalation.
    assert not any("OUTSIDE the working directory" in m for m in log.debug_msgs)


def test_config_tier_promotion_is_escalated_to_debug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = _promotion_log(tmp_path, monkeypatch, tmp_path / "config")
    assert any("Promoted token 'demo'" in m for m in log.trace_msgs)
    escalated = [m for m in log.debug_msgs if "OUTSIDE the working directory" in m]
    assert escalated, "a non-CWD tier must be escalated to DEBUG"
    assert "config/demo.yaml" in escalated[0]


def test_xdg_promotion_is_escalated_to_debug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated: Path) -> None:
    log = _promotion_log(tmp_path, monkeypatch, _isolated / "test-app")
    escalated = [m for m in log.debug_msgs if "OUTSIDE the working directory" in m]
    assert escalated, "an XDG-tier promotion must be escalated to DEBUG"
    assert "meant as a positional argument" in escalated[0]
