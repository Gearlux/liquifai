"""CLI override grammar — the runnable companion to ``docs/cli-overrides.md``.

One command, driven through the override forms: ``--key value``, ``--key=value``
(dotted), bare ``key=value``, polarity ``--key+``/``--key-``, implicit ``--key``,
and a deliberately unrecognized token (which liquifai warns about, never drops
silently).

Run with no arguments for the subprocess-driven tour; with arguments to act as
the app itself.
"""

import subprocess
import sys

from confluid import configurable

from liquifai import LiquifyApp


@configurable
class Trainer:
    def __init__(self, lr: float = 0.001, max_epochs: int = 10, verbose: bool = False) -> None:
        """A trainer with three overridable knobs.

        Args:
            lr: Learning rate.
            max_epochs: Epoch budget.
            verbose: Chatty mode.
        """
        self.lr = lr
        self.max_epochs = max_epochs
        self.verbose = verbose


app = LiquifyApp(name="overrides-demo")


@app.command(default=True)
def show(trainer: Trainer) -> None:
    """Print the trainer configuration after overrides applied."""
    print(f"RESULT lr={trainer.lr} max_epochs={trainer.max_epochs} verbose={trainer.verbose}")


def demo() -> None:
    cases = [
        (["show", "--max_epochs", "3"], "flag + value"),
        (["show", "--trainer.lr=0.5"], "equals form, dotted key"),
        (["show", "max_epochs=7"], "bare key=value"),
        (["show", "--verbose+"], "polarity: explicit True"),
        (["show", "--verbose"], "implicit True"),
        (["show", "oops", "0.1"], "unrecognized tokens -> warning, run proceeds on defaults"),
        # After `--` nothing is an option: `--max_epochs` is a literal value here,
        # so the override is NOT applied and max_epochs keeps its default.
        (["show", "--", "--max_epochs", "3"], "`--` ends option parsing"),
    ]
    for argv, label in cases:
        proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, check=True)
        result = [line for line in proc.stdout.splitlines() if line.startswith("RESULT")][0]
        print(f"$ overrides-demo {' '.join(argv):<24} # {label}\n  {result}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
