"""Tests for liquifai.bridge.policies — signature synthesis, call resolution,
status shaping, and the built-in call/items policies."""

import inspect
from typing import Any, Dict

import pytest
from bridge_fakes import FakeClient, FakeConn, FakeSub

from liquifai.bridge.policies import (
    CallPolicy,
    ItemsPolicy,
    PolicyContext,
    build_op_signature,
    default_target,
    resolve_call,
    resolve_policy,
    shape_status,
)
from liquifai.bridge.shaping import DEFAULT_ADAPTERS
from liquifai.bridge.spec import ExposeSpec, P


def _ctx(sub: str = "widget", **overrides: Any) -> PolicyContext:
    kwargs: Dict[str, Any] = dict(
        group="widget",
        sub=sub,
        conn_cls=FakeConn,
        adapters=DEFAULT_ADAPTERS,
        target=lambda conn: default_target(conn, sub),
        shape_status=shape_status,
    )
    kwargs.update(overrides)
    return PolicyContext(**kwargs)


def _conn(sub: str = "widget", results: Any = None, dry_run: bool = False) -> FakeConn:
    return FakeConn(client=FakeClient(**{sub: FakeSub(results or {})}), dry_run=dry_run)


# ---------------------------------------------------------------------------
# resolve_policy — explicit policy wins, else routed by presentation
# ---------------------------------------------------------------------------


def test_resolve_policy_auto_routes_on_presentation() -> None:
    assert resolve_policy(ExposeSpec(verb="list", presentation="list")) == "list"
    assert resolve_policy(ExposeSpec(verb="info", presentation="fields")) == "call"
    assert resolve_policy(ExposeSpec(verb="delete", presentation="status")) == "call"


def test_resolve_policy_explicit_wins_over_presentation() -> None:
    spec = ExposeSpec(verb="versions", presentation="list", policy="items")
    assert resolve_policy(spec) == "items"


# ---------------------------------------------------------------------------
# build_op_signature
# ---------------------------------------------------------------------------


def test_build_op_signature_conn_first_annotated_with_conn_cls() -> None:
    sig = build_op_signature([P("name")], FakeConn)
    params = list(sig.parameters.values())
    assert params[0].name == "conn"
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[0].annotation is FakeConn


def test_build_op_signature_required_params_keyword_only_no_default() -> None:
    sig = build_op_signature([P("name")], FakeConn)
    name = sig.parameters["name"]
    assert name.kind is inspect.Parameter.KEYWORD_ONLY
    assert name.default is inspect.Parameter.empty
    assert name.annotation is str


def test_build_op_signature_optional_params_carry_default_and_type() -> None:
    sig = build_op_signature([P("limit", default=0), P("tag", default=None)], FakeConn)
    assert sig.parameters["limit"].default == 0
    assert sig.parameters["limit"].annotation is int
    assert sig.parameters["tag"].default is None
    assert sig.parameters["tag"].annotation is str  # None default falls back to str
    assert sig.return_annotation == Dict[str, Any]


# ---------------------------------------------------------------------------
# resolve_call
# ---------------------------------------------------------------------------


def test_resolve_call_applies_adapters_and_renames() -> None:
    spec = ExposeSpec(verb="create", params=[P("tags", sdk="tag_list", adapt="list")])
    args, kwargs = resolve_call(spec, {"tags": "a,b"}, DEFAULT_ADAPTERS)
    assert args == []
    assert kwargs == {"tag_list": ["a", "b"]}


def test_resolve_call_missing_required_raises() -> None:
    spec = ExposeSpec(verb="info", params=[P("name")])
    with pytest.raises(ValueError, match="name"):
        resolve_call(spec, {}, DEFAULT_ADAPTERS)


def test_resolve_call_validate_flag_required_at_runtime() -> None:
    spec = ExposeSpec(verb="set", params=[P("value", default="", validate=True)])
    with pytest.raises(ValueError, match="value"):
        resolve_call(spec, {"value": ""}, DEFAULT_ADAPTERS)


def test_resolve_call_omit_empty_drops_optional() -> None:
    spec = ExposeSpec(verb="list", params=[P("status", default="")])
    _, kwargs = resolve_call(spec, {"status": ""}, DEFAULT_ADAPTERS)
    assert kwargs == {}


def test_resolve_call_omit_empty_false_forwards_empty() -> None:
    spec = ExposeSpec(verb="list", params=[P("status", default="", omit_empty=False)])
    _, kwargs = resolve_call(spec, {"status": ""}, DEFAULT_ADAPTERS)
    assert kwargs == {"status": ""}


def test_resolve_call_client_side_only_param_skipped() -> None:
    spec = ExposeSpec(verb="download", params=[P("name"), P("versioned", sdk=None, default=False)])
    _, kwargs = resolve_call(spec, {"name": "x", "versioned": True}, DEFAULT_ADAPTERS)
    assert kwargs == {"name": "x"}


def test_resolve_call_send_positional() -> None:
    spec = ExposeSpec(verb="info", params=[P("name", send_positional=True)])
    args, kwargs = resolve_call(spec, {"name": "x"}, DEFAULT_ADAPTERS)
    assert args == ["x"]
    assert kwargs == {}


def test_resolve_call_constants_sent_last() -> None:
    spec = ExposeSpec(verb="upload", params=[P("name")], constants={"show_progress": True})
    _, kwargs = resolve_call(spec, {"name": "x"}, DEFAULT_ADAPTERS)
    assert kwargs == {"name": "x", "show_progress": True}


# ---------------------------------------------------------------------------
# shape_status
# ---------------------------------------------------------------------------


def test_shape_status_word_and_echo() -> None:
    spec = ExposeSpec(verb="delete", status_word="deleted", status_echo=("name",))
    out = shape_status(spec, None, {"name": "x"})
    assert out == {"status": "deleted", "name": "x"}


def test_shape_status_result_key_with_field() -> None:
    class R:
        id = "abc"

    spec = ExposeSpec(verb="create", status_word="created", result_key="widget_id", result_field="id")
    assert shape_status(spec, R(), {}) == {"status": "created", "widget_id": "abc"}


def test_shape_status_result_key_without_field_stringifies() -> None:
    spec = ExposeSpec(verb="create", status_word="created", result_key="raw")
    assert shape_status(spec, 42, {}) == {"status": "created", "raw": "42"}


def test_shape_status_result_full_and_spread() -> None:
    spec = ExposeSpec(verb="finish", status_word="done", result_full=True)
    out = shape_status(spec, {"a": 1}, {})
    assert out == {"status": "done", "result": {"a": 1}}

    spec2 = ExposeSpec(verb="finish", status_word="done", spread_result=True)
    assert shape_status(spec2, {"a": 1}, {}) == {"status": "done", "a": 1}


# ---------------------------------------------------------------------------
# default_target
# ---------------------------------------------------------------------------


def test_default_target_sub_attribute_and_bare_client() -> None:
    conn = _conn("widget")
    assert default_target(conn, "widget") is conn.client.widget
    assert default_target(conn, "") is conn.client


# ---------------------------------------------------------------------------
# CallPolicy
# ---------------------------------------------------------------------------


def test_call_policy_dry_run_never_touches_client() -> None:
    spec = ExposeSpec(verb="info", presentation="fields", sdk_method="get_widget", params=[P("name")])
    op = CallPolicy().build(spec, _ctx())
    conn = _conn(dry_run=True)
    result = op(conn, name="x")
    assert result["dry_run"] is True
    assert result["command"] == "widget"
    assert result["action"] == "info"
    assert "client.widget.get_widget" in result["call"]
    assert conn.get_client_calls == 0  # dry-run checked BEFORE get_client()


def test_call_policy_fields_returns_full_record() -> None:
    spec = ExposeSpec(verb="info", presentation="fields", sdk_method="get_widget", params=[P("name")])
    op = CallPolicy().build(spec, _ctx())
    conn = _conn(results={"get_widget": {"name": "x", "size": 3}})
    assert op(conn, name="x") == {"name": "x", "size": 3}
    assert conn.client.widget.last_call() == ("get_widget", (), {"name": "x"})


def test_call_policy_nullable_none_result() -> None:
    spec = ExposeSpec(verb="info", presentation="fields", sdk_method="get_widget", params=[P("name")], nullable=True)
    op = CallPolicy().build(spec, _ctx())
    assert op(_conn(), name="x") == {"found": False, "name": "x"}


def test_call_policy_nullable_found_result() -> None:
    spec = ExposeSpec(verb="info", presentation="fields", sdk_method="get_widget", params=[P("name")], nullable=True)
    op = CallPolicy().build(spec, _ctx())
    conn = _conn(results={"get_widget": {"name": "x"}})
    assert op(conn, name="x") == {"found": True, "name": "x"}


def test_call_policy_status_uses_ctx_shaper() -> None:
    spec = ExposeSpec(verb="delete", presentation="status", sdk_method="delete_widget", params=[P("name")])

    def custom_shaper(s: ExposeSpec, result: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {"custom": s.verb}

    op = CallPolicy().build(spec, _ctx(shape_status=custom_shaper))
    assert op(_conn(), name="x") == {"custom": "delete"}


def test_call_policy_signature_matches_contract() -> None:
    spec = ExposeSpec(verb="info", presentation="fields", sdk_method="get_widget", params=[P("name")])
    op = CallPolicy().build(spec, _ctx())
    sig = inspect.signature(op)
    assert list(sig.parameters) == ["conn", "name"]
    assert sig.parameters["conn"].annotation is FakeConn


# ---------------------------------------------------------------------------
# ItemsPolicy
# ---------------------------------------------------------------------------


def test_items_policy_wraps_records() -> None:
    class Page:
        records = [{"v": "1.0"}, {"v": "2.0"}]

    spec = ExposeSpec(
        verb="versions", presentation="list", policy="items", sdk_method="get_widget_versions", params=[P("name")]
    )
    op = ItemsPolicy().build(spec, _ctx())
    conn = _conn(results={"get_widget_versions": Page()})
    assert op(conn, name="x") == {"items": [{"v": "1.0"}, {"v": "2.0"}], "count": 2}


def test_items_policy_custom_result_attr() -> None:
    class Page:
        rows = [{"v": "1.0"}]

    spec = ExposeSpec(
        verb="versions",
        presentation="list",
        policy="items",
        sdk_method="get_widget_versions",
        params=[P("name")],
        result_attr="rows",
    )
    op = ItemsPolicy().build(spec, _ctx())
    conn = _conn(results={"get_widget_versions": Page()})
    assert op(conn, name="x")["count"] == 1


def test_items_policy_dry_run() -> None:
    spec = ExposeSpec(
        verb="versions", presentation="list", policy="items", sdk_method="get_widget_versions", params=[P("name")]
    )
    op = ItemsPolicy().build(spec, _ctx())
    conn = _conn(dry_run=True)
    assert op(conn, name="x")["dry_run"] is True
    assert conn.get_client_calls == 0
