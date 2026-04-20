import sys
from pathlib import Path
from typing import Any

from confluid import configurable

from liquifai import LiquifyApp
from liquifai.context import set_context


@configurable
class MockSub:
    def __init__(self, value: int = 10) -> None:
        self.value = value


@configurable
class MockMid:
    def __init__(self, sub: MockSub) -> None:
        self.sub = sub


@configurable
class MockTop:
    def __init__(self, mid: MockMid, name: str = "default") -> None:
        self.mid = mid
        self.name = name


def test_nested_di_with_overrides(tmp_path: Path, monkeypatch: Any) -> None:
    """
    Reproduces the Waivefront failure:
    Deeply nested component instantiation with scoped dotted-key overrides.
    """
    # 1. Setup Config File
    config_content = """
MockTop:
  mid: !class:MockMid()
    sub: !class:MockSub()
      value: 10
  name: "root"

debug:
  MockTop.mid.sub.value: 99
  MockTop.name: "debug-mode"
"""
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(config_content)

    # 2. Setup App
    app = LiquifyApp(name="test_app")

    # We use a mutable list to capture the injected object
    captured = []

    @app.command()
    def run_test(top: MockTop) -> None:
        captured.append(top)

    # 3. Simulate CLI execution: wf run-test --config ... --scope debug
    # We must patch sys.argv
    test_args = ["test_app", "--config", str(config_file), "--scope", "debug", "run-test"]
    monkeypatch.setattr(sys, "argv", test_args)

    # Reset singleton context for clean test
    set_context(None)  # type: ignore

    # 4. Run the app
    app.run()

    # 5. Assertions
    assert len(captured) == 1, "Command was not executed"
    top_instance = captured[0]

    assert top_instance.name == "debug-mode", "Root attribute override failed"
    assert top_instance.mid.sub.value == 99, "Deeply nested dotted override failed"


def test_late_registration_di(tmp_path: Path, monkeypatch: Any) -> None:
    """
    Simulates late registration via YAML 'import:' directive.
    """
    # 1. Define app and command BEFORE class is registered
    app = LiquifyApp(name="late_app")

    # We use a dummy type for the hint initially
    class Placeholder:
        pass

    captured = []

    # In real world, cli.py imports the class first. Let's do that.
    @configurable
    class TargetClass:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    @app.command()
    def late_test(obj: TargetClass) -> None:
        captured.append(obj)

    # 2. Config with import (though importlib will just re-import it)
    # and a scope override
    config_content = """
TargetClass:
  value: 10
debug:
  TargetClass.value: 50
"""
    config_file = tmp_path / "late_config.yaml"
    config_file.write_text(config_content)

    # 3. Execute
    monkeypatch.setattr(sys, "argv", ["late_app", "--config", str(config_file), "--scope", "debug", "late-test"])
    set_context(None)  # type: ignore
    app.run()

    assert len(captured) == 1
    assert captured[0].value == 50
