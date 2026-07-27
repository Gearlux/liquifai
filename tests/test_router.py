"""Tests for liquifai.router.route — the app-side adapter for the shared walk."""

from liquifai import router
from liquifai.core import LiquifyApp


def test_cli_router_basic() -> None:
    app = LiquifyApp("test_app")

    @app.command("run")
    def run_cmd() -> None:
        pass

    inv = router.route(app, ["run", "extra_arg"])

    assert inv.target_app is app
    assert inv.target_func is run_cmd
    assert [t.text for t in inv.remaining_tokens] == ["extra_arg"]


def test_cli_router_sub_app() -> None:
    root = LiquifyApp("root")
    sub = LiquifyApp("sub")

    @sub.command("action")
    def action_cmd() -> None:
        pass

    root.add_app(sub, "sub")

    inv = router.route(root, ["sub", "action"])

    assert inv.target_app is sub
    assert inv.target_func is action_cmd
