"""``script_command(config_keys=fn)``: a command adds keys to the loaded configuration before it is dumped or run.

``fn`` receives the loaded document — includes, scopes, positionals and the command-line keys applied — and returns
``{key: value}``; liquifai merges those keys in after the command-line ones, as dotted overrides are merged, before
``--dump-config`` / ``--save-config`` write it and before the command's objects are built. So a command that forces a
setting (a trainer verb's task) shows it in the dumped file, and can add it only where the document accepts it.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

import confluid
import pytest
import yaml
from confluid import configurable

from liquifai import LiquifyApp
from liquifai.context import set_context
from liquifai.exceptions import CommandDefinitionError


@configurable
class KeysTrainer:
    def __init__(self, task: str = "fit", epochs: int = 1) -> None:
        self.task = task
        self.epochs = epochs


TRAINER = f"{KeysTrainer.__module__}.KeysTrainer"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    set_context(None)  # type: ignore[arg-type]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    saved = confluid.get_app_name()
    yield tmp_path
    confluid.set_app_name(saved)


def _app(provider: Any) -> "tuple[LiquifyApp, List[Any]]":
    app = LiquifyApp(name="keys-app")
    runs: List[Any] = []

    @app.script_command(flow_mode="manual", default=True, config_keys=provider)
    def run(runnable: Any = None) -> None:
        """Build the runnable and record it."""
        assert app.context is not None
        runs.append(confluid.load(runnable, context=app.context.config_data))

    return app, runs


def _run(app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["keys-app", *args])
    try:
        app.run()
    except SystemExit as stopped:
        return int(stopped.code or 0)
    return 0


def _write(tmp_path: Path, text: str) -> None:
    (tmp_path / "run.yaml").write_text(text)


def test_the_added_keys_reach_the_dumped_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n  task: fit\n")
    app, runs = _app(lambda document: {"runnable.task": "test"})
    assert _run(app, monkeypatch, "run.yaml", "--dump-config") == 0
    assert runs == []
    assert yaml.safe_load(capsys.readouterr().out)["runnable"]["task"] == "test"


def test_the_added_keys_reach_the_objects_the_command_builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n")
    app, runs = _app(lambda document: {"runnable.task": "predict"})
    assert _run(app, monkeypatch, "run.yaml") == 0
    assert runs[0].task == "predict"


def test_the_function_sees_the_document_after_the_command_line_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n")
    seen: List[Mapping[str, Any]] = []

    def provider(document: Mapping[str, Any]) -> Dict[str, Any]:
        seen.append(document)
        return {}

    app, runs = _app(provider)
    assert _run(app, monkeypatch, "run.yaml", "--runnable.epochs", "5") == 0
    assert seen[0]["runnable"].kwargs["epochs"] == 5
    assert runs[0].epochs == 5


def test_an_added_key_wins_over_the_same_key_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n")
    app, runs = _app(lambda document: {"runnable.task": "test"})
    assert _run(app, monkeypatch, "run.yaml", "--runnable.task", "fit") == 0
    assert runs[0].task == "test"


def test_no_keys_change_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n  task: evaluate\n")
    app, runs = _app(lambda document: {})
    assert _run(app, monkeypatch, "run.yaml") == 0
    assert runs[0].task == "evaluate"


def test_the_function_is_not_called_without_a_config(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Optional[Mapping[str, Any]]] = []
    app = LiquifyApp(name="keys-app")

    def provider(document: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(document)
        return {}

    @app.script_command(config_keys=provider)
    def run() -> None:
        """Run without a config."""

    assert _run(app, monkeypatch, "run") == 0
    assert calls == []


def test_an_added_key_is_not_reported_as_a_typed_key_that_reached_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n")
    app, _ = _app(lambda document: {"runnable.task": "test"})
    assert _run(app, monkeypatch, "run.yaml", "--epochs", "2") == 0
    captured = capsys.readouterr()
    assert "task" not in captured.out + captured.err


def test_a_function_returning_something_else_than_a_mapping_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n")
    app, runs = _app(lambda document: ["runnable.task"])
    assert _run(app, monkeypatch, "run.yaml") == 1
    assert runs == []
    said = " ".join(capsys.readouterr().out.split())  # the console wraps a long line
    assert "Error: keys-app run: config_keys= returned a list, not a mapping of keys to values." in said


def test_a_config_keys_that_is_not_callable_is_refused_at_decoration() -> None:
    app = LiquifyApp(name="keys-app")
    with pytest.raises(CommandDefinitionError, match="config_keys= takes a callable"):

        @app.script_command(config_keys={"runnable.task": "test"})  # type: ignore[arg-type]
        def run(runnable: Any = None) -> None:
            """Run."""
