"""Pins the extended CLI override grammar parsed by ``_parse_override_args``.

Forms covered:
  * ``--key value``           (legacy, still primary)
  * ``--key=value``
  * ``key=value``             (bare, no ``--``)
  * ``--key+`` / ``--key-``   (polarity)
  * ``--key``                 (implicit ``True``)
  * ``+key=value``            (add — treated same as override today)
  * ``~key``                  (delete)
  * Mixed orderings + dotted keys
"""

from typing import Any

import pytest

from liquifai.core import _delete_dotted_key, _parse_override_args


def test_legacy_dash_space_form() -> None:
    overrides, deletions = _parse_override_args(["--max_epochs", "10"])
    assert overrides == {"max_epochs": 10}
    assert deletions == []


def test_dash_equals_form() -> None:
    overrides, deletions = _parse_override_args(["--max_epochs=10"])
    assert overrides == {"max_epochs": 10}
    assert deletions == []


def test_bare_equals_form() -> None:
    overrides, deletions = _parse_override_args(["max_epochs=10"])
    assert overrides == {"max_epochs": 10}
    assert deletions == []


def test_polarity_plus_minus_forms() -> None:
    overrides, _ = _parse_override_args(["--enable+", "--debug-"])
    assert overrides == {"enable": True, "debug": False}


def test_implicit_boolean_true() -> None:
    overrides, _ = _parse_override_args(["--verbose"])
    assert overrides == {"verbose": True}


def test_add_operator() -> None:
    overrides, _ = _parse_override_args(["+new_key=42"])
    assert overrides == {"new_key": 42}


def test_add_operator_with_dashes() -> None:
    overrides, _ = _parse_override_args(["+--new_key=42"])
    assert overrides == {"new_key": 42}


def test_delete_operator() -> None:
    overrides, deletions = _parse_override_args(["~stale_key"])
    assert overrides == {}
    assert deletions == ["stale_key"]


def test_delete_operator_with_dashes() -> None:
    overrides, deletions = _parse_override_args(["~--stale_key"])
    assert deletions == ["stale_key"]


def test_dotted_keys_supported_in_all_forms() -> None:
    overrides, deletions = _parse_override_args(
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
    overrides, _ = _parse_override_args(["--name=test_model", "--count=5", "--ratio=0.7"])
    assert overrides == {"name": "test_model", "count": 5, "ratio": 0.7}


def test_value_starting_with_dash_is_not_consumed() -> None:
    """``--key`` followed by ``--other`` must not eat ``--other`` as a value."""
    overrides, _ = _parse_override_args(["--key", "--other", "value"])
    assert overrides == {"key": True, "other": "value"}


def test_value_starting_with_tilde_is_not_consumed() -> None:
    overrides, deletions = _parse_override_args(["--key", "~stale"])
    assert overrides == {"key": True}
    assert deletions == ["stale"]


def test_unrecognised_token_is_skipped() -> None:
    """Loose non-flag tokens are dropped (legacy behaviour preserved)."""
    overrides, deletions = _parse_override_args(["bare_token_no_equals", "--key", "value"])
    assert overrides == {"key": "value"}
    assert deletions == []


def test_bare_form_with_invalid_key_shape_is_skipped() -> None:
    """Tokens like ``http://...`` happen to contain ``=`` but aren't keys."""
    overrides, _ = _parse_override_args(["http://x?a=b"])
    assert overrides == {}


def test_mixed_grammar_in_one_invocation() -> None:
    overrides, deletions = _parse_override_args(
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


def test_empty_args_returns_empty() -> None:
    overrides, deletions = _parse_override_args([])
    assert overrides == {}
    assert deletions == []


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
