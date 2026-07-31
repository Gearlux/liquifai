"""Policy layer: how a declarative :class:`~liquifai.bridge.spec.ExposeSpec`
becomes a runnable operation.

A *policy* is a spec-in / op-out builder. The bridge ships the two SDK-agnostic
ones — :class:`CallPolicy` (one SDK call, ``fields``/``status`` shaped result)
and :class:`ItemsPolicy` (one SDK call whose records render as a list table) —
and consumers register their own for SDK-dialect surfaces (e.g. a paginated
``list`` policy whose search/sort/page vocabulary is specific to one platform)
via ``SdkBridge(policies={"list": MyListPolicy()})``.

NOTE: no ``from __future__ import annotations`` in ``liquifai.bridge`` — live
annotation objects are required by liquifai/confluid/FastMCP introspection.
"""

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Protocol, Tuple, runtime_checkable

from liquifai.bridge.shaping import attr_str, dry_descriptor, format_call, items_result, record_to_dict, records_of
from liquifai.bridge.shaping import require as _require
from liquifai.bridge.spec import ExposeSpec, P


@runtime_checkable
class BridgeConnection(Protocol):
    """What a synthesized operation needs from its first (``conn``) parameter.

    The concrete connection class (typically a confluid ``@configurable``) is
    passed once as ``SdkBridge(conn_cls=...)`` and baked into every synthesized
    signature so liquifai DI can resolve it; this Protocol documents the
    runtime contract that class must satisfy.
    """

    dry_run: bool  # True -> return dry_descriptor(), never touch get_client()

    def get_client(self) -> Any:
        """Lazily build/return the SDK client; sub-clients are attributes on it."""
        ...  # pragma: no cover - protocol


@dataclass(frozen=True)
class PolicyContext:
    """Everything a policy needs about the group it is building ops for.

    Built by :class:`~liquifai.bridge.group.SdkBridge` per group; policies use
    :meth:`target` to reach the SDK call target and :meth:`call_label` for
    dry-run output, and go through :attr:`adapters` / :attr:`shape_status` so
    per-bridge overrides apply uniformly.
    """

    #: Group/domain name — the op-name prefix and the dry-run ``command`` field.
    group: str
    #: Sub-client attribute on the SDK client; ``""`` = the client itself.
    sub: str
    #: Concrete connection class, baked into op signatures (see BridgeConnection).
    conn_cls: type
    #: Merged adapter table (DEFAULT_ADAPTERS + per-bridge extras).
    adapters: Mapping[str, Callable[[Any], Any]]
    #: ``conn -> SDK call target`` (default: ``getattr(conn.get_client(), sub)``).
    target: Callable[[Any], Any]
    #: ``(spec, result, kwargs) -> status dict`` (default: :func:`shape_status`).
    shape_status: Callable[[ExposeSpec, Any, Dict[str, Any]], Dict[str, Any]]

    def call_label(self, method: str) -> str:
        """Dry-run call label, e.g. ``client.workspace.list_workspaces``."""
        return f"client.{self.sub}.{method}" if self.sub else f"client.{method}"


class OpPolicy(Protocol):
    """Spec in, operation out — the bridge's policy extension point."""

    def build(self, spec: ExposeSpec, ctx: PolicyContext) -> Callable[..., Dict[str, Any]]:
        """Build the operation callable for ``spec``.

        Contract for the returned callable:

        * first param ``conn`` (POSITIONAL_OR_KEYWORD) annotated ``ctx.conn_cls``;
          all others KEYWORD_ONLY, required ones with no default
          (:func:`build_op_signature` produces exactly this shape);
        * ``__signature__`` set to live objects;
        * returns a JSON-serializable ``Dict[str, Any]``;
        * honors ``conn.dry_run`` via :func:`~liquifai.bridge.shaping.dry_descriptor`
          BEFORE calling ``conn.get_client()``.

        ``__name__`` / ``__qualname__`` / ``__doc__`` / ``__annotations__`` are
        stamped by the bridge afterwards — the policy does not name the op.
        """
        ...  # pragma: no cover - protocol


def resolve_policy(spec: ExposeSpec) -> str:
    """The policy key a spec routes to: explicit ``policy=`` wins, else by presentation."""
    if spec.policy != "auto":
        return spec.policy
    return "list" if spec.presentation == "list" else "call"


def default_target(conn: Any, sub: str) -> Any:
    """Default SDK call-target resolution: the ``sub`` attribute of the lazy client."""
    client = conn.get_client()
    return getattr(client, sub) if sub else client


def build_op_signature(params: List[P], conn_cls: type) -> inspect.Signature:
    """Build the synthesized op signature: ``conn`` first, then one param per ``P``.

    Required params are keyword-only with no default (liquifai's
    ``build_commands`` turns those into CLI positionals); optional params carry
    their default. The ``conn`` annotation is the bridge's concrete connection
    class so liquifai DI resolves it.
    """
    sig_params = [inspect.Parameter("conn", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=conn_cls)]
    for p in params:
        default = inspect.Parameter.empty if p.required else p.default
        annotation = type(p.default) if (not p.required and p.default is not None) else str
        sig_params.append(
            inspect.Parameter(p.cli, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation)
        )
    return inspect.Signature(sig_params, return_annotation=Dict[str, Any])


def resolve_call(
    spec: ExposeSpec, kwargs: Dict[str, Any], adapters: Mapping[str, Callable[[Any], Any]]
) -> Tuple[List[Any], Dict[str, Any]]:
    """Map the CLI kwargs onto the SDK call ``(args, kwargs)`` per the param specs.

    Applies adapters (rename/parse), drops empty optionals (``omit_empty``) and
    client-side-only params (``sdk is None``), and routes ``send_positional``
    params to positional args. Validates required params first.
    """
    required = {p.cli: kwargs.get(p.cli, "") for p in spec.params if p.required or p.validate}
    if required:
        _require(**required)
    call_args: List[Any] = []
    call_kwargs: Dict[str, Any] = {}
    for p in spec.params:
        if p.sdk is None:  # client-side-only param
            continue
        value = kwargs.get(p.cli, p.default if not p.required else "")
        if p.omit_empty and not p.required and not value:
            continue
        if p.adapt is not None:
            value = adapters[p.adapt](value)
        if p.send_positional:
            call_args.append(value)
        else:
            call_kwargs[p.sdk] = value
    call_kwargs.update(spec.constants)  # always-sent kwargs come last
    return call_args, call_kwargs


def shape_status(spec: ExposeSpec, result: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Build a ``status`` presentation dict from the spec + the call result + inputs.

    The default status shaper; override per-bridge with
    ``SdkBridge(shape_status=...)`` when an SDK's result envelopes need a
    different vocabulary.
    """
    out: Dict[str, Any] = {"status": spec.status_word}
    for name in spec.status_echo:
        out[name] = kwargs.get(name)
    if spec.result_key:
        out[spec.result_key] = attr_str(result, spec.result_field, str(result)) if spec.result_field else str(result)
    if spec.result_full:
        out["result"] = record_to_dict(result)
    if spec.spread_result:
        out.update(record_to_dict(result))
    return out


class CallPolicy:
    """One SDK call; result shaped as ``fields`` (full record) or ``status``."""

    def build(self, spec: ExposeSpec, ctx: PolicyContext) -> Callable[..., Dict[str, Any]]:
        method = spec.sdk_method
        fn_label = ctx.call_label(method)

        def op(conn: Any, **kwargs: Any) -> Dict[str, Any]:
            call_args, call_kwargs = resolve_call(spec, kwargs, ctx.adapters)
            if conn.dry_run:
                return dry_descriptor(ctx.group, spec.verb, format_call(fn_label, *call_args, **call_kwargs))
            result = getattr(ctx.target(conn), method)(*call_args, **call_kwargs)
            if spec.presentation == "fields":
                if spec.nullable and result is None:
                    return {"found": False, **{p.cli: kwargs.get(p.cli) for p in spec.params if p.required}}
                record = record_to_dict(result)
                return {"found": True, **record} if spec.nullable else record
            return ctx.shape_status(spec, result, kwargs)

        op.__signature__ = build_op_signature(spec.params, ctx.conn_cls)  # type: ignore[attr-defined]
        return op


class ItemsPolicy:
    """One SDK call whose records render as a list table.

    Like :class:`CallPolicy` for argument handling, but the result is wrapped
    as ``items_result(records_of(result, attr=result_attr))`` — for
    positional-addressed lists (versions / metrics / artifacts), NOT a full
    paginated list surface (that is an SDK dialect, hence consumer-side).
    """

    def build(self, spec: ExposeSpec, ctx: PolicyContext) -> Callable[..., Dict[str, Any]]:
        method = spec.sdk_method
        fn_label = ctx.call_label(method)

        def op(conn: Any, **kwargs: Any) -> Dict[str, Any]:
            call_args, call_kwargs = resolve_call(spec, kwargs, ctx.adapters)
            if conn.dry_run:
                return dry_descriptor(ctx.group, spec.verb, format_call(fn_label, *call_args, **call_kwargs))
            result = getattr(ctx.target(conn), method)(*call_args, **call_kwargs)
            return items_result(records_of(result, attr=spec.result_attr))

        op.__signature__ = build_op_signature(spec.params, ctx.conn_cls)  # type: ignore[attr-defined]
        return op


#: The SDK-agnostic policies every bridge starts with. Consumers merge their
#: dialect policies over this table via ``SdkBridge(policies={...})``.
BUILTIN_POLICIES: Dict[str, OpPolicy] = {"call": CallPolicy(), "items": ItemsPolicy()}
