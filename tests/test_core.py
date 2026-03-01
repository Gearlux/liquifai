import typer
from typer.testing import CliRunner

from liquify import LiquifyApp, LiquifyContext

runner = CliRunner()


def test_app_initialization() -> None:
    app = LiquifyApp(name="test-app")

    @app.command()
    def hello(name: str = "World") -> None:
        print(f"Hello {name}")

    result = runner.invoke(app.typer_app, ["hello"])
    assert result.exit_code == 0
    assert "Hello World" in result.stdout


def test_global_context_extraction() -> None:
    app = LiquifyApp(name="test-app")
    captured_context = None

    @app.command()
    def check(ctx: typer.Context) -> None:
        nonlocal captured_context
        captured_context = ctx.obj

    # Run with global flags
    result = runner.invoke(app.typer_app, ["--config", "test.yaml", "--scope", "debug", "--debug", "check"])

    assert result.exit_code == 0
    assert captured_context is not None
    assert isinstance(captured_context, LiquifyContext)
    assert captured_context.config_path is not None
    assert captured_context.config_path.name == "test.yaml"
    assert "debug" in captured_context.scopes
    assert captured_context.debug is True
