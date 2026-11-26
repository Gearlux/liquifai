"""Keys a command adds to its configuration — the runnable companion to ``docs/commands-and-di.md``.

A ``script_command(config_keys=fn)`` hands ``fn`` the loaded document and merges the keys it returns in after the
command line, before ``--dump-config`` writes the configuration and before the objects are built. Here the
``evaluate`` command forces ``runnable.task: test`` — but only on a runnable whose class takes a ``task`` — so the
dumped configuration shows the task the command will run.

Run with no arguments for the subprocess-driven tour; with arguments to act as the app itself.
"""

import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, Mapping

import confluid
from confluid import configurable

from liquifai import LiquifyApp


@configurable
class Job:
    def __init__(self, task: str = "fit", epochs: int = 1) -> None:
        """A job that runs one task.

        Args:
            task: What the job does: fit or test.
            epochs: Passes over the data.
        """
        self.task = task
        self.epochs = epochs


app = LiquifyApp(name="config-keys-demo")


def evaluate_keys(document: Mapping[str, Any]) -> Dict[str, Any]:
    """``runnable.task: test`` when the runnable takes a ``task``; nothing otherwise."""
    runnable = document.get("runnable")
    if isinstance(runnable, confluid.Target) and confluid.accepts_key(runnable.target, "task"):
        return {"runnable.task": "test"}
    return {}


@app.script_command(name="evaluate", flow_mode="manual", config_keys=evaluate_keys)
def evaluate(runnable: Any = None) -> None:
    """Build the config's runnable and report its task."""
    assert app.context is not None
    job = confluid.load(runnable, context=app.context.config_data)
    print(f"RESULT task={job.task} epochs={job.epochs}")


def demo() -> None:
    env = {**os.environ, "NO_COLOR": "1"}
    with tempfile.TemporaryDirectory() as workdir:
        config = os.path.join(workdir, "job.yaml")
        with open(config, "w") as handle:
            handle.write("runnable: !class:Job\n  task: fit\n")

        proc = subprocess.run(
            [sys.executable, __file__, "evaluate", config, "--dump-config"], capture_output=True, text=True, env=env
        )
        assert proc.returncode == 0, proc.stderr
        assert "task: test" in proc.stdout and "RESULT" not in proc.stdout, proc.stdout
        print("$ config-keys-demo evaluate job.yaml --dump-config   (the file says task: fit)")
        for line in proc.stdout.splitlines():
            print(f"  {line}")

        proc = subprocess.run(
            [sys.executable, __file__, "evaluate", config, "--epochs", "3"], capture_output=True, text=True, env=env
        )
        assert "RESULT task=test epochs=3" in proc.stdout, proc.stdout + proc.stderr
        print("$ config-keys-demo evaluate job.yaml --epochs 3")
        print(f"  {proc.stdout.strip()}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
