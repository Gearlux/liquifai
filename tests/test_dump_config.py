"""``--dump-config`` and ``--save-config``: the configuration a run resolves to, every setting of every class.

``--dump-config`` prints the resolved configuration and stops — the command does not run; ``--dump-config=PATH``
writes it to ``PATH`` instead. ``--save-config PATH`` writes it and then runs the command as usual. Both write the
same text: the document after includes, scopes, positionals and command-line keys, every object in it built (without
``solidify()``) and dumped with each constructor parameter — written or defaulted — and each class named by its
importable path, so the file runs again as it is. The detected ``platform:`` block is left out unless the config
wrote one.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import confluid
import pytest
import yaml
from confluid import configurable

from liquifai import LiquifyApp
from liquifai.context import set_context
from liquifai.flags import parse_globals
from liquifai.walk import tokenize


@configurable
class DumpOptimizer:
    def __init__(self, lr: float = 0.001, momentum: float = 0.9, nesterov: bool = False) -> None:
        self.lr = lr
        self.momentum = momentum
        self.nesterov = nesterov


@configurable
class DumpTrainer:
    def __init__(
        self, optimizer: Optional[DumpOptimizer] = None, epochs: int = 10, seed: int = 0, run_name: str = "demo"
    ) -> None:
        self.optimizer = optimizer
        self.epochs = epochs
        self.seed = seed
        self.run_name = run_name


#: How many times a ``DumpHeavy`` was solidified — a dump must leave it at zero.
SOLIDIFIED: List[int] = []


@configurable
class DumpHeavy:
    def __init__(self, size: int = 4) -> None:
        self.size = size

    def solidify(self) -> None:
        SOLIDIFIED.append(self.size)


TRAINER = f"{DumpTrainer.__module__}.DumpTrainer"
OPTIMIZER = f"{DumpOptimizer.__module__}.DumpOptimizer"

CONFIG = f"""\
epochs: 3
runnable: !class:{TRAINER}
  optimizer: !class:{OPTIMIZER}
    lr: 0.01
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    set_context(None)  # type: ignore[arg-type]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    SOLIDIFIED.clear()
    saved = confluid.get_app_name()
    yield tmp_path
    confluid.set_app_name(saved)


def _app() -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    """A config runner like ``recordstream run``: it builds the runnable itself (``flow_mode="manual"``)."""
    app = LiquifyApp(name="dump-app")
    runs: List[Dict[str, Any]] = []

    @app.script_command(flow_mode="manual", default=True)
    def run(runnable: Any = None) -> None:
        """Run a config."""
        assert app.context is not None
        runs.append(dict(app.context.config_data))

    @app.command()
    def plain() -> None:
        """A command that runs without a config."""
        runs.append({})

    return app, runs


def _run(app: LiquifyApp, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["dump-app", *args])
    try:
        app.run()
    except SystemExit as stopped:
        return int(stopped.code or 0)
    return 0


def _write(tmp_path: Path, text: str = CONFIG, name: str = "run.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


# -- --dump-config: print, and run nothing -----------------------------------------------------------------------


def test_dump_config_prints_every_setting_of_every_class_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config") == 0
    assert runs == []
    assert yaml.safe_load(capsys.readouterr().out) == {
        "epochs": 3,
        "runnable": {
            "_target_": TRAINER,
            "optimizer": {"_target_": OPTIMIZER, "lr": 0.01, "momentum": 0.9, "nesterov": False},
            "epochs": 3,
            "seed": 0,
            "run_name": "demo",
        },
    }


def test_dump_config_shows_the_command_line_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, _ = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config", "--lr", "0.05", "--runnable.seed", "7") == 0
    dumped = yaml.safe_load(capsys.readouterr().out)
    assert dumped["runnable"]["optimizer"]["lr"] == 0.05
    assert dumped["runnable"]["seed"] == 7


def test_dump_config_shows_the_active_scope_and_the_included_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, f"runnable: !class:{TRAINER}\n  run_name: base\n", name="base.yaml")
    _write(tmp_path, "include: base.yaml\nepochs: 3\nfast: !scope:fast\n  epochs: 1\n")
    app, _ = _app()
    assert _run(app, monkeypatch, "run.yaml", "--scope", "fast", "--dump-config") == 0
    dumped = yaml.safe_load(capsys.readouterr().out)
    assert dumped["runnable"]["run_name"] == "base"
    assert dumped["runnable"]["epochs"] == 1


def test_dump_config_leaves_out_the_detected_platform_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, _ = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config") == 0
    assert "platform" not in yaml.safe_load(capsys.readouterr().out)


def test_dump_config_keeps_a_platform_block_the_config_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, CONFIG + "platform:\n  os: plan9\n")  # a value no host is detected as
    app, _ = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config") == 0
    assert yaml.safe_load(capsys.readouterr().out)["platform"]["os"] == "plan9"


def test_dump_config_builds_without_solidifying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, f"runnable: !class:{DumpHeavy.__module__}.DumpHeavy\n  size: 8\n")
    app, _ = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config") == 0
    assert yaml.safe_load(capsys.readouterr().out)["runnable"]["size"] == 8
    assert SOLIDIFIED == []


def test_dump_config_with_a_path_writes_the_file_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config=out/resolved.yaml") == 0
    assert runs == []
    written = yaml.safe_load((tmp_path / "out" / "resolved.yaml").read_text())
    assert written["runnable"]["optimizer"]["momentum"] == 0.9
    assert "_target_" not in capsys.readouterr().out


def test_dump_config_with_an_empty_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml", "--dump-config=") == 1
    assert runs == []
    assert "Error: --dump-config= names no file" in capsys.readouterr().out


def test_dump_config_without_a_config_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, runs = _app()
    assert _run(app, monkeypatch, "plain", "--dump-config") == 1
    assert runs == []
    assert "Error: --dump-config: no configuration file was given" in capsys.readouterr().out


def test_the_dumped_file_runs_to_the_same_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, _ = _app()
    assert _run(app, monkeypatch, "run.yaml", "--lr", "0.05", "--dump-config=first.yaml") == 0
    set_context(None)  # type: ignore[arg-type]
    again, _ = _app()
    assert _run(again, monkeypatch, "first.yaml", "--dump-config=second.yaml") == 0
    assert (tmp_path / "second.yaml").read_text() == (tmp_path / "first.yaml").read_text()


# -- --save-config: write, then run -------------------------------------------------------------------------------


def test_save_config_writes_the_file_and_runs_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml", "--save-config", "saved.yaml", "--epochs", "5") == 0
    assert len(runs) == 1
    assert "save_config" not in runs[0]
    saved = yaml.safe_load((tmp_path / "saved.yaml").read_text())
    assert saved["runnable"]["epochs"] == 5
    assert saved["runnable"]["optimizer"]["nesterov"] is False


def test_dump_config_beside_save_config_writes_both_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml", "--save-config", "saved.yaml", "--dump-config") == 0
    assert runs == []
    assert yaml.safe_load(capsys.readouterr().out) == yaml.safe_load((tmp_path / "saved.yaml").read_text())


def test_save_config_without_a_config_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, runs = _app()
    assert _run(app, monkeypatch, "plain", "--save-config", "saved.yaml") == 1
    assert runs == []
    assert "Error: --save-config: no configuration file was given" in capsys.readouterr().out


@pytest.mark.parametrize("after", [[], ["--lr", "1"]])
def test_save_config_without_a_file_is_refused(
    after: List[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml", "--save-config", *after) == 1
    assert runs == []
    assert "Error: --save-config needs a file: `--save-config PATH`." in capsys.readouterr().out


def test_a_run_without_either_flag_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path)
    app, runs = _app()
    assert _run(app, monkeypatch, "run.yaml") == 0
    assert len(runs) == 1
    assert sorted(p.name for p in tmp_path.glob("*.yaml")) == ["run.yaml"]


# -- the flags themselves ------------------------------------------------------------------------------------------


def test_the_flags_are_read_by_the_global_parser_and_never_become_config_keys() -> None:
    parsed = parse_globals(tokenize(["--dump-config", "--save-config", "a.yaml", "--lr", "1"]))
    assert parsed.dump_config is True
    assert parsed.dump_config_path is None
    assert parsed.save_config == Path("a.yaml")
    assert [t.text for t in parsed.remaining] == ["--lr", "1"]
    assert parse_globals(tokenize(["--dump-config=b.yaml"])).dump_config_path == Path("b.yaml")


def test_a_flag_after_double_dash_is_a_literal() -> None:
    parsed = parse_globals(tokenize(["--", "--dump-config"]))
    assert parsed.dump_config is False
    assert [t.text for t in parsed.remaining] == ["--dump-config"]


def test_help_lists_both_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, _ = _app()
    _run(app, monkeypatch, "plain", "--help")
    out = capsys.readouterr().out
    assert "--dump-config" in out
    assert "--save-config" in out
