"""
Liquify: A streamlined, type-safe application framework.

Top-level imports are lazy via :pep:`562` ``__getattr__`` so importing
``liquifai.completion`` (or the ``liquifai-complete`` fast-path entry) does
not pay the cost of pulling in confluid / loggair / rich. ``__version__`` is
lazy for the same reason: it is read from the installed metadata on first access.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "LiquifyApp",
    "LiquifyContext",
    "get_context",
    "set_context",
    "make_mcp_tools",
    "Presentation",
    "HelpLayout",
    "LiquifaiError",
    "CommandDefinitionError",
    "ConfigNotFoundError",
    "MissingArgumentError",
    "UnknownCommandError",
    "UnknownFlagError",
    "UnknownOperationError",
    "UnsupportedShellError",
]

if TYPE_CHECKING:
    __version__: str
    from liquifai.context import LiquifyContext, get_context, set_context
    from liquifai.core import HelpLayout, LiquifyApp, Presentation
    from liquifai.exceptions import (
        CommandDefinitionError,
        ConfigNotFoundError,
        LiquifaiError,
        MissingArgumentError,
        UnknownCommandError,
        UnknownFlagError,
        UnknownOperationError,
        UnsupportedShellError,
    )
    from liquifai.tools import make_mcp_tools


def __getattr__(name: str) -> Any:
    if name == "__version__":
        # Read on FIRST ACCESS, never at import: every shell TAB imports this package on the
        # completion fast path, and the metadata read (~15 ms) outweighs that whole import
        # (~11 ms). Single source of truth: pyproject.toml's `version`, via the installed
        # distribution's metadata — never type the number in here as well.
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("liquifai")
        except PackageNotFoundError:  # an uninstalled source checkout
            return "0.0.0.dev0"
    if name == "LiquifyApp":
        from liquifai.core import LiquifyApp

        return LiquifyApp
    if name in ("LiquifyContext", "get_context", "set_context"):
        from liquifai import context

        return getattr(context, name)
    if name == "make_mcp_tools":
        from liquifai.tools import make_mcp_tools

        return make_mcp_tools
    if name in ("Presentation", "HelpLayout"):
        from liquifai import core

        return getattr(core, name)
    if name in (
        "LiquifaiError",
        "CommandDefinitionError",
        "ConfigNotFoundError",
        "MissingArgumentError",
        "UnknownCommandError",
        "UnknownFlagError",
        "UnknownOperationError",
        "UnsupportedShellError",
    ):
        from liquifai import exceptions

        return getattr(exceptions, name)
    raise AttributeError(f"module 'liquifai' has no attribute {name!r}")
