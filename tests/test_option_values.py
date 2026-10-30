"""An option of a declared type other than ``bool`` needs a value: ``--name`` alone is refused, never ``name=True``.

``--key`` with nothing after it (or another option after it) is the implicit-``True`` form, right for a switch and for
a config key, whose type nobody declared. For a parameter the command declares ``str``, ``int``, a list … it put
``True`` where text, a number or a list was promised — ``show --name --dry-run`` ran with ``name=True``. Such an
option given without a value now stops the command with one line, naming the command and both spellings, before
anything runs. A switch, a config key and an unannotated parameter keep the implicit ``True``.
"""

import re
import sys
from typing import Any, Dict, List, Optional

import pytest

from liquifai import LiquifyApp, MissingOptionValueError
from liquifai.overrides import parse_override_args, valued_param_names


def test_the_options_that_need_a_value_are_the_typed_parameters_but_the_switches() -> None:
    def command(  # type: ignore[no-untyped-def]  # `untyped` has no annotation on purpose
        dry_run: bool = False,
        name: str = "",
        label: Optional[str] = None,
        count: int = 0,
        nodes: Optional[List[str]] = None,
        anything: Any = None,
        untyped=None,
    ) -> None:
        return None

    assert valued_param_names(command) == {"name", "label", "count", "nodes"}
    assert valued_param_names(None) == set()


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (["--name"], "--name"),
        (["--name", "--dry-run"], "--name"),
        (["+name"], "+name"),
        (["--custom-node", "~x"], "--custom-node"),
    ],
)
def test_an_option_that_needs_a_value_is_refused_without_one(args: List[str], named: str) -> None:
    with pytest.raises(MissingOptionValueError, match=re.escape(f"{named} needs a value")):
        parse_override_args(args, value_keys={"name", "custom_node"})
    assert issubclass(MissingOptionValueError, ValueError)


def test_with_a_value_every_spelling_still_sets_it() -> None:
    overrides, _, _ = parse_override_args(
        ["--name", "x", "--count=3", "label=y", "+nodes", "[a]"],
        value_keys={
            "name",
            "count",
            "label",
            "nodes",
        },
    )
    assert overrides == {"name": "x", "count": 3, "label": "y", "nodes": ["a"]}


def test_a_switch_a_config_key_and_an_option_not_named_keep_the_implicit_true() -> None:
    overrides, _, _ = parse_override_args(
        ["--dry-run", "--verbose", "--name.inner"], flag_keys={"dry_run"}, value_keys={"name"}
    )
    assert overrides == {"dry_run": True, "verbose": True, "name.inner": True}


# --------------------------------------------------------------------------- #
# A real command
# --------------------------------------------------------------------------- #
def _app() -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    app = LiquifyApp(name="probe")
    calls: List[Dict[str, Any]] = []

    @app.command(default=True)
    def show(dry_run: bool = False, name: str = "", count: int = 0, nodes: Optional[List[str]] = None) -> None:
        """Record what each parameter received.

        Args:
            dry_run: A switch.
            name: Text.
            count: A number.
            nodes: A list.
        """
        calls.append({"dry_run": dry_run, "name": name, "count": count, "nodes": nodes})

    return app, calls


def _run(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


@pytest.mark.parametrize(
    ("argv", "option"),
    [(["show", "--name", "--dry-run"], "--name"), (["show", "--count"], "--count"), (["show", "--nodes"], "--nodes")],
)
def test_the_command_stops_with_one_line_naming_the_option_and_both_spellings(
    argv: List[str], option: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, calls = _app()
    with pytest.raises(SystemExit) as ended:
        _run(app, argv, monkeypatch)
    assert ended.value.code == 1
    said = " ".join(capsys.readouterr().out.split())
    key = option.lstrip("-")
    assert f"Error: probe show: {option} needs a value — pass it as `{option} <value>` or `{key}=<value>`" in said
    assert calls == []


def test_under_debug_the_refusal_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    app, calls = _app()
    with pytest.raises(MissingOptionValueError, match=r"probe show: --name needs a value"):
        _run(app, ["-d", "show", "--name"], monkeypatch)


def test_an_option_given_its_value_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    app, calls = _app()
    _run(app, ["show", "--name", "x", "--dry-run", "--count", "2"], monkeypatch)
    assert calls == [{"dry_run": True, "name": "x", "count": 2, "nodes": None}]
