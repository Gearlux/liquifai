"""A command that RUNS a config of its own choosing names it — ``@command(config_files=...)`` — and liquifai reads
the file for its scopes.

``--scope`` activates a scope of the APP's own config. A command whose real config is one it starts itself (a runner
handing a YAML to another tool) had no way to receive one: ``tool compile run2 --scope onnx`` activated ``onnx`` on a
config the app does not have, said nothing, and ran without it. Naming the files hands the typed ``--scope`` values to
the command's ``scopes`` parameter, lists the files' scopes in ``--help``, offers them on ``--scope <TAB>`` and refuses
one a file does not declare.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from liquifai import LiquifyApp
from liquifai.completion.engine import complete
from liquifai.completion.tree import CACHE_VERSION, refresh_value_caches, serialize_app
from liquifai.exceptions import CommandDefinitionError

COMPILE = "backend: torchscript\nonnx: !scope:onnx\n  backend: onnx\nfp16: !scope:fp16\n  half: true\n"
PICTURE_A = "size: 1\nval: !scope:val\n  size: 2\noriginal: !scope:original\n  size: 3\n"
PICTURE_B = "size: 1\nval: !scope:val\n  size: 2\nmode: !scope:mode=fast\n  size: 4\n"


@pytest.fixture
def configs(tmp_path: Path) -> Path:
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "compile.yaml").write_text(COMPILE)
    (folder / "a.yaml").write_text(PICTURE_A)
    (folder / "b.yaml").write_text(PICTURE_B)
    return folder


def _app(configs: Path) -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    app = LiquifyApp(name="probe", description="config-scope probe", strict_flags=True)
    calls: List[Dict[str, Any]] = []

    @app.command(positionals=["run"], config_files=lambda: [configs / "compile.yaml"])
    def compile(run: str = "", scopes: Optional[List[str]] = None, port: int = 0) -> None:
        """Compile a run.

        Args:
            run: The run folder.
            scopes: The scopes to pass on.
            port: A port.
        """
        calls.append({"run": run, "scopes": scopes, "port": port})

    def picture_files(typed: Dict[str, str]) -> List[Path]:
        words = (typed.get("options") or "a,b").split(",")
        return [configs / f"{word}.yaml" for word in words]

    @app.command(positionals=["split", "options"], config_files=picture_files)
    def pictures(split: str = "all", options: str = "", scopes: Optional[List[str]] = None) -> None:
        """Make pictures."""
        calls.append({"options": options, "scopes": scopes})

    def opaque(typed: Dict[str, str]) -> List[Path]:
        raise KeyError(typed.get("name", ""))

    @app.command(positionals=["name"], config_files=opaque)
    def guarded(name: str = "", scopes: Optional[List[str]] = None) -> None:
        """A command whose provider refuses a word: the command decides."""
        calls.append({"name": name, "scopes": scopes})

    @app.command()
    def plain(port: int = 0) -> None:
        """A command with no config of its own."""
        calls.append({"port": port})

    return app, calls


def _run(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


def _help(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch, capsys: Any) -> str:
    monkeypatch.setenv("COLUMNS", "200")
    _run(app, argv, monkeypatch)
    return str(capsys.readouterr().out)


# --- the typed scopes reach the command ------------------------------------------------------------------------------


class TestTheTypedScopesReachTheCommand:
    def test_one_scope(self, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app(configs)
        _run(app, ["compile", "run2", "--scope", "onnx"], monkeypatch)
        assert calls[-1] == {"run": "run2", "scopes": ["onnx"], "port": 0}

    def test_the_short_spelling_a_comma_list_and_a_repeat_keep_the_typed_order(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, calls = _app(configs)
        _run(app, ["compile", "run2", "-s", "fp16", "--scope", "onnx"], monkeypatch)
        assert calls[-1]["scopes"] == ["fp16", "onnx"]
        _run(app, ["compile", "run2", "-s", "onnx,fp16"], monkeypatch)
        assert calls[-1]["scopes"] == ["onnx", "fp16"]

    def test_none_typed_leaves_the_parameter_its_default(self, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app(configs)
        _run(app, ["compile", "run2", "--port", "7"], monkeypatch)
        assert calls[-1] == {"run": "run2", "scopes": None, "port": 7}

    def test_a_keyed_scope_is_named_key_equals_value(self, configs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app(configs)
        _run(app, ["pictures", "all", "b", "--scope", "mode=fast"], monkeypatch)
        assert calls[-1] == {"options": "b", "scopes": ["mode=fast"]}

    def test_the_scopes_are_not_activated_on_the_apps_own_config(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        own = tmp_path / "own.yaml"
        own.write_text("port: 1\nonnx: !scope:onnx\n  port: 2\n")
        app, calls = _app(configs)
        _run(app, ["compile", "run2", "-c", str(own), "--scope", "onnx"], monkeypatch)
        assert calls[-1] == {"run": "run2", "scopes": ["onnx"], "port": 1}

    def test_a_command_without_config_files_still_activates_them_on_the_apps_config(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        own = tmp_path / "own.yaml"
        own.write_text("port: 1\nonnx: !scope:onnx\n  port: 2\n")
        app, calls = _app(configs)
        _run(app, ["plain", "-c", str(own), "--scope", "onnx"], monkeypatch)
        assert calls[-1] == {"port": 2}


# --- a scope the file does not declare is refused before the command runs ---------------------------------------------


class TestTheRefusal:
    def test_a_scope_the_file_lacks_is_one_error_line_naming_the_file(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        app, calls = _app(configs)
        monkeypatch.chdir(configs.parent)
        with pytest.raises(SystemExit) as stopped:
            _run(app, ["compile", "run2", "--scope", "tensorrt"], monkeypatch)
        assert stopped.value.code == 1 and calls == []
        out = " ".join(capsys.readouterr().out.split())
        assert "Error: configs/compile.yaml has no scope 'tensorrt' (its scopes: fp16, onnx)" in out

    def test_every_file_the_typed_words_name_must_declare_it(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """``pictures all a,b --scope original``: b runs too, and b has no ``original``."""
        app, calls = _app(configs)
        monkeypatch.chdir(configs.parent)
        with pytest.raises(SystemExit):
            _run(app, ["pictures", "all", "a,b", "--scope", "original"], monkeypatch)
        out = " ".join(capsys.readouterr().out.split())
        assert "configs/b.yaml has no scope 'original' (its scopes: mode=fast, val)" in out and calls == []
        _run(app, ["pictures", "all", "a", "--scope", "original"], monkeypatch)
        assert calls[-1] == {"options": "a", "scopes": ["original"]}

    def test_a_provider_that_refuses_the_words_leaves_the_command_to_answer(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, calls = _app(configs)
        _run(app, ["guarded", "nope", "--scope", "x"], monkeypatch)
        assert calls[-1] == {"name": "nope", "scopes": ["x"]}


# --- the declaration is checked when the command is decorated --------------------------------------------------------


class TestTheDeclaration:
    def test_a_provider_taking_two_arguments_is_refused(self) -> None:
        app = LiquifyApp(name="probe")
        takes_two: Any = lambda typed, extra: []  # the wrong shape on purpose: Any lets the type checker pass it
        with pytest.raises(CommandDefinitionError, match="no argument or one"):

            @app.command(config_files=takes_two)
            def compile(scopes: Optional[List[str]] = None) -> None:
                pass

    def test_a_command_without_a_scopes_parameter_is_refused(self) -> None:
        app = LiquifyApp(name="probe")
        with pytest.raises(CommandDefinitionError, match="'scopes' parameter"):

            @app.command(config_files=lambda: ["compile.yaml"])
            def compile(run: str = "") -> None:
                pass


# --- help ------------------------------------------------------------------------------------------------------------


class TestTheHelp:
    def test_help_lists_the_files_scopes_and_hides_the_parameter(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        app, _ = _app(configs)
        monkeypatch.chdir(configs.parent)
        out = _help(app, ["compile", "--help"], monkeypatch, capsys)
        assert "Scopes" in out and "configs/compile.yaml" in out
        assert "onnx" in out and "fp16" in out
        assert "--scopes" not in out, "the parameter is filled from --scope, never advertised as its own flag"
        assert "--port" in out

    def test_help_reads_the_files_of_the_words_typed(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        app, _ = _app(configs)
        only_a = _help(app, ["pictures", "all", "a", "--help"], monkeypatch, capsys)
        assert "original" in only_a and "mode=fast" not in only_a
        both = _help(app, ["pictures", "--help"], monkeypatch, capsys)
        assert "original" in both and "mode=fast" in both and "val" in both

    def test_the_docs_layout_lists_them_too(self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        app, _ = _app(configs)
        out = _help(app, ["compile", "--docs"], monkeypatch, capsys)
        assert "onnx" in out and "--scopes" not in out

    def test_a_failing_provider_still_renders_the_help(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        app, _ = _app(configs)
        out = _help(app, ["guarded", "nope", "--help"], monkeypatch, capsys)
        assert "scopes unavailable" in out and "nope" in out

    def test_a_command_without_config_files_shows_no_block(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        app, _ = _app(configs)
        assert "of the config this command runs" not in _help(app, ["plain", "--help"], monkeypatch, capsys)


# --- completion ------------------------------------------------------------------------------------------------------


class TestTheCompletion:
    def test_the_tree_records_the_commands_scope_cache(self, configs: Path) -> None:
        app, _ = _app(configs)
        tree = serialize_app(app)
        assert set(tree["scope_completions"]) == {"compile", "pictures", "guarded"}
        assert "scopes" not in " ".join(tree["signature_flags"]["compile"])
        assert CACHE_VERSION >= 7

    def test_tab_after_scope_offers_the_files_scopes_for_the_words_typed(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        app, _ = _app(configs)
        assert complete(app, ["probe", "compile", "run2", "--scope", ""], 4) == []
        refresh_value_caches(app)
        assert complete(app, ["probe", "compile", "run2", "--scope", ""], 4) == ["fp16", "onnx"]
        assert complete(app, ["probe", "compile", "-s", "o"], 3) == ["onnx"]
        # none typed: the provider's defaults (a and b)
        assert complete(app, ["probe", "pictures", "--scope", ""], 3) == ["mode=fast", "original", "val"]

    def test_tab_for_typed_words_the_refresh_did_not_enumerate_self_heals(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``options`` has no value provider here, so ``pictures all a`` is never enumerated: the TAB asks for it in
        the background and the refresh it would start answers for exactly those words."""
        from liquifai.completion.engine import complete_from_tree
        from liquifai.completion.tree import refresh_one

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        app, _ = _app(configs)
        asked: List[Any] = []
        line = ["probe", "pictures", "all", "a", "--scope", ""]
        assert complete_from_tree(serialize_app(app), line, 5, lazy_refresh=lambda *a: asked.append(a)) == []
        assert asked and asked[0][1] == {"split": "all", "options": "a"}
        refresh_one(app, *asked[0])
        assert complete(app, line, 5) == ["original", "val"]

    def test_a_command_without_config_files_offers_none(
        self, configs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        app, _ = _app(configs)
        refresh_value_caches(app)
        assert complete(app, ["probe", "plain", "--scope", ""], 3) == []


# --- help text keeps its brackets ------------------------------------------------------------------------------------


def test_help_text_keeps_square_brackets(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """Help text is printed as plain text: ``[key=value, ...]`` once read as a style tag and vanished."""
    app = LiquifyApp(name="probe", description="Runs [stage] steps.")

    @app.command(positionals=["option"])
    def train(option: str = "", settings: Optional[List[str]] = None) -> None:
        """Train [one] option.

        Args:
            option: The option ([a] or [b]).
            settings: Extra arguments, a list ("[epochs=1, batch=2]").
        """

    out = _help(app, ["train", "--help"], monkeypatch, capsys)
    assert "Runs [stage] steps." in out
    assert "Train [one] option." in out
    assert "The option ([a] or [b])." in out
    assert 'a list ("[epochs=1, batch=2]")' in out
    docs = _help(app, ["train", "--docs"], monkeypatch, capsys)
    assert 'a list ("[epochs=1, batch=2]")' in docs
    index = _help(app, ["--help"], monkeypatch, capsys)
    assert "Train [one] option." in index
