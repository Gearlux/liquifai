"""Tests for the typed exception hierarchy (``liquifai.exceptions``).

Two contracts are pinned here:

1. **Dual inheritance** — every concrete exception subclasses both the
   :class:`liquifai.LiquifaiError` root AND the builtin it semantically
   replaces, so pre-existing ``except ValueError`` / ``except KeyError``
   call sites keep working.
2. **Raise sites** — each error condition empirically raises the new type.
"""

from pathlib import Path
from typing import Type

import pytest

import liquifai
from liquifai import LiquifyApp
from liquifai.completion import install_script, render_helpers, render_script
from liquifai.exceptions import (
    CommandDefinitionError,
    ConfigNotFoundError,
    LiquifaiError,
    UnknownCommandError,
    UnknownOperationError,
    UnsupportedShellError,
)

HIERARCHY = [
    (CommandDefinitionError, ValueError),
    (UnknownOperationError, KeyError),
    (UnknownCommandError, ValueError),
    (ConfigNotFoundError, FileNotFoundError),
    (UnsupportedShellError, ValueError),
]


@pytest.mark.parametrize("exc_cls,builtin", HIERARCHY)
def test_dual_inheritance(exc_cls: Type[Exception], builtin: Type[Exception]) -> None:
    assert issubclass(exc_cls, LiquifaiError)
    assert issubclass(exc_cls, builtin)


@pytest.mark.parametrize(
    "name",
    [
        "LiquifaiError",
        "CommandDefinitionError",
        "ConfigNotFoundError",
        "UnknownCommandError",
        "UnknownOperationError",
        "UnsupportedShellError",
    ],
)
def test_exceptions_exported_from_package(name: str) -> None:
    assert getattr(liquifai, name) is not None
    assert name in liquifai.__all__


def test_bad_script_command_flow_mode_raises_command_definition_error() -> None:
    app = LiquifyApp(name="test-app")
    with pytest.raises(CommandDefinitionError) as ei:
        app.script_command(flow_mode="weird")  # type: ignore[arg-type]
    assert isinstance(ei.value, ValueError)


def test_bad_operation_presentation_raises_command_definition_error() -> None:
    app = LiquifyApp(name="test-app")
    with pytest.raises(CommandDefinitionError) as ei:
        app.operation(presentation="bogus")(lambda: None)  # type: ignore[arg-type]
    assert isinstance(ei.value, ValueError)


def test_set_completions_unknown_operation_raises_unknown_operation_error() -> None:
    app = LiquifyApp(name="test-app")
    with pytest.raises(UnknownOperationError) as ei:
        app.set_completions("no-such-op", {})
    assert isinstance(ei.value, KeyError)


def test_render_script_unsupported_shell_raises() -> None:
    with pytest.raises(UnsupportedShellError) as ei:
        render_script("myprog", "tcsh")
    assert isinstance(ei.value, ValueError)


def test_render_helpers_unsupported_shell_raises() -> None:
    with pytest.raises(UnsupportedShellError):
        render_helpers("tcsh")


def test_install_script_unsupported_shell_raises(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedShellError):
        install_script("myprog", "tcsh", home=tmp_path)


def test_unknown_command_raises_typed_error_not_bare_sys_exit() -> None:
    """``_execute`` raises; ``run()`` alone owns the exit code (embedding-safe)."""
    from liquifai.core import Invocation

    app = LiquifyApp(name="test-app")
    inv = Invocation(
        target_app=app,
        target_func=None,
        config_path=None,
        config_token=None,
        positional_names=[],
        positional_values=[],
        remaining_tokens=[],
    )
    with pytest.raises(UnknownCommandError) as ei:
        app._execute(inv)
    assert isinstance(ei.value, ValueError)
