"""Global flags — the runnable companion to ``docs/global-flags.md``.

Drives one app through the shared global-flag vocabulary: ``--level`` (log
control) and ``--docs`` (the greppable one-option-per-line variant of ``--help``).

Run with no arguments for the subprocess-driven tour; with arguments to act as
the app itself.
"""

import subprocess
import sys

from confluid import configurable

from liquifai import LiquifyApp


@configurable
class Job:
    def __init__(self, name: str = "demo", retries: int = 2) -> None:
        """A configurable job.

        Args:
            name: Job label.
            retries: Retry budget on failure.
        """
        self.name = name
        self.retries = retries


app = LiquifyApp(name="global-flags-demo")


@app.command(default=True)
def run(job: Job) -> None:
    """Run the job and report its knobs."""
    print(f"RESULT job={job.name!r} retries={job.retries}")


def demo() -> None:
    # --level controls console + file log level in one flag (console sink -> stderr).
    counts = []
    for argv in (["run"], ["--level", "DEBUG", "run"]):
        proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, check=True)
        debug_lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if "| DEBUG" in ln]
        counts.append(len(debug_lines))
        print(f"$ global-flags-demo {' '.join(argv):<18} -> {len(debug_lines)} DEBUG log lines on the console")
    assert counts[1] > counts[0], "--level DEBUG should surface DEBUG lines the default level hides"

    # --docs renders the same code-extracted option docs as --help, one per line.
    proc = subprocess.run([sys.executable, __file__, "run", "--docs"], capture_output=True, text=True, check=True)
    option_lines = [ln for ln in proc.stdout.splitlines() if ln.lstrip().startswith("--")]
    assert any("retries" in ln for ln in option_lines), option_lines
    print("$ global-flags-demo run --docs   -> one greppable line per option:")
    for ln in option_lines:
        print(f"  {ln.strip()}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
