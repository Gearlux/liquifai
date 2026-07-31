"""Generic MCP tool builder for :class:`~liquifai.LiquifyApp` operations.

NOTE: do not add ``from __future__ import annotations`` — FastMCP and pydantic-ai
introspect live annotation objects on tool parameters.
"""

import functools
import inspect
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union, cast

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp

from liquifai.introspection import graft_signature, split_context_param


def make_mcp_tools(
    app_or_ops: "Union[LiquifyApp, Dict[str, Callable[..., Any]]]",
    *,
    context_factory: Optional[Callable[..., Any]] = None,
) -> List[Callable[..., Dict[str, Any]]]:
    """Build MCP-compatible tool wrappers from an operations dict or a LiquifyApp.

    Accepts either:

    * A :class:`~liquifai.LiquifyApp` — reads ``_operations`` and
      ``_mcp_context_factory`` from it; the ``context_factory`` kwarg is ignored.
    * A plain ``{name: fn}`` dict paired with an explicit ``context_factory`` kwarg.

    The factory's *signature* determines the extra params prepended to every tool.
    The factory is called with those param values to produce the context injected as
    the first argument of each operation.

    Example (LiquifyApp form — backward-compatible)::

        app.set_mcp_context_factory(
            lambda server="PROD", dry_run=False: SairenClient(server=server, dry_run=dry_run)
        )
        for tool in make_mcp_tools(app):
            mcp_server.tool()(tool)

    Example (dict form — used by sairen's MCP server)::

        ops = dict(discover_operations())
        for tool in make_mcp_tools(ops, context_factory=_mcp_conn_factory):
            mcp_server.tool()(tool)

    Each generated tool:

    * has ``__name__`` == the operation function name (e.g. ``"dataset_list"``).
    * advertises the factory params + the operation's own params in its
      ``__signature__`` / ``__annotations__`` so FastMCP generates correct JSON Schema.
    * catches all exceptions and returns ``{"error": str(exc)}`` so a single tool
      failure cannot break an MCP session.

    Args:
        app_or_ops: A :class:`~liquifai.LiquifyApp` or a ``{name: fn}`` dict of
            operations.
        context_factory: Factory callable whose signature defines the extra params
            prepended to every tool.  Used only when ``app_or_ops`` is a dict;
            ignored when a :class:`~liquifai.LiquifyApp` is passed (the app's own
            ``_mcp_context_factory`` is used instead).

    Returns:
        List of wrapped callables, one per operation, in iteration order.
    """
    # Resolve operations dict and factory regardless of which form was passed.
    if isinstance(app_or_ops, dict):
        ops_items: Any = app_or_ops.items()
        factory = context_factory
    else:
        ops_items = app_or_ops._operations.items()
        factory = app_or_ops._mcp_context_factory

    if factory is not None:
        factory_sig = inspect.signature(factory)
        factory_params = list(factory_sig.parameters.values())
    else:
        factory_params = []

    tools: List[Callable[..., Dict[str, Any]]] = []

    for _op_name, op_func in ops_items:
        op_sig, op_params = split_context_param(op_func)

        # Capture loop variables — Python closures capture by reference.
        def _make_tool(
            op_func_: Callable[..., Any] = op_func,
            factory_: Any = factory,
            factory_params_: List[inspect.Parameter] = factory_params,
        ) -> Callable[..., Dict[str, Any]]:
            @functools.wraps(op_func_)
            def tool(**kwargs: Any) -> Dict[str, Any]:
                fkwargs = {
                    p.name: kwargs.pop(
                        p.name,
                        p.default if p.default is not inspect.Parameter.empty else None,
                    )
                    for p in factory_params_
                }
                conn = factory_(**fkwargs) if factory_ is not None else None
                try:
                    result = op_func_(conn, **kwargs) if conn is not None else op_func_(**kwargs)
                    return cast(Dict[str, Any], result)
                except Exception as exc:  # noqa: BLE001 — MCP tools report failures in-band
                    return {"error": str(exc)}

            return tool

        tool = _make_tool()
        graft_signature(tool, op_sig, op_params, return_annotation=Dict[str, Any], extra_params=factory_params)
        tools.append(tool)

    return tools
