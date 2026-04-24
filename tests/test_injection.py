import sys
from pathlib import Path
from typing import Any, Dict

import confluid

from liquifai import LiquifyApp
from liquifai.context import set_context


@confluid.configurable
class MyModel:
    def __init__(self, layers: int = 3):
        self.layers = layers


@confluid.configurable
class MyTrainer:
    def __init__(self, lr: float = 0.01):
        self.lr = lr


def test_command_injection(tmp_path: Path, monkeypatch: Any) -> None:
    # 1. Create a config file
    config_file = tmp_path / "inject.yaml"
    config_file.write_text("MyModel:\n  layers: 100\nMyTrainer:\n  lr: 0.0001")

    app = LiquifyApp(name="inject-app")
    captured: Dict[str, Any] = {}

    @app.command()
    def train(model: MyModel, trainer: MyTrainer, name: str = "Test") -> None:
        captured["model"] = model
        captured["trainer"] = trainer
        captured["name"] = name

    # 2. Run app
    test_args = ["inject-app", "--config", str(config_file), "train", "--name", "RealRun"]
    monkeypatch.setattr(sys, "argv", test_args)
    set_context(None)  # type: ignore

    app.run()

    assert captured["name"] == "RealRun"
    assert isinstance(captured["model"], MyModel)
    assert captured["model"].layers == 100
    assert isinstance(captured["trainer"], MyTrainer)
    assert captured["trainer"].lr == 0.0001


def test_injection_without_config(monkeypatch: Any) -> None:
    # Should use defaults if no config provided
    app = LiquifyApp(name="default-app")
    captured: Dict[str, Any] = {}

    @app.command()
    def run(model: MyModel) -> None:
        captured["model"] = model

    test_args = ["default-app", "run"]
    monkeypatch.setattr(sys, "argv", test_args)
    set_context(None)  # type: ignore

    app.run()

    assert captured["model"].layers == 3  # Default value


def test_injection_from_tagged_top_level_fluid(tmp_path: Path, monkeypatch: Any) -> None:
    """``trainer: !class:...`` binds the Fluid's kwargs instead of dropping them."""
    config_file = tmp_path / "inject_tagged.yaml"
    config_file.write_text(
        "trainer: !class:MyTrainer\n"
        "  lr: 0.0007\n"
        "model: !class:MyModel\n"
        "  layers: 42\n"
    )

    app = LiquifyApp(name="tagged-app")
    captured: Dict[str, Any] = {}

    @app.command()
    def train(trainer: MyTrainer, model: MyModel) -> None:
        captured["trainer"] = trainer
        captured["model"] = model

    test_args = ["tagged-app", "--config", str(config_file), "train"]
    monkeypatch.setattr(sys, "argv", test_args)
    set_context(None)  # type: ignore

    app.run()

    assert isinstance(captured["trainer"], MyTrainer)
    assert captured["trainer"].lr == 0.0007
    assert isinstance(captured["model"], MyModel)
    assert captured["model"].layers == 42
