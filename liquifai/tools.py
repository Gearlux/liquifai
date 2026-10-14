"""Generic MCP tool builder for :class:`~liquifai.LiquifyApp` operations.

NOTE: do not add ``from __future__ import annotations`` — the MCP SDK's ``MCPServer`` and
pydantic-ai introspect live annotation objects on tool parameters.
"""

import functools
import inspect
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union, cast

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp

from liquifai.exceptions import CommandDefinitionError
from liquifai.introspection import CONTEXT_PARAM, graft_signature, split_context_param


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
    * advertises the factory params + the operation's own params, all keyword-only, in its
      ``__signature__`` / ``__annotations__`` so ``MCPServer`` generates correct JSON Schema.
    * passes the built context to the operation BY NAME, as ``conn``. An operation without a
      ``conn`` parameter gets no context: its tool neither calls the factory nor advertises
      the factory's params.
    * catches all exceptions — the factory's included — and returns ``{"error": str(exc)}``
      so a single tool failure cannot break an MCP session.

    Raises:
        CommandDefinitionError: an operation parameter has the name of a factory parameter
            (the tool could take that name only once).

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

    for op_name, op_func in ops_items:
        op_sig, op_params = split_context_param(op_func)
        # The factory's parameters configure the context, so only an operation that declares
        # one is built a context and advertises them.
        uses_context = factory is not None and CONTEXT_PARAM in op_sig.parameters
        extra_params = factory_params if uses_context else []
        clash = [p.name for p in op_params if p.name in {e.name for e in extra_params}]
        if clash:
            raise CommandDefinitionError(
                f"{op_name}: parameter {clash[0]!r} is also a parameter of the MCP context factory "
                f"{getattr(factory, '__name__', repr(factory))!r}; an MCP tool takes each name once — "
                f"rename the operation's parameter."
            )

        # Capture loop variables — Python closures capture by reference.
        def _make_tool(
            op_func_: Callable[..., Any] = op_func,
            factory_: Any = factory if uses_context else None,
            factory_params_: List[inspect.Parameter] = extra_params,
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
                # The factory runs INSIDE the try: a context that cannot be built (an unreachable
                # server, bad credentials) is a tool failure like any other, reported in-band.
                try:
                    if factory_ is not None:
                        kwargs[CONTEXT_PARAM] = factory_(**fkwargs)
                    return cast(Dict[str, Any], op_func_(**kwargs))
                except Exception as exc:  # noqa: BLE001 — MCP tools report failures in-band
                    return {"error": str(exc)}

            return tool

        tool = _make_tool()
        # Every advertised parameter is KEYWORD-ONLY: a tool is only ever called by keyword, and
        # the factory's defaulted parameters come first, so a required positional-or-keyword
        # operation parameter after them would be an invalid signature.
        graft_signature(
            tool,
            op_sig,
            _keyword_only(op_params),
            return_annotation=Dict[str, Any],
            extra_params=_keyword_only(extra_params),
        )
        tools.append(tool)

    return tools


def _keyword_only(params: List[inspect.Parameter]) -> List[inspect.Parameter]:
    """``params`` as keyword-only, ``**kwargs`` kept last; a ``*args`` no keyword call can fill is dropped."""
    named = [
        p.replace(kind=inspect.Parameter.KEYWORD_ONLY)
        for p in params
        if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    return named + [p for p in params if p.kind is inspect.Parameter.VAR_KEYWORD]
