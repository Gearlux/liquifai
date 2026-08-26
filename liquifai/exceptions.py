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


class UnknownCommandError(LiquifaiError, ValueError):
    """The argv tokens did not resolve to a command or group with a default."""


class UnknownFlagError(LiquifaiError, ValueError):
    """A CLI flag named no parameter of the command it was given to.

    Raised only by an app built with ``strict_flags=True``. The permissive default lets an override
    fall through to the config document, which is a legitimate pattern; an app whose commands take
    plain values has no such fall-through, and for it a silently-ignored flag is indistinguishable
    from a flag that worked.
    """


class ConfigNotFoundError(LiquifaiError, FileNotFoundError):
    """The requested configuration file does not exist.

    Dual-inherits ``FileNotFoundError`` (not ``confluid.ConfigFileNotFoundError``)
    because the file was named on the CLI, not discovered by confluid's search
    tiers — but it renders through the same failure contract: one clean
    ``Error: …`` line + exit 1, or a full traceback under ``--debug``.
    """


class UnsupportedShellError(LiquifaiError, ValueError):
    """A shell name is not one of the supported completion targets."""
