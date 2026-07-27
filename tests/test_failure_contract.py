"""Pins the CLI failure/exit-code contract.

* A :class:`liquifai.exceptions.LiquifaiError` or :class:`confluid.ConfluidError`
  raised during phases 3–6 of ``run()`` is an *expected* user-facing failure:
  one clean ``Error: …`` line on the console, full traceback to the log at
  DEBUG, exit code 1.
* Under ``--debug`` the same exception PROPAGATES (full traceback for the
  developer).
* Any other exception is a bug and always propagates unchanged.
* A missing ``--config`` file keeps its dedicated message + exit 1 (pinned in
  ``test_core_extended.py``); unknown command/group exits 1 as before.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

from liquifai import LiquifyApp
from liquifai.context import set_context


def _make_app(name: str) -> LiquifyApp:
    app = LiquifyApp(name=name)

    @app.script_command(flow_mode="auto")
    def go(thing: Any) -> None:  # pragma: no cover - never reached on failure
        pass

    return app


def _bad_config(tmp_path: Path) -> Path:
    config = tmp_path / "bad.yaml"
    config.write_text("thing: !class:no.such.module.NoSuchClassAnywhere\n")
    return config


def test_config_error_exits_1_with_clean_message(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    app = _make_app("contract-app")
    config = _bad_config(tmp_path)
    monkeypatch.setattr(sys, "argv", ["contract-app", "go", str(config)])
    set_context(None)

    with pytest.raises(SystemExit) as exc:
        app.run()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Error:" in out


def test_config_error_propagates_under_debug(tmp_path: Path, monkeypatch: Any) -> None:
    import confluid

    app = _make_app("contract-debug-app")
    config = _bad_config(tmp_path)
    monkeypatch.setattr(sys, "argv", ["contract-debug-app", "go", str(config), "--debug"])
    set_context(None)

    with pytest.raises(confluid.ConfluidError):
        app.run()


def test_unexpected_exception_propagates(tmp_path: Path, monkeypatch: Any) -> None:
    """Non-Liquifai/Confluid exceptions are bugs — never converted to exit 1."""
    app = LiquifyApp(name="contract-bug-app")

    @app.command()
    def boom() -> None:
        raise RuntimeError("a genuine bug")

    monkeypatch.setattr(sys, "argv", ["contract-bug-app", "boom"])
    set_context(None)

    with pytest.raises(RuntimeError, match="a genuine bug"):
        app.run()


def test_context_is_contextvar_isolated() -> None:
    """The active context lives in a ContextVar — a copied Context sees its
    own value without clobbering the outer one (thread/async embedding)."""
    import contextvars

    from liquifai.context import LiquifyContext, get_context

    outer = LiquifyContext(name="outer")
    set_context(outer)

    def _inner() -> None:
        inner = LiquifyContext(name="inner")
        set_context(inner)
        assert get_context() is inner

    ctx = contextvars.copy_context()
    ctx.run(_inner)
    # The inner set_context ran in a COPIED context — the outer one is intact.
    assert get_context() is outer
    set_context(None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
