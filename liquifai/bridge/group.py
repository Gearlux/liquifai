"""The SDK bridge itself: :class:`SdkBridge` and its group class-decorator.

A consumer builds ONE bridge instance binding its connection class, dialect
policies/adapters, and app-configuration hook, then decorates SDK sub-client
subclasses::

    bridge = SdkBridge(conn_cls=MyConn, policies={"list": MyListPolicy()})

    @bridge.group(name="widget", sub="widget", aliases=["w"])
    class WidgetClient(sdk.WidgetClient):
        @expose(verb="info", presentation="fields")
        def get_widget(self) -> None: ...

The decorator walks the class's :func:`~liquifai.bridge.spec.expose` /
:func:`~liquifai.bridge.spec.custom` members, synthesizes one liquifai
operation each (``<name>_<verb>``), registers them on a fresh
:class:`~liquifai.core.LiquifyApp`, wires completions, runs the configure
hook (which typically calls ``build_commands()``), and records the group on
THIS bridge instance — registration is per-bridge, never a module global, so
two bridged apps in one process cannot collide.

NOTE: no ``from __future__ import annotations`` in ``liquifai.bridge`` — live
annotation objects are required by liquifai/confluid/FastMCP introspection.
"""

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from loggair import get_logger

from liquifai.bridge.policies import BUILTIN_POLICIES, OpPolicy, PolicyContext, default_target, resolve_policy
from liquifai.bridge.policies import shape_status as _default_shape_status
from liquifai.bridge.shaping import DEFAULT_ADAPTERS
from liquifai.bridge.spec import _CUSTOM_ATTR, _EXPOSE_ATTR, CustomSpec, ExposeSpec
from liquifai.core import LiquifyApp
from liquifai.exceptions import CommandDefinitionError

logger = get_logger("liquifai.bridge")

#: Marker stamped on every op synthesized from an ``@expose`` spec, so
#: consumers can distinguish generated ops from hand-written (``@custom``)
#: bodies (e.g. to relax per-param docstring requirements for generated ops).
BRIDGED_ATTR = "__liquifai_bridged__"


@dataclass
class Group:
    """A registered group — its built app + the metadata the CLI mounts it with."""

    name: str
    sub: str
    aliases: List[str]
    app: LiquifyApp


def _sdk_docstring(cls: type, method: str) -> str:
    """Return the wrapped SDK method's own docstring (from the inherited base class)."""
    for base in cls.__mro__[1:]:
        member = base.__dict__.get(method)
        if member is not None:
            return inspect.getdoc(member) or ""
    return ""


def _synthesize(spec: ExposeSpec, ctx: PolicyContext, policy: OpPolicy, sdk_doc: str = "") -> Callable[..., Any]:
    """Build + name + document one operation from its spec via the routed policy.

    ``sdk_doc`` is the wrapped SDK method's own docstring; it becomes the op
    summary unless ``spec.doc`` overrides it — so CLI ``--help`` / MCP
    descriptions mirror the SDK automatically.
    """
    op = policy.build(spec, ctx)
    op_name = f"{ctx.group}_{spec.verb}".replace("-", "_")
    op.__name__ = op_name
    op.__qualname__ = op_name
    op.__doc__ = spec.doc or sdk_doc or f"{spec.verb.replace('-', ' ').title()} ({ctx.sub or ctx.group})."
    setattr(op, BRIDGED_ATTR, True)
    # mypy/introspection: mirror annotations from the policy-built signature.
    op.__annotations__ = {
        p.name: p.annotation
        for p in op.__signature__.parameters.values()  # type: ignore[attr-defined]
        if p.name != "conn" and p.annotation is not inspect.Parameter.empty
    }
    return op


class SdkBridge:
    """Turns decorated SDK sub-client subclasses into liquifai operation groups.

    Args:
        conn_cls: The concrete connection class (see
            :class:`~liquifai.bridge.policies.BridgeConnection` for the runtime
            contract) — baked into every synthesized op signature so liquifai
            DI resolves the ``conn`` param.
        configure: Hook called with each group's freshly-built
            :class:`~liquifai.core.LiquifyApp` AFTER all ops are registered —
            wire ``set_context_factory`` / ``set_presenter`` /
            ``set_mcp_context_factory`` here. Defaults to plain
            ``app.build_commands()``; a custom hook must call it itself.
        adapters: Extra/override input adapters merged OVER
            :data:`~liquifai.bridge.shaping.DEFAULT_ADAPTERS` (consumer wins).
        policies: Extra/override policies merged OVER
            :data:`~liquifai.bridge.policies.BUILTIN_POLICIES` (``call`` /
            ``items``) — register SDK-dialect surfaces (e.g. ``"list"``) here.
        target: Optional ``(conn, sub) -> SDK call target`` override for SDKs
            whose sub-surfaces are not plain client attributes (e.g. a
            ``client.resource(name)`` factory).
        shape_status: Optional ``(spec, result, kwargs) -> dict`` override of
            the default :func:`~liquifai.bridge.policies.shape_status` status
            shaper.
    """

    def __init__(
        self,
        *,
        conn_cls: type,
        configure: Optional[Callable[[LiquifyApp], None]] = None,
        adapters: Optional[Mapping[str, Callable[[Any], Any]]] = None,
        policies: Optional[Mapping[str, OpPolicy]] = None,
        target: Optional[Callable[[Any, str], Any]] = None,
        shape_status: Optional[Callable[[ExposeSpec, Any, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        if not callable(getattr(conn_cls, "get_client", None)):
            logger.warning(
                f"SdkBridge conn_cls {conn_cls!r} does not define a callable get_client() — "
                "synthesized operations will fail at their first non-dry-run call "
                "(see liquifai.bridge.BridgeConnection for the contract)."
            )
        self._conn_cls = conn_cls
        self._configure = configure
        self._adapters: Dict[str, Callable[[Any], Any]] = {**DEFAULT_ADAPTERS, **(adapters or {})}
        self._policies: Dict[str, OpPolicy] = {**BUILTIN_POLICIES, **(policies or {})}
        self._target = target
        self._shape_status = shape_status or _default_shape_status
        self._groups: Dict[str, Group] = {}

    # -- registration -------------------------------------------------------

    def group(
        self, *, name: str, sub: str = "", aliases: Optional[List[str]] = None, description: str = ""
    ) -> Callable[[type], type]:
        """Class decorator: turn an SDK sub-client subclass into a CLI/MCP domain.

        Args:
            name: domain name = op-name prefix + CLI sub-app name
                (``workspace`` -> ``workspace_list`` / ``app workspace list``).
            sub: the sub-client attribute ops call
                (``conn.get_client().<sub>``); ``""`` targets the client itself.
            aliases: CLI aliases for the sub-app (e.g. ``["ws"]``).
            description: sub-app help text.
        """

        def _decorate(cls: type) -> type:
            app = LiquifyApp(name=name, description=description or f"{name.title()} operations.")
            ctx = self._context(name, sub)
            completions: List[Tuple[str, Dict[str, Callable[..., Any]]]] = []

            for _attr_name, member in list(vars(cls).items()):
                espec: Optional[ExposeSpec] = getattr(member, _EXPOSE_ATTR, None)
                cspec: Optional[CustomSpec] = getattr(member, _CUSTOM_ATTR, None)
                if espec is not None:
                    self._validate_spec(espec, name)
                    policy_key = resolve_policy(espec)
                    policy = self._policies.get(policy_key)
                    if policy is None:
                        raise CommandDefinitionError(
                            f"@expose({espec.sdk_method!r}) in group {name!r} routes to policy "
                            f"{policy_key!r}, which is not registered on this bridge "
                            f"(available: {sorted(self._policies)}); pass it via SdkBridge(policies=...)"
                        )
                    op: Callable[..., Any] = _synthesize(espec, ctx, policy, _sdk_docstring(cls, espec.sdk_method))
                    # The default title's placeholder must be a param the op actually
                    # has (the presenter ``format_map``s it even for status ops), so
                    # derive it from the first required param; pure-list ops have none.
                    if policy_key == "list":
                        default_title = f"{name.title()}s"
                    else:
                        first_req = next((p.cli for p in espec.params if p.required), "")
                        default_title = f"{name.title()}: {{{first_req}}}" if first_req else name.title()
                    app.operation(
                        presentation=espec.presentation,
                        columns=espec.columns,
                        title=espec.title or default_title,
                        empty=espec.empty,
                    )(op)
                    if espec.completions:
                        completions.append((op.__name__, espec.completions))
                elif cspec is not None:
                    op = member  # the function IS the op body (conn-first); keep its docstring
                    op.__name__ = f"{name}_{cspec.verb}".replace("-", "_")
                    op.__qualname__ = op.__name__
                    app.operation(
                        presentation=cspec.presentation,
                        columns=cspec.columns,
                        title=cspec.title or f"{name.title()}: result",
                        empty=cspec.empty,
                    )(op)
                    if cspec.completions:
                        completions.append((op.__name__, cspec.completions))

            for op_name, provider_map in completions:
                app.set_completions(op_name, provider_map)

            # configure runs LAST — every op is already registered.
            if self._configure is not None:
                self._configure(app)
            else:
                app.build_commands()
            self._groups[name] = Group(name=name, sub=sub, aliases=list(aliases or []), app=app)
            return cls

        return _decorate

    # -- discovery ----------------------------------------------------------

    def iter_groups(self) -> List[Group]:
        """Every registered group, in registration order (CLI/MCP discovery)."""
        return list(self._groups.values())

    def mount(self, root: LiquifyApp) -> None:
        """Mount every registered group onto ``root`` as a sub-app (name + aliases)."""
        for group in self._groups.values():
            root.add_app(group.app, group.name, aliases=group.aliases)

    def get_app(self, name: str) -> LiquifyApp:
        """The built :class:`~liquifai.core.LiquifyApp` for a registered group."""
        return self._groups[name].app

    # -- internals ----------------------------------------------------------

    def _context(self, name: str, sub: str) -> PolicyContext:
        target_override = self._target
        if target_override is not None:
            resolve_target: Callable[[Any], Any] = lambda conn: target_override(conn, sub)  # noqa: E731
        else:
            resolve_target = lambda conn: default_target(conn, sub)  # noqa: E731
        return PolicyContext(
            group=name,
            sub=sub,
            conn_cls=self._conn_cls,
            adapters=self._adapters,
            target=resolve_target,
            shape_status=self._shape_status,
        )

    def _validate_spec(self, spec: ExposeSpec, group_name: str) -> None:
        """Fail at decoration time (not first call) on unknown adapter keys."""
        for p in spec.params:
            if p.adapt is not None and p.adapt not in self._adapters:
                raise CommandDefinitionError(
                    f"@expose({spec.sdk_method!r}) in group {group_name!r}: P({p.cli!r}) uses "
                    f"unknown adapter {p.adapt!r} (available: {sorted(self._adapters)}); "
                    f"pass it via SdkBridge(adapters=...)"
                )
