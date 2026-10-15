"""The scopes of the config a command runs — the runnable companion to ``docs/commands-and-di.md``.

A command that runs a config of ITS choosing (here: a small YAML written to the temp folder, handed to a pretend
tool) names the file with ``config_files=``; liquifai reads it: ``--help`` lists its scopes, ``--scope <TAB>`` offers
them, the typed values go to the command's ``scopes`` parameter, and a scope the file lacks is refused.

Run with no arguments to see the help block and three runs exercised via subprocess; run with arguments to act as the
app itself.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

import confluid

from liquifai import LiquifyApp

app = LiquifyApp(name="scopes-demo")

#: The config the command runs: written to the temp folder on the first call, so the example ships no YAML.
CONFIG = Path(tempfile.gettempdir()) / "liquifai-scopes-demo.yaml"
CONFIG_TEXT = "backend: torchscript\nonnx: !scope:onnx\n  backend: onnx\nfp16: !scope:fp16\n  half: true\n"


def compile_config() -> List[Path]:
    """The file the command runs — all liquifai needs to know its scopes."""
    if not CONFIG.is_file():
        CONFIG.write_text(CONFIG_TEXT)
    return [CONFIG]


@app.command("compile", positionals=["run"], config_files=compile_config)
def compile_run(run: str = "", scopes: Optional[List[str]] = None) -> None:
    """Pretend-compile a run with the config the command chooses.

    Args:
        run: The run folder.
        scopes: The config's scopes to run it with (--scope NAME).
    """
    resolved = confluid.load(compile_config()[0], scopes=scopes or None, until="document")
    print(f"RESULT run={run!r} scopes={scopes!r} backend={resolved['backend']!r} half={resolved.get('half', False)!r}")


def demo() -> None:
    env = {**os.environ, "NO_COLOR": "1", "COLUMNS": "200"}

    def run(*argv: str) -> str:
        proc = subprocess.run([sys.executable, __file__, *argv], capture_output=True, text=True, env=env, check=False)
        return proc.stdout + proc.stderr

    help_text = run("compile", "--help")
    block = help_text[help_text.index("Scopes (") :].split("\n\n")[0]
    print(f"$ scopes-demo compile --help   (the block)\n{block}\n")
    for argv in (["compile", "run2"], ["compile", "run2", "--scope", "onnx,fp16"], ["compile", "run2", "-s", "x"]):
        line = [ln for ln in run(*argv).splitlines() if ln.startswith(("RESULT", "Error"))][-1]
        print(f"$ scopes-demo {' '.join(argv)}\n  {line}")
    assert "onnx" in block and "fp16" in block


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
