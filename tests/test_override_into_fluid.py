"""Regression tests for the dotted-CLI-override-on-Fluid bug.

The bug: ``--processor.lookback_days 5`` was silently dropped when the
YAML declared ``processor: !class:Download...``. Root cause: ``deep_merge``
left the dotted override as a literal-string top-level key, and the
``flow_mode="auto"`` path read ``fluid.kwargs`` directly without going
through ``materialize()`` (which internally would have expanded the
dotted key into the Fluid's kwargs).

Fix: ``_apply_overrides`` now calls ``confluid.expand_dotted_keys`` after
``deep_merge``, so dotted overrides land in the right place regardless of
whether the consumer later goes through ``materialize`` or directly
through ``flow()``.

These tests exercise the path end-to-end through a real ``LiquifyApp``
with a ``script_command(flow_mode="auto")``, matching the marainer
``download`` / ``convert`` / ``process`` shape.
"""

import sys
from pathlib import Path
from typing import Any, List, Optional

import pytest
from confluid import configurable
from confluid.fluid import Class
from confluid.merger import deep_merge, expand_dotted_keys

from liquifai import LiquifyApp
from liquifai.context import set_context
from liquifai.core import _confluid_active_context, _deep_flow, _merge_overrides_into_fluids


@pytest.fixture(autouse=True)
def reset_context() -> Any:
    set_context(None)  # type: ignore[arg-type]
    yield


# ---------------------------------------------------------------------------
# Direct path: simulate what ``_apply_overrides`` does today.
# ---------------------------------------------------------------------------


@configurable
class _DownloadFearGreed:
    """Mirrors traidwind.process.DownloadFearGreed's shape — single scalar
    + bool + (in the symbols variant) a list, all with defaults."""

    def __init__(
        self,
        out_root: str = "/tmp/default",
        lookback_days: int = 365,
        skip_if_fresh: bool = True,
        symbols: Optional[List[str]] = None,
    ) -> None:
        self.out_root = out_root
        self.lookback_days = lookback_days
        self.skip_if_fresh = skip_if_fresh
        self.symbols = symbols if symbols is not None else []


def test_dotted_override_reaches_fluid_kwargs_via_expand() -> None:
    """The fix: ``expand_dotted_keys`` after ``deep_merge`` pushes the
    override INTO the Fluid's kwargs dict so ``flow()`` reads the new
    value.
    """
    fluid = Class(_DownloadFearGreed, lookback_days=365, skip_if_fresh=True)
    config_data: Any = {"processor": fluid}
    overrides = {"processor.lookback_days": 5, "processor.skip_if_fresh": False}

    config_data = deep_merge(config_data, overrides)
    config_data = expand_dotted_keys(config_data)
    _merge_overrides_into_fluids(config_data, overrides)

    # Top level is clean — no literal dotted keys polluting it.
    assert list(config_data.keys()) == ["processor"]
    # Fluid's kwargs now carry the override values.
    assert fluid.kwargs["lookback_days"] == 5
    assert fluid.kwargs["skip_if_fresh"] is False


def test_flow_mode_auto_applies_dotted_overrides_to_processor_fluid(tmp_path: Path, monkeypatch: Any) -> None:
    """End-to-end through a real LiquifyApp: ``--processor.lookback_days 5``
    must reach the live ``_DownloadFearGreed`` instance.

    This mirrors the marainer ``download`` / ``convert`` / ``process``
    shape: a single ``processor: Any`` parameter with ``flow_mode="auto"``.
    """
    yaml = tmp_path / "smoke.yaml"
    yaml.write_text(
        "processor:\n"
        "  !class:_DownloadFearGreed\n"
        "  out_root: /tmp/yaml_value\n"
        "  lookback_days: 365\n"
        "  skip_if_fresh: true\n"
    )

    app = LiquifyApp(name="dl-app")
    captured: dict[str, Any] = {}

    @app.script_command(flow_mode="auto")
    def download(processor: Any) -> None:
        captured["processor"] = processor

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dl-app",
            "download",
            str(yaml),
            "--processor.lookback_days",
            "5",
            "--processor.skip_if_fresh-",
        ],
    )
    app.run()

    proc = captured["processor"]
    assert isinstance(proc, _DownloadFearGreed)
    assert proc.lookback_days == 5, "scalar dotted override must reach the Fluid"
    assert proc.skip_if_fresh is False, "polarity dotted override must reach the Fluid"
    # Untouched value preserved.
    assert proc.out_root == "/tmp/yaml_value"


def test_dotted_list_override_reaches_fluid(tmp_path: Path, monkeypatch: Any) -> None:
    """Lists work too: ``--processor.symbols '[a,b]'`` must land as a list."""
    yaml = tmp_path / "smoke_list.yaml"
    yaml.write_text("processor:\n" "  !class:_DownloadFearGreed\n" "  symbols: [original]\n")

    app = LiquifyApp(name="dl-list-app")
    captured: dict[str, Any] = {}

    @app.script_command(flow_mode="auto")
    def download(processor: Any) -> None:
        captured["processor"] = processor

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dl-list-app",
            "download",
            str(yaml),
            "--processor.symbols",
            "[BTCUSDT, ETHUSDT]",
        ],
    )
    app.run()

    proc = captured["processor"]
    assert proc.symbols == ["BTCUSDT", "ETHUSDT"]


def test_equals_form_dotted_override_reaches_fluid(tmp_path: Path, monkeypatch: Any) -> None:
    """The new ``=`` grammar (``--key=value``) also routes through the fix."""
    yaml = tmp_path / "smoke_eq.yaml"
    yaml.write_text("processor:\n" "  !class:_DownloadFearGreed\n" "  lookback_days: 365\n")

    app = LiquifyApp(name="dl-eq-app")
    captured: dict[str, Any] = {}

    @app.script_command(flow_mode="auto")
    def download(processor: Any) -> None:
        captured["processor"] = processor

    monkeypatch.setattr(
        sys,
        "argv",
        ["dl-eq-app", "download", str(yaml), "--processor.lookback_days=7"],
    )
    app.run()
    assert captured["processor"].lookback_days == 7


def test_bare_equals_form_dotted_override_reaches_fluid(tmp_path: Path, monkeypatch: Any) -> None:
    """Bare ``key=value`` (no ``--``) also routes through the fix."""
    yaml = tmp_path / "smoke_bare.yaml"
    yaml.write_text("processor:\n" "  !class:_DownloadFearGreed\n" "  lookback_days: 365\n")

    app = LiquifyApp(name="dl-bare-app")
    captured: dict[str, Any] = {}

    @app.script_command(flow_mode="auto")
    def download(processor: Any) -> None:
        captured["processor"] = processor

    monkeypatch.setattr(
        sys,
        "argv",
        ["dl-bare-app", "download", str(yaml), "processor.lookback_days=9"],
    )
    app.run()
    assert captured["processor"].lookback_days == 9


def test_dotted_override_does_not_disturb_other_fluid_kwargs() -> None:
    """``--processor.lookback_days 5`` must NOT clobber unrelated kwargs."""
    fluid = Class(
        _DownloadFearGreed,
        out_root="/tmp/yaml",
        lookback_days=365,
        skip_if_fresh=True,
        symbols=["original"],
    )
    config: Any = {"processor": fluid}
    overrides = {"processor.lookback_days": 5}

    config = deep_merge(config, overrides)
    config = expand_dotted_keys(config)
    _merge_overrides_into_fluids(config, overrides)

    assert fluid.kwargs["lookback_days"] == 5
    # Untouched kwargs preserved.
    assert fluid.kwargs["out_root"] == "/tmp/yaml"
    assert fluid.kwargs["skip_if_fresh"] is True
    assert fluid.kwargs["symbols"] == ["original"]


def test_flow_mode_auto_path_via_deep_flow_directly() -> None:
    """Unit-level: the fix at the precise layer the bug fires from."""
    fluid = Class(_DownloadFearGreed, lookback_days=365, skip_if_fresh=True)
    config: Any = {"processor": fluid}
    overrides = {"processor.lookback_days": 5, "processor.skip_if_fresh": False}

    config = deep_merge(config, overrides)
    config = expand_dotted_keys(config)
    _merge_overrides_into_fluids(config, overrides)

    proc_fluid = config["processor"]
    with _confluid_active_context(config):
        proc = _deep_flow(proc_fluid)

    assert isinstance(proc, _DownloadFearGreed)
    assert proc.lookback_days == 5
    assert proc.skip_if_fresh is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
