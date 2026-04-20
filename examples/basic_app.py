from liquifai import LiquifyApp, get_context

app = LiquifyApp(name="basic-app")


@app.command()
def hello(name: str = "World") -> None:
    """A simple greeting command."""
    print(f"Hello {name}!")


@app.command()
def info() -> None:
    """Show information about the application context."""
    ctx = get_context()
    if ctx:
        print(f"App Name: {ctx.name}")
        print(f"Debug Mode: {ctx.debug}")


if __name__ == "__main__":
    app.run()
