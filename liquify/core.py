from pathlib import Path
from typing import Any, Callable, List, Optional

import typer
from rich.console import Console

from liquify.context import LiquifyContext

# Shared rich console for beautiful CLI output
console = Console()


class LiquifyApp:
    """The main entry point for a Liquify-based application."""

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.typer_app = typer.Typer(name=name, **kwargs)
        self.context: Optional[LiquifyContext] = None

        # Define the global callback for common options
        self.typer_app.callback()(self._global_callback)

    def _global_callback(
        self,
        ctx: typer.Context,
        config: Optional[Path] = typer.Option(
            None, "--config", "-c", help="Path to the configuration YAML file.", show_default=False
        ),
        scope: List[str] = typer.Option(
            [], "--scope", "-s", help="Configuration scopes to activate.", show_default=False
        ),
        debug: bool = typer.Option(False, "--debug", "-d", help="Enable ultra-detailed debugging output."),
    ) -> None:
        """Global callback to initialize the Liquify context."""
        self.context = LiquifyContext(name=self.name, config_path=config, scopes=scope, debug=debug)
        # Store context in Typer's internal context for access in commands
        ctx.obj = self.context

    def command(self, *args: Any, **kwargs: Any) -> Callable[[Any], Any]:
        """Decorator to register a command with the application."""
        return self.typer_app.command(*args, **kwargs)

    def run(self) -> Any:
        """Execute the Typer application."""
        return self.typer_app()
