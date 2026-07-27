import sys
from pathlib import Path

from confluid import NoBroadcast, configurable

from liquifai import LiquifyApp


@configurable
class DataLoader:
    def __init__(self, name: str, batch_size: int = 16, shuffle: bool = False):
        """
        A generic data loader.

        Args:
            name: The name of this loader (e.g. 'train', 'test').
            batch_size: Number of records per batch.
            shuffle: Whether to shuffle the data every epoch.
        """
        self.name = name
        self.batch_size = batch_size
        self.shuffle = shuffle


@configurable(broadcast=False)
class Checkpointer:
    def __init__(self, path: str = "ckpt", batch_size: int = 1):
        """
        A sink that opts OUT of bare-key broadcasting entirely.

        Its `batch_size` deliberately shares a name with the loaders' knob: a
        bare `--batch_size 64` must NOT reach it (the class declared
        `broadcast=False`), while an addressed `--checkpointer.batch_size 4`
        still does. A CLI override obeys the same opt-outs a bare YAML key
        does, because liquifai asks confluid the settability question instead
        of re-deriving it.

        Args:
            path: Where checkpoints are written.
            batch_size: Same name as the loaders' knob — must stay untouched.
        """
        self.path = path
        self.batch_size = batch_size


@configurable
class Trainer:
    def __init__(
        self,
        train: DataLoader,
        test: DataLoader,
        checkpointer: Checkpointer,
        epochs: int = 10,
        run_id: NoBroadcast[str] = "local",
    ):
        """
        A model trainer that coordinates training and testing.

        Args:
            train: The loader used for training.
            test: The loader used for evaluation.
            checkpointer: The broadcast-opted-out checkpoint sink.
            epochs: Total number of training epochs.
            run_id: Identity label — too generic to accept a broadcast key, so
                it is marked `NoBroadcast[str]`. A bare `--run_id x` skips it;
                `--trainer.run_id x` still sets it.
        """
        self.train = train
        self.test = test
        self.checkpointer = checkpointer
        self.epochs = epochs
        self.run_id = run_id


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

      5. A bare broadcast STOPS at a declared opt-out; an addressed key
         still gets through:
         python broadcast_demo.py --batch_size 64          # checkpointer keeps 1
         python broadcast_demo.py --checkpointer.batch_size 4
    """
    print(f"Trainer Configuration (Epochs: {trainer.epochs}, run_id: {trainer.run_id})")
    print(f"  [Train] batch_size: {trainer.train.batch_size}, shuffle: {trainer.train.shuffle}")
    print(f"  [Test]  batch_size: {trainer.test.batch_size}, shuffle: {trainer.test.shuffle}")
    print(f"  [Ckpt]  batch_size: {trainer.checkpointer.batch_size} (broadcast=False -> bare keys skip it)")


if __name__ == "__main__":
    # Auto-load the companion config if not provided
    if "--config" not in sys.argv and "-c" not in sys.argv:
        default_yaml = Path(__file__).parent / "broadcast_demo.yaml"
        if default_yaml.exists():
            sys.argv.extend(["--config", str(default_yaml)])

    app.run()
