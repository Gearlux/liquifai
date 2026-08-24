"""Host facts — the runnable companion to ``docs/host-facts.md``.

Liquifai detects the machine's ``os`` and ``device`` once per run and offers each
one twice: as a scope activation (``!scope:os=darwin`` blocks) and as a key in the
injected ``platform`` namespace (``${platform.os}`` interpolation). This script
writes a config that uses BOTH, runs it, and then forces the other branch with
``--scope`` so the two outcomes sit side by side.

Run with no arguments for the tour; with arguments to act as the app itself.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from liquifai import LiquifyApp, host

CONFIG = """\
# `${{platform.os}}` reads the injected namespace. A bare `${{os}}` would NOT work:
# confluid reads a dotted name from the config tree and a bare one from the
# environment, and liquifai sets no environment variables.
logdir: /runs/${{platform.os}}
workers: 8

# One key, overridden only on the machine this block names. A host the document
# does not mention keeps the value above instead of failing.
here: !scope:os={os}
  workers: 0
"""

app = LiquifyApp(name="host-facts-demo")


@app.command(default=True)
def show(logdir: str = "", workers: int = -1) -> None:
    """Print what the document resolved to on this machine."""
    print(f"RESULT logdir={logdir!r} workers={workers}")


def demo() -> None:
    facts = host.host_facts({})
    print(f"detected: os={facts['os']!r} device={facts['device']!r}")

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "demo.yaml"
        cfg.write_text(CONFIG.format(os=facts["os"]))

        # 1. A bare run: liquifai passes the detected facts, so the block fires.
        for argv, note in (
            (["-c", str(cfg), "show"], "the detected os selects the block"),
            (["-c", str(cfg), "--scope", f"os={facts['os']}", "show"], "the same value, typed by hand"),
        ):
            proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, check=True)
            result = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT"))
            print(f"$ host-facts-demo {' '.join(argv[2:]):<28} -> {result}   # {note}")
            assert "workers=0" in result, result
            assert f"/runs/{facts['os']}" in result, result

        # 2. The same document on a machine it does not name: the block is inert
        #    rather than an error, which is what lets a config grow into a host.
        other = "linux" if facts["os"] != "linux" else "darwin"
        cfg.write_text(CONFIG.format(os=other))
        proc = subprocess.run(
            [sys.executable, __file__, "-c", str(cfg), "show"], capture_output=True, text=True, check=True
        )
        result = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT"))
        print(f"$ host-facts-demo (block names {other:<6})          -> {result}   # inert here, not an error")
        assert "workers=8" in result, result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
