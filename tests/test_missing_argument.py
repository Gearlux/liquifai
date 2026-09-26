"""A required argument the user left out is a clean ``Error:`` line, never a traceback.

Measured before this existed, on the pypeek example (``versions`` takes a required ``package``)::

    $ pypeek versions
    Traceback (most recent call last):
      ...
    TypeError: pypeek_versions() missing 1 required keyword-only argument: 'package'

The same happened to every ``@command`` whose function has a parameter without a default. The CLI
failure contract promises one error line and exit 1 for a user mistake; a bare ``TypeError`` fell
into its "any other exception is a bug" row instead.

The check runs AFTER dependency injection, so an argument is only "missing" when neither the
command line, nor the config file, nor DI supplied it — a config ``package:`` key and an injected
``@configurable`` parameter must keep working exactly as before.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from confluid import configurable

from liquifai import LiquifyApp
from liquifai.exceptions import LiquifaiError, MissingArgumentError


def _run(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", [app.name, *argv])
    app.run()


def _error_line(argv: List[str], app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> str:
    """Run ``argv`` expecting the clean failure; return the console text with Rich's wrapping undone."""
    with pytest.raises(SystemExit) as exit_info:
        _run(app, argv, monkeypatch)
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    return " ".join(captured.out.split())


def _command_app() -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    app = LiquifyApp(name="probe")
    calls: List[Dict[str, Any]] = []

    @app.command("show", positionals=["name"])
    def show(name: str) -> None:
        calls.append({"name": name})

    @app.command("tag")
    def tag(label: str, force: bool = False) -> None:
        calls.append({"label": label, "force": force})

    @app.command("passthrough")
    def passthrough(**extra: Any) -> None:
        calls.append(dict(extra))

    return app, calls


def _operation_app() -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    """The pypeek shape: operations with an injected ``conn`` and keyword-only required parameters."""
    app = LiquifyApp(name="peek")
    calls: List[Dict[str, Any]] = []

    @app.operation()
    def peek_versions(conn: Any, *, package: str, limit: int = 10) -> Dict[str, Any]:
        calls.append({"package": package, "limit": limit})
        return {}

    @app.operation()
    def peek_files(conn: Any, *, package: str, version: str) -> Dict[str, Any]:
        calls.append({"package": package, "version": version})
        return {}

    app.set_context_factory(lambda: object())
    app.build_commands()
    return app, calls


def test_the_exception_is_a_liquifai_error_and_a_type_error() -> None:
    """Dual inheritance: `except TypeError` — what Python raised before — still catches it."""
    assert issubclass(MissingArgumentError, LiquifaiError)
    assert issubclass(MissingArgumentError, TypeError)


class TestCommand:
    def test_a_missing_positional_is_one_error_line_and_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app, calls = _command_app()
        out = _error_line(["show"], app, monkeypatch, capsys)
        assert (
            "Error: probe show: missing required argument 'name' — pass it as `probe show <name>` "
            "or `--name <value>`." in out
        )
        assert calls == []

    def test_a_missing_flag_only_argument_names_only_the_flag_form(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`label` is not a positional, so `probe tag <label>` would be a wrong instruction."""
        app, calls = _command_app()
        out = _error_line(["tag"], app, monkeypatch, capsys)
        assert "Error: probe tag: missing required argument 'label' — pass it as `--label <value>`." in out

    def test_under_debug_the_exception_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`-d` is the CLI failure contract's traceback switch, not a special case of this check."""
        app, calls = _command_app()
        with pytest.raises(MissingArgumentError, match="missing required argument 'name'"):
            _run(app, ["-d", "show"], monkeypatch)

    def test_a_stray_word_before_the_command_ends_in_the_same_clean_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The reported `pypeek confluid versions`: the stray word is ignored with a warning (the
        permissive default), and the command then lacks its argument — which must read as an error
        telling the user where the value goes, not as a crash."""
        app, calls = _command_app()
        out = _error_line(["foo", "show"], app, monkeypatch, capsys)
        assert "missing required argument 'name' — pass it as `probe show <name>`" in out

    @pytest.mark.parametrize("argv", [["show", "foo"], ["show", "--name", "foo"]])
    def test_a_supplied_argument_still_runs(self, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _command_app()
        _run(app, argv, monkeypatch)
        assert calls == [{"name": "foo"}]

    def test_the_config_file_can_supply_the_argument(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Checked AFTER DI: a config key named like the parameter is a supplied argument."""
        config = tmp_path / "probe.yaml"
        config.write_text("label: from-config\n")
        app, calls = _command_app()
        _run(app, ["-c", str(config), "tag"], monkeypatch)
        assert calls == [{"label": "from-config", "force": False}]

    def test_a_var_keyword_parameter_is_never_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _command_app()
        _run(app, ["passthrough"], monkeypatch)
        assert calls == [{}]

    def test_an_injected_configurable_is_never_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A required `@configurable`-typed parameter is built by DI, not typed by the user."""

        @configurable
        class MissingArgProbeJob:
            def __init__(self, label: str = "demo") -> None:
                self.label = label

        app = LiquifyApp(name="probe")
        seen: List[str] = []

        @app.script_command()
        def run(job: MissingArgProbeJob) -> None:
            seen.append(job.label)

        _run(app, ["run"], monkeypatch)
        assert seen == ["demo"]

    def test_a_command_in_a_group_names_its_full_path(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = LiquifyApp(name="root")
        group = LiquifyApp(name="dataset")

        @group.command("pull", positionals=["name"])
        def pull(name: str) -> None:
            pass

        root.add_app(group, aliases=["ds"])
        out = _error_line(["ds", "pull"], root, monkeypatch, capsys)
        assert "root dataset pull: missing required argument 'name' — pass it as `root dataset pull <name>`" in out


class TestCommandPath:
    def test_an_alias_is_skipped_so_the_canonical_group_is_named(self) -> None:
        """The search passes `a` (an alias of the non-matching `alpha`) and finds `pull` under `beta`."""
        root, alpha, beta = LiquifyApp(name="root"), LiquifyApp(name="alpha"), LiquifyApp(name="beta")

        @beta.command("pull")
        def pull(name: str) -> None:
            pass

        root.add_app(alpha, aliases=["a"])
        root.add_app(beta, aliases=["b"])
        assert root._command_path(pull) == ["root", "beta", "pull"]

    def test_an_unregistered_function_is_named_by_the_app_and_its_function_name(self) -> None:
        """An embedding host may dispatch a function it never registered; the message still names it."""
        app = LiquifyApp(name="probe")

        def helper(target: str) -> None:
            pass

        assert app._command_path(helper) == []
        with pytest.raises(MissingArgumentError) as exc:
            app._check_missing_arguments(helper, {})
        assert str(exc.value) == "probe helper: missing required argument 'target' — pass it as `--target <value>`."


class TestOperation:
    def test_a_missing_keyword_only_argument_names_the_cli_verb(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The reported case: the message names `peek versions`, not the generated `peek_versions_cmd`."""
        app, calls = _operation_app()
        out = _error_line(["versions"], app, monkeypatch, capsys)
        assert (
            "Error: peek versions: missing required argument 'package' — pass it as "
            "`peek versions <package>` or `--package <value>`." in out
        )
        assert calls == []

    def test_two_missing_arguments_are_reported_together(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app, calls = _operation_app()
        out = _error_line(["files"], app, monkeypatch, capsys)
        assert (
            "Error: peek files: missing required arguments 'package', 'version' — pass them as "
            "`peek files <package> <version>` or `--package <value> --version <value>`." in out
        )

    def test_only_the_argument_still_missing_is_named(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        app, calls = _operation_app()
        out = _error_line(["files", "rich"], app, monkeypatch, capsys)
        assert (
            "Error: peek files: missing required argument 'version' — pass it as "
            "`peek files <package> <version>` or `--version <value>`." in out
        )

    def test_supplied_arguments_still_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _operation_app()
        _run(app, ["files", "rich", "14.0.0"], monkeypatch)
        assert calls == [{"package": "rich", "version": "14.0.0"}]
