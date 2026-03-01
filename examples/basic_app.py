import typer

from liquify import LiquifyApp, LiquifyContext

app = LiquifyApp(name="basic-app")


@app.command()
def hello(name: str = "World") -> None:
    """A simple greeting command."""
    print(f"Hello {name}!")


@app.command()
def info(ctx: typer.Context) -> None:
    """Show information about the application context."""
    liquify_ctx: LiquifyContext = ctx.obj
    print(f"App Name: {liquify_ctx.name}")
    print(f"Debug Mode: {liquify_ctx.debug}")


if __name__ == "__main__":
    app.run()
