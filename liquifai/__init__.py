"""
Liquify: A streamlined, type-safe application framework.

Top-level imports are lazy via :pep:`562` ``__getattr__`` so importing
``liquifai.completion`` (or the ``liquifai-complete`` fast-path entry) does
not pay the cost of pulling in confluid / loggair / rich.
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
    "UnknownCommandError",
    "UnknownFlagError",
    "UnknownOperationError",
    "UnsupportedShellError",
]

if TYPE_CHECKING:
    from liquifai.context import LiquifyContext, get_context, set_context
    from liquifai.core import HelpLayout, LiquifyApp, Presentation
    from liquifai.exceptions import (
        CommandDefinitionError,
        ConfigNotFoundError,
        LiquifaiError,
        UnknownCommandError,
        UnknownFlagError,
        UnknownOperationError,
        UnsupportedShellError,
    )
    from liquifai.tools import make_mcp_tools


def __getattr__(name: str) -> Any:
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
        "UnknownCommandError",
        "UnknownFlagError",
        "UnknownOperationError",
        "UnsupportedShellError",
    ):
        from liquifai import exceptions

        return getattr(exceptions, name)
    raise AttributeError(f"module 'liquifai' has no attribute {name!r}")
