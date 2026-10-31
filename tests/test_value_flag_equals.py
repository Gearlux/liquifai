"""Every value-taking global flag takes ``--flag=VALUE`` as it takes ``--flag VALUE``.

``tool run cfg.yaml --scope=quick`` used to run with no scope and say nothing: only a flag token followed by its
value was read, so ``--scope=quick`` reached the document as the key ``scope`` (``--level=DEBUG`` as ``level``,
``--config=cfg.yaml`` as ``config``). ``-s=quick`` warned that it was an unrecognised token. A ``--flag=`` with
nothing after the ``=`` gives no value and is refused.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from liquifai import LiquifyApp
from liquifai.flags import parse_globals
from liquifai.walk import tokenize


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text("size: 1\nquick: !scope:quick\n  size: 2\nkeyed: !scope:task=a\n  size: 3\n")
    return path


def _run(config: Optional[Path], monkeypatch: pytest.MonkeyPatch, *argv: str) -> List[Dict[str, Any]]:
    app = LiquifyApp(name="probe")
    calls: List[Dict[str, Any]] = []

    @app.script_command("run")
    def run(size: int = 0, scope: str = "", level: str = "", config: str = "") -> None:
        """Run."""
        calls.append({"size": size, "scope": scope, "level": level, "config": config})

    monkeypatch.setattr(sys, "argv", ["probe", "run", *([str(config)] if config else []), *argv])
    app.run()
    return calls


@pytest.mark.parametrize(
    "argv, size",
    [
        ([], 1),
        (["--scope", "quick"], 2),
        (["--scope=quick"], 2),
        (["-s=quick"], 2),
        (["--scope=task=a"], 3),
        (["-s=quick,task=a"], 3),
    ],
)
def test_every_spelling_activates_the_scope(
    config: Path, monkeypatch: pytest.MonkeyPatch, argv: List[str], size: int
) -> None:
    assert _run(config, monkeypatch, *argv) == [{"size": size, "scope": "", "level": "", "config": ""}]


def test_the_equals_spelling_never_reaches_the_command_as_a_key(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A command parameter named ``scope`` is not fed by ``--scope=quick``: the flag is the global option."""
    assert _run(config, monkeypatch, "--scope=quick", "--size", "7") == [
        {"size": 7, "scope": "", "level": "", "config": ""}
    ]


@pytest.mark.parametrize("flag", ["--config", "-c"])
def test_the_config_is_read_from_its_equals_spelling(config: Path, monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    assert _run(None, monkeypatch, f"{flag}={config}", "--scope=quick") == [
        {"size": 2, "scope": "", "level": "", "config": ""}
    ]


def test_a_log_level_in_its_equals_spelling_is_a_log_level_not_a_key(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(config, monkeypatch, "--level=DEBUG") == [{"size": 1, "scope": "", "level": "", "config": ""}]


def test_save_config_writes_where_its_equals_spelling_says(
    config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "saved.yaml"
    assert _run(config, monkeypatch, f"--save-config={target}", "-s=quick")[0]["size"] == 2
    assert "size: 2" in target.read_text()


def test_the_parser_reads_both_spellings_in_order() -> None:
    flags = parse_globals(tokenize(["--scope=quick", "-s", "deep", "-s=task=a,val", "--lr", "1"]))
    assert flags.scopes == ["quick", "deep", "task=a", "val"]
    assert [t.text for t in flags.remaining] == ["--lr", "1"]


def test_the_parser_reads_every_value_flag_in_its_equals_spelling() -> None:
    flags = parse_globals(
        tokenize(
            [
                "-c=a.yaml",
                "--level=DEBUG",
                "--console-level=INFO",
                "--file-level=TRACE",
                "--log-dir=logs",
                "--save-config=out.yaml",
                "--lr=1",
            ]
        )
    )
    assert flags.config_path == Path("a.yaml")
    assert flags.save_config == Path("out.yaml")
    assert flags.log_overrides == {
        "log_level": "DEBUG",
        "console_level": "INFO",
        "file_level": "TRACE",
        "log_dir": Path("logs"),
    }
    assert [t.text for t in flags.remaining] == ["--lr=1"]  # an override keeps its own `=` spelling


def test_a_value_may_hold_an_equals_sign() -> None:
    assert parse_globals(tokenize(["--config=dir=a/cfg.yaml"])).config_path == Path("dir=a/cfg.yaml")


@pytest.mark.parametrize(
    "word, error",
    [
        ("--scope=", "Error: --scope= gives no NAME — write `--scope NAME` or `--scope=NAME`."),
        ("-s=", "Error: -s= gives no NAME — write `-s NAME` or `-s=NAME`."),
        ("--level=", "Error: --level= gives no LEVEL — write `--level LEVEL` or `--level=LEVEL`."),
        ("--save-config=", "Error: --save-config= gives no PATH — write `--save-config PATH` or `--save-config=PATH`."),
    ],
)
def test_an_equals_sign_with_no_value_after_it_is_refused(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, word: str, error: str
) -> None:
    with pytest.raises(SystemExit) as stopped:
        _run(config, monkeypatch, word)
    assert stopped.value.code == 1
    assert " ".join(capsys.readouterr().out.split()) == error


def test_a_literal_word_after_the_separator_is_left_alone() -> None:
    flags = parse_globals(tokenize(["--", "--scope=quick"]))
    assert flags.scopes == []
    assert [t.text for t in flags.remaining] == ["--scope=quick"]
