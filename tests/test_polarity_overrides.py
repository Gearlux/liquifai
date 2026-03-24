import sys
from typing import Any, Dict, Optional

import pytest

from liquify import LiquifyApp
from liquify.context import set_context


@pytest.fixture(autouse=True)
def reset_context() -> Any:
    set_context(None)  # type: ignore
    yield


def test_polarity_overrides(monkeypatch: Any) -> None:
    app = LiquifyApp(name="polarity-app")
    captured_config: Optional[Dict[str, Any]] = None

    @app.command()
    def run() -> None:
        nonlocal captured_config
        captured_config = app.context.config_data if app.context else None

    # Test Cases:
    # 1. Standard key-value
    # 2. Positive polarity (+)
    # 3. Negative polarity (-)
    # 4. Implicit positive (no suffix)
    # 5. Nested polarity
    test_args = [
        "polarity-app",
        "run",
        "--standard",
        "val",
        "--pos+",
        "--neg-",
        "--implicit",
        "--model.enabled-",
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    app.run()

    assert captured_config is not None
    assert captured_config["standard"] == "val"
    assert captured_config["pos"] is True
    assert captured_config["neg"] is False
    assert captured_config["implicit"] is True
    # In simplified mode, overrides stay flat in config_data
    # Materialize will find "model.enabled" during broadcast search
    assert captured_config["model.enabled"] is False


def test_polarity_override_yaml_state(monkeypatch: Any, tmp_path: Any) -> None:
    # Verify that polarity overrides can flip values set in YAML
    config_file = tmp_path / "test.yaml"
    config_file.write_text("feature_a: false\nfeature_b: true")

    app = LiquifyApp(name="yaml-app")
    captured_config: Optional[Dict[str, Any]] = None

    @app.command()
    def run() -> None:
        nonlocal captured_config
        captured_config = app.context.config_data if app.context else None

    # Override: a -> true, b -> false
    test_args = ["yaml-app", "--config", str(config_file), "run", "--feature_a+", "--feature_b-"]
    monkeypatch.setattr(sys, "argv", test_args)

    app.run()

    assert captured_config is not None
    assert captured_config["feature_a"] is True
    assert captured_config["feature_b"] is False


def test_broadcast_polarity(monkeypatch: Any) -> None:
    # Verify that a flat CLI flag broadcasts to a nested object parameter
    app = LiquifyApp(name="broadcast-app")
    captured_config: Optional[Dict[str, Any]] = None

    from confluid import configurable

    @configurable
    class MyComponent:
        def __init__(self, enabled: bool = True):
            self.enabled = enabled

    @app.command()
    def run(comp: MyComponent) -> None:
        nonlocal captured_config
        captured_config = app.context.config_data if app.context else None
        # In this test we also want to check the actual injected object
        assert comp.enabled is False

    # Pass flat flag '--enabled-' which should broadcast to MyComponent
    test_args = ["broadcast-app", "run", "--enabled-"]
    monkeypatch.setattr(sys, "argv", test_args)

    app.run()

    assert captured_config is not None
    assert captured_config["enabled"] is False
