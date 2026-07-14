"""Tests for liquifai.bridge.group — SdkBridge end-to-end with a fake SDK."""

import inspect
from typing import Any, Callable, Dict, List

import pytest
from bridge_fakes import BaseWidgetClient, FakeClient, FakeConn, FakeSub

from liquifai import LiquifyApp, make_mcp_tools
from liquifai.bridge import BRIDGED_ATTR, ExposeSpec, P, PolicyContext, SdkBridge, custom, expose
from liquifai.bridge.policies import build_op_signature
from liquifai.bridge.shaping import dry_descriptor
from liquifai.exceptions import CommandDefinitionError


class FakeListPolicy:
    """A consumer-dialect list policy — proves the policy seam + options passthrough."""

    def __init__(self) -> None:
        self.seen_options: List[Dict[str, Any]] = []

    def build(self, spec: ExposeSpec, ctx: PolicyContext) -> Callable[..., Dict[str, Any]]:
        self.seen_options.append(dict(spec.options))
        method = spec.sdk_method

        def op(conn: Any, **kwargs: Any) -> Dict[str, Any]:
            if conn.dry_run:
                return dry_descriptor(ctx.group, spec.verb, ctx.call_label(method) + "()")
            records = getattr(ctx.target(conn), method)()
            return {"items": list(records or []), "count": len(records or [])}

        op.__signature__ = build_op_signature(spec.params, ctx.conn_cls)  # type: ignore[attr-defined]
        return op


def _bridge(**kwargs: Any) -> SdkBridge:
    kwargs.setdefault("conn_cls", FakeConn)
    kwargs.setdefault("policies", {"list": FakeListPolicy()})
    return SdkBridge(**kwargs)


def _decorate_widget_group(bridge: SdkBridge) -> LiquifyApp:
    @bridge.group(name="widget", sub="widget", aliases=["w"])
    class WidgetClient(BaseWidgetClient):
        @expose(verb="list", presentation="list", columns=(("name", "Name"),), client_filter="name")
        def list_widgets(self) -> None: ...

        @expose(verb="info", presentation="fields", params=[P("name")])
        def get_widget(self) -> None: ...

        @expose(verb="delete", presentation="status", params=[P("name")], status_word="deleted", status_echo=("name",))
        def delete_widget(self) -> None: ...

        @custom(verb="export", presentation="status")
        def widget_export(conn: Any, *, name: str) -> Dict[str, Any]:  # noqa: N805 - conn-first by contract
            """Export one widget somewhere custom."""
            return {"status": "exported", "name": name, "dry": conn.dry_run}

    return bridge.get_app("widget")


# ---------------------------------------------------------------------------
# registration + naming + docs
# ---------------------------------------------------------------------------


def test_group_registers_ops_with_group_prefix() -> None:
    app = _decorate_widget_group(_bridge())
    assert set(app._operations) == {"widget_list", "widget_info", "widget_delete", "widget_export"}


def test_group_cli_verbs_strip_group_prefix() -> None:
    app = _decorate_widget_group(_bridge())
    # default configure ran build_commands() -> CLI verbs registered
    assert {"list", "info", "delete", "export"} <= set(app._commands)


def test_expose_op_inherits_sdk_docstring() -> None:
    app = _decorate_widget_group(_bridge())
    assert app._operations["widget_info"].__doc__ == "Get one widget by name."


def test_expose_doc_override_wins() -> None:
    bridge = _bridge()

    @bridge.group(name="gadget", sub="gadget")
    class GadgetClient(BaseWidgetClient):
        @expose(verb="info", presentation="fields", params=[P("name")], doc="Custom doc.")
        def get_widget(self) -> None: ...

    assert bridge.get_app("gadget")._operations["gadget_info"].__doc__ == "Custom doc."


def test_custom_op_keeps_its_own_docstring_and_body() -> None:
    app = _decorate_widget_group(_bridge())
    op = app._operations["widget_export"]
    assert op.__doc__ == "Export one widget somewhere custom."
    assert op(FakeConn(dry_run=True), name="x") == {"status": "exported", "name": "x", "dry": True}


def test_bridged_marker_on_expose_ops_only() -> None:
    app = _decorate_widget_group(_bridge())
    assert getattr(app._operations["widget_info"], BRIDGED_ATTR, False) is True
    assert getattr(app._operations["widget_export"], BRIDGED_ATTR, False) is False


def test_default_titles() -> None:
    app = _decorate_widget_group(_bridge())
    meta = lambda op: getattr(app._operations[op], "__liquifai_op_metadata__", {})  # noqa: E731
    assert meta("widget_list")["title"] == "Widgets"  # list policy -> plural
    assert meta("widget_info")["title"] == "Widget: {name}"  # first required param
    assert meta("widget_export")["title"] == "Widget: result"  # custom default


# ---------------------------------------------------------------------------
# policy seam
# ---------------------------------------------------------------------------


def test_consumer_policy_receives_options() -> None:
    policy = FakeListPolicy()
    _decorate_widget_group(_bridge(policies={"list": policy}))
    assert policy.seen_options == [{"client_filter": "name"}]


def test_unregistered_policy_raises_at_decoration() -> None:
    bridge = SdkBridge(conn_cls=FakeConn)  # no "list" policy registered
    with pytest.raises(CommandDefinitionError, match="routes to policy 'list'"):

        @bridge.group(name="widget", sub="widget")
        class WidgetClient(BaseWidgetClient):
            @expose(verb="list", presentation="list")
            def list_widgets(self) -> None: ...


def test_unknown_adapter_raises_at_decoration() -> None:
    bridge = _bridge()
    with pytest.raises(CommandDefinitionError, match="unknown adapter 'bogus'"):

        @bridge.group(name="widget", sub="widget")
        class WidgetClient(BaseWidgetClient):
            @expose(verb="info", presentation="fields", params=[P("name", adapt="bogus")])
            def get_widget(self) -> None: ...


def test_consumer_adapter_merges_over_defaults() -> None:
    upper = lambda s: str(s).upper()  # noqa: E731
    bridge = _bridge(adapters={"upper": upper, "str": upper})  # extend AND override

    @bridge.group(name="widget", sub="widget")
    class WidgetClient(BaseWidgetClient):
        @expose(
            verb="info", presentation="fields", params=[P("name", adapt="upper"), P("tag", default="", adapt="str")]
        )
        def get_widget(self) -> None: ...

    conn = FakeConn(client=FakeClient(widget=FakeSub({"get_widget": {}})))
    bridge.get_app("widget")._operations["widget_info"](conn, name="abc", tag="x")
    assert conn.client.widget.last_call() == ("get_widget", (), {"name": "ABC", "tag": "X"})


# ---------------------------------------------------------------------------
# hooks: configure / target / shape_status
# ---------------------------------------------------------------------------


def test_configure_hook_runs_last_with_all_ops_registered() -> None:
    seen: List[Any] = []

    def configure(app: LiquifyApp) -> None:
        seen.append(set(app._operations))
        app.build_commands()

    _decorate_widget_group(_bridge(configure=configure))
    assert seen == [{"widget_list", "widget_info", "widget_delete", "widget_export"}]


def test_target_override_hook() -> None:
    sub = FakeSub({"get_widget": {"name": "x"}})

    def factory_target(conn: Any, sub_name: str) -> Any:
        return conn.get_client().resource(sub_name)

    class FactoryClient:
        def resource(self, name: str) -> FakeSub:
            assert name == "widget"
            return sub

    bridge = _bridge(target=factory_target)

    @bridge.group(name="widget", sub="widget")
    class WidgetClient(BaseWidgetClient):
        @expose(verb="info", presentation="fields", params=[P("name")])
        def get_widget(self) -> None: ...

    conn = FakeConn(client=FactoryClient())  # type: ignore[arg-type]
    assert bridge.get_app("widget")._operations["widget_info"](conn, name="x") == {"name": "x"}


def test_shape_status_override_hook() -> None:
    def envelope_shaper(spec: ExposeSpec, result: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "verb": spec.verb}

    bridge = _bridge(shape_status=envelope_shaper)
    app = _decorate_widget_group(bridge)
    conn = FakeConn(client=FakeClient(widget=FakeSub()))
    assert app._operations["widget_delete"](conn, name="x") == {"ok": True, "verb": "delete"}


def test_conn_cls_without_get_client_warns_but_constructs() -> None:
    class NoClient:
        dry_run = True

    SdkBridge(conn_cls=NoClient)  # logs a warning; must not raise


# ---------------------------------------------------------------------------
# per-instance registry + discovery
# ---------------------------------------------------------------------------


def test_two_bridges_do_not_collide() -> None:
    b1, b2 = _bridge(), _bridge()
    _decorate_widget_group(b1)
    _decorate_widget_group(b2)
    assert b1.get_app("widget") is not b2.get_app("widget")
    assert len(b1.iter_groups()) == len(b2.iter_groups()) == 1


def test_mount_registers_sub_apps_with_aliases() -> None:
    bridge = _bridge()
    _decorate_widget_group(bridge)
    root = LiquifyApp(name="root")
    bridge.mount(root)
    assert root._sub_apps["widget"] is bridge.get_app("widget")
    assert root._sub_apps["w"] is bridge.get_app("widget")
    assert root._sub_app_aliases["w"] == "widget"


def test_completions_wired_from_expose() -> None:
    provider = lambda: ["a"]  # noqa: E731
    bridge = _bridge()

    @bridge.group(name="widget", sub="widget")
    class WidgetClient(BaseWidgetClient):
        @expose(verb="info", presentation="fields", params=[P("name")], completions={"name": provider})
        def get_widget(self) -> None: ...

    op = bridge.get_app("widget")._operations["widget_info"]
    assert getattr(op, "__liquifai_op_metadata__", {})["completions"] == {"name": provider}


# ---------------------------------------------------------------------------
# integration: bridged group -> CLI handler + MCP tools
# ---------------------------------------------------------------------------


def test_integration_cli_handler_and_mcp_tools() -> None:
    presented: List[Any] = []
    conn = FakeConn(client=FakeClient(widget=FakeSub({"get_widget": {"name": "x", "size": 3}})))

    def configure(app: LiquifyApp) -> None:
        app.set_context_factory(lambda: conn)
        app.set_mcp_context_factory(lambda dry_run=False: FakeConn(dry_run=dry_run) if dry_run else conn)
        app.set_presenter(lambda result, presentation, **kw: presented.append((result, presentation)))
        app.build_commands()

    bridge = _bridge(configure=configure)
    app = _decorate_widget_group(bridge)

    # CLI path: the generated handler injects conn and presents the result.
    handler = app._commands["info"]
    assert [p.name for p in inspect.signature(handler).parameters.values()] == ["name"]
    handler(name="x")
    assert presented == [({"name": "x", "size": 3}, "fields")]

    # MCP path: the same operation surfaces as a tool with the factory param prepended.
    tool = next(t for t in make_mcp_tools(app) if t.__name__ == "widget_info")
    assert list(inspect.signature(tool).parameters) == ["dry_run", "name"]
    assert tool(name="x") == {"name": "x", "size": 3}
    dry = tool(dry_run=True, name="x")
    assert dry["dry_run"] is True
