"""Centralized signature surgery and introspection utilities for liquifai."""

import inspect
from typing import Any, Callable, List, Sequence, Tuple


def split_context_param(
    op_func: Callable[..., Any], context_param: str = "conn"
) -> Tuple[inspect.Signature, List[inspect.Parameter]]:
    """Return ``op_func``'s signature and its parameters minus the context one.

    The shared first step of wrapping an operation for an outward surface
    (CLI handler in :meth:`liquifai.core.LiquifyApp.build_commands`, MCP tool
    in :func:`make_mcp_tools`): the ``conn`` context parameter is supplied by
    the wrapper's context factory, never by the caller, so it is stripped
    from the advertised parameter list.
    """
    sig = inspect.signature(op_func)
    params = [p for n, p in sig.parameters.items() if n != context_param]
    return sig, params


def graft_signature(
    fn: Callable[..., Any],
    op_sig: inspect.Signature,
    params: List[inspect.Parameter],
    *,
    return_annotation: Any,
    extra_params: Sequence[inspect.Parameter] = (),
) -> None:
    """Stamp ``fn`` with a synthesized ``__signature__`` / ``__annotations__``.

    The shared second step: introspection consumers (FastMCP JSON-Schema
    generation, liquifai's own DI/help/completion walkers) read the wrapper's
    signature, so it must advertise ``extra_params`` (e.g. context-factory
    params) followed by the operation's own ``params``. Extra params fall
    back to ``Any`` when unannotated (a factory is often a lambda).
    """
    extra = list(extra_params)
    fn.__signature__ = op_sig.replace(  # type: ignore[attr-defined]
        parameters=extra + params, return_annotation=return_annotation
    )
    fn.__annotations__ = {
        **{p.name: (p.annotation if p.annotation is not inspect.Parameter.empty else Any) for p in extra},
        **{p.name: p.annotation for p in params if p.annotation is not inspect.Parameter.empty},
        "return": return_annotation,
    }
