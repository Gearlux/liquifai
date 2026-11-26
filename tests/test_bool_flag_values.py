"""A ``bool`` parameter is a switch: ``--dry-run`` takes the word after it only when that word is a yes/no value.

``--key value`` gives ``key`` the next word unless that word starts like another option (``--``, ``+``, ``~``). For a
parameter the command declares ``bool`` that swallowed whatever came next: ``--dry-run name=x`` bound
``dry_run='name=x'`` and the ``name`` override was lost without a warning. A declared ``bool`` now takes the next
word only when it reads as a yes/no value — ``true``/``false``/``yes``/``no``/``on``/``off`` or ``0``/``1`` — and
otherwise means ``True``, the word parsed on its own. Completion already offers no value after such a flag.

Only BARE keys naming a ``bool`` parameter of the running command change: a config key has no declared type, so
``--trainer.fast x`` keeps the ``--key value`` form, and so does every other parameter (``--count 3``).
"""

import sys
from typing import Any, Dict, List, Optional

import pytest

from liquifai import LiquifyApp
from liquifai.exceptions import UnknownFlagError
from liquifai.overrides import bool_param_names, parse_override_args


# --------------------------------------------------------------------------- #
# Which parameters are switches
# --------------------------------------------------------------------------- #
def test_the_switches_are_the_parameters_annotated_bool() -> None:
    def command(
        dry_run: bool = False,
        verbose: Optional[bool] = None,
        quoted: "bool" = False,
        count: int = 0,
        name: str = "",
        anything: Any = None,
    ) -> None:
        return None

    assert bool_param_names(command) == {"dry_run", "verbose", "quoted"}
    assert bool_param_names(None) == set()


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("args", "overrides", "dropped"),
    [
        (["--dry-run", "name=x"], {"dry_run": True, "name": "x"}, []),
        (["--dry-run", "runnable.only=[full]"], {"dry_run": True, "runnable.only": ["full"]}, []),
        (["--dry-run", "x"], {"dry_run": True}, ["x"]),
        (["--dry_run", "x"], {"dry_run": True}, ["x"]),
        (["+dry_run", "x"], {"dry_run": True}, ["x"]),
    ],
)
def test_a_switch_does_not_take_a_word_that_is_no_yes_no_value(
    args: List[str], overrides: Dict[str, Any], dropped: List[str]
) -> None:
    assert parse_override_args(args, flag_keys={"dry_run"}) == (overrides, [], dropped)


@pytest.mark.parametrize(
    ("word", "value"),
    [("true", True), ("false", False), ("yes", True), ("no", False), ("on", True), ("off", False), ("0", 0), ("1", 1)],
)
def test_a_switch_takes_a_yes_no_value_after_it(word: str, value: Any) -> None:
    assert parse_override_args(["--dry-run", word], flag_keys={"dry_run"}) == ({"dry_run": value}, [], [])


def test_the_other_spellings_of_a_switch_are_unchanged() -> None:
    for args, value in ((["--dry-run"], True), (["--dry-run=false"], False), (["--dry-run-"], False)):
        assert parse_override_args(args, flag_keys={"dry_run"})[0] == {"dry_run": value}


def test_a_word_after_any_other_key_is_still_its_value() -> None:
    overrides, _, dropped = parse_override_args(
        ["--count", "3", "--name", "hello", "--trainer.fast", "x", "--dry_run.x", "y"], flag_keys={"dry_run"}
    )
    assert overrides == {"count": 3, "name": "hello", "trainer.fast": "x", "dry_run.x": "y"}
    assert dropped == []


def test_without_switches_named_the_parser_is_as_it_was() -> None:
    assert parse_override_args(["--dry-run", "name=x"]) == ({"dry_run": "name=x"}, [], [])


# --------------------------------------------------------------------------- #
# A real command
# --------------------------------------------------------------------------- #
def _app(strict: bool = False) -> "tuple[LiquifyApp, List[Dict[str, Any]]]":
    app = LiquifyApp(name="probe", strict_flags=strict)
    calls: List[Dict[str, Any]] = []

    @app.command(default=True)
    def show(dry_run: bool = False, name: str = "", count: int = 0) -> None:
        """Record what each parameter received.

        Args:
            dry_run: A switch.
            name: Text.
            count: A number.
        """
        calls.append({"dry_run": dry_run, "name": name, "count": count})

    return app, calls


def _run(app: LiquifyApp, argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


@pytest.mark.parametrize(
    ("argv", "received"),
    [
        (["show", "--dry-run", "name=x"], {"dry_run": True, "name": "x", "count": 0}),
        (["show", "--dry-run", "--name", "x"], {"dry_run": True, "name": "x", "count": 0}),
        (["show", "--dry-run", "false"], {"dry_run": False, "name": "", "count": 0}),
        (["show", "--dry-run", "true", "count=2"], {"dry_run": True, "name": "", "count": 2}),
        (["show", "--count", "3", "--dry-run"], {"dry_run": True, "name": "", "count": 3}),
        (["show", "--name", "hello"], {"dry_run": False, "name": "hello", "count": 0}),
    ],
)
def test_a_command_receives_its_switch_and_the_setting_after_it(
    argv: List[str], received: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, calls = _app()
    _run(app, argv, monkeypatch)
    assert calls == [received]


def test_a_stray_word_after_a_switch_is_refused_under_strict_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    app, calls = _app(strict=True)
    with pytest.raises(UnknownFlagError, match="'x'"):
        _run(app, ["-d", "show", "--dry-run", "x"], monkeypatch)
    assert calls == []
