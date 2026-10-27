"""The grouped option listing a second TAB shows: global options first, then the command's own, each with its help.

``complete_with_listing`` returns the candidates ``complete_from_tree`` returns, plus — at an option position — the
listing the shell prints instead of its own alphabetical grid. ``wire_lines`` is what ``liquifai-complete`` prints
for a wrapper that reads it; ``LIQUIFAI_COMPLETION_LIST=plain`` keeps today's output byte for byte. The shells' side
(bash printing the list, zsh's groups) is driven in a real terminal in ``tests/test_completion_shells.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from liquifai import LiquifyApp
from liquifai import completion as comp
from liquifai.completion import tree as tree_mod
from liquifai.grammar import GLOBAL_FLAG_SPECS, flag_display

#: The widest left column of the listing below: ``--install-completion [SHELL]``.
LEFT = 28


def _row(left: str, doc: str = "") -> str:
    return f"  {left:<{LEFT}}  {doc}".rstrip()


@pytest.fixture(autouse=True)
def iso_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LIQUIFAI_NO_LAZY_COMPLETE", "1")
    monkeypatch.delenv("LIQUIFAI_COMPLETION_LIST", raising=False)
    return tmp_path


def _app() -> LiquifyApp:
    app = LiquifyApp(name="listdemo")

    @app.command("serve", short={"b": "background"})
    def serve(port: int = 8787, background: bool = False, browser: bool = False, label: str = "") -> None:
        """Serve the workspace.

        Args:
            port: the port to serve on
            background: run detached
            browser: open a
                browser window
        """

    @app.command("download", positionals=["name"], completions={"name": lambda: ["alpha", "beta"]})
    def download(name: str = "", path: str = "") -> None:
        """Download one item."""

    @app.script_command("train")
    def train(layers: int = 1) -> None:
        """Train.

        Args:
            layers: how many layers
        """

    sub = LiquifyApp(name="data", description="data")

    @sub.command("fetch")
    def fetch(force: bool = False) -> None:
        """Fetch.

        Args:
            force: fetch again
        """

    app.add_app(sub)
    return app


def _listing(line: str, width: int = 120) -> comp.Listing:
    words = line.split(" ")
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1, width=width)
    assert listing is not None, f"no listing for {line!r}"
    return listing


def _text(listing: comp.Listing) -> List[str]:
    """The lines a shell prints: each header, then its rows (a spelling with no row is completable, not listed)."""
    out: List[str] = []
    for header, rows in listing:
        out.append(header)
        out.extend(display for _, display in rows if display)
    return out


GLOBAL_ROWS = [_row(flag_display(spec), spec.help) for spec in GLOBAL_FLAG_SPECS if not spec.hidden]


def test_the_global_options_come_first_then_the_commands_own_each_with_its_help() -> None:
    assert _text(_listing("listdemo serve --")) == [
        "global options",
        *GLOBAL_ROWS,
        "options of listdemo serve",
        _row("-b, --background", "run detached"),
        _row("--browser", "open a browser window"),
        _row("--label LABEL"),
        _row("--port PORT", "the port to serve on"),
    ]


def test_a_short_spelling_completes_but_shares_its_long_spellings_row() -> None:
    header, rows = _listing("listdemo serve -")[0]
    assert header == "global options"
    assert ("--config", _row("-c, --config PATH", "Configuration file.")) in rows
    assert ("-c", "") in rows


def test_the_listing_holds_exactly_the_candidates() -> None:
    tree = tree_mod.serialize_app(_app())
    for line in (
        "listdemo serve --",
        "listdemo serve -",
        "listdemo serve --b",
        "listdemo --",
        "listdemo data fetch --",
    ):
        words = line.split(" ")
        candidates, listing = comp.complete_with_listing(tree, words, len(words) - 1)
        assert candidates == comp.complete_from_tree(tree, words, len(words) - 1), line
        assert listing is not None
        assert [cand for _, rows in listing for cand, _ in rows] == candidates, line


def test_only_the_groups_the_typed_word_matches_are_listed() -> None:
    assert _text(_listing("listdemo serve --b")) == [
        "options of listdemo serve",
        _row("-b, --background", "run detached"),
        _row("--browser", "open a browser window"),
    ]
    assert _text(_listing("listdemo serve --con")) == [
        "global options",
        _row("-c, --config PATH", "Configuration file."),
        _row("--console-level LEVEL", "Set console log level (overrides --level)."),
    ]


def test_before_a_command_only_the_global_options_are_listed() -> None:
    assert _text(_listing("listdemo --")) == ["global options", *GLOBAL_ROWS]


def test_a_sub_apps_command_is_named_by_its_whole_path() -> None:
    assert _text(_listing("listdemo data fetch --f"))[-2:] == [
        "options of listdemo data fetch",
        _row("--force", "fetch again"),
    ]


def test_a_used_option_drops_out_of_the_listing() -> None:
    assert "--port PORT" not in "\n".join(_text(_listing("listdemo serve --port 1 --")))


def test_a_row_wider_than_the_terminal_is_cut_with_an_ellipsis() -> None:
    lines = _text(_listing("listdemo serve --", width=50))
    assert all(len(line) <= 49 for line in lines)
    assert _row("-b, --background", "run detached") in lines
    assert _row("--console-level LEVEL", "Set console log level (overrides --level).")[:48] + "…" in lines


def test_a_config_on_the_line_adds_its_keys_to_the_commands_group(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text("layers: 3\nepochs: 5\n")
    text = _text(_listing(f"listdemo train {config} --"))
    assert text[text.index("options of listdemo train") :] == [
        "options of listdemo train",
        _row("--epochs"),
        _row("--layers LAYERS", "how many layers"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "listdemo ",  # command names
        "listdemo se",  # a command name being typed
        "listdemo download ",  # a positional's values beside the options
        "listdemo train ",  # a script command's config files beside the options
        "listdemo serve --port ",  # an option's value
        "listdemo serve --level ",  # a global option's value
        "listdemo serve --port",  # a single match: the shell completes it
    ],
)
def test_no_listing_where_the_list_is_not_options_alone(line: str) -> None:
    words = line.split(" ")
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1)
    assert listing is None


# ---------------------------------------------------------------- the wire --------------------------------------


def test_the_wire_marks_each_header_and_gives_each_candidate_its_row() -> None:
    words = "listdemo serve --b".split(" ")
    candidates, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, 2)
    assert comp.wire_lines(candidates, listing) == [
        "\x1foptions of listdemo serve",
        "--background\t" + _row("-b, --background", "run detached"),
        "--browser\t" + _row("--browser", "open a browser window"),
    ]


def test_without_a_listing_the_wire_is_one_escaped_candidate_per_line() -> None:
    assert comp.wire_lines(["Raidar Base Model", "<name>"], None) == ["Raidar\\ Base\\ Model", "<name>"]


def test_a_tab_in_a_help_text_never_reaches_the_wire() -> None:
    lines = comp.wire_lines(["--x"], [("options of x", [("--x", "  --x  a\tb")])])
    assert lines == ["\x1foptions of x", "--x\t  --x  a b"]


@pytest.mark.parametrize(
    ("value", "style"),
    [(None, "grouped"), ("", "grouped"), ("grouped", "grouped"), ("plain", "plain"), ("x", "grouped")],
)
def test_the_listing_style_is_read_from_its_variable(value: str, style: str) -> None:
    assert comp.completion_list_style(value) == style


# ------------------------------------------------------- liquifai-complete --------------------------------------


def _complete(line: str, **env: str) -> str:
    tree_mod.write_cache(_app())
    full = {k: v for k, v in os.environ.items() if k != "LIQUIFAI_COMPLETE_WIRE"}
    full.update({"COMP_LINE": line, "COMP_POINT": str(len(line)), "COLUMNS": "120", **env})
    result = subprocess.run(
        [sys.executable, "-m", "liquifai._fast_complete", "listdemo"],
        env=full,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout


TODAY = "\n".join(comp.complete_from_tree(tree_mod.serialize_app(_app()), ["listdemo", "serve", "--"], 2)) + "\n"


def test_a_wrapper_that_reads_no_listing_gets_todays_output() -> None:
    assert _complete("listdemo serve --") == TODAY


def test_plain_gives_todays_output_to_a_wrapper_that_reads_the_listing() -> None:
    assert _complete("listdemo serve --", LIQUIFAI_COMPLETE_WIRE="2", LIQUIFAI_COMPLETION_LIST="plain") == TODAY


def test_a_wrapper_that_reads_the_listing_gets_it() -> None:
    out = _complete("listdemo serve --b", LIQUIFAI_COMPLETE_WIRE="2")
    assert out.splitlines() == [
        "\x1foptions of listdemo serve",
        "--background\t" + _row("-b, --background", "run detached"),
        "--browser\t" + _row("--browser", "open a browser window"),
    ]


def test_the_listing_fits_the_terminal_the_wrapper_reports() -> None:
    out = _complete("listdemo serve --", LIQUIFAI_COMPLETE_WIRE="2", COLUMNS="50")
    assert max(len(line.split("\t", 1)[-1]) for line in out.splitlines()) <= 49


def test_a_width_that_is_no_number_means_80_columns() -> None:
    out = _complete("listdemo serve --", LIQUIFAI_COMPLETE_WIRE="2", COLUMNS="wide")
    rows = [line.split("\t", 1)[-1] for line in out.splitlines()]
    assert max(len(row) for row in rows) <= 79
    assert any(row.endswith("…") for row in rows)
