"""``--scope <TAB>``, ``-s <TAB>`` and ``--scope=<TAB>`` complete the scopes of the config on the line.

The scopes offered are the ones ``scopes of <config>`` lists on a second TAB: the file's own, then each include's, a
keyed one as ``KEY=VAL``, then the document's ``scope_aliases:`` in the order it merges them (an alias named like a
scope is that scope). Without a config on the line — or with one that cannot be read — nothing is offered, as
for every other value flag that has no candidates.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, List

import pytest

from liquifai import LiquifyApp, _fast_complete
from liquifai import completion as comp
from liquifai.completion import tree as tree_mod
from liquifai.completion.config_help import ConfigScope, read_config_scopes
from liquifai.grammar import SCOPE_VALUE_FLAGS


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LIQUIFAI_NO_LAZY_COMPLETE", "1")


def _write(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text(textwrap.dedent(body))
    return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A config with scopes of its own, a keyed one, and an include's (one declared in both)."""
    _write(
        tmp_path,
        "shared.yaml",
        """\
        count: 1
        quick: !scope:quick
          count: 5
        # A long run.
        long: !scope:long
          count: 500
        scope_aliases:
          thorough: long
        """,
    )
    return _write(
        tmp_path,
        "run.yaml",
        """\
        include: shared.yaml
        # A quick run.
        quick: !scope:quick
          count: 20
        debug: !scope:debug
          count: 2
        task_a: !scope:task=a
          count: 3
        scope_aliases:
          fast: [quick]
          both: [quick, debug]
          quick: [debug]
        """,
    )


def _app() -> LiquifyApp:
    app = LiquifyApp(name="scopedemo")

    @app.script_command("run")
    def run(count: int = 0) -> None:
        """Run it."""

    @app.command("show")
    def show(count: int = 0) -> None:
        """Show it."""

    return app


def _tab(line: str) -> List[str]:
    words = line.split(" ")
    return comp.complete_from_tree(tree_mod.serialize_app(_app()), words, len(words) - 1)


def test_the_scope_flags_are_the_ones_the_grammar_declares_a_scope_value_for() -> None:
    assert SCOPE_VALUE_FLAGS == {"--scope", "-s"}


def test_the_scopes_of_a_config_are_its_own_then_its_includes(config: Path) -> None:
    assert read_config_scopes(config) == [
        ConfigScope("quick", "A quick run."),
        ConfigScope("debug", ""),
        ConfigScope("task=a", ""),
        ConfigScope("long", "A long run."),
        ConfigScope("thorough", "Alias of long."),  # the aliases in the order the document merges them
        ConfigScope("fast", "Alias of quick."),
        ConfigScope("both", "Alias of quick, debug."),
    ]


@pytest.mark.parametrize("flag", ["-s", "--scope"])
def test_every_scope_of_the_config_is_offered(config: Path, flag: str) -> None:
    assert _tab(f"scopedemo run {config} {flag} ") == ["quick", "debug", "task=a", "long", "thorough", "fast", "both"]


def test_the_scopes_matching_what_is_typed_are_offered(config: Path) -> None:
    assert _tab(f"scopedemo run {config} -s q") == ["quick"]
    assert _tab(f"scopedemo run {config} --scope t") == ["task=a", "thorough"]
    assert _tab(f"scopedemo run {config} -s sc") == []


def test_a_scope_already_typed_is_not_offered_again(config: Path) -> None:
    assert _tab(f"scopedemo run {config} -s quick --scope ") == ["debug", "task=a", "long", "thorough", "fast", "both"]
    assert _tab(f"scopedemo run {config} --scope=quick,long -s=fast -s t") == ["task=a", "thorough"]


def test_after_a_comma_the_lists_next_scope_is_offered(config: Path) -> None:
    assert _tab(f"scopedemo run {config} -s quick,d") == ["quick,debug"]
    assert _tab(f"scopedemo run {config} -s quick,l") == ["quick,long"]
    assert _tab(f"scopedemo run {config} -s quick,t") == ["quick,task=a", "quick,thorough"]


@pytest.mark.parametrize("flag", ["-s", "--scope"])
def test_the_equals_spelling_offers_the_scopes_with_their_flag(config: Path, flag: str) -> None:
    assert _tab(f"scopedemo run {config} {flag}=") == [
        f"{flag}={scope}" for scope in ["quick", "debug", "task=a", "long", "thorough", "fast", "both"]
    ]
    assert _tab(f"scopedemo run {config} {flag}=t") == [f"{flag}=task=a", f"{flag}=thorough"]
    assert _tab(f"scopedemo run {config} {flag}=quick,l") == [f"{flag}=quick,long"]
    assert _tab(f"scopedemo run {flag}=") == []


def test_the_listing_shows_the_aliases_after_the_scopes(config: Path) -> None:
    words = f"scopedemo run {config} --".split(" ")
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1, width=120)
    assert listing is not None
    rows = [" ".join(display.split()) for _, display in dict(listing)[f"scopes of {config}"] if display]
    assert rows[-3:] == ["-s thorough Alias of long.", "-s fast Alias of quick.", "-s both Alias of quick, debug."]


def test_a_config_given_by_flag_offers_its_scopes_for_any_command(config: Path) -> None:
    assert _tab(f"scopedemo show --config {config} -s d") == ["debug"]
    assert _tab(f"scopedemo run -c {config} -s d") == ["debug"]
    assert _tab(f"scopedemo show --config={config} -s d") == ["debug"]
    assert _tab(f"scopedemo show -c={config} --scope=d") == ["--scope=debug"]


@pytest.mark.parametrize("flag", ["--config", "-c", "--log-dir", "--save-config"])
def test_a_path_options_equals_spelling_offers_files_with_the_option(
    config: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.chdir(config.parent)
    assert _tab(f"scopedemo show {flag}=ru") == [f"{flag}=run.yaml"]
    assert _tab(f"scopedemo show {flag} ru") == ["run.yaml"]


def test_a_config_found_by_its_bare_name_offers_its_scopes(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(config.parent)
    assert _tab("scopedemo run run -s d") == ["debug"]


def test_without_a_config_nothing_is_offered() -> None:
    assert _tab("scopedemo run -s ") == []
    assert _tab("scopedemo show -s ") == []


def test_a_missing_config_offers_nothing(tmp_path: Path) -> None:
    assert _tab(f"scopedemo run {tmp_path / 'absent.yaml'} -s ") == []


def test_a_config_that_cannot_be_read_offers_nothing(tmp_path: Path) -> None:
    broken = _write(tmp_path, "broken.yaml", "quick: !scope:quick\n  count: [1\n")
    assert _tab(f"scopedemo run {broken} -s ") == []


def test_the_other_value_flags_still_offer_nothing(config: Path) -> None:
    assert _tab(f"scopedemo run {config} --level ") == []


def test_a_second_tab_at_a_scope_shows_no_listing(config: Path) -> None:
    words = f"scopedemo run {config} -s ".split(" ")
    candidates, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1)
    assert candidates == ["quick", "debug", "task=a", "long", "thorough", "fast", "both"]
    assert listing is None


def test_liquifai_complete_hands_bash_the_part_after_a_keyed_scopes_equals_sign(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    tree_mod.write_cache(_app())
    monkeypatch.setattr("sys.argv", ["liquifai-complete", "scopedemo"])
    line = f"scopedemo run {config} -s task="
    monkeypatch.setenv("COMP_LINE", line)
    monkeypatch.setenv("COMP_POINT", str(len(line)))
    _fast_complete.main()
    assert capsys.readouterr().out.splitlines() == ["a"]
    line = f"scopedemo run {config} --scope=f"
    monkeypatch.setenv("COMP_LINE", line)
    monkeypatch.setenv("COMP_POINT", str(len(line)))
    _fast_complete.main()
    assert capsys.readouterr().out.splitlines() == ["fast"]
    line = f"scopedemo run {config} -s d"
    monkeypatch.setenv("COMP_LINE", line)
    monkeypatch.setenv("COMP_POINT", str(len(line)))
    _fast_complete.main()
    assert capsys.readouterr().out.splitlines() == ["debug"]
