"""Override-matcher tests: `_merge_overrides_into_fluids` and friends.

Pins the rule: a flat CLI override `--key value` is a BARE broadcast, so it
lands on every Fluid whose target class accepts it per
:func:`confluid.accepts_broadcast` — constructor params, settable properties,
``__init__``-body slots, ``**kwargs`` targets — MINUS the classes/params that
declared a broadcast opt-out (``@configurable(broadcast=False)`` /
``NoBroadcast[T]``). A dotted `--name.key` is an ADDRESSED write and uses the
looser :func:`confluid.accepts_key`, bypassing those opt-outs.

Other Fluids are left alone — no typo broadcasting.
"""

from pathlib import Path
from typing import Any, Optional

import confluid
import pytest
from confluid import NoBroadcast, accepts_broadcast, accepts_key, configurable
from confluid.fluid import Target

from liquifai.overrides import apply_overrides, merge_overrides_into_fluids


@configurable
class _WithDefaultKwarg:
    """RFUAVSource-shaped: has a kwarg with a default that isn't in YAML by default."""

    def __init__(self, root: str = "", max_packs: Optional[int] = None) -> None:
        self.root = root
        self.max_packs = max_packs


@configurable
class _WithProperty:
    """Configurable with a settable @property that's not a __init__ param."""

    def __init__(self, x: int = 0) -> None:
        self.x = x
        self._threshold = 10

    @property
    def threshold(self) -> int:
        return self._threshold

    @threshold.setter
    def threshold(self, v: int) -> None:
        self._threshold = v


@configurable
class _WithReadOnlyProperty:
    def __init__(self, x: int = 0) -> None:
        self.x = x

    @property
    def computed(self) -> int:
        return self.x * 2


class _NotConfigurable:
    def __init__(self, a: int = 1) -> None:
        self.a = a


def test_accepted_keys_for_configurable_includes_ctor_and_public_attrs() -> None:
    assert accepts_key(_WithProperty, "x")  # ctor param
    assert accepts_key(_WithProperty, "threshold")  # settable @property


def test_accepted_keys_skips_readonly_property() -> None:
    assert accepts_key(_WithReadOnlyProperty, "x")
    assert not accepts_key(_WithReadOnlyProperty, "computed")  # read-only @property is skipped


def test_accepted_keys_for_non_configurable_is_ctor_only() -> None:
    assert accepts_key(_NotConfigurable, "a")
    assert not accepts_key(_NotConfigurable, "anything_else")


def test_merge_applies_ctor_kwarg_even_when_missing_from_yaml() -> None:
    """Override for `max_packs` must land even though YAML doesn't set it."""
    fluid = Target(_WithDefaultKwarg, root="/data")
    merge_overrides_into_fluids({"src": fluid}, {"max_packs": 1})
    assert fluid.kwargs.get("max_packs") == 1


def test_merge_applies_property_kwarg_for_configurable() -> None:
    fluid = Target(_WithProperty, x=0)
    merge_overrides_into_fluids({"obj": fluid}, {"threshold": 42})
    assert fluid.kwargs.get("threshold") == 42


def test_merge_skips_unknown_kwarg_on_non_configurable() -> None:
    fluid = Target(_NotConfigurable, a=1)
    merge_overrides_into_fluids({"obj": fluid}, {"typo": 99})
    assert "typo" not in fluid.kwargs


def test_merge_preserves_existing_kwarg_override_path() -> None:
    """The legacy "already in kwargs" path still wins — post-construction toggles stay overridable."""
    fluid = Target(_WithDefaultKwarg, root="/data", max_packs=7)
    merge_overrides_into_fluids({"src": fluid}, {"max_packs": 1})
    assert fluid.kwargs["max_packs"] == 1


def test_dotted_override_targets_instance_by_name() -> None:
    """`--overlay.visualize true` lands only on the Fluid whose `name: overlay`."""
    overlay = Target(_WithDefaultKwarg, root="/a", name="overlay")
    ls = Target(_WithDefaultKwarg, root="/b", name="labelstudio")
    merge_overrides_into_fluids(
        {"o": overlay, "l": ls},
        {"overlay.max_packs": 1},
    )
    assert overlay.kwargs.get("max_packs") == 1
    # labelstudio unaffected.
    assert "max_packs" not in ls.kwargs


def test_flat_override_still_broadcasts_to_named_instances() -> None:
    """Plain `--max_packs 1` continues to broadcast (legacy behaviour preserved)."""
    a = Target(_WithDefaultKwarg, root="/a", name="overlay")
    b = Target(_WithDefaultKwarg, root="/b", name="labelstudio")
    merge_overrides_into_fluids(
        {"a": a, "b": b},
        {"max_packs": 5},
    )
    assert a.kwargs["max_packs"] == 5
    assert b.kwargs["max_packs"] == 5


def test_dotted_override_ignored_when_head_doesnt_match_name() -> None:
    """Unknown names don't fall back to broadcast — avoid surprise matches."""
    fluid = Target(_WithDefaultKwarg, root="/a", name="overlay")
    merge_overrides_into_fluids(
        {"o": fluid},
        {"wrong_name.max_packs": 99},
    )
    # The dotted head "wrong_name" doesn't match "overlay" — the tail is NOT
    # applied flatly either, because the user intended a targeted override.
    assert "max_packs" not in fluid.kwargs


def test_dotted_override_on_unnamed_fluid_is_noop() -> None:
    """Without a YAML `name`, dotted keys can't target the instance."""
    fluid = Target(_WithDefaultKwarg, root="/a")  # no name
    merge_overrides_into_fluids(
        {"o": fluid},
        {"overlay.max_packs": 1},
    )
    assert "max_packs" not in fluid.kwargs


def test_merge_broadcasts_to_nested_fluids() -> None:
    inner = Target(_WithDefaultKwarg, root="/inner")
    outer = Target(_WithDefaultKwarg, root="/outer", sub=inner)
    merge_overrides_into_fluids({"s": outer}, {"max_packs": 5})
    # Both Fluids had `max_packs` in their ctor → both get the override.
    assert outer.kwargs["max_packs"] == 5
    assert inner.kwargs["max_packs"] == 5


# ---------------------------------------------------------------------------
# Broadcast opt-outs: a flat `--key` is a BARE broadcast and must obey the same
# opt-outs a bare top-level YAML key does. Before the accept-list was unified on
# confluid's, liquifai re-derived its own and these declarations were bypassed.
# ---------------------------------------------------------------------------


@configurable(broadcast=False)
class _OptedOutOfBroadcast:
    def __init__(self, lr: float = 0.1, name: str = "") -> None:
        self.lr = lr
        self.name = name


@configurable
class _WithNoBroadcastParam:
    def __init__(self, lr: float = 0.1, tag: NoBroadcast[str] = "x") -> None:
        self.lr = lr
        self.tag = tag


@configurable
class _KwargsTarget:
    def __init__(self, **kw: object) -> None:
        self.kw = kw


def test_flat_override_skips_class_that_opted_out_of_broadcast() -> None:
    fluid = Target(_OptedOutOfBroadcast, lr=0.001)
    merge_overrides_into_fluids({"m": fluid}, {"lr": 0.9})
    assert fluid.kwargs["lr"] == 0.001  # bare key must not land


def test_dotted_override_still_reaches_broadcast_opted_out_class() -> None:
    """The opt-out gates BARE keys only — an addressed write is still allowed."""
    fluid = Target(_OptedOutOfBroadcast, lr=0.001, name="model")
    merge_overrides_into_fluids({"m": fluid}, {"model.lr": 0.9})
    assert fluid.kwargs["lr"] == 0.9


def test_flat_override_skips_no_broadcast_param_but_not_its_siblings() -> None:
    fluid = Target(_WithNoBroadcastParam, lr=0.1, tag="x")
    merge_overrides_into_fluids({"m": fluid}, {"tag": "y", "lr": 0.5})
    assert fluid.kwargs["tag"] == "x"  # NoBroadcast[str] slot untouched
    assert fluid.kwargs["lr"] == 0.5  # ordinary slot still broadcasts


def test_flat_override_is_not_written_into_a_kwargs_targets_own_kwargs() -> None:
    """A `**kwargs` target cannot REFUSE a key, which is not the same as declaring it.

    A marker's own kwargs are confluid's ADDRESSED channel — what lands there
    becomes a CONSTRUCTOR ARGUMENT. Writing a bare key there claims the user aimed
    it at this node, and for a target with no accept-list that claim is never
    justified: every key in the document fits through a `**kwargs` signature. The
    key is left to cascade instead (see the end-to-end test below).
    """
    fluid = Target(_KwargsTarget)
    merge_overrides_into_fluids({"m": fluid}, {"anything": 7})
    assert "anything" not in fluid.kwargs


def test_flat_override_still_reaches_a_kwargs_target_as_an_attribute() -> None:
    """Not written as an ARGUMENT is not the same as dropped — it still lands.

    The end-to-end guarantee the test above only tells half of: `apply_overrides`
    has already merged the key into the document, so confluid's own broadcasting
    delivers it with BARE provenance — a post-init attribute rather than a
    constructor argument.
    """
    doc = confluid.load(apply_overrides({"m": Target(_KwargsTarget)}, {"anything": 7}, []))
    assert doc["m"].kw == {}  # the constructor was NOT called with it ...
    assert doc["m"].anything == 7  # ... and it landed anyway


def test_flat_override_beats_a_key_the_document_already_declares() -> None:
    """A cascading override must not lose the position contest to the file.

    The other half of leaving a bare key to cascade: delivery is then decided by
    confluid's ONE precedence rule (document order, last spec wins), and
    ``deep_merge`` replaces a key the document ALREADY has *in place* — so the
    CLI value inherits line 1's position and a marker addressing the same key
    further down wins. Nothing warns, because the key IS used; it just carries
    the file's value. ``_move_cli_keys_last`` re-seats it at the end, which is
    where the user typed it.
    """
    doc = {"anything": "from_yaml", "m": Target(_KwargsTarget, anything="addressed_in_yaml")}
    built = confluid.load(apply_overrides(doc, {"anything": "from_cli"}, []))
    assert built["m"].kw == {}  # still not a constructor argument ...
    assert built["m"].anything == "from_cli"  # ... and the CLI value is the one that lands


def test_flat_override_still_reaches_a_class_that_declares_the_key() -> None:
    """The narrowing is scoped to targets with NO accept-list — nothing else moves.

    A class that DECLARES the key keeps taking it as a constructor argument, which
    is what every `--num_workers` / `--max_epochs` style override relies on.
    """
    fluid = Target(_WithDefaultKwarg)
    merge_overrides_into_fluids({"m": fluid}, {"root": "/data"})
    assert fluid.kwargs["root"] == "/data"


def test_unresolvable_target_falls_back_to_keys_already_in_yaml() -> None:
    """A `!class:` naming an unimportable module has no accept-list to consult."""
    fluid = Target("not.importable.Anywhere", lr=0.001)
    merge_overrides_into_fluids({"m": fluid}, {"lr": 0.9, "unknown": 1})
    assert fluid.kwargs["lr"] == 0.9  # present in YAML -> overridable
    assert "unknown" not in fluid.kwargs  # absent -> not invented


def test_merge_is_cycle_safe() -> None:
    """A marker graph with a back-edge is visited once, not until stack exhaustion."""
    a = Target(_WithDefaultKwarg, root="/a")
    b = Target(_WithDefaultKwarg, root="/b", sub=a)
    a.kwargs["sub"] = b  # cycle
    merge_overrides_into_fluids({"a": a}, {"max_packs": 3})
    assert a.kwargs["max_packs"] == 3
    assert b.kwargs["max_packs"] == 3


@configurable
class _ReportRunner:
    """End-to-end fixture: a runnable with one declared knob and no `optimizer`."""

    def __init__(self, max_packs: int = 0, name: str = "") -> None:
        self.max_packs, self.name = max_packs, name

    def run(self) -> None:
        pass


def _warnings_from(config: object, argv_overrides: dict, monkeypatch: pytest.MonkeyPatch) -> list:
    """Collect the post-materialization unused-override warnings — loggair does not reach caplog.

    Mirrors ``core.run_command``'s ordering exactly: overrides applied first,
    DI materialization inside ``confluid.collect_report()``, then
    ``warn_unused_overrides`` judged from the report. The pre-materialization
    heuristic this replaced could only guess (and guessed wrong on glob heads
    and multi-hop paths); the report is confluid's own delivery ledger.
    """
    from liquifai import di
    from liquifai import overrides as overrides_module

    collected: list = []
    monkeypatch.setattr(overrides_module.logger, "warning", lambda msg, *a, **k: collected.append(str(msg)))
    cfg = apply_overrides(config, argv_overrides, [])
    with confluid.collect_report() as rep:
        with di.confluid_active_context(cfg):
            for key, value in cfg.items():
                di.deep_flow(value, context=cfg)
    overrides_module.warn_unused_overrides(argv_overrides, rep)
    return collected


def test_a_dotted_override_that_addresses_nothing_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--optimizer.lr` against a code-declared slot used to be a SILENT no-op.

    The dotted form addresses an instance by its YAML `name:`. Aimed anywhere else it
    expands into a top-level block matching no node, so the value vanished and the run
    proceeded on the default looking configured — the same failure class the
    dropped-token warning exists to prevent, one step further in.
    """
    warned = _warnings_from({"runnable": Target(_WithDefaultKwarg)}, {"optimizer.lr": 0.5}, monkeypatch)
    assert any("'optimizer.lr'" in w and "matched nothing" in w for w in warned)
    assert any("--lr" in w for w in warned)  # names the spelling that does work


def test_a_dotted_override_that_names_a_real_instance_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    fluid = Target(_WithDefaultKwarg, name="overlay")
    warned = _warnings_from({"m": fluid}, {"overlay.max_packs": 3}, monkeypatch)
    assert warned == [] and fluid.kwargs["max_packs"] == 3


def test_a_dotted_override_onto_an_existing_config_key_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The head names a real document key, so the expanded block lands on real content."""
    warned = _warnings_from({"runnable": Target(_WithDefaultKwarg)}, {"runnable.max_packs": 3}, monkeypatch)
    assert warned == []


def test_a_glob_headed_override_is_not_warned_about(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--**.lr` routes by SHAPE, not by naming a node — and it lands.

    Confluid's dotted grammar has more legal heads than an instance `name:`:
    `**` reaches every accepting descendant, `*` the direct children. Warning
    that such a key "matched nothing and was ignored" states the opposite of
    what confluid's own report says (`applied=[('lr', "… 'opt'", "glob '**'")]`).
    """
    fluid = Target(_WithDefaultKwarg, name="opt")
    for head in ("**", "*"):
        assert _warnings_from({"m": fluid}, {f"{head}.max_packs": 3}, monkeypatch) == []


def test_a_multi_hop_dotted_override_at_a_named_instance_is_not_warned_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A head that names a real marker is addressed, however long the tail is.

    `--runner.opt.lr` is confluid's strict-hop form: the head floats to the node
    named `runner`, later segments are one-level hops. liquifai can only write a
    single-segment tail, but "I could not write it here" is not "it addressed
    nothing" — the previous rule warned that no object is named `runner` while
    the marker named `runner` was the one being walked.
    """
    inner = Target(_WithDefaultKwarg, name="opt")
    outer = Target(_WithDefaultKwarg, name="runner", sub=inner)
    assert _warnings_from({"m": outer}, {"runner.opt.max_packs": 3}, monkeypatch) == []


def test_a_dotted_override_at_an_unknown_head_is_still_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """The narrowing must not swallow the case the warning was written for."""
    inner = Target(_WithDefaultKwarg, name="opt")
    warned = _warnings_from({"m": inner}, {"optimizer.max_packs": 3}, monkeypatch)
    assert any("'optimizer.max_packs'" in w and "matched nothing" in w for w in warned)


def test_a_bare_override_some_node_accepts_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare key that cascades into ANY accepting node is used — no warning."""
    warned = _warnings_from({"runnable": Target(_WithDefaultKwarg)}, {"max_packs": 3}, monkeypatch)
    assert warned == []


def test_a_bare_override_nothing_accepts_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEW coverage the pre-materialization heuristic could never provide.

    A bare key is a document-wide cascade, so "did it land?" is unknowable
    before materialization — the old rule simply never warned about bare keys,
    and a typo'd ``--max_pcaks 3`` ran the job on defaults, silently. The
    report knows: a registered candidate no node accepted reports unused.
    """
    warned = _warnings_from({"runnable": Target(_WithDefaultKwarg)}, {"max_pcaks": 3}, monkeypatch)
    assert any("'max_pcaks'" in w and "matched nothing" in w for w in warned)


def test_broadcast_predicates_agree_with_the_merge() -> None:
    """The merge asks confluid; these are the answers it gets (drift pin)."""
    assert accepts_key(_OptedOutOfBroadcast, "lr")
    assert not accepts_broadcast(_OptedOutOfBroadcast, "lr")
    assert accepts_broadcast(_WithDefaultKwarg, "max_packs")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_end_to_end_a_doomed_override_warns_from_the_real_run(
    tmp_path: "Path", monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full CLI path: run_command wraps DI in collect_report and warns pre-execution.

    One doomed override (`--optimizer.lr` — no object named `optimizer`) and one
    good one (`--max_packs`) through a real ``app.run()``: exactly the doomed key
    is warned about, the good one lands, and the command still executes.
    """
    import sys

    from liquifai import LiquifyApp
    from liquifai import overrides as overrides_module
    from liquifai.context import set_context

    collected: list = []
    monkeypatch.setattr(overrides_module.logger, "warning", lambda msg, *a, **k: collected.append(str(msg)))

    seen: list = []
    app = LiquifyApp(name="report-app")

    @app.script_command(flow_mode="auto")
    def go(runnable: Any) -> None:
        seen.append(runnable)

    config = tmp_path / "cfg.yaml"
    config.write_text("runnable: !class:_ReportRunner\n")
    monkeypatch.setattr(sys, "argv", ["report-app", "go", str(config), "--optimizer.lr", "0.5", "--max_packs", "3"])
    set_context(None)

    app.run()

    (runner,) = seen
    assert runner.max_packs == 3  # the good override landed
    doomed = [w for w in collected if "'optimizer.lr'" in w and "matched nothing" in w]
    assert doomed, collected
    assert not any("max_packs" in w for w in collected)  # the good one is not warned about
