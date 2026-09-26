"""A command may declare single-letter shorts: ``-b`` for ``--background``.

liquifai derives every option from the parameter NAME, so before this a parameter had exactly one
spelling and ``restart -b`` did nothing recognisable. Two shapes were possible and only one is safe:

* **auto-derive the first letter** — which silently shadows the globals. ``-c``/``-s``/``-d``/``-h``
  are already ``--config``/``--scope``/``--debug``/``--help``, so ``comfy_dir``, ``config``,
  ``debug_x``, ``declared_nodes`` and ``default_nodes`` would each collide, and the collision would
  be invisible until someone typed it;
* **declare them**, as argparse and click do, and REFUSE a collision at decoration time — so the
  clash is a startup error for the author, never a wrong action for the user.

The second. A short is sugar for the long spelling; everything downstream sees the long one.
"""

import sys
from typing import Any, Dict, List, Tuple

import pytest

from liquifai import LiquifyApp
from liquifai.exceptions import CommandDefinitionError
from liquifai.grammar import GLOBAL_FLAG_SPECS

RESERVED = sorted(f for spec in GLOBAL_FLAG_SPECS for f in spec.flags if not f.startswith("--"))


def _app(short: Dict[str, str]) -> Tuple["LiquifyApp", List[Any]]:
    app = LiquifyApp(name="probe", description="short-option probe", strict_flags=True)
    calls: List[Any] = []

    @app.command(short=short)
    def restart(port: int = 8188, background: bool = False) -> None:
        """Restart it.

        Args:
            port: Port to use.
            background: Start detached.
        """
        calls.append({"port": port, "background": background})

    return app, calls


def _run(app: "LiquifyApp", argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


class TestAShortReachesTheParameter:
    def test_a_boolean_short_sets_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app({"b": "background"})
        _run(app, ["restart", "-b"], monkeypatch)
        assert calls[-1]["background"] is True

    def test_a_valued_short_takes_the_next_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app({"p": "port"})
        _run(app, ["restart", "-p", "9000"], monkeypatch)
        assert calls[-1]["port"] == 9000

    def test_the_equals_form_works_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app({"p": "port"})
        _run(app, ["restart", "-p=9000"], monkeypatch)
        assert calls[-1]["port"] == 9000

    def test_a_short_works_before_the_command_name_as_well(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app({"b": "background"})
        _run(app, ["-b", "restart"], monkeypatch)
        assert calls[-1]["background"] is True

    def test_the_long_spelling_is_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app({"b": "background"})
        _run(app, ["restart", "--background"], monkeypatch)
        assert calls[-1]["background"] is True

    def test_an_undeclared_short_is_still_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Declaring one short must not open the single-dash space generally."""
        from liquifai.exceptions import UnknownFlagError

        app, _ = _app({"b": "background"})
        with pytest.raises(UnknownFlagError):
            _run(app, ["-d", "restart", "-z"], monkeypatch)


class TestACollisionIsAnAuthorError:
    """Refused where the author can see it — at decoration — not where the user pays for it."""

    @pytest.mark.parametrize("letter", [f.lstrip("-") for f in RESERVED])
    def test_a_reserved_global_letter_is_refused(self, letter: str) -> None:
        with pytest.raises(CommandDefinitionError) as exc:
            _app({letter: "background"})
        assert letter in str(exc.value)

    def test_a_short_for_a_parameter_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(CommandDefinitionError) as exc:
            _app({"z": "not_a_parameter"})
        assert "not_a_parameter" in str(exc.value)

    def test_two_parameters_cannot_share_a_letter(self) -> None:
        with pytest.raises(CommandDefinitionError):
            _app({"b": "background", "b ": "port"})  # normalised, so this IS the same letter

    def test_a_multi_letter_short_is_refused(self) -> None:
        """``-bg`` would be ambiguous with a cluster of single-letter shorts."""
        with pytest.raises(CommandDefinitionError):
            _app({"bg": "background"})


class TestHelpAdvertisesIt:
    def test_the_short_is_shown_beside_the_long(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An option you cannot discover is the bug this whole area keeps producing."""
        app, _ = _app({"b": "background"})
        _run(app, ["restart", "--help"], monkeypatch)
        out = capsys.readouterr().out
        # The exact rendered spelling — a bare `"-b" in out` passes on any stray hyphen-b.
        assert "-b, --background" in out
        assert "--port" in out and "-b, --port" not in out  # only the declared one gets a short
