import sys
from pathlib import Path

from confluid import configurable

from liquifai import LiquifyApp


@configurable
class DataLoader:
    def __init__(self, name: str, batch_size: int = 16, shuffle: bool = False):
        """
        A generic data loader.

        Args:
            name: The name of this loader (e.g. 'train', 'test').
            batch_size: Number of samples per batch.
            shuffle: Whether to shuffle the data every epoch.
        """
        self.name = name
        self.batch_size = batch_size
        self.shuffle = shuffle


@configurable
class Trainer:
    def __init__(self, train: DataLoader, test: DataLoader, epochs: int = 10):
        """
        A model trainer that coordinates training and testing.

        Args:
            train: The loader used for training.
            test: The loader used for evaluation.
            epochs: Total number of training epochs.
        """
        self.train = train
        self.test = test
        self.epochs = epochs


app = LiquifyApp(name="broadcast-demo")


@app.command(default=True)
def run(trainer: Trainer) -> None:
    """
    Run a training session demonstrating parameter broadcasting.

    Usage Examples:
      1. Default values:
         python broadcast_demo.py

      2. Broadcast 'batch_size' to BOTH loaders:
         python broadcast_demo.py --batch_size 64

      3. Specific override for one loader:
         python broadcast_demo.py --train.batch_size 32 --test.batch_size 128

      4. Broadcast and specific mixed:
         python broadcast_demo.py --batch_size 64 --test.shuffle
    """
    print(f"Trainer Configuration (Epochs: {trainer.epochs})")
    print(f"  [Train] batch_size: {trainer.train.batch_size}, shuffle: {trainer.train.shuffle}")
    print(f"  [Test]  batch_size: {trainer.test.batch_size}, shuffle: {trainer.test.shuffle}")


if __name__ == "__main__":
    # Auto-load the companion config if not provided
    if "--config" not in sys.argv and "-c" not in sys.argv:
        default_yaml = Path(__file__).parent / "broadcast_demo.yaml"
        if default_yaml.exists():
            sys.argv.extend(["--config", str(default_yaml)])

    app.run()
