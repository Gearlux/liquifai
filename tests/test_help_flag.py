"""``-h`` shows help. It must never fall through and RUN the command.

liquifai declared only ``--help``. A single-dash token is not an override form, so ``-h`` landed in
the parser's ``dropped`` list — a warning — and execution continued to the command. For a read-only
command that is a papercut; for a command that DOES something it is a trap. Reported from a live
CLI on 2026-08-26: ``streamstudio restart -h`` restarted the server instead of describing it.

``-h`` is the universal convention (argparse, click, every GNU tool), so its absence is not a
deliberate smaller vocabulary — it is a hole, and the cost of the hole is running a destructive
command that the user was asking a question about.
"""

import sys
from typing import List

import pytest

from liquifai import LiquifyApp
from liquifai.grammar import GLOBAL_FLAG_SPECS


def _app() -> "tuple[LiquifyApp, List[str]]":
    app = LiquifyApp(name="probe", description="help-flag probe")
    ran: List[str] = []

    @app.command()
    def restart(port: int = 8188) -> None:
        """Restart the thing.

        Args:
            port: Port.
        """
        ran.append("restart")

    return app, ran


def _spellings() -> set:
    return {flag for spec in GLOBAL_FLAG_SPECS for flag in spec.flags}


def test_the_short_spelling_is_declared() -> None:
    assert "-h" in _spellings()
    assert "--help" in _spellings()


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_does_not_run_the_command(flag: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression: the command must NOT execute while the user is asking what it does."""
    app, ran = _app()
    monkeypatch.setattr(sys, "argv", ["probe", "restart", flag])
    app.run()
    assert ran == [], f"{flag} executed the command instead of describing it"


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_describes_the_command(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, _ = _app()
    monkeypatch.setattr(sys, "argv", ["probe", "restart", flag])
    app.run()
    out = capsys.readouterr().out
    assert "port" in out and "Restart the thing" in out


def test_the_command_still_runs_without_a_help_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ran = _app()
    monkeypatch.setattr(sys, "argv", ["probe", "restart"])
    app.run()
    assert ran == ["restart"]
