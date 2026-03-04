import confluid
import logflow
import typer

from liquify import LiquifyApp, LiquifyContext

# --- 1. Define Configurable Components ---


@confluid.configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1):
        self.layers = layers
        self.dropout = dropout
        self._secret = "hidden"

    @property
    @confluid.ignore_config
    def secret(self) -> str:
        """This attribute will be hidden from the --show-config table."""
        return self._secret

    def __repr__(self) -> str:
        return f"Model(layers={self.layers}, dropout={self.dropout})"


@confluid.configurable
class Trainer:
    def __init__(self, model: Model, epochs: int = 5):
        self.model = model
        self.epochs = epochs

    def __repr__(self) -> str:
        return f"Trainer(epochs={self.epochs}, model={self.model})"


# --- 2. Initialize Liquify Application ---

app = LiquifyApp(name="liquify-demo")


@app.command(default=True)
def train(trainer: Trainer, name: str = "Experiment") -> None:
    """
    Run a simulated training experiment with automatic injection.
    """
    # 1. Access the logger (automatically initialized by Liquify via LogFlow)
    logger = logflow.get_logger("train")

    logger.info(f"Starting {name}...")
    logger.debug(f"Configuration received: {trainer}")

    # 2. Simulate training
    for epoch in range(trainer.epochs):
        logger.info(f"Epoch {epoch + 1}/{trainer.epochs} | Layers: {trainer.model.layers}")

    logger.success(f"{name} completed successfully!")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the current configuration status."""
    liquify_ctx: LiquifyContext = ctx.obj
    print(f"App: {liquify_ctx.name}")
    print(f"Config File: {liquify_ctx.config_path}")
    print(f"Active Scopes: {liquify_ctx.scopes}")


if __name__ == "__main__":
    app.run()
