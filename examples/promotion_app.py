"""Config promotion & its provenance — a companion to ``docs/commands-and-di.md``.

A ``@script_command``'s first positional token is its YAML config, resolved
through the search tiers ``./`` → ``./config/`` → the XDG config dirs. Promotion
is **eager**: the token is consumed as soon as a matching file exists in ANY
tier. That is convenient when you meant it and a trap when you didn't — a
forgotten ``~/.config/<app>/report.yaml`` silently swallows
``my-app process report`` that meant ``report`` as a positional argument.

So every promotion is logged: at TRACE always, and at DEBUG when the file came
from OUTSIDE the working directory. This script demonstrates all four outcomes
against an isolated temp workspace (your real ``~/.config`` is never read).

Run with no arguments for the demo; run with arguments to act as the app.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from liquifai import LiquifyApp

app = LiquifyApp(name="promotion-demo")


@app.script_command("process", positionals=["target"])
def process(value: int = 0, target: str = "") -> None:
    """Process a target, configured from a promoted YAML file.

    Args:
        value: Comes from the config file when one is promoted.
        target: The positional — bound ONLY when the token was not swallowed
            as a config path.
    """
    print(f"RESULT value={value} target={target!r}")


# ---------------------------------------------------------------------------
# Demo harness
# ---------------------------------------------------------------------------

#: The marker the DEBUG provenance line carries when a config was resolved from
#: somewhere other than the working directory.
ESCALATION = "OUTSIDE the working directory"


def _run(workspace: Path, argv: List[str]) -> Tuple[str, str]:
    """Run this file as the app inside ``workspace``; return (result, log).

    The subprocess gets a sandboxed ``HOME`` / XDG environment so the example
    can create an "XDG tier" config without touching the real one — and so the
    completion cache and log file land in the temp workspace too.
    """
    env: Dict[str, str] = {
        **os.environ,
        "HOME": str(workspace / "home"),
        "XDG_CONFIG_HOME": str(workspace / "home" / ".config"),
        "XDG_CONFIG_DIRS": str(workspace / "home" / ".xdg-sys"),
        "XDG_CACHE_HOME": str(workspace / "home" / ".cache"),
    }
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *argv],
        capture_output=True,
        text=True,
        cwd=workspace,
        env=env,
        check=True,
    )
    combined = proc.stdout + proc.stderr
    result = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")), "<no result>")
    return result, combined


def _tier(workspace: Path, relative: str) -> Path:
    """Create ``report.yaml`` at ``relative`` under the workspace, wiping the others."""
    for stale in workspace.rglob("report.yaml"):
        stale.unlink()
    target = workspace / relative / "report.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value: 42\n")
    return target


def demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        (workspace / "home").mkdir()

        # 1. The config sits in the working directory — the unsurprising case.
        _tier(workspace, ".")
        result, log = _run(workspace, ["process", "report", "--level", "DEBUG"])
        assert "value=42" in result, result
        assert ESCALATION not in log, "a CWD promotion must not be escalated to DEBUG"
        print("1. ./report.yaml            promoted, no DEBUG notice (nothing surprising)")
        print(f"     {result}")

        # 2. Same token, file one tier out. Still promoted — and now announced.
        _tier(workspace, "config")
        result, log = _run(workspace, ["process", "report", "--level", "DEBUG"])
        assert "value=42" in result, result
        assert ESCALATION in log, "a ./config/ promotion must be escalated to DEBUG"
        print("2. ./config/report.yaml     promoted from another tier -> DEBUG notice")
        print(f"     {result}")

        # 3. The footgun: nothing local, but a stale XDG file with that name.
        #    `report` was meant as the <target> positional; promotion eats it.
        _tier(workspace, "home/.config/promotion-demo")
        result, log = _run(workspace, ["process", "report", "--level", "DEBUG"])
        assert "value=42" in result, result
        assert "target=''" in result, "the token was swallowed as a config, so <target> stayed empty"
        assert ESCALATION in log, "an XDG promotion must be escalated to DEBUG"
        print("3. ~/.config/<app>/…        SWALLOWED the positional -> DEBUG notice")
        print(f"     {result}   <- target is empty; the token became the config")

        # 4. No file anywhere: the token is not promoted and binds as declared.
        for stale in workspace.rglob("report.yaml"):
            stale.unlink()
        result, log = _run(workspace, ["process", "report", "--level", "DEBUG"])
        assert "target='report'" in result, result
        assert "value=0" in result, "no config -> the default survives"
        print("4. (no report.yaml)         not promoted -> binds as the <target> positional")
        print(f"     {result}")

    print("\npromotion is eager across all tiers; every resolution outside the CWD is logged at DEBUG")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
