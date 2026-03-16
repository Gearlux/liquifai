from pathlib import Path
from typing import Any, Dict

import confluid
from typer.testing import CliRunner

from liquify import LiquifyApp

runner = CliRunner()


@confluid.configurable
class MyModel:
    def __init__(self, layers: int = 3):
        self.layers = layers


@confluid.configurable
class MyTrainer:
    def __init__(self, lr: float = 0.01):
        self.lr = lr


def test_command_injection(tmp_path: Path) -> None:
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
    result = runner.invoke(app.typer_app, ["--config", str(config_file), "train", "--name", "RealRun"])  # type: ignore

    assert result.exit_code == 0
    assert captured["name"] == "RealRun"
    assert isinstance(captured["model"], MyModel)
    assert captured["model"].layers == 100
    assert isinstance(captured["trainer"], MyTrainer)
    assert captured["trainer"].lr == 0.0001


def test_injection_without_config() -> None:
    # Should use defaults if no config provided
    app = LiquifyApp(name="default-app")
    captured: Dict[str, Any] = {}

    @app.command()
    def run(model: MyModel) -> None:
        captured["model"] = model

    result = runner.invoke(app.typer_app, ["run"])  # type: ignore
    assert result.exit_code == 0
    assert captured["model"].layers == 3  # Default value
