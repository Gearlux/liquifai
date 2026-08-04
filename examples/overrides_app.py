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
import tempfile
import textwrap
from pathlib import Path

from confluid import configurable

from liquifai import LiquifyApp

# A config that sets `lr` TWICE: once as a top-level key, once at the node — and
# the node sits LATER, so by confluid's document-order rule it wins. A CLI `--lr`
# must still beat both: it was typed after the whole file, so it is applied last.
PRECEDENCE_CONFIG = """
lr: 0.1
trainer: !class:Trainer
  lr: 0.5
"""


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
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "precedence.yaml"
        cfg.write_text(textwrap.dedent(PRECEDENCE_CONFIG))
        cases += [
            (["show", "--config", str(cfg)], "config alone: the node's `lr` wins, being later"),
            (["show", "--config", str(cfg), "--lr", "0.9"], "the CLI beats it — an override applies last"),
        ]
        for argv, label in cases:
            proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, check=True)
            result = [line for line in proc.stdout.splitlines() if line.startswith("RESULT")][0]
            shown = " ".join("<config>" if a.endswith(".yaml") else a for a in argv)
            print(f"$ overrides-demo {shown:<24} # {label}\n  {result}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
