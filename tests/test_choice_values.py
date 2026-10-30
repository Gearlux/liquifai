"""A parameter that takes one of a closed set of values refuses any other, before the command runs.

``--plan_format`` is ``Literal["text", "json"]``: help says ``text | json``, so ``--plan_format xml`` (or
``JSON``) stops the command with one line naming the command, the option, the values it takes and the one given —
wherever the value came from (the command line or a config key of the same name). A parameter whose type mixes a
choice with an open type (``Union[int, Literal["auto"]]``) is not judged.
"""

import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import pytest

from liquifai import InvalidChoiceError, LiquifyApp


def _app() -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    app = LiquifyApp(name="probe")
    calls: List[Dict[str, Any]] = []

    @app.command(default=True)
    def show(
        plan_format: Literal["text", "json"] = "text",
        depth: Literal[1, 2] = 1,
        mode: Optional[Literal["fast", "slow"]] = None,
        workers: Union[int, Literal["auto"]] = 1,
    ) -> None:
        """Record what each parameter received.

        Args:
            plan_format: How to print.
            depth: How deep.
            mode: How quick, or unset.
            workers: How many, or auto.
        """
        calls.append({"plan_format": plan_format, "depth": depth, "mode": mode, "workers": workers})

    @app.command(positionals=["kind"])
    def make(kind: Literal["a", "b"] = "a") -> None:
        """Make one kind.

        Args:
            kind: Which kind.
        """
        calls.append({"kind": kind})

    @app.script_command("run")
    def run(plan_format: Literal["text", "json"] = "text") -> None:
        """Run a config.

        Args:
            plan_format: How to print.
        """
        calls.append({"plan_format": plan_format})

    return app, calls


def _run(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


def _refused(argv: List[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    app, calls = _app()
    with pytest.raises(SystemExit) as ended:
        _run(app, argv, monkeypatch)
    assert ended.value.code == 1
    assert calls == [], "the command ran"
    captured = capsys.readouterr()
    return " ".join((captured.out + captured.err).split())


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["show", "--plan_format", "xml"], "Error: probe show: --plan_format must be one of text, json; got 'xml'"),
        (["show", "--plan-format", "JSON"], "Error: probe show: --plan_format must be one of text, json; got 'JSON'"),
        (["show", "plan_format=xml"], "Error: probe show: --plan_format must be one of text, json; got 'xml'"),
        (["show", "--depth", "3"], "Error: probe show: --depth must be one of 1, 2; got 3"),
        (["show", "--mode", "medium"], "Error: probe show: --mode must be one of fast, slow; got 'medium'"),
        (["make", "c"], "Error: probe make: <kind> must be one of a, b; got 'c'"),
    ],
)
def test_a_value_outside_the_choices_stops_the_command_with_one_line(
    argv: List[str], said: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert said in _refused(argv, monkeypatch, capsys)


def test_a_config_key_outside_the_choices_is_refused_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "job.yaml").write_text(
        textwrap.dedent(
            """\
            plan_format: xml
            """
        )
    )
    said = _refused(["run", "job.yaml"], monkeypatch, capsys)
    assert "Error: probe run: --plan_format must be one of text, json; got 'xml'" in said


@pytest.mark.parametrize(
    ("argv", "received"),
    [
        (["show"], {"plan_format": "text", "depth": 1, "mode": None, "workers": 1}),
        (["show", "--plan_format", "json"], {"plan_format": "json", "depth": 1, "mode": None, "workers": 1}),
        (
            ["show", "--plan-format", "json", "--depth", "2"],
            {"plan_format": "json", "depth": 2, "mode": None, "workers": 1},
        ),
        (["show", "--mode", "slow"], {"plan_format": "text", "depth": 1, "mode": "slow", "workers": 1}),
        (["show", "--mode", "null"], {"plan_format": "text", "depth": 1, "mode": None, "workers": 1}),
        (["show", "--workers", "auto"], {"plan_format": "text", "depth": 1, "mode": None, "workers": "auto"}),
        (["show", "--workers", "many"], {"plan_format": "text", "depth": 1, "mode": None, "workers": "many"}),
        (["make", "b"], {"kind": "b"}),
    ],
)
def test_a_value_among_the_choices_or_a_mixed_type_runs_the_command(
    argv: List[str], received: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, calls = _app()
    _run(app, argv, monkeypatch)
    assert calls == [received]


def test_under_debug_the_refusal_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    app, calls = _app()
    with pytest.raises(InvalidChoiceError, match="must be one of text, json"):
        _run(app, ["--debug", "show", "--plan_format", "xml"], monkeypatch)
    assert calls == []
    assert issubclass(InvalidChoiceError, ValueError)
