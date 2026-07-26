"""Tests for liquifai.router.CliRouter."""

from liquifai.core import LiquifyApp
from liquifai.router import CliRouter


def test_cli_router_basic() -> None:
    app = LiquifyApp("test_app")

    @app.command("run")
    def run_cmd() -> None:
        pass

    router = CliRouter(app)
    inv = router.route(["run", "extra_arg"])

    assert inv.target_app is app
    assert inv.cmd_name == "run"
    assert inv.target_func is run_cmd
    assert inv.remaining_argv == ["extra_arg"]


def test_cli_router_sub_app() -> None:
    root = LiquifyApp("root")
    sub = LiquifyApp("sub")

    @sub.command("action")
    def action_cmd() -> None:
        pass

    root.add_app(sub, "sub")

    router = CliRouter(root)
    inv = router.route(["sub", "action"])

    assert inv.target_app is sub
    assert inv.cmd_name == "action"
    assert inv.target_func is action_cmd
