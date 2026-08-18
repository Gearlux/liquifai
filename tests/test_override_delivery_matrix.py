"""Characterization matrix: WHERE a CLI override actually lands, per node shape.

`test_override_broadcast.py` pins the *gating* rule — may this key set this
attribute (confluid's `accepts_key` / `accepts_broadcast` / `accepts_any_key`).
This file pins the *delivery* question underneath it: given a config tree, does
the value reach the built object, and by which of the two channels?

It exists because `apply_overrides` delivers a bare `--key value` **twice** — once
by merging it into the document (so confluid's own broadcasting carries it) and
once by writing it into every accepting `Fluid.kwargs`. Whether the second is
load-bearing or redundant cannot be settled by reading the code: it depends on
whether every marker is eventually materialized against the document, which
varies by node shape (nested, listed, deferred, `!ref:`-shared, unresolvable).

So each case below states the shape and asserts the observed value. Change the
delivery strategy and this file says exactly which shapes moved — which is the
point: the removal it guards is a silent regression if it is wrong, because a
dropped override looks identical to a config that was never overridden.
"""

from typing import Any, Optional

import confluid
import pytest
import yaml
from confluid import NoBroadcast, Partial, PartialClass, configurable, flow
from confluid.fluid import Target
from confluid.loader import ConfluidLoader
from liquifai.overrides import apply_overrides, expand_strings, parse_override_args


@configurable
class Leaf:
    """An ordinary receiver: it declares `rate`, so `--rate` is a ctor argument."""

    def __init__(self, rate: float = 0.001, tag: str = "default") -> None:
        self.rate, self.tag = rate, tag


@configurable
class Holder:
    """A parent whose slot holds another marker — the nested-delivery shape."""

    def __init__(self, child: Any = None, rate: float = 0.001) -> None:
        self.child, self.rate = child, rate


@configurable
class Deferred:
    """A parent with a `!lazy:` slot the domain code flows later, with a runtime arg."""

    def __init__(self, rate: float = 0.001) -> None:
        self.rate = rate
        self.slot: Partial[Leaf] = PartialClass(Leaf)


@configurable
class NeedsRuntimeArg:
    """The `!lazy:` target proper: it cannot be built until the run supplies `params`."""

    def __init__(self, params: Any = None, rate: float = 0.001) -> None:
        self.params, self.rate = params, rate


@configurable(broadcast=False)
class ShieldedClass:
    def __init__(self, rate: float = 0.001) -> None:
        self.rate = rate


@configurable
class ShieldedParam:
    def __init__(self, rate: NoBroadcast[float] = 0.001, tag: str = "default") -> None:
        self.rate, self.tag = rate, tag


@configurable
class Forwards:
    """No accept-list: it cannot refuse a key, which is not the same as declaring one."""

    def __init__(self, **kwargs: Any) -> None:
        self.received = dict(kwargs)


def config_after_cli(text: str, argv: list) -> Any:
    """The document as `_execute` sees it, in the ORDER `core.py` actually does it.

    The ordering is load-bearing and easy to get backwards: `_bootstrap` resolves the
    document with `confluid.load(..., until="document")` (core.py:672) — scopes, includes and
    `!ref:`s applied, markers NOT built — and only THEN does `_apply_overrides` run
    (core.py:701). Overriding a document that has already been through that pass is a
    different problem from overriding raw YAML, so a harness that loads afterwards
    measures a pipeline liquifai does not have.
    """
    data = yaml.load(text, Loader=ConfluidLoader)
    config_data = expand_strings(confluid.load(data, until="document"))
    overrides, deletions, _ = parse_override_args(argv)
    return apply_overrides(config_data, overrides, deletions)


def run_cli(text: str, argv: list) -> Any:
    """`config_after_cli`, then materialize every node against the document.

    Mirrors what the run phase does — DI materializes a `@configurable`-annotated
    block with `context=config_data`, and a generic runner reaches the same call
    through its own helper. Materializing WITH the document is what applies
    broadcasting, so passing it is part of the path under test, not a convenience.
    """
    return confluid.load(config_after_cli(text, argv))


# --------------------------------------------------------------------------- #
# Shapes where the key must REACH the object                                    #
# --------------------------------------------------------------------------- #


def test_top_level_marker_takes_it_as_a_constructor_argument() -> None:
    doc = run_cli("node: !class:Leaf()\n", ["--rate", "0.5"])
    assert doc["node"].rate == 0.5


def test_a_marker_nested_in_another_markers_kwargs_is_reached() -> None:
    doc = run_cli("node: !class:Holder()\n  child: !class:Leaf()\n", ["--rate", "0.5"])
    assert doc["node"].child.rate == 0.5  # the cascade does not stop at the parent
    assert doc["node"].rate == 0.5  # ... and the parent takes it too


def test_a_marker_inside_a_list_is_reached() -> None:
    """A listed marker is BUILT by `load()` (confluid builds every marker at any depth since
    record 19 phase 3 — it used to survive as an unbuilt marker), and the override is on it.
    """
    doc = run_cli("nodes:\n  - !class:Leaf()\n  - !class:Leaf()\n", ["--rate", "0.5"])
    assert [n.rate for n in doc["nodes"]] == [0.5, 0.5]
    assert [flow(n).rate for n in doc["nodes"]] == [0.5, 0.5]  # flow() of a live object is the object


def test_an_override_beats_the_markers_own_inline_kwarg() -> None:
    """Document order, last write wins — the CLI value is appended last."""
    doc = run_cli("node: !class:Leaf(rate=0.001)\n", ["--rate", "0.5"])
    assert doc["node"].rate == 0.5


def test_a_ref_shared_marker_is_one_object_carrying_the_override() -> None:
    doc = run_cli("shared: !class:Leaf()\na: !ref:shared\nb: !ref:shared\n", ["--rate", "0.5"])
    assert doc["a"] is doc["b"]  # identity preserved ...
    assert doc["a"].rate == 0.5  # ... and overridden once


# --------------------------------------------------------------------------- #
# The DEFERRED shapes — flowed later, outside any document context              #
# --------------------------------------------------------------------------- #


def test_a_document_level_lazy_marker_carries_the_override_into_a_later_flow() -> None:
    """The case the delivery question turns on.

    A `!lazy:` marker survives `load()` unbuilt; domain code flows it later with a
    runtime argument, by which time NO document context is active. Whatever the
    value is going to be must therefore already be ON the marker.
    """
    doc = run_cli("slot: !lazy:NeedsRuntimeArg(rate=0.001)\n", ["--rate", "0.5"])
    built = flow(doc["slot"], params=[1, 2, 3])
    assert built.params == [1, 2, 3] and built.rate == 0.5


def test_a_lazy_slot_assigned_in_an_init_BODY_is_reached_at_construction() -> None:
    """The slot's marker is created by `Deferred.__init__`, so pass 7 (the document walk)
    cannot see it — and confluid delivers the document's TOP-LEVEL keys to exactly such
    ctor-born markers at construction, from the active context (its "one delivery the
    emitted document cannot show"). This pinned the OPPOSITE until 2026-08-17, because
    `run_cli` materialized WITHOUT the document as context — a spelling the real DI path
    never used (`di.resolve_kwargs` passes `context=config_data`); with `confluid.load(doc)`
    the context is the document, as it is for DI.
    """
    doc = run_cli("node: !class:Deferred()\n", ["--rate", "0.5"])
    assert doc["node"].rate == 0.5  # the parent itself is reached ...
    assert flow(doc["node"].slot).rate == 0.5  # ... and so is its body-slot marker


# --------------------------------------------------------------------------- #
# Shapes where the key must NOT land                                            #
# --------------------------------------------------------------------------- #


def test_a_class_that_opted_out_of_broadcast_keeps_its_default() -> None:
    doc = run_cli("node: !class:ShieldedClass()\n", ["--rate", "0.5"])
    assert doc["node"].rate == 0.001


def test_a_no_broadcast_param_keeps_its_default_while_its_sibling_moves() -> None:
    doc = run_cli("node: !class:ShieldedParam()\n", ["--rate", "0.5", "--tag", "cli"])
    assert doc["node"].rate == 0.001
    assert doc["node"].tag == "cli"


def test_a_kwargs_target_gets_an_attribute_not_a_constructor_argument() -> None:
    """The 2026-08-03 rule, stated as delivery rather than as gating."""
    doc = run_cli("node: !class:Forwards()\n", ["--rate", "0.5"])
    assert doc["node"].received == {}  # the constructor was not called with it ...
    assert doc["node"].rate == 0.5  # ... it landed as an attribute


# --------------------------------------------------------------------------- #
# The addressed form, and the targets confluid cannot introspect                #
# --------------------------------------------------------------------------- #


def test_the_addressed_form_targets_one_instance_and_bypasses_the_opt_out() -> None:
    doc = run_cli(
        'a: !class:ShieldedClass()\n  name: "pinned"\nb: !class:ShieldedClass()\n',
        ["--pinned.rate", "0.5"],
    )
    assert doc["a"].rate == 0.5  # addressed -> the class-level opt-out does not apply
    assert doc["b"].rate == 0.001  # its sibling is untouched


@pytest.mark.parametrize(
    "key, expected",
    [("rate", 0.9), ("unknown", None)],
)
def test_an_unresolvable_target_is_overridable_only_where_the_yaml_already_had_the_key(
    key: str, expected: Optional[float]
) -> None:
    """No accept-list to consult, so "already in the YAML" is the only safe signal.

    Asserted on the MARKER, because an unimportable target can never be built —
    this shape's whole purpose is that it stays a marker for someone else to flow.
    """
    marker = Target("not.importable.Anywhere", rate=0.001)
    raw: Any = {"node": marker}
    overrides, deletions, _ = parse_override_args([f"--{key}", "0.9"])
    apply_overrides(raw, overrides, deletions)
    assert marker.kwargs.get(key) == expected


def test_a_marker_in_a_parents_slot_is_BUILT_with_the_override() -> None:
    """A marker in an ordinary slot builds during `load()`, override already applied.

    This shape MOVED (confluid 2026-08-11), which is what this file exists to record: the
    parens-less `!class:X` used to reach the parent as a STUB, deferred because the parent
    happened to be `@configurable`. That parent-context rule is gone — there are two
    construction modes and `partial` alone picks between them, so `X` and `X()` are one
    thing. Deferral is now a slot's own declaration (`Partial[T]`), covered by
    `test_a_document_level_lazy_marker_carries_the_override_into_a_later_flow`.

    The delivery property under test is unchanged: the override reaches the nested node.
    """
    doc = run_cli("node: !class:Holder()\n  child: !class:Leaf\n", ["--rate", "0.5"])
    child = doc["node"].child
    assert isinstance(child, Leaf) and child.rate == 0.5
