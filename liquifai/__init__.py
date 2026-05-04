"""
Liquify: A streamlined, type-safe application framework.

Top-level imports are lazy via :pep:`562` ``__getattr__`` so importing
``liquifai.completion`` (or the ``liquifai-complete`` fast-path entry) does
not pay the cost of pulling in confluid / logflow / rich.
"""

from typing import TYPE_CHECKING, Any

__all__ = ["LiquifyApp", "LiquifyContext", "get_context", "set_context"]

if TYPE_CHECKING:
    from liquifai.context import LiquifyContext, get_context, set_context
    from liquifai.core import LiquifyApp


def __getattr__(name: str) -> Any:
    if name == "LiquifyApp":
        from liquifai.core import LiquifyApp

        return LiquifyApp
    if name in ("LiquifyContext", "get_context", "set_context"):
        from liquifai import context

        return getattr(context, name)
    raise AttributeError(f"module 'liquifai' has no attribute {name!r}")
