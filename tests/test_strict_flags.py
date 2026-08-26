"""``strict_flags=True`` refuses a flag that names no parameter, instead of ignoring it.

liquifai already warns about an override confluid's materialization REPORT says matched nothing
(:func:`liquifai.overrides.warn_unused_overrides`). That check is authoritative but it only speaks
when something was materialized: *"A command that materializes nothing registers no candidates, so
the check degrades to silence rather than to guessing."* A CLI whose commands take plain scalars —
a launcher, a service manager — materializes nothing, so EVERY typo there is silent. Measured before
this existed: a probe app accepted ``--totally-bogus-flag``, applied it as a config key nothing
reads, ran on defaults, and said not one word.

An app opts in. The default stays permissive, because passing extra keys through to a config
document is a legitimate liquifai pattern and an app that does it must keep working.

The check is deliberately narrow: only a BARE key is judged, against the resolved command's own
parameters plus the top-level config keys. A DOTTED key (``--opt.lr``) addresses a nested object and
stays with the report-based warning, which can see delivery this cannot.
"""

from typing import Any, List, Optional, Tuple

import pytest

from liquifai import LiquifyApp
from liquifai.exceptions import UnknownFlagError


def _app(strict: bool) -> Tuple["LiquifyApp", List[Any]]:
    """An app plus the list its command records into — returned, never stashed on the app."""
    app = LiquifyApp(name="probe", description="strict-flag probe", strict_flags=strict)
    calls: List[Any] = []

    @app.command(default=True)
    def launch(port: int = 8188, background: bool = False, custom_node: Optional[List[str]] = None) -> None:
        """Launch it.

        Args:
            port: Port to listen on.
            background: Start detached.
            custom_node: Extra node URL.
        """
        calls.append({"port": port, "background": background, "custom_node": custom_node})

    return app, calls


def _run(app: "LiquifyApp", argv: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    app.run()


class TestStrict:
    def test_a_flag_naming_no_parameter_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Under `-d` the exception propagates; that is the CLI failure contract, not a special case."""
        app, calls = _app(strict=True)
        with pytest.raises(UnknownFlagError) as exc:
            _run(app, ["-d", "launch", "--totally-bogus-flag", "x"], monkeypatch)
        assert "totally_bogus_flag" in str(exc.value)

    def test_what_the_user_sees_is_one_error_line_and_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without `-d` a LiquifaiError renders as one clean line and exits 1 (the contract)."""
        app, calls = _app(strict=True)
        with pytest.raises(SystemExit) as exit_info:
            _run(app, ["launch", "--totally-bogus-flag", "x"], monkeypatch)
        assert exit_info.value.code == 1
        assert "totally_bogus_flag" in capsys.readouterr().out.replace("\n", " ")

    def test_the_refusal_suggests_the_closest_parameter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo is the common case, so the message must point at what was meant."""
        app, calls = _app(strict=True)
        with pytest.raises(UnknownFlagError) as exc:
            _run(app, ["-d", "launch", "--custom-nod", "URL"], monkeypatch)
        assert "custom_node" in str(exc.value)

    def test_a_real_parameter_still_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app(strict=True)
        _run(app, ["launch", "--port", "9000"], monkeypatch)
        assert calls[-1]["port"] == 9000

    def test_the_kebab_spelling_is_accepted_not_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Strictness must judge the NORMALISED key, or it would reject every hyphenated flag."""
        app, calls = _app(strict=True)
        _run(app, ["launch", "--custom-node", "URL"], monkeypatch)
        assert calls[-1]["custom_node"] == "URL"

    def test_a_dotted_key_is_left_to_the_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--opt.lr`` addresses a nested object; this check cannot see that delivery."""
        app, calls = _app(strict=True)
        _run(app, ["launch", "--opt.lr", "0.1"], monkeypatch)
        assert calls  # ran rather than refused

    def test_no_overrides_is_never_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app(strict=True)
        _run(app, ["launch"], monkeypatch)
        assert calls[-1]["port"] == 8188


class TestStrictAlsoCatchesUnrecognisedTokens:
    """A token matching NO override form is `dropped` — a warning, and execution continues.

    That is the same class of hole `-h` fell through (a single-dash token is no override form), and
    for a command that DOES something a warning is not enough. Under strict flags it refuses.
    """

    def test_a_single_dash_flag_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _app(strict=True)
        with pytest.raises(UnknownFlagError) as exc:
            _run(app, ["-d", "launch", "-x"], monkeypatch)
        assert "-x" in str(exc.value)

    def test_it_still_only_warns_when_permissive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, calls = _app(strict=False)
        _run(app, ["launch", "-x"], monkeypatch)
        assert calls  # ran, as before


class TestPermissiveIsStillTheDefault:
    def test_an_unknown_flag_runs_as_before(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An app passing extra keys into a config document must keep working."""
        app, calls = _app(strict=False)
        _run(app, ["launch", "--totally-bogus-flag", "x"], monkeypatch)
        assert calls[-1]["port"] == 8188

    def test_strict_is_off_unless_asked_for(self) -> None:
        assert LiquifyApp(name="p").strict_flags is False
