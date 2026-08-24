"""Pins the rule: ``confluid.PartialClass`` (the YAML ``!lazy:`` Fluid) is
NEVER auto-flowed by liquifai's ``_deep_flow``, even in ``flow_mode="auto"``.

The receiving code is responsible for calling ``confluid.flow(value,
**runtime_kwargs)`` later (e.g. inside ``configure_optimizers``).
"""

from typing import Any

from confluid import PartialClass, configurable, flow, register

from liquifai.di import deep_flow


@configurable
class _Trainer:
    def __init__(self, optimizer: Any = None, max_epochs: int = 1) -> None:
        self.optimizer = optimizer
        self.max_epochs = max_epochs


class _Adam:
    def __init__(self, params: Any = None, lr: float = 0.01) -> None:
        self.params = params
        self.lr = lr


def test_top_level_lazy_survivesdeep_flow() -> None:
    register(_Adam)
    lazy = PartialClass(_Adam, lr=0.005)
    out = deep_flow(lazy)
    assert isinstance(out, PartialClass)
    assert out.kwargs == {"lr": 0.005}


def test_lazy_attribute_on_instance_survivesdeep_flow() -> None:
    """A live ``_Trainer`` whose ``optimizer`` slot holds a ``PartialClass``
    must come out the other side with the Partial intact — domain code will
    flow it later with ``params=model.parameters()``.
    """
    register(_Adam)
    register(_Trainer)
    trainer = _Trainer(optimizer=PartialClass(_Adam, lr=0.001), max_epochs=5)
    out = deep_flow(trainer)
    assert out is trainer  # mutated in place
    assert isinstance(trainer.optimizer, PartialClass)
    assert trainer.optimizer.kwargs == {"lr": 0.001}


def test_lazy_inside_list_survivesdeep_flow() -> None:
    register(_Adam)
    callbacks = [PartialClass(_Adam, lr=0.001), "scalar"]
    out = deep_flow(callbacks)
    assert isinstance(out[0], PartialClass)
    assert out[1] == "scalar"


def test_lazy_inside_dict_survivesdeep_flow() -> None:
    register(_Adam)
    mapping = {"opt": PartialClass(_Adam, lr=0.01), "x": 1}
    out = deep_flow(mapping)
    assert isinstance(out["opt"], PartialClass)
    assert out["x"] == 1


def test_explicit_flow_with_runtime_kwargs_constructs_target() -> None:
    """The flip side: when the user explicitly flows a Partial with the missing
    runtime arg, it constructs the target normally."""
    register(_Adam)
    lazy = PartialClass(_Adam, lr=0.001)
    live = flow(lazy, params=["w1", "w2"])
    assert isinstance(live, _Adam)
    assert live.params == ["w1", "w2"]
    assert live.lr == 0.001
