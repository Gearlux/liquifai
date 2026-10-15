"""TAB values beyond a positional slot, and the same values in ``--help``.

* A ``completions=`` entry naming an OPTION (not a positional) completes its value: ``review a --library <TAB>``. Its
  provider takes nothing, or the positionals typed so far.
* A word after a comma completes the next item of a list: ``pictures all a,<TAB>`` → ``a,b  a,c``, the words already
  in the list left out — positionals, option values and ``--scope`` alike.
* ``--help`` shows the values TAB would offer, read from the same cache (never by running a provider: one may query a
  server). Nothing is shown for a slot whose cache is not filled yet.
"""

import sys
from pathlib import Path
from typing import Any, List, Optional

import pytest

from liquifai import LiquifyApp
from liquifai.completion.engine import complete
from liquifai.completion.tree import CACHE_VERSION, refresh_value_caches, serialize_app
from liquifai.exceptions import CommandDefinitionError


def _app(tmp_path: Path) -> LiquifyApp:
    app = LiquifyApp(name="probe", description="value probe", strict_flags=True)
    compile_yaml = tmp_path / "compile.yaml"
    compile_yaml.write_text("b: 1\nonnx: !scope:onnx\n  b: 2\nfp16: !scope:fp16\n  b: 3\n")

    @app.command(
        positionals=["option", "set"],
        completions={
            "option": lambda: ["a", "b", "c"],
            "set": lambda typed: [f"{typed['option']}-tier1", f"{typed['option']}-tier2"],
            "library": lambda: ["unofficial", "complete"],
            "mode": lambda typed: [f"{typed.get('option', 'none')}-fast"],
        },
    )
    def review(option: str = "", set: str = "", library: str = "", mode: str = "", port: int = 0) -> None:
        """Review an option."""

    @app.command(positionals=["run"], config_files=lambda: [compile_yaml])
    def compile(run: str = "", scopes: Optional[List[str]] = None) -> None:
        """Compile a run."""

    return app


def _tab(app: LiquifyApp, *words: str) -> List[str]:
    line = ["probe", *words]
    return [value for value in complete(app, line, len(line) - 1) if not value.startswith("-")]


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LiquifyApp:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return _app(tmp_path)


# --- an option's value ------------------------------------------------------------------------------------------------


class TestAnOptionsValue:
    def test_a_static_provider_completes_the_value_after_its_flag(self, app: LiquifyApp) -> None:
        assert _tab(app, "review", "a", "--library", "") == []
        refresh_value_caches(app)
        assert _tab(app, "review", "a", "--library", "") == ["unofficial", "complete"]
        assert _tab(app, "review", "--library", "c") == ["complete"]

    def test_a_provider_taking_the_typed_words_answers_for_them(self, app: LiquifyApp) -> None:
        refresh_value_caches(app)
        assert _tab(app, "review", "b", "--mode", "") == ["b-fast"]
        assert _tab(app, "review", "--mode", "") == ["none-fast"]

    def test_an_option_without_a_provider_stays_silent(self, app: LiquifyApp) -> None:
        refresh_value_caches(app)
        assert complete(app, ["probe", "review", "a", "--port", ""], 4) == []

    def test_the_tree_lists_the_options_apart_from_the_positionals(self, app: LiquifyApp) -> None:
        tree = serialize_app(app)
        assert set(tree["flag_completions"]["review"]) == {"library", "mode"}
        assert set(tree["positional_completions"]["review"]) == {"option", "set"}
        assert CACHE_VERSION >= 8

    def test_a_provider_for_no_parameter_is_refused_when_the_command_is_decorated(self) -> None:
        app = LiquifyApp(name="probe")
        with pytest.raises(CommandDefinitionError, match="'libary' names no parameter"):

            @app.command(completions={"libary": lambda: ["x"]})
            def review(library: str = "") -> None:
                pass


# --- a word after a comma ---------------------------------------------------------------------------------------------


class TestAListsNextWord:
    def test_a_positional_list_completes_its_next_word(self, app: LiquifyApp) -> None:
        refresh_value_caches(app)
        assert _tab(app, "review", "a,") == ["a,b", "a,c"]
        assert _tab(app, "review", "a,c") == ["a,c"]
        assert _tab(app, "review", "a,b,") == ["a,b,c"]
        assert _tab(app, "review", "a,b,c,") == []

    def test_an_options_value_and_a_scope_list_too(self, app: LiquifyApp) -> None:
        refresh_value_caches(app)
        assert _tab(app, "review", "a", "--library", "unofficial,") == ["unofficial,complete"]
        assert _tab(app, "compile", "run2", "--scope", "onnx,") == ["onnx,fp16"]


# --- the values in --help ---------------------------------------------------------------------------------------------


def _help(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> str:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()
    return str(capsys.readouterr().out)


class TestTheHelp:
    def test_help_shows_the_cached_values_of_each_slot(
        self, app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        refresh_value_caches(app)
        out = _help(app, ["review", "--help"], monkeypatch, capsys)
        assert "Values" in out
        assert "<option>" in out and "a | b | c" in out
        assert "--library" in out and "unofficial | complete" in out
        assert "a-tier1" not in out, "a slot depending on a word not typed shows nothing"

    def test_help_shows_the_values_for_the_words_typed(
        self, app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        refresh_value_caches(app)
        out = _help(app, ["review", "b", "--help"], monkeypatch, capsys)
        assert "b-tier1 | b-tier2" in out and "b-fast" in out

    def test_nothing_cached_shows_how_to_fill_the_cache(
        self, app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        out = _help(app, ["review", "--help"], monkeypatch, capsys)
        assert "probe --refresh-completions" in out and "a | b | c" not in out

    def test_a_long_list_is_cut_with_a_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        app = LiquifyApp(name="probe")
        names = [f"run{i:02d}" for i in range(30)]

        @app.command(positionals=["run"], completions={"run": lambda: names})
        def show(run: str = "") -> None:
            """Show a run."""

        refresh_value_caches(app)
        out = _help(app, ["show", "--help"], monkeypatch, capsys)
        assert "run00 | run01" in out and "run19" in out and "run20" not in out and "(+10 more)" in out

    def test_a_command_without_providers_shows_no_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        app = LiquifyApp(name="probe")

        @app.command()
        def plain(port: int = 0) -> None:
            """No values."""

        assert "Values" not in _help(app, ["plain", "--help"], monkeypatch, capsys)
