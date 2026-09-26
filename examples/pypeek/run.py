"""CI self-test for the pypeek example app (see pypeek.py / README.md).

Drives ``python pypeek.py <verb> ...`` as subprocesses and asserts on the output. The
``examples/*/run.py`` glob in CI executes this file.

pypeek's real job needs the network, so this self-test sticks to what runs OFFLINE: the
installed-metadata modes (``list``, ``--local``), the dry-run mode (``--dry_run+`` prints the
request instead of sending it, which also proves the override reached the injected client), and
every failure path — all of which fail before any request would be made.
"""

import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent
APP = APP_DIR / "pypeek.py"


def run(*args: str, expect_rc: int = 0) -> str:
    """Run a pypeek invocation; return its stdout with Rich's line wrapping undone."""
    # No background completion refresh: its version provider would query PyPI.
    env = {k: v for k, v in os.environ.items() if k != "LIQUIFAI_BG_REFRESH"}
    result = subprocess.run(
        [sys.executable, str(APP), *args], cwd=APP_DIR, capture_output=True, text=True, timeout=120, env=env
    )
    label = "pypeek " + " ".join(args)
    assert result.returncode == expect_rc, (
        f"{label!r} exited {result.returncode} (expected {expect_rc})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    if expect_rc != 0:
        # The CLI failure contract: ONE clean error line, never a traceback.
        assert "Traceback" not in result.stdout + result.stderr, f"expected a clean error, got:\n{result.stderr}"
    print(f"ok: {label}")
    return " ".join(result.stdout.split())


def main() -> None:
    # 1. list reads the installed distributions — liquifai itself is one of them.
    out = run("list", "--prefix", "liquifai")
    assert "liquifai" in out, f"liquifai should be listed as installed:\n{out}"

    # 2. --local reads installed metadata instead of PyPI.
    out = run("info", "liquifai", "--local")
    assert "installed metadata (--local)" in out, out
    out = run("versions", "liquifai", "--local")
    assert "Versions of liquifai" in out, out

    # 3. --dry_run+ reaches the injected PyPI client (a CLI override broadcast into a
    #    @configurable the command never names) and prints the request instead of sending it.
    out = run("files", "rich", "14.0.0", "--dry_run+")
    assert "DRY RUN GET https://pypi.org/pypi/rich/14.0.0/json" in out, out
    out = run("info", "rich", "--dry_run+", "--base_url", "https://mirror.example/pypi")
    assert "DRY RUN GET https://mirror.example/pypi/rich/json" in out, out

    # 4. A missing argument is a clean error naming where the value goes.
    out = run("versions", expect_rc=1)
    assert "missing required argument 'package' — pass it as `pypeek versions <package>`" in out, out

    # 5. The package name typed BEFORE the command: the stray word is ignored with a warning,
    #    and the command then reports its missing argument the same way.
    out = run("confluid", "versions", expect_rc=1)
    assert "missing required argument 'package'" in out, out

    # 6. An app-raised PyPeekError (a LiquifaiError) follows the same contract.
    out = run("info", "no-such-distribution-xyz", "--local", expect_rc=1)
    assert "is not installed in this environment" in out, out

    print("PASS: pypeek self-test (6 scenarios)")


if __name__ == "__main__":
    main()
