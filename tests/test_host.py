"""Host facts: the ``os`` / ``device`` scopes and the injected ``platform`` namespace.

Two surfaces, one detected fact each (:mod:`liquifai.host`):

* a scope ACTIVATION (``os=darwin`` / ``device=mps``) so a document can carry
  ``!scope:os=darwin`` blocks;
* a document KEY (``platform: {os: ..., device: ...}``) so a value can be read
  with ordinary interpolation — ``${platform.os}``. A plain ``${os}`` cannot
  work: confluid routes a dotted name to the config tree and a bare one to
  ``os.getenv`` (``confluid/resolver.py:161``), and liquifai sets no env vars.

The activation is ADVISORY — an auto value a document cannot use is not passed
at all, because confluid rejects an undeclared value for a declared dimension
(``No scope block matches device='cpu'``). A value the USER typed keeps that
error: it is the typo guard.
"""

import importlib.machinery
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from confluid.exceptions import ScopeError

from liquifai import LiquifyApp, host
from liquifai.context import set_context


def _fake_torch(*, cuda: bool, mps: bool) -> SimpleNamespace:
    """A stand-in for the real torch module, spec'd so ``find_spec`` accepts it."""
    return SimpleNamespace(
        __spec__=importlib.machinery.ModuleSpec("torch", None),
        cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )


def _raw(text: str) -> Any:
    import confluid

    return confluid.load(text, until="raw")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


def test_detect_os_is_the_python_platform_word() -> None:
    """``darwin`` / ``linux`` / ``windows`` — `platform.system()` lowered, never a translation."""
    import platform as stdlib_platform

    assert host.detect_os() == stdlib_platform.system().lower()


def test_detect_device_is_cpu_when_torch_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both doors: the `sys.modules` fast path (another test in the suite may have
    # imported torch already) and the installed-package check behind it.
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(host.importlib.util, "find_spec", lambda name: None)
    assert host.detect_device() == "cpu"


def test_detect_device_prefers_cuda_over_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=True, mps=True))
    assert host.detect_device() == "cuda"


def test_detect_device_uses_mps_when_cuda_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=False, mps=True))
    assert host.detect_device() == "mps"


def test_detect_device_is_cpu_when_neither_backend_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=False, mps=False))
    assert host.detect_device() == "cpu"


def test_a_forced_dimension_is_never_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--scope device=cpu`` answers the question, so the torch import is not paid."""

    def _boom() -> str:
        raise AssertionError("detection ran for a dimension the caller forced")

    monkeypatch.setattr(host, "detect_device", _boom)
    assert host.host_facts(host.forced_values(["device=cpu"]))["device"] == "cpu"


def test_forced_values_reads_the_last_entry_per_dimension() -> None:
    """Mirrors confluid's last-write-wins activation map."""
    assert host.forced_values(["device=cpu", "task=fit", "device=cuda"]) == {"device": "cuda"}


# ---------------------------------------------------------------------------
# the activation filter
# ---------------------------------------------------------------------------


def _scopes(facts: Dict[str, str], text: str) -> List[str]:
    return host.auto_scopes(facts, _raw(text))


def test_auto_scope_passes_when_the_document_declares_no_such_dimension() -> None:
    """Confluid treats an undeclared dimension as an inert no-op, so it always goes."""
    assert _scopes({"os": "darwin"}, "x: 1\n") == ["os=darwin"]


def test_auto_scope_passes_when_the_document_declares_this_value() -> None:
    doc = "mac: !scope:os=darwin\n  x: 2\n"
    assert _scopes({"os": "darwin"}, doc) == ["os=darwin"]


def test_auto_scope_is_dropped_when_the_document_declares_other_values_only() -> None:
    """The reason this filter exists: passing it raises `No scope block matches os='darwin'`."""
    doc = "linux_only: !scope:os=linux\n  x: 2\n"
    assert _scopes({"os": "darwin"}, doc) == []


def test_auto_scope_passes_for_a_negation_only_dimension() -> None:
    """`!notscope:` declares no selectable value; every value is meaningful there."""
    doc = "not_linux: !notscope:os=linux\n  x: 2\n"
    assert _scopes({"os": "darwin"}, doc) == ["os=darwin"]


def test_auto_scopes_survive_a_document_with_no_mapping_root() -> None:
    assert host.auto_scopes({"os": "darwin"}, ["a", "b"]) == ["os=darwin"]


# ---------------------------------------------------------------------------
# the injected namespace
# ---------------------------------------------------------------------------


def test_inject_adds_the_namespace() -> None:
    data = host.inject_facts({"x": 1}, {"os": "darwin", "device": "mps"})
    assert data["platform"] == {"os": "darwin", "device": "mps"}
    assert data["x"] == 1


def test_a_document_key_wins_per_key() -> None:
    """How a framework re-spells one fact: `platform: {device: gpu}` keeps liquifai's `os`."""
    data = host.inject_facts({"platform": {"device": "gpu"}}, {"os": "darwin", "device": "mps"})
    assert data["platform"] == {"os": "darwin", "device": "gpu"}


def test_a_non_mapping_namespace_key_is_left_alone() -> None:
    data = host.inject_facts({"platform": "whatever"}, {"os": "darwin"})
    assert data["platform"] == "whatever"


def test_inject_does_not_mutate_the_caller_document() -> None:
    original: Dict[str, Any] = {"x": 1}
    host.inject_facts(original, {"os": "darwin"})
    assert original == {"x": 1}


def test_inject_leaves_a_non_mapping_document_alone() -> None:
    assert host.inject_facts(["a"], {"os": "darwin"}) == ["a"]


# ---------------------------------------------------------------------------
# end to end, through the CLI
# ---------------------------------------------------------------------------


def _pin_host(monkeypatch: pytest.MonkeyPatch, *, os_: str = "darwin", device: str = "mps") -> None:
    monkeypatch.setattr(host, "detect_os", lambda: os_)
    monkeypatch.setattr(host, "detect_device", lambda: device)


def _run_show(app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, argv: List[str]) -> Dict[str, Any]:
    captured: Dict[str, Any] = {}

    @app.command()
    def show() -> None:
        from liquifai.context import get_context

        ctx = get_context()
        assert ctx is not None
        captured.update(ctx.config_data)

    monkeypatch.setattr(sys, "argv", [app.name, *argv, "show"])
    set_context(None)
    app.run()
    return captured


def test_the_namespace_is_referenceable_by_interpolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("logdir: /runs/${platform.os}\nwhich: ${platform.device}\n")
    _pin_host(monkeypatch)

    data = _run_show(LiquifyApp(name="interp-app"), monkeypatch, ["-c", str(cfg)])
    assert data["logdir"] == "/runs/darwin"
    assert data["which"] == "mps"


def test_a_scope_block_selects_on_the_detected_os(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("workers: 8\nmac: !scope:os=darwin\n  workers: 0\n")
    _pin_host(monkeypatch)

    assert _run_show(LiquifyApp(name="scope-app"), monkeypatch, ["-c", str(cfg)])["workers"] == 0


def test_a_scope_block_for_another_os_is_inert_here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pin for the filter: a linux-only document RUNS on darwin instead of raising."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text("workers: 8\nlin: !scope:os=linux\n  workers: 0\n")
    _pin_host(monkeypatch)

    assert _run_show(LiquifyApp(name="inert-app"), monkeypatch, ["-c", str(cfg)])["workers"] == 8


def test_a_forced_scope_moves_both_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--scope device=cpu` picks the block AND changes what `${platform.device}` reads."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text("which: ${platform.device}\ncpu: !scope:device=cpu\n  picked: yes\n")
    _pin_host(monkeypatch)

    data = _run_show(LiquifyApp(name="forced-app"), monkeypatch, ["-c", str(cfg), "--scope", "device=cpu"])
    assert data["which"] == "cpu"
    assert data["picked"] is True


def test_a_typed_value_the_document_lacks_still_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The typo guard is untouched — only the AUTO copy is filtered."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text("mac: !scope:os=darwin\n  x: 1\n")
    _pin_host(monkeypatch)

    app = LiquifyApp(name="typo-app")

    @app.command()
    def show() -> None:  # pragma: no cover - the run fails before dispatch
        raise AssertionError("should not run")

    monkeypatch.setattr(sys, "argv", ["typo-app", "-c", str(cfg), "--scope", "os=windows", "--debug", "show"])
    set_context(None)
    with pytest.raises(ScopeError, match="No scope block matches os='windows'"):
        app.run()


def test_a_document_key_wins_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("platform:\n  device: gpu\nwhich: ${platform.device}\nwhere: ${platform.os}\n")
    _pin_host(monkeypatch)

    data = _run_show(LiquifyApp(name="respell-app"), monkeypatch, ["-c", str(cfg)])
    assert data["which"] == "gpu"
    assert data["where"] == "darwin"


def test_plain_dimension_flags_stay_config_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--device cpu` keeps reaching config keys; only `--scope device=cpu` moves the scope.

    Auto dimensions are deliberately NOT bound as implicit `--KEY VAL` flags: binding
    them would swallow the override, which is how a `--device cpu` meant for a ctor
    kwarg would silently stop arriving.
    """
    cfg = tmp_path / "c.yaml"
    cfg.write_text("device: auto\n")
    _pin_host(monkeypatch)

    data = _run_show(LiquifyApp(name="override-app"), monkeypatch, ["-c", str(cfg), "--device", "cpu"])
    assert data["device"] == "cpu"


def test_the_introspection_path_sees_the_same_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`liquify()` builds its own context (help rendering, graph export) and must not
    resolve an unscoped variant of the document."""
    from confluid import configurable

    @configurable
    class Job:
        def __init__(self, logdir: str = "", workers: int = -1) -> None:
            self.logdir, self.workers = logdir, workers

    cfg = tmp_path / "c.yaml"
    cfg.write_text("logdir: /runs/${platform.os}\nworkers: 8\nmac: !scope:os=darwin\n  workers: 0\n")
    _pin_host(monkeypatch)

    app = LiquifyApp(name="liquify-app")

    def command(job: Job) -> None: ...

    set_context(None)
    job = app.liquify(command, config_path=cfg)["job"]
    assert (job.logdir, job.workers) == ("/runs/darwin", 0)
