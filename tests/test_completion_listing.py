"""The grouped listing a second TAB shows: commands, arguments, the command's own options, the global options last.

``complete_with_listing`` returns the candidates ``complete_from_tree`` returns, plus — at an option position — the
listing the shell prints instead of its own alphabetical grid. ``wire_lines`` is what ``liquifai-complete`` prints
for a wrapper that reads it; ``LIQUIFAI_COMPLETION_LIST=plain`` keeps today's output byte for byte. The shells' side
(bash printing the list, zsh's groups) is driven in a real terminal in ``tests/test_completion_shells.py``.
"""

from __future__ import annotations

import os
import re
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


def _next(left: str, doc: str = "") -> str:
    """The row of the argument to type next: marked ``▸``."""
    return "▸ " + _row(left, doc)[2:]


#: A terminal colour code, as the listing writes it around a piece of text.
_SGR = re.compile(r"\x1b\[[0-9;]*m")


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
        """Download one item.

        Args:
            name: the item to download
            path: where it goes
        """

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


def test_the_commands_own_options_come_first_then_the_global_ones_each_with_its_help() -> None:
    assert _text(_listing("listdemo serve --")) == [
        "options of listdemo serve",
        _row("-b, --background", "run detached"),
        _row("--browser", "open a browser window"),
        _row("--label LABEL"),
        _row("--port PORT", "the port to serve on (8787)"),
        "global options",
        *GLOBAL_ROWS,
    ]


def test_a_short_spelling_completes_but_shares_its_long_spellings_row() -> None:
    rows = dict(_listing("listdemo serve -"))["global options"]
    assert ("--config", _row("-c, --config PATH", "Configuration file.")) in rows
    assert ("-c", "") in rows


def test_the_listing_holds_exactly_the_candidates() -> None:
    for line in (
        "listdemo serve --",
        "listdemo serve -",
        "listdemo serve --b",
        "listdemo --",
        "listdemo data fetch --",
        "listdemo ",
        "listdemo download ",
        "listdemo download alpha --",
        "dflt ",
    ):
        words = line.split(" ")
        tree = tree_mod.serialize_app(_default_app() if words[0] == "dflt" else _app())
        candidates, listing = comp.complete_with_listing(tree, words, len(words) - 1)
        assert candidates == comp.complete_from_tree(tree, words, len(words) - 1), line
        assert listing is not None
        assert sorted(cand for _, rows in listing for cand, _ in rows if cand) == sorted(candidates), line


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
    text = _text(_listing("listdemo data fetch --f"))
    assert text[: text.index("global options")] == [
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
    assert text[text.index("options of listdemo train") : text.index("global options")] == [
        "options of listdemo train",
        _row("--epochs"),
        _row("--layers LAYERS", "how many layers (1)"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "listdemo se",  # one command name matches: the shell completes it
        "listdemo serve --port ",  # an option's value
        "listdemo serve --level ",  # a global option's value
        "listdemo serve --port",  # a single match
    ],
)
def test_no_listing_at_a_value_or_a_single_match(line: str) -> None:
    words = line.split(" ")
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1)
    assert listing is None


def _default_app() -> LiquifyApp:
    """An app whose default command takes a positional: a bare prompt offers its commands AND that argument."""
    app = LiquifyApp(name="dflt")

    @app.command("open", default=True, positionals=["workspace"])
    def open_(workspace: str = "", port: int = 8787) -> None:
        """Serve a workspace.

        Args:
            workspace: the workspace file to serve
            port: the port to serve on
        """

    @app.command("stop")
    def stop() -> None:
        """Stop the server."""

    return app


def test_a_bare_prompt_lists_the_commands_then_the_arguments_then_the_options() -> None:
    words = ["dflt", ""]
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_default_app()), words, 1, width=120)
    assert listing is not None
    assert _text(listing) == [
        "commands of dflt",
        _row("open", "Serve a workspace."),
        _row("stop", "Stop the server."),
        "arguments of dflt open",
        _next("<workspace>", "the workspace file to serve"),
        "options of dflt open",
        _row("--port PORT", "the port to serve on (8787)"),
        "global options",
        *GLOBAL_ROWS,
    ]


def test_a_level_without_a_default_command_lists_its_commands_and_sub_apps() -> None:
    assert _text(_listing("listdemo ")) == [
        "commands of listdemo",
        _row("serve", "Serve the workspace."),
        _row("download", "Download one item."),
        _row("train", "Train."),
        _row("data", "data"),
    ]


def test_the_arguments_say_which_are_missing() -> None:
    text = _text(_listing("listdemo download "))
    assert text[: text.index("options of listdemo download")] == [
        "arguments of listdemo download",
        _next("<name>", "the item to download"),
    ]


def test_a_typed_argument_shows_its_value_before_the_options() -> None:
    listing = _listing("listdemo download alpha --")
    assert listing[0] == ("arguments of listdemo download", [("", _row("name", "alpha"))])
    assert [header for header, _ in listing] == [
        "arguments of listdemo download",
        "options of listdemo download",
        "global options",
    ]


def test_an_arguments_cached_values_are_listed_under_it() -> None:
    tree_mod.refresh_value_caches(_app())
    listing = _listing("listdemo download ")
    assert listing[0] == (
        "arguments of listdemo download",
        [("", _next("<name>", "the item to download")), ("alpha", _row("  alpha")), ("beta", _row("  beta"))],
    )


def test_a_script_commands_config_files_are_listed_under_its_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run.yaml").write_text("layers: 3\n")
    (tmp_path / "configs").mkdir()
    listing = _listing("listdemo train ")
    assert listing[0] == (
        "arguments of listdemo train",
        [
            ("", _next("<config>", "the YAML config it runs")),
            ("configs/", _row("  configs/")),
            ("run.yaml", _row("  run.yaml")),
        ],
    )
    assert _listing("listdemo train run.yaml --")[0] == (
        "arguments of listdemo train",
        [("", _row("config", "run.yaml"))],
    )


def test_an_argument_given_as_its_flag_counts_as_given() -> None:
    assert _listing("listdemo download --name=alpha --")[0] == (
        "arguments of listdemo download",
        [("", _row("name", "given as --name"))],
    )


def test_a_value_options_default_follows_its_help_and_a_flag_shows_none() -> None:
    text = _text(_listing("listdemo serve --"))
    assert _row("--port PORT", "the port to serve on (8787)") in text
    assert _row("--browser", "open a browser window") in text  # a bool: no (False)
    assert _row("--label LABEL") in text  # an empty default: nothing to show


def test_only_the_next_argument_is_marked(tmp_path: Path) -> None:
    app = LiquifyApp(name="two")

    @app.command("fetch", positionals=["name", "version"])
    def fetch(name: str = "", version: str = "") -> None:
        """Fetch.

        Args:
            name: the item
            version: its version
        """

    words = ["two", "fetch", ""]
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(app), words, 2, width=120)
    assert listing is not None and listing[0] == (
        "arguments of two fetch",
        [("<name>", _next("<name>", "the item")), ("", _row("<version>", "its version"))],
    )


def _coloured(line: str, width: int = 120) -> comp.Listing:
    words = line.split(" ")
    tree = tree_mod.serialize_app(_default_app() if words[0] == "dflt" else _app())
    _, listing = comp.complete_with_listing(tree, words, len(words) - 1, width=width, colour=True)
    assert listing is not None
    return listing


def test_in_colour_the_text_is_the_same_once_the_colour_codes_are_taken_out() -> None:
    for line in ("dflt ", "listdemo download alpha --", "listdemo serve --"):
        words = line.split(" ")
        plain = comp.complete_with_listing(
            tree_mod.serialize_app(_default_app() if words[0] == "dflt" else _app()), words, len(words) - 1, width=120
        )[1]
        coloured = _coloured(line)
        assert [(_SGR.sub("", h), [(c, _SGR.sub("", r)) for c, r in rows]) for h, rows in coloured] == plain, line


def test_each_kind_of_text_has_its_colour() -> None:
    groups = dict(_coloured("dflt "))
    assert "\x1b[1;36mcommands of dflt\x1b[0m" in groups  # a title: bold cyan
    commands = groups["\x1b[1;36mcommands of dflt\x1b[0m"]
    assert commands[0][1].startswith("  \x1b[34mopen\x1b[0m")  # a command: blue
    assert "\x1b[2mServe a workspace.\x1b[0m" in commands[0][1]  # its help: dim
    arguments = groups["\x1b[1;36marguments of dflt open\x1b[0m"]
    assert arguments[0][1].startswith("\x1b[33m▸ \x1b[0m\x1b[33m<workspace>\x1b[0m")  # missing, next: yellow
    options = groups["\x1b[1;36moptions of dflt open\x1b[0m"]
    assert options[0][1].startswith("  \x1b[1m--port\x1b[0m\x1b[2;36m PORT\x1b[0m")  # option bold, value dim cyan
    typed = dict(_coloured("listdemo download alpha --"))["\x1b[1;36marguments of listdemo download\x1b[0m"]
    assert typed == [("", "  \x1b[32mname\x1b[0m" + " " * (LEFT - 4) + "  \x1b[32malpha\x1b[0m")]  # typed: green


def test_a_coloured_row_is_cut_at_the_terminal_edge_as_a_plain_one() -> None:
    for _, rows in _coloured("listdemo serve --", width=50):
        for _, row in rows:
            assert len(_SGR.sub("", row)) <= 49


def test_for_zsh_a_coloured_row_counts_its_codes_against_the_width() -> None:
    """zsh measures a row's colour codes as characters: counted, a row stays within the terminal for zsh too."""
    words = ["listdemo", "serve", "--"]
    tree = tree_mod.serialize_app(_app())
    _, listing = comp.complete_with_listing(tree, words, 2, width=60, colour=True, codes_count=True)
    assert listing is not None
    rows = [row for _, group in listing for _, row in group]
    assert all(len(row) <= 59 for row in rows)
    assert any(_SGR.sub("", row).endswith("…") for row in rows)


@pytest.mark.parametrize(
    ("setting", "environment", "colour"),
    [
        (None, {"TERM": "xterm-256color"}, True),
        ("auto", {"TERM": "xterm"}, True),
        ("auto", {"TERM": "xterm", "NO_COLOR": "1"}, False),
        ("auto", {"TERM": "dumb"}, False),
        ("auto", {}, False),
        ("never", {"TERM": "xterm"}, False),
        ("always", {"TERM": "dumb", "NO_COLOR": "1"}, True),
        ("purple", {"TERM": "xterm"}, True),  # an unknown word: the default, auto
    ],
)
def test_colour_follows_its_setting_no_color_and_the_terminal(setting: str, environment: dict, colour: bool) -> None:
    assert comp.completion_colour(setting, environment) is colour


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


def test_a_row_that_is_no_candidate_is_marked_on_the_wire() -> None:
    lines = comp.wire_lines(
        ["--x"], [("arguments of x", [("", "  name  alpha")]), ("options of x", [("--x", "  --x")])]
    )
    assert lines == ["\x1farguments of x", "\x1e  name  alpha", "\x1foptions of x", "--x\t  --x"]


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
    full.update(
        {"COMP_LINE": line, "COMP_POINT": str(len(line)), "COLUMNS": "120", "LIQUIFAI_COMPLETION_COLOR": "never"}
    )
    full.update(env)
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


def test_a_wrapper_that_reads_the_listing_gets_it_in_colour_on_a_colour_terminal() -> None:
    out = _complete("listdemo serve --b", LIQUIFAI_COMPLETE_WIRE="2", LIQUIFAI_COMPLETION_COLOR="auto", TERM="xterm")
    assert out.splitlines()[0] == "\x1f\x1b[1;36moptions of listdemo serve\x1b[0m"
    assert out.splitlines()[1].startswith("--background\t  \x1b[1m-b, --background\x1b[0m")
    assert "\x1b" not in _complete(
        "listdemo serve --b", LIQUIFAI_COMPLETE_WIRE="2", NO_COLOR="1", LIQUIFAI_COMPLETION_COLOR="auto"
    )
