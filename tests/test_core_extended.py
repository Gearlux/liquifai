import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from liquify import LiquifyApp
from liquify.context import set_context


@pytest.fixture(autouse=True)
def reset_context() -> Any:
    set_context(None)  # type: ignore
    yield


def test_script_command_promotion(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    app = LiquifyApp(name="test-app")

    # Create a config file named "mycfg.yaml"
    config_file = tmp_path / "mycfg.yaml"
    config_file.write_text("val: 123")

    captured_config: Optional[Dict[str, Any]] = None

    @app.script_command()
    def process() -> None:
        nonlocal captured_config
        captured_config = app.context.config_data if app.context else None

    # Run with promotion: "process mycfg" (should find mycfg.yaml)
    # We need to be in a directory where mycfg.yaml is visible or use absolute path
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["test-app", "process", "mycfg"])

    app.run()

    assert captured_config is not None
    assert captured_config["val"] == 123
    assert app.context is not None
    assert app.context.config_path is not None
    assert app.context.config_path.name == "mycfg.yaml"


def test_apply_overrides(tmp_path: Path, monkeypatch: Any) -> None:
    app = LiquifyApp(name="test-app")

    config_file = tmp_path / "test.yaml"
    config_file.write_text("model:\n  layers: 3\n  name: 'orig'")

    captured_config: Optional[Dict[str, Any]] = None

    @app.command()
    def run() -> None:
        nonlocal captured_config
        captured_config = app.context.config_data if app.context else None

    # Override: --model.layers 10 --model.name 'new'
    test_args = ["test-app", "--config", str(config_file), "run", "--model.layers", "10", "--model.name", "new"]
    monkeypatch.setattr(sys, "argv", test_args)

    app.run()

    assert captured_config is not None
    # In simplified mode, overrides stay flat in config_data
    assert captured_config["model.layers"] == 10
    assert captured_config["model.name"] == "new"


def test_help_menu(capsys: Any, monkeypatch: Any) -> None:
    app = LiquifyApp(name="test-app")

    @app.command()
    def my_cmd() -> None:
        """This is my command description."""
        pass

    monkeypatch.setattr(sys, "argv", ["test-app", "--help"])

    app.run()

    captured = capsys.readouterr()
    assert "TEST-APP" in captured.out
    assert "my-cmd" in captured.out
    assert "This is my command description." in captured.out


def test_log_overrides(tmp_path: Path, monkeypatch: Any) -> None:
    app = LiquifyApp(name="test-app")

    log_dir = tmp_path / "logs"

    @app.command()
    def log_check() -> None:
        pass

    test_args = [
        "test-app",
        "--level",
        "TRACE",
        "--console-level",
        "INFO",
        "--file-level",
        "DEBUG",
        "--log-dir",
        str(log_dir),
        "log-check",
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    app.run()

    ctx = app.context
    assert ctx is not None
    assert ctx.log_level == "TRACE"
    assert ctx.console_level == "INFO"
    assert ctx.file_level == "DEBUG"
    assert ctx.log_dir == log_dir


def test_default_command(monkeypatch: Any) -> None:
    app = LiquifyApp(name="test-app")
    called = False

    @app.command(default=True)
    def main_cmd() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(sys, "argv", ["test-app"])
    app.run()

    assert called is True


def test_subgroup_without_command_shows_help(monkeypatch: Any, capsys: Any) -> None:
    app = LiquifyApp(name="test-app", description="Root app.")
    sub = LiquifyApp(name="sub", description="Sub group.")
    app.add_app(sub)

    @sub.command()
    def hello() -> None:
        """Say hello."""
        pass

    monkeypatch.setattr(sys, "argv", ["test-app", "sub"])

    app.run()

    captured = capsys.readouterr()
    assert "SUB" in captured.out
    assert "hello" in captured.out
    assert "Say hello." in captured.out


def test_missing_config(monkeypatch: Any, capsys: Any) -> None:
    app = LiquifyApp(name="test-app")

    @app.command()
    def run() -> None:
        pass

    monkeypatch.setattr(sys, "argv", ["test-app", "--config", "nonexistent.yaml", "run"])

    with pytest.raises(SystemExit) as exc:
        app.run()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Configuration file not found" in captured.out
