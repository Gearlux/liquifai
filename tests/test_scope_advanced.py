"""Integration tests: scope unwrap + confluid load + materialize.

These exercise the full pipeline that liquifai bootstrap performs:
``load_config`` (or YAML → dict) → ``resolve_scopes`` → ``confluid.load``.
"""

import tempfile
from pathlib import Path
from typing import Any, Dict, List, cast

import confluid
import pytest
import yaml
from confluid import configurable, get_registry, load_config, materialize

from liquifai.scopes import resolve_scopes


@pytest.fixture(autouse=True)
def setup_registry() -> None:
    get_registry().clear()


def _load_with_scopes(yaml_text: str, scopes: List[str]) -> Dict[str, Any]:
    """Replicates liquifai's bootstrap pipeline for a YAML string."""
    raw = yaml.safe_load(yaml_text)
    unwrapped = resolve_scopes(raw, scopes) if scopes else raw
    return cast(Dict[str, Any], confluid.load(unwrapped, flow=False))


def _load_path_with_scopes(path: Path, scopes: List[str]) -> Dict[str, Any]:
    raw = load_config(path)
    unwrapped = resolve_scopes(raw, scopes) if scopes else raw
    return cast(Dict[str, Any], confluid.load(unwrapped, flow=False))


@configurable
class Service:
    def __init__(self, port: int = 80, env: str = "dev") -> None:
        self.port = port
        self.env = env


def test_hierarchical_and_negative_scopes() -> None:
    config_yaml = """
env: 'base'
port: 80

prod:
  env: 'production'
  port: 443

prod.gpu:
  gpu_enabled: True

not prod:
  env: 'development'
"""
    # 'not prod' applies (prod not active)
    obj = _load_with_scopes(config_yaml, ["debug"])
    assert obj["env"] == "development"
    assert obj["port"] == 80

    # 'prod.gpu' inheritance (prod parent + prod.gpu child)
    obj = _load_with_scopes(config_yaml, ["prod.gpu"])
    assert obj["env"] == "production"
    assert obj["port"] == 443
    assert obj["gpu_enabled"] is True


def test_recursive_includes_with_scopes() -> None:
    """Scopes inside included files are correctly merged after include resolution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir) / "base.yaml"
        ext_path = Path(tmpdir) / "ext.yaml"

        ext_path.write_text(
            """
port: 1000
debug:
  port: 2000
not debug:
  port: 3000
"""
        )

        base_path.write_text(
            """
include: ext.yaml
port: 80
"""
        )

        # debug active: ext.debug.port (2000) wins, base.port (80) earlier
        obj = _load_path_with_scopes(base_path, ["debug"])
        assert obj["port"] == 2000

        # prod active (no debug): not-debug applies (3000 wins, base.port 80 earlier)
        obj = _load_path_with_scopes(base_path, ["prod"])
        assert obj["port"] == 3000


def test_scope_cleanup_keeps_inactive_blocks() -> None:
    """Active scope, scope_aliases, and `not debug` are stripped; inactive
    scope blocks (e.g. ``prod:``) are preserved verbatim."""
    config_yaml = """
val: 1
scope_aliases:
  d: debug
debug:
  val: 2
prod:
  val: 3
not debug:
  val: 4
"""
    obj = _load_with_scopes(config_yaml, ["debug"])
    assert obj["val"] == 2
    assert obj["prod"] == {"val": 3}  # inactive scope block kept
    assert "debug" not in obj
    assert "not debug" not in obj
    assert "scope_aliases" not in obj


def test_load_with_active_scopes_via_path(tmp_path: Path) -> None:
    config_file = tmp_path / "app.yaml"
    config_file.write_text(
        """
val: 1
debug:
  val: 10
"""
    )
    data = _load_path_with_scopes(config_file, ["debug"])
    assert data["val"] == 10


def test_repro_scope_override_into_tagged_class() -> None:
    """A scope provides a dotted override for a tagged class in the root."""

    @configurable
    class MockSource:
        def __init__(self, count: int = 10) -> None:
            self.count = count

    @configurable
    class MockFlux:
        def __init__(self, source: Any = None) -> None:
            self.source = source

    @configurable
    class MockProcessor:
        def __init__(self, flux: Any = None) -> None:
            self.flux = flux

    config = {
        "MockProcessor": {"flux": "!class:MockFlux(source=!class:MockSource(count=10))"},
        "debug": {"MockProcessor.flux.source.count": 2},
    }
    unwrapped = resolve_scopes(config, ["debug"])
    resolved = confluid.load(unwrapped, flow=False)

    processor_block = resolved.get("MockProcessor")
    marker_dict = {
        "_confluid_class_": "MockProcessor",
        **(processor_block if isinstance(processor_block, dict) else {}),
    }
    instance = materialize(marker_dict)
    assert instance.flux.source.count == 2


def test_repro_scope_replacing_class() -> None:
    """A scope replaces an entire tagged class with another one."""

    @configurable
    class SimpleModel:
        def __init__(self, layers: int = 3) -> None:
            self.layers = layers

    @configurable
    class ComplexModel:
        def __init__(self, layers: int = 10) -> None:
            self.layers = layers

    @configurable
    class Trainer:
        def __init__(self, model: Any = None) -> None:
            self.model = model

    config = {
        "Trainer": {"model": "!class:SimpleModel"},
        "heavy": {"Trainer.model": "!class:ComplexModel"},
    }
    unwrapped = resolve_scopes(config, ["heavy"])
    resolved = confluid.load(unwrapped, flow=False)

    trainer_block = resolved.get("Trainer")
    marker_dict = {
        "_confluid_class_": "Trainer",
        **(trainer_block if isinstance(trainer_block, dict) else {}),
    }
    instance = materialize(marker_dict)
    assert isinstance(instance.model, ComplexModel)
    assert instance.model.layers == 10
