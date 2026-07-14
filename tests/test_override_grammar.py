"""Pins the extended CLI override grammar parsed by ``parse_override_args``.

Forms covered:
  * ``--key value``           (legacy, still primary)
  * ``--key=value``
  * ``key=value``             (bare, no ``--``)
  * ``--key+`` / ``--key-``   (polarity)
  * ``--key``                 (implicit ``True``)
  * ``+key=value``            (add — treated same as override today)
  * ``~key``                  (delete)
  * Mixed orderings + dotted keys
  * Unrecognised tokens are collected in ``dropped`` (never silently lost)
"""

from typing import Any

import pytest

from liquifai.core import _delete_dotted_key, _parse_override_args


def test_legacy_dash_space_form() -> None:
    overrides, deletions, dropped = _parse_override_args(["--max_epochs", "10"])
    assert overrides == {"max_epochs": 10}
    assert deletions == []
    assert dropped == []


def test_dash_equals_form() -> None:
    overrides, deletions, _ = _parse_override_args(["--max_epochs=10"])
    assert overrides == {"max_epochs": 10}
    assert deletions == []


def test_bare_equals_form() -> None:
    overrides, deletions, _ = _parse_override_args(["max_epochs=10"])
    assert overrides == {"max_epochs": 10}
    assert deletions == []


def test_polarity_plus_minus_forms() -> None:
    overrides, _, _ = _parse_override_args(["--enable+", "--debug-"])
    assert overrides == {"enable": True, "debug": False}


def test_implicit_boolean_true() -> None:
    overrides, _, _ = _parse_override_args(["--verbose"])
    assert overrides == {"verbose": True}


def test_add_operator() -> None:
    overrides, _, _ = _parse_override_args(["+new_key=42"])
    assert overrides == {"new_key": 42}


def test_add_operator_with_dashes() -> None:
    overrides, _, _ = _parse_override_args(["+--new_key=42"])
    assert overrides == {"new_key": 42}


def test_delete_operator() -> None:
    overrides, deletions, _ = _parse_override_args(["~stale_key"])
    assert overrides == {}
    assert deletions == ["stale_key"]


def test_delete_operator_with_dashes() -> None:
    _, deletions, _ = _parse_override_args(["~--stale_key"])
    assert deletions == ["stale_key"]


def test_dotted_keys_supported_in_all_forms() -> None:
    overrides, deletions, _ = _parse_override_args(
        [
            "--trainer.max_epochs",
            "10",
            "--trainer.lr=0.001",
            "model.dropout=0.2",
            "~trainer.stale",
        ]
    )
    assert overrides == {
        "trainer.max_epochs": 10,
        "trainer.lr": 0.001,
        "model.dropout": 0.2,
    }
    assert deletions == ["trainer.stale"]


def test_string_values_parsed_correctly() -> None:
    """Values are run through ``confluid.parse_value`` for type coercion."""
    overrides, _, _ = _parse_override_args(["--name=test_model", "--count=5", "--ratio=0.7"])
    assert overrides == {"name": "test_model", "count": 5, "ratio": 0.7}


def test_value_starting_with_dash_is_not_consumed() -> None:
    """``--key`` followed by ``--other`` must not eat ``--other`` as a value."""
    overrides, _, _ = _parse_override_args(["--key", "--other", "value"])
    assert overrides == {"key": True, "other": "value"}


def test_value_starting_with_tilde_is_not_consumed() -> None:
    overrides, deletions, _ = _parse_override_args(["--key", "~stale"])
    assert overrides == {"key": True}
    assert deletions == ["stale"]


def test_unrecognised_token_is_dropped_and_reported() -> None:
    """Loose non-flag tokens are never applied — but they are REPORTED via the
    ``dropped`` list (a typo'd override silently vanishing can cost a whole
    training run on defaults)."""
    overrides, deletions, dropped = _parse_override_args(["bare_token_no_equals", "--key", "value"])
    assert overrides == {"key": "value"}
    assert deletions == []
    assert dropped == ["bare_token_no_equals"]


def test_bare_form_with_invalid_key_shape_is_dropped_and_reported() -> None:
    """Tokens like ``http://...`` happen to contain ``=`` but aren't keys."""
    overrides, _, dropped = _parse_override_args(["http://x?a=b"])
    assert overrides == {}
    assert dropped == ["http://x?a=b"]


def test_multiple_dropped_tokens_all_reported_in_order() -> None:
    overrides, _, dropped = _parse_override_args(["bogus", "--lr", "0.1", "also-bogus"])
    assert overrides == {"lr": 0.1}
    assert dropped == ["bogus", "also-bogus"]


def test_mixed_grammar_in_one_invocation() -> None:
    overrides, deletions, dropped = _parse_override_args(
        [
            "--max_epochs",
            "10",
            "trainer.lr=0.001",
            "+new_feature=true",
            "~old_feature",
            "--debug+",
            "--name=mlp",
        ]
    )
    assert overrides == {
        "max_epochs": 10,
        "trainer.lr": 0.001,
        "new_feature": True,
        "debug": True,
        "name": "mlp",
    }
    assert deletions == ["old_feature"]
    assert dropped == []


def test_empty_args_returns_empty() -> None:
    overrides, deletions, dropped = _parse_override_args([])
    assert overrides == {}
    assert deletions == []
    assert dropped == []


# ---------------------------------------------------------------------------
# _apply_overrides — dropped tokens surface as warnings on the context logger.
# ---------------------------------------------------------------------------


class _RecordingLogger:
    """Minimal logger double recording warning calls (deterministic, no loggair capture)."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def debug(self, msg: str) -> None:  # pragma: no cover - noise sink
        pass

    def trace(self, msg: str) -> None:  # pragma: no cover - noise sink
        pass


def test_apply_overrides_warns_per_dropped_token() -> None:
    from liquifai import LiquifyApp
    from liquifai.context import LiquifyContext

    app = LiquifyApp(name="warn-app")
    ctx = LiquifyContext(name="warn-app")
    recorder = _RecordingLogger()
    ctx.logger = recorder
    ctx.config_data = {"lr": 0.5}
    app.context = ctx

    app._apply_overrides(["bogus_token", "--lr", "0.1", "http://not?a=key"])

    assert app.context is not None and app.context.config_data["lr"] == 0.1
    assert len(recorder.warnings) == 2
    assert "'bogus_token'" in recorder.warnings[0]
    assert "'http://not?a=key'" in recorder.warnings[1]


def test_apply_overrides_warns_even_when_nothing_else_applies() -> None:
    """A dropped token must be reported even when no valid override is present
    (the early-return for empty overrides/deletions runs AFTER the warning)."""
    from liquifai import LiquifyApp
    from liquifai.context import LiquifyContext

    app = LiquifyApp(name="warn-app-2")
    ctx = LiquifyContext(name="warn-app-2")
    recorder = _RecordingLogger()
    ctx.logger = recorder
    ctx.config_data = {}
    app.context = ctx

    app._apply_overrides(["only_bogus"])

    assert len(recorder.warnings) == 1
    assert "'only_bogus'" in recorder.warnings[0]


# ---------------------------------------------------------------------------
# _delete_dotted_key — applies deletions to the live config dict.
# ---------------------------------------------------------------------------


def test_delete_dotted_key_top_level() -> None:
    cfg: dict[str, Any] = {"a": 1, "b": 2}
    _delete_dotted_key(cfg, "a")
    assert cfg == {"b": 2}


def test_delete_dotted_key_nested() -> None:
    cfg: dict[str, Any] = {"trainer": {"max_epochs": 1, "lr": 0.01}}
    _delete_dotted_key(cfg, "trainer.max_epochs")
    assert cfg == {"trainer": {"lr": 0.01}}


def test_delete_dotted_key_missing_is_noop() -> None:
    cfg: dict[str, Any] = {"a": 1}
    _delete_dotted_key(cfg, "b.c.d")  # path doesn't exist
    assert cfg == {"a": 1}


def test_delete_dotted_key_into_fluid_kwargs() -> None:
    """Deletion walks into ``Fluid.kwargs`` so ``~trainer.lr`` works even
    when ``trainer`` is a Class fluid loaded from ``!class:Trainer``."""
    from confluid.fluid import Class

    class _Trainer:
        def __init__(self, max_epochs: int = 1, lr: float = 0.01) -> None:
            self.max_epochs = max_epochs
            self.lr = lr

    fluid = Class(_Trainer, max_epochs=10, lr=0.001)
    cfg: dict[str, Any] = {"trainer": fluid}
    _delete_dotted_key(cfg, "trainer.lr")
    assert "lr" not in fluid.kwargs
    assert fluid.kwargs == {"max_epochs": 10}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
