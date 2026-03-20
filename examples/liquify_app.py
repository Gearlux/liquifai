import confluid
import logflow

from liquify import LiquifyApp, get_context

# --- 1. Define Configurable Components ---


@confluid.configurable
class Model:
    def __init__(self, layers: int = 3, dropout: float = 0.1) -> None:
        """
        A simple neural network model.

        Args:
            layers: Number of hidden layers in the network.
            dropout: Dropout probability for regularization.
        """
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
    def __init__(self, model: Model, epochs: int = 5) -> None:
        """
        Handles the training loop for a model.

        Args:
            model: The neural network model to train.
            epochs: Total number of training passes over the dataset.
        """
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
def status() -> None:
    """Show the current configuration status."""
    ctx = get_context()
    if ctx:
        print(f"App: {ctx.name}")
        print(f"Config File: {ctx.config_path}")
        print(f"Active Scopes: {ctx.scopes}")


if __name__ == "__main__":
    app.run()
