"""Tests for the unified @command(presentation=...) / @operation enrichment path
and the generic make_mcp_tools() builder."""

from typing import Any, Dict

from liquifai import LiquifyApp, make_mcp_tools

# ---------------------------------------------------------------------------
# @command(presentation=...) decorator
# ---------------------------------------------------------------------------


def test_command_with_presentation_goes_into_operations() -> None:
    app = LiquifyApp(name="dataset")

    @app.command("list", presentation="list", columns=("name",))
    def dataset_list(conn: Any) -> Dict[str, Any]:
        return {"items": []}

    # Must be in _operations, NOT in _commands (configure_app wires the CLI later).
    assert "dataset_list" in app._operations
    assert "list" not in app._commands


def test_command_with_presentation_metadata_stored() -> None:
    app = LiquifyApp(name="dataset")

    @app.command("info", presentation="fields", title="Dataset: {name}", columns=("name",))
    def dataset_info(conn: Any, *, name: str) -> Dict[str, Any]:
        return {}

    meta = getattr(dataset_info, "__liquifai_op_metadata__", {})
    assert meta["presentation"] == "fields"
    assert meta["cmd_name"] == "info"
    assert meta["title"] == "Dataset: {name}"


def test_command_without_presentation_still_goes_into_commands() -> None:
    app = LiquifyApp(name="auth")

    @app.command("token-info")
    def auth_token_info_cmd() -> None:
        pass

    assert "token-info" in app._commands
    assert "auth_token_info_cmd" not in app._operations


def test_command_presentation_derives_op_name_from_function_name() -> None:
    app = LiquifyApp(name="model")

    @app.command("list", presentation="list")
    def model_list(conn: Any) -> Dict[str, Any]:
        return {}

    # op_name comes from f.__name__
    assert "model_list" in app._operations


# ---------------------------------------------------------------------------
# @operation backward compat — cmd_name now in metadata
# ---------------------------------------------------------------------------


def test_operation_stores_cmd_name_in_metadata() -> None:
    app = LiquifyApp(name="dataset")

    @app.operation(presentation="list")
    def dataset_list(conn: Any) -> Dict[str, Any]:
        return {}

    meta = getattr(dataset_list, "__liquifai_op_metadata__", {})
    # group prefix "dataset_" is stripped → cmd_name = "list"
    assert meta["cmd_name"] == "list"


def test_operation_no_group_prefix() -> None:
    app = LiquifyApp(name="auth")

    @app.operation(presentation="status")
    def auth_clear_cache(conn: Any) -> Dict[str, Any]:
        return {}

    meta = getattr(auth_clear_cache, "__liquifai_op_metadata__", {})
    # "auth_" stripped → "clear-cache"
    assert meta["cmd_name"] == "clear-cache"


# ---------------------------------------------------------------------------
# LiquifyApp hooks
# ---------------------------------------------------------------------------


def test_set_context_factory() -> None:
    app = LiquifyApp(name="t")
    factory = lambda: "conn"  # noqa: E731
    result = app.set_context_factory(factory)
    assert app._context_factory is factory
    assert result is factory  # returns the function unchanged


def test_set_mcp_context_factory() -> None:
    app = LiquifyApp(name="t")
    factory = lambda server="PROD", dry_run=False: (server, dry_run)  # noqa: E731
    app.set_mcp_context_factory(factory)
    assert app._mcp_context_factory is factory


def test_set_presenter() -> None:
    app = LiquifyApp(name="t")
    calls: list = []

    def presenter(result: Any, presentation: str, **kwargs: Any) -> None:
        calls.append((result, presentation))

    app.set_presenter(presenter)
    assert app._presenter is presenter


# ---------------------------------------------------------------------------
# make_mcp_tools
# ---------------------------------------------------------------------------


def _mcp_factory(server: str = "PROD", dry_run: bool = False) -> Dict[str, Any]:
    return {"server": server, "dry_run": dry_run}


def _make_ops_app() -> LiquifyApp:
    """Minimal app with an MCP context factory and a couple of operations."""
    app = LiquifyApp(name="ops")
    app.set_mcp_context_factory(_mcp_factory)

    @app.operation(presentation="list")
    def ops_list(conn: Any) -> Dict[str, Any]:
        return {"server": conn["server"], "dry_run": conn["dry_run"]}

    @app.operation(presentation="fields")
    def ops_info(conn: Any, *, name: str) -> Dict[str, Any]:
        return {"name": name, "server": conn["server"]}

    return app


def test_make_mcp_tools_returns_one_per_operation() -> None:
    app = _make_ops_app()
    tools = make_mcp_tools(app)
    assert len(tools) == 2


def test_make_mcp_tools_names_match_operations() -> None:
    app = _make_ops_app()
    names = {t.__name__ for t in make_mcp_tools(app)}
    assert names == {"ops_list", "ops_info"}


def test_make_mcp_tools_factory_params_prepended() -> None:
    import inspect

    app = _make_ops_app()
    list_tool = next(t for t in make_mcp_tools(app) if t.__name__ == "ops_list")
    sig = inspect.signature(list_tool)
    param_names = list(sig.parameters)
    # factory params (server, dry_run) come before operation params
    assert param_names[:2] == ["server", "dry_run"]


def test_make_mcp_tools_required_op_params_stay_required() -> None:
    import inspect

    app = _make_ops_app()
    info_tool = next(t for t in make_mcp_tools(app) if t.__name__ == "ops_info")
    sig = inspect.signature(info_tool)
    # 'name' has no default in the operation, should remain required
    name_param = sig.parameters["name"]
    assert name_param.default is inspect.Parameter.empty


def test_make_mcp_tools_calls_factory_with_provided_params() -> None:
    app = _make_ops_app()
    list_tool = next(t for t in make_mcp_tools(app) if t.__name__ == "ops_list")
    result = list_tool(server="DEV", dry_run=True)
    assert result == {"server": "DEV", "dry_run": True}


def test_make_mcp_tools_factory_defaults_used() -> None:
    app = _make_ops_app()
    list_tool = next(t for t in make_mcp_tools(app) if t.__name__ == "ops_list")
    result = list_tool()  # no args — uses factory defaults
    assert result == {"server": "PROD", "dry_run": False}


def test_make_mcp_tools_exception_returns_error_dict() -> None:
    app = LiquifyApp(name="ops")
    app.set_mcp_context_factory(lambda: {})

    @app.operation(presentation="status")
    def ops_boom(conn: Any) -> Dict[str, Any]:
        raise ValueError("kaboom")

    (tool,) = make_mcp_tools(app)
    result = tool()
    assert result == {"error": "kaboom"}


def test_make_mcp_tools_no_factory() -> None:
    """Without a factory, tools call the operation with no conn arg."""
    app = LiquifyApp(name="ops")

    @app.operation(presentation="status")
    def ops_plain() -> Dict[str, Any]:  # no conn param
        return {"ok": True}

    # Patch _operations to use the no-conn function
    app._operations["ops_plain"] = ops_plain
    (tool,) = make_mcp_tools(app)
    result = tool()
    assert result == {"ok": True}


def test_make_mcp_tools_annotations_include_factory_and_op_params() -> None:
    app = _make_ops_app()
    info_tool = next(t for t in make_mcp_tools(app) if t.__name__ == "ops_info")
    assert "server" in info_tool.__annotations__
    assert "dry_run" in info_tool.__annotations__
    assert "name" in info_tool.__annotations__


# ---------------------------------------------------------------------------
# make_mcp_tools() — dict form (Concern 2)
# ---------------------------------------------------------------------------


def test_make_mcp_tools_dict_form_basic() -> None:
    """Dict + context_factory kwarg produces identical tools to the LiquifyApp form."""

    def my_op(conn: Any, *, name: str) -> Dict[str, Any]:
        return {"name": name, "server": conn}

    def factory(server: str = "PROD", dry_run: bool = False) -> str:
        return server

    tools = make_mcp_tools({"my_op": my_op}, context_factory=factory)
    assert len(tools) == 1
    assert tools[0].__name__ == "my_op"


def test_make_mcp_tools_dict_form_factory_params_prepended() -> None:
    import inspect

    def my_op(conn: Any, *, value: int = 0) -> Dict[str, Any]:
        return {}

    def factory(server: str = "PROD") -> str:
        return server

    (tool,) = make_mcp_tools({"my_op": my_op}, context_factory=factory)
    params = list(inspect.signature(tool).parameters)
    assert params[0] == "server"
    assert "value" in params


def test_make_mcp_tools_dict_form_calls_factory_and_op() -> None:
    calls: Dict[str, Any] = {}

    def my_op(conn: Any, *, tag: str = "x") -> Dict[str, Any]:
        calls["conn"] = conn
        calls["tag"] = tag
        return {"done": True}

    def factory(server: str = "PROD") -> str:
        return f"conn-{server}"

    (tool,) = make_mcp_tools({"my_op": my_op}, context_factory=factory)
    result = tool(server="DEV", tag="hello")
    assert result == {"done": True}
    assert calls["conn"] == "conn-DEV"
    assert calls["tag"] == "hello"


def test_make_mcp_tools_dict_form_no_factory() -> None:
    """Dict form without a factory calls the op with no conn arg."""

    def plain_op() -> Dict[str, Any]:
        return {"ok": True}

    (tool,) = make_mcp_tools({"plain_op": plain_op})
    assert tool() == {"ok": True}


# ---------------------------------------------------------------------------
# command(presentation=...) — Literal validation (Concern 6)
# ---------------------------------------------------------------------------


def test_command_invalid_presentation_raises() -> None:
    import pytest

    app = LiquifyApp("test")
    with pytest.raises(ValueError, match="presentation must be one of"):

        @app.command("foo", presentation="invalid")  # type: ignore[arg-type]
        def fn(conn: Any) -> Dict[str, Any]:
            return {}


def test_command_valid_presentations_all_accepted() -> None:
    app = LiquifyApp("test")
    for p in ("list", "fields", "status"):

        @app.command(f"cmd-{p}", presentation=p)
        def fn(conn: Any) -> Dict[str, Any]:
            return {}
