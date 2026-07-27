"""liquifai.bridge — declarative SDK-to-operations bridge (PROVISIONAL API).

Turn a Python SDK client's methods into liquifai operations — and therefore
CLI commands and MCP tools — by decorating a subclass of the SDK class instead
of hand-writing one wrapper per action::

    from liquifai.bridge import P, SdkBridge, custom, expose

    bridge = SdkBridge(conn_cls=MyConn)          # your @configurable connection

    @bridge.group(name="widget", sub="widget", aliases=["w"])
    class WidgetClient(sdk.WidgetClient):        # inherits the real SDK class

        @expose(verb="info", presentation="fields", params=[P("name")])
        def get_widget(self) -> None: ...        # method name = the SDK method

        @custom(verb="export", presentation="status")
        def widget_export(conn, *, name: str) -> dict: ...   # escape hatch

    bridge.mount(root_app)                       # CLI sub-app `widget` / `w`

The bridge stays SDK-agnostic: connection specifics live in your ``conn_cls``
(see :class:`BridgeConnection`), string parsing in the adapter table, and any
SDK-dialect surface (e.g. a paginated ``list`` with platform-specific
search/sort/page vocabulary) in a consumer-registered :class:`OpPolicy`.
Consumer-dialect code never lands in liquifai itself.

.. warning:: **Provisional API — excluded from liquifai's version contract.**
   This subpackage has a single production consumer so far; its names and spec
   vocabulary may change in 0.x minor releases WITHOUT a deprecation cycle,
   and it may move to its own distribution. It is deliberately not re-exported
   from the top-level ``liquifai`` package, so ``import liquifai`` never
   reaches it.

   Depend on it explicitly — ``liquifai[bridge]`` — so that dependency is
   visible in YOUR metadata rather than implied by the core requirement.
   :data:`__provisional__` is the machine-readable form of this notice.

NOTE: no ``from __future__ import annotations`` anywhere in this subpackage —
liquifai/confluid/FastMCP introspect live annotation objects on the
synthesized operation signatures.
"""

from liquifai.bridge.group import BRIDGED_ATTR, Group, SdkBridge
from liquifai.bridge.policies import (
    BUILTIN_POLICIES,
    BridgeConnection,
    CallPolicy,
    ItemsPolicy,
    OpPolicy,
    PolicyContext,
    build_op_signature,
    default_target,
    resolve_call,
    resolve_policy,
    shape_status,
)
from liquifai.bridge.shaping import (
    DEFAULT_ADAPTERS,
    attr_str,
    coerce_scalar,
    dry_descriptor,
    filter_records,
    format_call,
    items_result,
    jsonify,
    parse_csv,
    parse_kv,
    parse_tags,
    record_to_dict,
    records_of,
    require,
)
from liquifai.bridge.spec import CustomSpec, ExposeSpec, P, custom, expose

#: Machine-readable stability marker: this subpackage sits OUTSIDE liquifai's
#: version contract (see the module warning). Tooling that audits a codebase
#: for provisional dependencies can assert on it.
__provisional__ = True

#: The optional-dependency name a consumer should declare — ``liquifai[bridge]``.
__extra__ = "bridge"

__all__ = [
    "__provisional__",
    "__extra__",
    # group / registration
    "SdkBridge",
    "Group",
    "BRIDGED_ATTR",
    # specs
    "P",
    "ExposeSpec",
    "CustomSpec",
    "expose",
    "custom",
    # policy layer
    "BridgeConnection",
    "OpPolicy",
    "PolicyContext",
    "CallPolicy",
    "ItemsPolicy",
    "BUILTIN_POLICIES",
    "resolve_policy",
    "default_target",
    "build_op_signature",
    "resolve_call",
    "shape_status",
    # shaping helpers
    "DEFAULT_ADAPTERS",
    "dry_descriptor",
    "format_call",
    "jsonify",
    "record_to_dict",
    "records_of",
    "items_result",
    "attr_str",
    "filter_records",
    "require",
    "parse_csv",
    "parse_kv",
    "parse_tags",
    "coerce_scalar",
]
