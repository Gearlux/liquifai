"""The CLI failure contract — the runnable companion to ``docs/error-handling.md``.

Shows (1) a typed ``CommandDefinitionError`` at decoration time, dual-inheriting
``ValueError``; (2) an expected config failure producing ONE clean ``Error:`` line
and exit code 1 in a subprocess (never a traceback — unless ``--debug``).

Run with no arguments for the tour; with arguments to act as the app itself.
"""

import subprocess
import sys
from typing import Any

from confluid import configurable

from liquifai import CommandDefinitionError, LiquifyApp


@configurable
class Job:
    def __init__(self, name: str = "demo") -> None:
        """A configurable job.

        Args:
            name: Job label.
        """
        self.name = name


app = LiquifyApp(name="failure-demo")


@app.script_command()
def run(job: Job) -> None:
    """Run the job."""
    print(f"RESULT job={job.name!r}")


def demo() -> None:
    # 1. An invalid declaration raises a TYPED error that is also a ValueError.
    try:

        @app.script_command(flow_mode="aggressive")  # type: ignore[arg-type]
        def broken(job: Any) -> None: ...

    except CommandDefinitionError as exc:
        assert isinstance(exc, ValueError), "dual-inherits the builtin"
        print(f"bad flow_mode -> {type(exc).__name__} (also ValueError): {exc}")
    else:
        raise AssertionError("expected CommandDefinitionError")

    # 2. A missing config file is an EXPECTED failure: clean message, exit 1, no traceback.
    proc = subprocess.run(
        [sys.executable, __file__, "run", "--config", "/nonexistent/experiment.yaml"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}"
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, "expected a clean error, not a traceback"
    error_lines = [ln for ln in combined.splitlines() if "not found" in ln.lower()]
    print(f"missing config -> exit {proc.returncode}, message: {error_lines[0].strip()}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
