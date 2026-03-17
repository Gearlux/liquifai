from pathlib import Path

import pytest

from liquify import LiquifyApp, LiquifyContext


def test_bootstrap_logic_direct(tmp_path: Path) -> None:
    # 1. Create a config file
    config_file = tmp_path / "logic_config.yaml"
    config_file.write_text("Model:\n  layers: 50\nbase_lr: 0.001")

    app = LiquifyApp(name="logic-app")
    app.context = LiquifyContext(name="logic-app", config_path=config_file)

    # Run bootstrap directly
    app._bootstrap()

    assert app.context.config_data["base_lr"] == 0.001
    assert app.context.config_data["Model"]["layers"] == 50
    assert app.context.logger is not None


def test_bootstrap_with_scopes_direct(tmp_path: Path) -> None:
    config_file = tmp_path / "scoped_direct.yaml"
    config_file.write_text("val: 1\ndebug:\n  val: 10")

    app = LiquifyApp(name="scope-direct")
    app.context = LiquifyContext(name="scope-direct", config_path=config_file, scopes=["debug"])

    app._bootstrap()
    assert app.context.config_data["val"] == 10


def test_bootstrap_invalid_config_direct() -> None:
    app = LiquifyApp(name="fail-direct")
    app.context = LiquifyContext(name="fail-direct", config_path=Path("non_existent.yaml"))

    # Should raise SystemExit
    with pytest.raises(SystemExit) as excinfo:
        app._bootstrap()

    assert excinfo.value.code == 1


def test_bootstrap_no_context() -> None:
    app = LiquifyApp(name="no-ctx")
    app.context = None
    # Should return early without error
    app._bootstrap()
    assert app.context is None
