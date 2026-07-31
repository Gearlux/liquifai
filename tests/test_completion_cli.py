"""Tests for :mod:`liquifai.completion_cli`.

These functions were extracted from ``LiquifyApp`` (they are the Rich-using CLI
glue for the completion machinery, kept off the stdlib-only ``completion/``
fast-path). They are pure argv-guards + delegation, so the tests assert two
things: the guard return value for present/absent flags, and that the delegated
``liquifai.completion.*`` helper is called with the app when the flag is present.
"""

from io import StringIO
from typing import Any, Dict, List

import pytest
from rich.console import Console

from liquifai import completion_cli


class _FakeApp:
    """Minimal stand-in — the completion_cli functions only touch ``.name``."""

    def __init__(self, name: str = "myapp") -> None:
        self.name = name


def _install_recording_console(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    """Redirect the module console to a StringIO buffer and return it."""
    buf = StringIO()
    monkeypatch.setattr(completion_cli, "console", Console(file=buf, force_terminal=False, width=200))
    return buf


# --- guard return values (flag absent → False, no delegation) --------------


def test_refresh_completion_cache_delegates_to_write_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: List[Any] = []
    monkeypatch.setattr("liquifai.completion.write_cache", lambda app: seen.append(app), raising=True)
    app = _FakeApp()
    completion_cli.refresh_completion_cache(app)
    assert seen == [app]


def test_handle_completion_install_absent_returns_false() -> None:
    assert completion_cli.handle_completion_install(_FakeApp(), ["train", "--foo"]) is False


def test_handle_refresh_completions_absent_returns_false() -> None:
    assert completion_cli.handle_refresh_completions(_FakeApp(), ["train"]) is False


def test_handle_refresh_completion_value_absent_returns_false() -> None:
    assert completion_cli.handle_refresh_completion_value(_FakeApp(), ["train"]) is False


# --- delegation when the flag IS present -----------------------------------


def test_show_completion_prints_script_and_seeds_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: Dict[str, Any] = {}
    monkeypatch.setattr(
        "liquifai.completion.render_script", lambda name, shell: f"# script {name} {shell}", raising=True
    )
    monkeypatch.setattr("liquifai.completion.write_cache", lambda app: calls.setdefault("cache", app), raising=True)

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)  # render_script goes to plain print()
    app = _FakeApp()
    handled = completion_cli.handle_completion_install(app, ["--show-completion", "bash"])

    assert handled is True
    assert "# script myapp bash" in buf.getvalue()
    assert calls["cache"] is app  # cache primed while the app is loaded


def test_install_completion_reports_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("liquifai.completion.install_script", lambda name, shell: "/rc/file", raising=True)
    monkeypatch.setattr("liquifai.completion.write_cache", lambda app: "/cache/myapp.json", raising=True)
    out = _install_recording_console(monkeypatch)

    handled = completion_cli.handle_completion_install(_FakeApp(), ["--install-completion", "bash"])

    assert handled is True
    text = out.getvalue()
    assert "Installed" in text and "/rc/file" in text and "/cache/myapp.json" in text


def test_refresh_completions_reports_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("liquifai.completion.write_cache", lambda app: None, raising=True)
    monkeypatch.setattr("liquifai.completion.refresh_value_caches", lambda app: {"ds": 3, "ver": 2}, raising=True)
    out = _install_recording_console(monkeypatch)

    handled = completion_cli.handle_refresh_completions(_FakeApp(), ["--refresh-completions"])

    assert handled is True
    text = out.getvalue()
    assert "Refreshed" in text and "2" in text  # 2 caches, 5 values


def test_refresh_completions_no_providers_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("liquifai.completion.write_cache", lambda app: None, raising=True)
    monkeypatch.setattr("liquifai.completion.refresh_value_caches", lambda app: {}, raising=True)
    out = _install_recording_console(monkeypatch)

    completion_cli.handle_refresh_completions(_FakeApp(), ["--refresh-completions"])

    assert "No positional completion providers" in out.getvalue()


def test_refresh_completion_value_calls_refresh_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}
    monkeypatch.setattr(
        "liquifai.completion.refresh_one",
        lambda app, key, inputs: seen.update(key=key, inputs=inputs),
        raising=True,
    )
    app = _FakeApp()
    handled = completion_cli.handle_refresh_completion_value(
        app, ["--refresh-completion-value", '{"key": "ds.ver", "inputs": {"name": "d1"}}']
    )

    assert handled is True
    assert seen == {"key": "ds.ver", "inputs": {"name": "d1"}}


def test_refresh_completion_value_swallows_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    # Malformed JSON must not raise — the helper runs detached in the background.
    called: List[Any] = []
    monkeypatch.setattr("liquifai.completion.refresh_one", lambda *a, **k: called.append(a), raising=True)
    handled = completion_cli.handle_refresh_completion_value(_FakeApp(), ["--refresh-completion-value", "{not json"])
    assert handled is True
    assert called == []  # never reached the provider


# --- background refresh: env-gated -----------------------------------------


def test_background_refresh_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIQUIFAI_BG_REFRESH", raising=False)
    called: List[Any] = []
    monkeypatch.setattr("liquifai.completion.has_stale_value_caches", lambda *a, **k: called.append(1), raising=True)
    completion_cli.maybe_background_refresh_values(_FakeApp())
    assert called == []  # never checked staleness — env gate closed


def test_background_refresh_checks_staleness_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIQUIFAI_BG_REFRESH", "1")
    seen: Dict[str, Any] = {}
    monkeypatch.setattr(
        "liquifai.completion.has_stale_value_caches",
        lambda app, ttl: seen.setdefault("ttl", ttl) or False,  # not stale → no thread
        raising=True,
    )
    completion_cli.maybe_background_refresh_values(_FakeApp(), ttl=42.0)
    assert seen["ttl"] == 42.0
