"""Positional arguments — the runnable companion to ``docs/commands-and-di.md``.

``@command(positionals=[...])`` binds leading non-flag tokens in order (verbatim
strings), stopping at the first flag-like or ``key=value`` token — so positional,
``key=value``, and ``--flag`` forms interoperate.

Run with no arguments to see three invocation styles exercised via subprocess;
run with arguments to act as the app itself.
"""

import subprocess
import sys

from liquifai import LiquifyApp

app = LiquifyApp(name="positionals-demo")


@app.command("download", positionals=["name", "version"])
def download(name: str = "", version: str = "latest", path: str = ".") -> None:
    """Pretend-download a dataset.

    Args:
        name: Dataset name (first positional).
        version: Dataset version (second positional).
        path: Target directory (flag-only).
    """
    print(f"RESULT name={name!r} version={version!r} path={path!r}")


def demo() -> None:
    invocations = [
        ["download", "cifar10", "2.0"],  # pure positional
        ["download", "--name", "cifar10"],  # pure flag form
        ["download", "cifar10", "--path", "/tmp/data"],  # mixed: binding stops at the first flag
        # `--` ends option parsing: everything after it is a literal value, so a
        # positional starting with a dash binds instead of reading as an option.
        ["download", "--", "-latest", "-2.0"],
    ]
    for argv in invocations:
        proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, check=True)
        result = [line for line in proc.stdout.splitlines() if line.startswith("RESULT")][0]
        print(f"$ positionals-demo {' '.join(argv)}\n  {result}")

    print("positional, flag, mixed, and `--` literal forms all bound correctly")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
