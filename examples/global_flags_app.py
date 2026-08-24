"""Global flags — the runnable companion to ``docs/global-flags.md``.

Drives one app through the shared global-flag vocabulary: ``--level`` (log
control), the four-layer hierarchy a level is resolved through, and ``--docs``
(the greppable one-option-per-line variant of ``--help``).

Run with no arguments for the subprocess-driven tour; with arguments to act as
the app itself.
"""

import os
import subprocess
import sys
import tempfile

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
    # Parsing captured log output means pinning its FORMAT. Loguru colorizes whenever `CI`
    # and `GITHUB_ACTIONS` are both set (`loguru._colorama.should_colorize` treats a CI log
    # as color-capable and skips its isatty check), which buries ANSI codes between the "|"
    # and the level name — a plain `"| DEBUG" in line` test then matches nothing and the
    # assert below fails on CI while passing locally. NO_COLOR is loggair's documented
    # off-switch for exactly this non-interactive-consumer case (see `loggair.force_no_color`).
    env = {**os.environ, "NO_COLOR": "1"}

    # --level controls console + file log level in one flag (console sink -> stderr).
    counts = []
    for argv in (["run"], ["--level", "DEBUG", "run"]):
        proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, check=True, env=env)
        debug_lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if "| DEBUG" in ln]
        counts.append(len(debug_lines))
        print(f"$ global-flags-demo {' '.join(argv):<18} -> {len(debug_lines)} DEBUG log lines on the console")
    assert counts[1] > counts[0], "--level DEBUG should surface DEBUG lines the default level hides"

    # A level nobody typed comes from the environment. liquifai forwards an
    # unset flag as None, so the logging engine resolves
    # flag > LOGGAIR_CONSOLE_LEVEL > config file > default. Run each case in a
    # temp cwd so a loggair.yaml in the checkout cannot decide the outcome.
    print()
    with tempfile.TemporaryDirectory() as workdir:
        resolved = []
        for label, env, argv in (
            ("default", {}, ["run"]),
            ("env", {"LOGGAIR_CONSOLE_LEVEL": "DEBUG"}, ["run"]),
            ("env + flag", {"LOGGAIR_CONSOLE_LEVEL": "DEBUG"}, ["--level", "WARNING", "run"]),
        ):
            proc = subprocess.run(
                [sys.executable, __file__, *argv, "--log-dir", workdir],
                capture_output=True,
                text=True,
                check=True,
                cwd=workdir,
                # NO_COLOR: loguru colorizes under CI env vars, and ANSI codes between
                # "|" and "DEBUG" would defeat the substring count below (the AGENTS
                # example-parses-log-output mandate; same fix as the first loop).
                env={**os.environ, "NO_COLOR": "1", **env},
            )
            shown = len([ln for ln in (proc.stdout + proc.stderr).splitlines() if "| DEBUG" in ln])
            resolved.append(shown)
            setting = " ".join(f"{k}={v}" for k, v in env.items())
            cmd = f"{setting + ' ' if setting else ''}global-flags-demo {' '.join(argv)}"
            print(f"$ {cmd:<62} -> {shown} DEBUG lines ({label})")
        assert resolved[0] == 0, "the default console level hides DEBUG"
        assert resolved[1] > 0, "LOGGAIR_CONSOLE_LEVEL must beat the default"
        assert resolved[2] == 0, "--level must beat LOGGAIR_CONSOLE_LEVEL"

    print()
    # --docs renders the same code-extracted option docs as --help, one per line.
    proc = subprocess.run(
        [sys.executable, __file__, "run", "--docs"], capture_output=True, text=True, check=True, env=env
    )
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
