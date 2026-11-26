"""A command's own options are listed like every other option, with or without a config on the line.

``--help`` with a config shows the config's options, then the command's own (``--dry_run``), then a table per object
the config builds — the TAB listing's order. The parameters' help lives in those tables, so the description above them
leaves out the docstring's ``Args:`` block; and a script command's ``Any`` parameter is the object its config binds,
never a flag to type.
"""

import sys
import textwrap
from pathlib import Path
from typing import Any, List, Literal

import pytest
from confluid import configurable

from liquifai import LiquifyApp
from liquifai.report import command_description


@configurable
class Job:
    """A job the config builds.

    Args:
        size: How big the job is.
    """

    def __init__(self, size: int = 1) -> None:
        self.size = size

    def run(self) -> None:
        return None


def _app() -> LiquifyApp:
    app = LiquifyApp(name="helpcmd")

    @app.script_command("run", default=True)
    def run(runnable: Any, dry_run: bool = False, plan_format: Literal["text", "json"] = "text") -> None:
        """Run the object a config binds under its `runnable:` key.

        Args:
            runnable: The object bound under `runnable:`.
            dry_run: Run nothing; print what a run would do.
            plan_format: How the dry run prints its plan.

        Raises:
            SystemExit: When a run would stop.
        """

    @app.command("go")
    def go(value: Any = None) -> None:
        """Go somewhere.

        Args:
            value: Any value at all.
        """

    return app


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "job.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            # Where the job writes.
            folder: out
            runnable: !class:{__name__}.Job
              size: 4
            """
        )
    )
    return path


def _help(argv: List[str], capsys: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(sys, "argv", ["helpcmd", *argv])
    _app().run()
    return str(capsys.readouterr().out)


def _row(out: str, flag: str) -> str:
    """The one line of ``out`` that starts with ``flag``, its columns joined by single spaces."""
    rows = [" ".join(line.split()) for line in out.splitlines() if line.strip().startswith(flag + " ")]
    assert len(rows) == 1, f"{flag}: {rows}"
    return rows[0]


class TestWithAConfig:
    def test_the_commands_options_sit_between_the_configs_and_the_objects(
        self, config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _help(["run", str(config), "--help"], capsys, monkeypatch)
        own, command, flowed = out.index("Options of job.yaml"), out.index("Options of run"), out.index("flowed from")
        assert own < command < flowed
        assert "--dry_run" in out[command:flowed] and "--plan_format" in out[command:flowed]
        assert "--size" in out[flowed:]

    def test_each_option_has_its_type_value_and_help_in_the_same_columns(
        self, config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _help(["run", str(config), "--help"], capsys, monkeypatch)
        assert _row(out, "--dry_run") == "--dry_run bool False Run nothing; print what a run would do."
        assert _row(out, "--plan_format") == "--plan_format text | json 'text' How the dry run prints its plan."

    def test_the_object_the_config_binds_is_no_option(
        self, config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _help(["run", str(config), "--help"], capsys, monkeypatch)
        assert "--runnable" not in out
        assert "runnable (Job)" in out

    def test_the_description_leaves_the_args_block_to_the_tables(
        self, config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _help(["run", str(config), "--help"], capsys, monkeypatch)
        assert "Run the object a config binds under its `runnable:` key." in out
        assert "Args:" not in out and "The object bound under" not in out
        assert "Raises:" in out and "SystemExit: When a run would stop." in out

    def test_the_docs_lines_list_the_commands_options_the_same_way(
        self, config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _help(["run", str(config), "--docs"], capsys, monkeypatch)
        command, flowed = out.index("Options of run"), out.index("flowed from")
        assert command < flowed
        assert _row(out, "--plan_format") == "--plan_format text | json = 'text' How the dry run prints its plan."
        assert "--runnable" not in out


class TestWithoutAConfig:
    def test_the_commands_options_are_listed_with_readable_types(
        self, tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = _help(["run", "--help"], capsys, monkeypatch)
        assert _row(out, "--dry_run") == "--dry_run bool False Run nothing; print what a run would do."
        assert _row(out, "--plan_format") == "--plan_format text | json 'text' How the dry run prints its plan."

    def test_the_table_is_titled_and_headed_as_with_a_config(
        self, tmp_path: Path, config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bare = _help(["run", "--help"], capsys, monkeypatch)
        with_config = _help(["run", str(config), "--help"], capsys, monkeypatch)
        for out in (bare, with_config):
            lines = [" ".join(line.split()) for line in out.splitlines()]
            title = lines.index("Options of run")
            assert lines[title + 1] == "Option Type Current Value Description"
        assert "Shortest Unique" not in bare and "Command Configuration Options" not in bare

    def test_the_docs_lines_are_titled_as_with_a_config(
        self, tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = _help(["run", "--docs"], capsys, monkeypatch)
        assert "Options of run" in out
        assert _row(out, "--plan_format") == "--plan_format text | json = 'text' How the dry run prints its plan."

    def test_the_object_a_config_would_bind_is_no_option(
        self, tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = _help(["run", "--help"], capsys, monkeypatch)
        assert "--runnable" not in out
        assert "Args:" not in out

    def test_an_any_parameter_of_a_plain_command_stays_an_option(
        self, tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = _help(["go", "--help"], capsys, monkeypatch)
        assert _row(out, "--value") == "--value Any None Any value at all."


class TestTheDescription:
    def test_the_args_block_is_left_out_and_every_other_section_kept(self) -> None:
        doc = """Do the thing.

        More about it.

        Args:
            first: One.
                Continued.

            second: Two.

        Returns:
            Nothing.
        """
        assert command_description(doc) == "Do the thing.\n\nMore about it.\n\nReturns:\n    Nothing."

    def test_a_docstring_without_args_is_only_dedented(self) -> None:
        assert command_description("Do it.\n\n    Then more.\n") == "Do it.\n\nThen more."

    def test_no_docstring_says_so(self) -> None:
        assert command_description(None) == "No description."
