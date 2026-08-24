"""Log-level resolution — liquifai must DEFER to loggair's hierarchy.

``_bootstrap`` calls ``loggair.configure_logging``, and loggair resolves every
setting through ``args > LOGGAIR_* env > loggair.yaml/pyproject/XDG > defaults``
(``loggair/core.py`` ``resolve_settings``). An argument short-circuits that
chain on the FIRST layer, so passing a literal ``"INFO"`` when the user named no
flag silently shadows ``LOGGAIR_CONSOLE_LEVEL``.

liquifai therefore passes ``None`` unless a CLI flag actually named a level —
exactly as it already did for ``log_dir``, which is why ``LOGGAIR_DIR`` worked
while ``LOGGAIR_CONSOLE_LEVEL`` did not. The resulting order is
``CLI flag > env > config file > default``.

Every test runs in a hermetic environment: an empty CWD and XDG dir so no
developer's ``loggair.yaml`` / ``~/.config/loggair/config.yaml`` leaks in, and
every ``LOGGAIR_*`` variable cleared unless the case sets one.
"""

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import loggair
import pytest

from liquifai import LiquifyApp, LiquifyContext
from liquifai.context import set_context

_LOGGAIR_VARS = ("LOGGAIR_CONSOLE_LEVEL", "LOGGAIR_FILE_LEVEL", "LOGGAIR_DIR")


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Neutralize every layer BELOW the ones under test.

    Without this the suite's verdict depends on the machine it runs on: this
    workspace really does ship a ``waivefront/loggair.yaml`` and the developer
    really does have ``~/.config/loggair/config.yaml``.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in _LOGGAIR_VARS:
        monkeypatch.delenv(var, raising=False)
    yield workdir
    set_context(None)
    loggair.reset_logging()


def _levels(**ctx_kwargs: Any) -> Dict[str, Optional[str]]:
    """Bootstrap an app with ``ctx_kwargs`` and report the RESOLVED sink levels.

    Reads ``loggair.get_active_config()`` — what the sinks are actually running
    with, all hierarchy layers applied — rather than mocking
    ``configure_logging``, so the test fails if either side of the contract
    moves.
    """
    app = LiquifyApp(name="levels-app")
    app.context = LiquifyContext(name="levels-app", **ctx_kwargs)
    set_context(app.context)
    app._bootstrap()
    active = loggair.get_active_config()
    return {"console": active["console_level"], "file": active["file_level"]}


def test_defaults_when_nothing_is_set(isolated: Path) -> None:
    """No flag, no env, no config file -> loggair's own defaults.

    This is the case the fix must NOT change: the literals liquifai used to
    pass were byte-identical to loggair's defaults, so a bare run looks the
    same before and after.
    """
    assert _levels(log_dir=isolated) == {"console": "INFO", "file": "DEBUG"}


def test_env_beats_the_default(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reported defect: ``LOGGAIR_CONSOLE_LEVEL`` was ignored outright."""
    monkeypatch.setenv("LOGGAIR_CONSOLE_LEVEL", "TRACE")
    monkeypatch.setenv("LOGGAIR_FILE_LEVEL", "WARNING")
    assert _levels(log_dir=isolated) == {"console": "TRACE", "file": "WARNING"}


def test_config_file_beats_the_default(isolated: Path) -> None:
    """A ``loggair.yaml`` beside the app was shadowed by the same literals."""
    (isolated / "loggair.yaml").write_text('console_level: "ERROR"\nfile_level: "ERROR"\n')
    assert _levels(log_dir=isolated) == {"console": "ERROR", "file": "ERROR"}


def test_env_beats_the_config_file(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering INSIDE loggair, pinned here because liquifai now exposes it.

    liquifai deliberately does NOT re-rank these two layers; deferring means
    taking loggair's order as-is.
    """
    (isolated / "loggair.yaml").write_text('console_level: "ERROR"\nfile_level: "ERROR"\n')
    monkeypatch.setenv("LOGGAIR_CONSOLE_LEVEL", "WARNING")
    monkeypatch.setenv("LOGGAIR_FILE_LEVEL", "WARNING")
    assert _levels(log_dir=isolated) == {"console": "WARNING", "file": "WARNING"}


@pytest.mark.parametrize(
    "flag,expected",
    [
        ({"log_level": "TRACE"}, {"console": "TRACE", "file": "TRACE"}),
        ({"console_level": "TRACE"}, {"console": "TRACE", "file": "WARNING"}),
        ({"file_level": "TRACE"}, {"console": "WARNING", "file": "TRACE"}),
        ({"debug": True}, {"console": "DEBUG", "file": "WARNING"}),
    ],
    ids=["--level", "--console-level", "--file-level", "--debug"],
)
def test_cli_flag_beats_env(
    isolated: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: Dict[str, Any],
    expected: Dict[str, str],
) -> None:
    """A flag the user typed wins; the sink they did NOT name still takes env.

    ``--debug`` is included because it reaches the same slot by a different
    route (a bool, not a level string) and must keep beating env.
    """
    monkeypatch.setenv("LOGGAIR_CONSOLE_LEVEL", "WARNING")
    monkeypatch.setenv("LOGGAIR_FILE_LEVEL", "WARNING")
    assert _levels(log_dir=isolated, **flag) == expected


def test_cli_flag_beats_the_config_file(isolated: Path) -> None:
    (isolated / "loggair.yaml").write_text('console_level: "ERROR"\nfile_level: "ERROR"\n')
    assert _levels(log_dir=isolated, log_level="TRACE") == {"console": "TRACE", "file": "TRACE"}


def test_sink_specific_flag_beats_the_shared_one(isolated: Path) -> None:
    """``--console-level`` overrides ``--level`` for its own sink only."""
    assert _levels(log_dir=isolated, log_level="TRACE", console_level="ERROR") == {
        "console": "ERROR",
        "file": "TRACE",
    }


def test_debug_does_not_lower_a_console_level_the_user_named(isolated: Path) -> None:
    """``--debug -\\-console-level ERROR`` keeps ERROR: the explicit flag wins."""
    assert _levels(log_dir=isolated, debug=True, console_level="ERROR")["console"] == "ERROR"


def test_no_level_literal_reaches_loggair_when_no_flag_is_given(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism itself, pinned directly.

    The end-to-end tests above would all still pass if a future refactor
    re-read the env in liquifai and forwarded a literal. That would work until
    someone adds a fifth layer to loggair. Assert the contract instead: when no
    flag names a level, liquifai forwards ``None`` and lets loggair resolve.
    """
    seen: List[Dict[str, Any]] = []

    def spy(**kwargs: Any) -> None:
        seen.append(kwargs)

    monkeypatch.setattr("liquifai.core.loggair.configure_logging", spy)
    app = LiquifyApp(name="spy-app")
    app.context = LiquifyContext(name="spy-app", log_dir=isolated)
    set_context(app.context)
    app._bootstrap()

    assert seen[0]["console_level"] is None
    assert seen[0]["file_level"] is None
