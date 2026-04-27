"""Tests for ``script_command(flow_mode=...)`` — manual / auto + ``Lazy[T]``."""

import sys
from pathlib import Path
from typing import Any, Dict, List

import confluid
import pytest
from confluid import Lazy

from liquifai import LiquifyApp
from liquifai.context import set_context


@confluid.configurable
class _Store:
    def __init__(self, path: str = "/tmp/store") -> None:
        self.path = path


@confluid.configurable
class _Exporter:
    """Container with a nested ``!class:`` attribute (the annotaide pattern)."""

    def __init__(self, name: str, store: Any = None) -> None:
        self.name = name
        self.store = store


@confluid.configurable
class _NeedsRuntimeKwarg:
    """A class whose constructor needs a kwarg that's never in YAML/broadcast."""

    def __init__(self, params: List[int], lr: float = 0.01) -> None:
        self.params = params
        self.lr = lr


@confluid.configurable
class _ContainerWithLazy:
    """Holds a ``_NeedsRuntimeKwarg`` declared ``Lazy[Any]``."""

    def __init__(self, name: str, optimizer: Lazy[Any] = None, model: Any = None) -> None:
        self.name = name
        self.optimizer = optimizer  # stays a Class stub under flow_mode="auto"
        self.model = model  # eagerly flowed


@confluid.configurable
class _ContainerEager:
    """Same shape as ``_ContainerWithLazy`` but no Lazy annotation — auto must fail."""

    def __init__(self, name: str, optimizer: Any = None) -> None:
        self.name = name
        self.optimizer = optimizer


def _run(app: LiquifyApp, argv: List[str], monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    set_context(None)  # type: ignore[arg-type]
    app.run()


def test_manual_mode_keeps_nested_class_stub(tmp_path: Path, monkeypatch: Any) -> None:
    """Default ``manual`` mode preserves nested Class stubs as Fluids."""
    config = tmp_path / "manual.yaml"
    config.write_text("exporter: !class:_Exporter\n" "  name: m1\n" "  store: !class:_Store\n" "    path: /tmp/x\n")
    app = LiquifyApp(name="manual-app")
    captured: Dict[str, Any] = {}

    @app.script_command()  # default flow_mode="manual"
    def go(exporter: _Exporter) -> None:
        captured["exporter"] = exporter

    _run(app, ["manual-app", "go", str(config)], monkeypatch)

    from confluid.fluid import Fluid

    exporter = captured["exporter"]
    assert isinstance(exporter, _Exporter)
    assert exporter.name == "m1"
    assert isinstance(exporter.store, Fluid), "manual mode must leave nested Class as a Fluid"


def test_auto_mode_flows_nested_class_stub(tmp_path: Path, monkeypatch: Any) -> None:
    """``auto`` mode deep-flows nested Class stubs into live instances."""
    config = tmp_path / "auto.yaml"
    config.write_text("exporter: !class:_Exporter\n" "  name: m1\n" "  store: !class:_Store\n" "    path: /tmp/y\n")
    app = LiquifyApp(name="auto-app")
    captured: Dict[str, Any] = {}

    @app.script_command(flow_mode="auto")
    def go(exporter: _Exporter) -> None:
        captured["exporter"] = exporter

    _run(app, ["auto-app", "go", str(config)], monkeypatch)

    exporter = captured["exporter"]
    assert isinstance(exporter, _Exporter)
    assert isinstance(exporter.store, _Store)
    assert exporter.store.path == "/tmp/y"


def test_auto_mode_raises_on_unflowable_stub(tmp_path: Path, monkeypatch: Any) -> None:
    """``auto`` mode surfaces flow failures loudly when an attr is NOT marked Lazy."""
    config = tmp_path / "auto_fail.yaml"
    config.write_text(
        "container: !class:_ContainerEager\n" "  name: c1\n" "  optimizer: !class:_NeedsRuntimeKwarg\n" "    lr: 0.5\n"
    )
    app = LiquifyApp(name="auto-fail-app")

    @app.script_command(flow_mode="auto")
    def go(container: _ContainerEager) -> None:
        pass

    with pytest.raises(Exception):
        _run(app, ["auto-fail-app", "go", str(config)], monkeypatch)


def test_auto_mode_honors_lazy_annotation(tmp_path: Path, monkeypatch: Any) -> None:
    """Attributes declared ``Lazy[T]`` stay as Class stubs; non-Lazy ones flow."""
    config = tmp_path / "lazy.yaml"
    config.write_text(
        "container: !class:_ContainerWithLazy\n"
        "  name: c1\n"
        "  optimizer: !class:_NeedsRuntimeKwarg\n"
        "    lr: 0.5\n"
        "  model: !class:_Store\n"
        "    path: /tmp/lazy-model\n"
    )
    app = LiquifyApp(name="lazy-app")
    captured: Dict[str, Any] = {}

    @app.script_command(flow_mode="auto")
    def go(container: _ContainerWithLazy) -> None:
        captured["container"] = container

    _run(app, ["lazy-app", "go", str(config)], monkeypatch)

    from confluid.fluid import Fluid

    container = captured["container"]
    assert isinstance(container, _ContainerWithLazy)
    # Lazy[Any]-marked param: stays deferred so domain code can flow it with
    # runtime kwargs (e.g. params=self.parameters()).
    assert isinstance(container.optimizer, Fluid)
    # Non-Lazy attr: eagerly flowed.
    assert isinstance(container.model, _Store)
    assert container.model.path == "/tmp/lazy-model"


def test_invalid_flow_mode_rejected() -> None:
    app = LiquifyApp(name="bad-mode")
    with pytest.raises(ValueError, match="flow_mode must be one of"):

        @app.script_command(flow_mode="bogus")  # type: ignore[arg-type]
        def go() -> None:
            pass
