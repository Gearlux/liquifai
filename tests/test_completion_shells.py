"""The grouped option listing in a REAL interactive bash and zsh: what a person sees after TAB TAB, and that the
line they keep typing still runs as typed.

Each test starts the shell on its own terminal with the completion script liquifai renders, a cache written for
the ``listdemo`` app, and a ``listdemo`` shell function that echoes what it was run with. Every wait has a deadline
(``Terminal.wait_for``). A missing shell skips its tests, naming it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import pytest
from shell_terminal import Terminal

from liquifai import LiquifyApp
from liquifai.completion import render_helpers, render_script
from liquifai.completion import tree as tree_mod


def _app() -> LiquifyApp:
    app = LiquifyApp(name="listdemo")

    @app.command("serve", short={"b": "background"})
    def serve(port: int = 8787, background: bool = False, browser: bool = False, label: str = "") -> None:
        """Serve.

        Args:
            port: the port to serve on
            background: run detached
            browser: open a browser window
        """

    return app


def _bash_version(path: str) -> tuple:
    out = subprocess.run([path, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'], capture_output=True, text=True)
    major, minor = out.stdout.split()
    return int(major), int(minor)


def _modern_bash() -> Optional[str]:
    for candidate in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if candidate and Path(candidate).exists() and _bash_version(candidate) >= (4, 4):
            return candidate
    return None


BASH = _modern_bash()
ZSH = shutil.which("zsh")
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash 4.4 or newer on this machine")
needs_zsh = pytest.mark.skipif(ZSH is None, reason="no zsh on this machine")

#: The rows the second TAB shows after ``listdemo serve --`` (the global rows are the grammar table's).
COMMAND_BLOCK = (
    "options of listdemo serve\n"
    "  -b, --background              run detached\n"
    "  --browser                     open a browser window\n"
    "  --label LABEL\n"
    "  --port PORT                   the port to serve on\n"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cache for ``listdemo`` and an empty readline configuration, so no setting of this machine's applies."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    tree_mod.write_cache(_app())
    (tmp_path / "inputrc").write_text("")
    return tmp_path


def _env(home: Path, **extra: str) -> Dict[str, str]:
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / "cache"),
        "INPUTRC": str(home / "inputrc"),
        "HISTFILE": "/dev/null",
        "TERM": "xterm",
        "LANG": "en_US.UTF-8",
        "LIQUIFAI_NO_LAZY_COMPLETE": "1",
    }
    env.update(extra)
    return env


def _bashrc(home: Path, *, helpers: bool = False) -> Path:
    rc = home / "bashrc"
    parts = ["PS1='$ '", 'listdemo() { echo "ran: $*"; }']
    if helpers:
        parts += [render_helpers("bash"), 'lds() { listdemo serve "$@"; }', "liquifai-bind-alias lds listdemo serve"]
    rc.write_text("\n".join(parts + [render_script("listdemo", "bash")]) + "\n")
    return rc


@pytest.fixture
def terminals() -> Iterator[List[Terminal]]:
    opened: List[Terminal] = []
    yield opened
    for term in opened:
        term.close()


def _bash(
    terminals: List[Terminal],
    home: Path,
    *,
    shell: Optional[str] = None,
    rows: int = 40,
    cols: int = 110,
    helpers: bool = False,
    **env: str,
) -> Terminal:
    rc = _bashrc(home, helpers=helpers)
    term = Terminal([shell or str(BASH), "--noprofile", "--rcfile", str(rc), "-i"], _env(home, **env), rows, cols)
    terminals.append(term)
    term.wait_for("$ ")
    return term


def _ran(term: Terminal, keys: str, expected: str) -> None:
    """Type ``keys`` and Enter; the shell function must have run with exactly ``expected``."""
    term.send(keys + "\r")
    term.wait_for(f"ran: {expected}\n")


# ------------------------------------------------------------------- bash ---------------------------------------


@needs_bash
def test_bash_lists_the_global_options_then_the_commands_own_and_keeps_the_line(
    home: Path, terminals: List[Terminal]
) -> None:
    term = _bash(terminals, home)
    term.send("listdemo serve --\t\t")
    text = term.wait_for(COMMAND_BLOCK + "$ listdemo serve --")
    listing = text[text.index("global options\n") :]
    assert listing.startswith("global options\n  -c, --config PATH             Configuration file.\n")
    assert listing.index("global options") < listing.index("options of listdemo serve")
    _ran(term, "port 1", "serve --port 1")


@needs_bash
def test_bash_still_fills_in_a_unique_option_on_the_first_tab(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home)
    term.send("listdemo serve --bro\t")
    term.wait_for("listdemo serve --browser ")
    _ran(term, "", "serve --browser")


@needs_bash
def test_bash_lists_only_the_group_the_typed_word_matches(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home)
    term.send("listdemo serve --b\t\t")
    text = term.wait_for("  --browser                     open a browser window\n$ listdemo serve --b")
    assert "global options" not in text
    _ran(term, "rowser", "serve --browser")


@needs_bash
def test_plain_brings_back_bashs_own_sorted_list(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home, LIQUIFAI_COMPLETION_LIST="plain")
    term.send("listdemo serve --\t\t")
    text = term.wait_for("--show-completion")
    term.wait_for("$ listdemo serve --")
    assert "global options" not in text and "options of" not in text
    assert re.search(r"\n--background\s.*\n--browser\s.*\n--config\s", text), text  # bash's grid, sorted down
    _ran(term, "port 1", "serve --port 1")


@needs_bash
def test_bash_keeps_its_own_list_when_the_cursor_is_inside_the_line(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home)
    term.send("listdemo serve -- 1\x1b[D\x1b[D\t\t")  # cursor back before " 1"
    text = term.wait_for("--show-completion")
    assert "options of" not in text
    _ran(term, "", "serve -- 1")


@needs_bash
def test_bash_keeps_its_own_list_when_the_list_is_taller_than_the_screen(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home, rows=12)
    term.send("listdemo serve --\t\t")
    text = term.wait_for("--show-completion")
    assert "options of" not in text
    _ran(term, "port 1", "serve --port 1")


@needs_bash
def test_bash_keeps_its_own_list_when_the_line_is_wider_than_the_screen(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home, cols=60)
    label = "x" * 40
    term.send(f"listdemo serve --label {label} --\t\t")
    text = term.wait_for("--show-completion")
    assert "options of" not in text
    _ran(term, "port 1", f"serve --label {label} --port 1")


@needs_bash
def test_an_alias_bound_with_liquifai_bind_alias_lists_the_same_groups(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home, helpers=True)
    term.send("lds --\t\t")
    term.wait_for(COMMAND_BLOCK + "$ lds --")
    _ran(term, "port 1", "serve --port 1")


@pytest.mark.skipif(
    not Path("/bin/bash").exists() or _bash_version("/bin/bash") >= (4, 0), reason="no bash 3 at /bin/bash"
)
def test_an_old_bash_shows_its_own_list_without_an_error(home: Path, terminals: List[Terminal]) -> None:
    term = _bash(terminals, home, shell="/bin/bash")
    term.send("listdemo serve --\t\t")
    text = term.wait_for("--show-completion")
    assert "options of" not in text and "bad substitution" not in text
    _ran(term, "port 1", "serve --port 1")


# -------------------------------------------------------------------- zsh ---------------------------------------


def _zsh(terminals: List[Terminal], home: Path, *, helpers: bool = False, **env: str) -> Terminal:
    zdot = home / "zdot"
    zdot.mkdir(exist_ok=True)
    parts = ["PROMPT='$ '", 'listdemo() { echo "ran: $*"; }', render_script("listdemo", "zsh")]
    if helpers:
        parts += [render_helpers("zsh"), 'lds() { listdemo serve "$@"; }', "liquifai-bind-alias lds listdemo serve"]
    (zdot / ".zshrc").write_text("\n".join(parts) + "\n")
    term = Terminal([str(ZSH), "-i"], _env(home, ZDOTDIR=str(zdot), **env))
    terminals.append(term)
    term.wait_for("$ ")
    return term


@needs_zsh
def test_zsh_lists_the_global_options_then_the_commands_own(home: Path, terminals: List[Terminal]) -> None:
    term = _zsh(terminals, home)
    term.send("listdemo serve --\t")
    text = term.wait_for("  --port PORT                   the port to serve on")
    listing = text[text.index("global options\n") :]
    assert listing.startswith("global options\n  -c, --config PATH             Configuration file.\n")
    assert COMMAND_BLOCK.rstrip("\n") in listing
    _ran(term, "port 1", "serve --port 1")


@needs_zsh
def test_zsh_completes_a_short_spelling_it_does_not_list(home: Path, terminals: List[Terminal]) -> None:
    term = _zsh(terminals, home)
    term.send("listdemo serve -c\t")
    term.wait_for("listdemo serve -c ")


@needs_zsh
def test_plain_brings_back_zshs_list_without_groups(home: Path, terminals: List[Terminal]) -> None:
    term = _zsh(terminals, home, LIQUIFAI_COMPLETION_LIST="plain")
    term.send("listdemo serve --\t")
    text = term.wait_for("--show-completion")
    assert "global options" not in text and "options of" not in text
    _ran(term, "port 1", "serve --port 1")


@needs_zsh
def test_a_zsh_alias_bound_with_liquifai_bind_alias_lists_the_same_groups(
    home: Path, terminals: List[Terminal]
) -> None:
    term = _zsh(terminals, home, helpers=True)
    term.send("lds --\t")
    term.wait_for("  --port PORT                   the port to serve on")
    assert "options of listdemo serve" in term.text
    _ran(term, "port 1", "serve --port 1")
