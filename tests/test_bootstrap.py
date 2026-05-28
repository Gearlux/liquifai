import sys
from pathlib import Path

import pytest

from liquifai import LiquifyApp, LiquifyContext
from liquifai.context import set_context


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
    config_file.write_text("val: 1\nif_debug: !scope:debug\n  val: 10\n")

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


def test_context_included_paths_defaults_to_empty() -> None:
    """``LiquifyContext.included_paths`` is an empty list by default."""
    ctx = LiquifyContext(name="empty")
    assert ctx.included_paths == []


def test_full_run_populates_included_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: invoking the CLI path populates ``ctx.included_paths``
    from the resolved include tree (entrypoint + transitive ``include:``-d
    files), in load order, deduplicated, with the entrypoint first."""
    common = tmp_path / "common.yaml"
    common.write_text("base_val: 1\n")

    main = tmp_path / "main.yaml"
    main.write_text("include: common.yaml\nmain_val: 2\n")

    app = LiquifyApp(name="paths-app")

    captured: dict[str, object] = {}

    @app.command()
    def show() -> None:
        from liquifai.context import get_context

        ctx = get_context()
        assert ctx is not None
        captured["included_paths"] = list(ctx.included_paths)
        captured["config_path"] = ctx.config_path

    monkeypatch.setattr(sys, "argv", ["paths-app", "-c", str(main), "show"])
    set_context(None)  # type: ignore[arg-type]
    app.run()

    paths = captured["included_paths"]
    assert isinstance(paths, list) and len(paths) >= 2
    assert paths[0] == main.resolve()
    assert common.resolve() in paths
    # Deduplicated.
    assert len(paths) == len({p for p in paths})


def test_no_config_path_yields_empty_included_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no ``--config`` is supplied, ``included_paths`` stays empty."""
    app = LiquifyApp(name="no-config-app")
    captured: dict[str, object] = {}

    @app.command()
    def ping() -> None:
        from liquifai.context import get_context

        ctx = get_context()
        assert ctx is not None
        captured["included_paths"] = list(ctx.included_paths)

    monkeypatch.setattr(sys, "argv", ["no-config-app", "ping"])
    set_context(None)  # type: ignore[arg-type]
    app.run()
    assert captured["included_paths"] == []
