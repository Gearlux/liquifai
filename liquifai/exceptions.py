"""Typed exception hierarchy for liquifai.

Every concrete exception dual-inherits the :class:`LiquifaiError` root AND the
builtin it semantically replaces (``ValueError``, ``KeyError``), so
pre-existing ``except ValueError:`` / ``pytest.raises(ValueError)`` call sites
keep working while new callers can catch liquifai failures distinctly.

``LiquifaiError`` deliberately does NOT subclass ``confluid.ConfluidError`` —
CLI-definition errors are not configuration errors.
"""

from __future__ import annotations


class LiquifaiError(Exception):
    """Root of the liquifai exception hierarchy."""


class CommandDefinitionError(LiquifaiError, ValueError):
    """A ``@command`` / ``@script_command`` / ``@operation`` declaration is invalid."""


class UnknownOperationError(LiquifaiError, KeyError):
    """An operation name is not registered on this app."""


class UnsupportedShellError(LiquifaiError, ValueError):
    """A shell name is not one of the supported completion targets."""
