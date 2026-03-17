import sys
from pathlib import Path
from typing import Any

from liquify import LiquifyApp, LiquifyContext
from liquify.context import set_context


def test_app_initialization(capsys: Any, monkeypatch: Any) -> None:
    app = LiquifyApp(name="test-app")

    @app.command()
    def hello(name: str = "World") -> None:
        print(f"Hello {name}")

    monkeypatch.setattr(sys, "argv", ["test-app", "hello"])
    set_context(None)  # type: ignore
    app.run()

    captured = capsys.readouterr()
    assert "Hello World" in captured.out


def test_global_context_extraction(tmp_path: Path, monkeypatch: Any) -> None:
    app = LiquifyApp(name="test-app")
    captured_context = None

    # Create dummy config
    config_file = tmp_path / "test.yaml"
    config_file.write_text("val: 1")

    @app.command()
    def check() -> None:
        nonlocal captured_context
        captured_context = app.context

    # Run with global flags
    test_args = ["test-app", "--config", str(config_file), "--scope", "debug", "--debug", "check"]
    monkeypatch.setattr(sys, "argv", test_args)
    set_context(None)  # type: ignore

    app.run()

    assert captured_context is not None
    assert isinstance(captured_context, LiquifyContext)
    assert captured_context.config_path is not None
    assert captured_context.config_path.name == "test.yaml"
    assert "debug" in captured_context.scopes
    assert captured_context.debug is True
