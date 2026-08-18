"""A flat config's top-level keys must reach an ``Any``-annotated command parameter.

Liquifai's DI has always built a parameter annotated with a **configurable class**
against the loaded document, which is what makes broadcasting work — a top-level YAML
key injecting into the same-named constructor parameter. A parameter annotated ``Any``
took a different route: the raw Fluid was handed over and deep-flowed in ISOLATION, so
every top-level key was dropped.

That is not an edge case. A generic runner — one command that executes a trainer, an
evaluator, a converter or a workflow, whichever the YAML names — *cannot* annotate a
single class, so `Any` is the only honest annotation and the whole flat-config contract
silently stopped applying to it.

Silently is the point. A dropped ``dataset:`` surfaces much later as an empty dataset; a
dropped ``max_epochs: 3`` never surfaces at all — the run proceeds on the parameter
default and looks configured.
"""

import sys
from pathlib import Path
from typing import Any, Optional

import confluid
import pytest
from liquifai import LiquifyApp
from liquifai.context import set_context
from liquifai.di import deep_flow


@confluid.configurable
class _BroadcastRunner:
    """The ergonomic-knob shape a flat config drives."""

    def __init__(self, dataset: Optional[Any] = None, max_epochs: int = 10, name: str = "default") -> None:
        self.dataset = dataset
        self.max_epochs = max_epochs
        self.name = name
        self.ran = False

    def run(self) -> None:
        self.ran = True


@confluid.configurable
class _BroadcastStore:
    """Uniquely named: confluid's registry is keyed by BARE class name, so a `_Store`
    here would collide with the one in ``test_flow_modes.py`` and `!class:_Store`
    would resolve to whichever module imported last."""

    def __init__(self, path: str = "/tmp/store") -> None:
        self.path = path


FLAT_CONFIG = """
runnable: !class:_BroadcastRunner
max_epochs: 3
name: from_the_flat_config
dataset: !class:_BroadcastStore
"""


def _document(text: str = FLAT_CONFIG) -> Any:
    """The document as liquifai loads it — markers resolved, nothing built yet."""
    return confluid.load(text, until="document")


# --------------------------------------------------------------------------- #
# deep_flow: the unit
# --------------------------------------------------------------------------- #


def test_context_broadcasts_top_level_keys_into_a_fluid() -> None:
    document = _document()

    runner = deep_flow(document["runnable"], context=document)

    assert runner.max_epochs == 3
    assert runner.name == "from_the_flat_config"
    assert isinstance(runner.dataset, _BroadcastStore)


def test_without_context_the_keys_are_dropped() -> None:
    """The counterfactual, executed — so the pin above cannot quietly stop meaning anything."""
    document = _document()

    runner = deep_flow(document["runnable"])

    assert runner.max_epochs == 10 and runner.name == "default" and runner.dataset is None


def test_the_context_reaches_fluids_inside_containers() -> None:
    """A list-valued top-level key (``logger: [...]``) is document-shaped too."""
    document = _document()
    values = deep_flow([document["runnable"]], context=document)

    assert values[0].max_epochs == 3


def test_a_lazy_fluid_stays_deferred_even_with_a_context() -> None:
    """``!lazy:`` is a runtime-injection point — broadcasting must not build it early."""
    from confluid.fluid import Partial as LazyFluid

    document = confluid.load("runnable: !lazy:_BroadcastRunner\nmax_epochs: 3\n", until="document")

    assert isinstance(deep_flow(document["runnable"], context=document), LazyFluid)


def test_a_live_instances_attributes_are_not_broadcast_into() -> None:
    """An already-built object's Fluid attribute is not a document node.

    Threading the document in here would inject top-level keys into whatever happens to
    hang off an instance — far beyond what the flat-config contract promises.
    """
    document = _document()
    holder = _BroadcastRunner()
    holder.dataset = confluid.fluid.Target("_BroadcastRunner")

    deep_flow(holder, context=document)

    assert isinstance(holder.dataset, _BroadcastRunner)
    assert holder.dataset.max_epochs == 10, "the nested attr must NOT pick up the document's keys"


# --------------------------------------------------------------------------- #
# through the CLI: the integration that was actually broken
# --------------------------------------------------------------------------- #


def test_an_any_annotated_command_receives_the_flat_configs_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generic-runner shape, end to end."""
    seen: list = []

    app = LiquifyApp(name="flat-app")

    @app.script_command(flow_mode="auto")
    def go(runnable: Any) -> None:
        seen.append(runnable)
        runnable.run()

    config = tmp_path / "flat.yaml"
    config.write_text(FLAT_CONFIG)
    monkeypatch.setattr(sys, "argv", ["flat-app", "go", str(config)])
    set_context(None)

    app.run()

    (runner,) = seen
    assert runner.max_epochs == 3, "a bare flow() leaves this at the default of 10 — silently"
    assert runner.name == "from_the_flat_config"
    assert isinstance(runner.dataset, _BroadcastStore)
    assert runner.ran is True


def test_a_class_annotated_command_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The path that was always correct must be unchanged."""
    seen: dict = {}

    app = LiquifyApp(name="typed-app")

    @app.script_command(flow_mode="auto")
    def go(runnable: _BroadcastRunner) -> None:
        seen.update(vars(runnable))

    config = tmp_path / "flat.yaml"
    config.write_text(FLAT_CONFIG)
    monkeypatch.setattr(sys, "argv", ["typed-app", "go", str(config)])
    set_context(None)

    app.run()

    assert seen["max_epochs"] == 3 and seen["name"] == "from_the_flat_config"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
