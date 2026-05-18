"""Tests for liquifai.completion (shell tab completion)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from liquifai import LiquifyApp
from liquifai import completion as comp
from liquifai.context import set_context


@pytest.fixture
def app() -> LiquifyApp:
    """Build a representative LiquifyApp tree:

    root (myapp)
      ├── greet            (plain command)
      ├── train            (script_command)
      └── group            (sub-app)
            ├── alpha      (plain command)
            └── beta       (script_command)
    """
    root = LiquifyApp(name="myapp")

    @root.command()
    def greet(name: str = "world") -> None:
        print(f"hi {name}")

    @root.script_command()
    def train(layers: int = 1) -> None:
        pass

    sub = LiquifyApp(name="group", description="group")

    @sub.command()
    def alpha() -> None:
        pass

    @sub.script_command()
    def beta() -> None:
        pass

    root.add_app(sub)
    return root


# ------------------------------ complete() --------------------------------


def test_top_level_commands_and_subapps(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", ""], cword=1)
    assert "greet" in out
    assert "train" in out
    assert "group" in out


def test_top_level_prefix_filter(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "gr"], cword=1)
    assert "greet" in out
    assert "group" in out
    assert "train" not in out


def test_dash_prefix_emits_global_flags(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "--"], cword=1)
    assert "--config" in out
    assert "--scope" in out
    assert "--install-completion" in out
    assert "--show-completion" in out


def test_subapp_commands(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "group", ""], cword=2)
    assert "alpha" in out
    assert "beta" in out


def test_subapp_prefix_filter(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "group", "al"], cword=2)
    assert out == ["alpha"]


def test_script_command_expects_yaml(app: LiquifyApp, tmp_path: Path) -> None:
    cfg_a = tmp_path / "a.yaml"
    cfg_b = tmp_path / "b.yml"
    cfg_other = tmp_path / "notes.txt"
    sub = tmp_path / "sub"
    sub.mkdir()
    for p in (cfg_a, cfg_b, cfg_other):
        p.write_text("x: 1\n")

    out = comp.complete(app, ["myapp", "train", str(tmp_path) + "/"], cword=2)
    names = [Path(p).name for p in out if not p.endswith("/")]
    dirs = [p for p in out if p.endswith("/")]
    assert "a.yaml" in names
    assert "b.yml" in names
    assert "notes.txt" not in names
    assert any(d.endswith("sub/") for d in dirs)


def test_script_command_after_config_dashdash_lists_flags(app: LiquifyApp, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("layers: 5\nlearning_rate: 0.01\n")

    out = comp.complete(app, ["myapp", "train", str(cfg), "--"], cword=3)
    assert "--config" in out
    assert "--layers" in out
    assert "--learning_rate" in out


def test_script_command_after_config_dashprefix_filters_keys(app: LiquifyApp, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("layers: 5\nlearning_rate: 0.01\n")

    out = comp.complete(app, ["myapp", "train", str(cfg), "--lay"], cword=3)
    assert out == ["--layers"]


def test_script_command_after_config_empty_word_lists_overrides(app: LiquifyApp, tmp_path: Path) -> None:
    """`myapp train cfg.yaml <TAB>` (empty incomplete) should still suggest
    overrides — without this branch the shell falls back to file completion,
    which is the wrong default once the config slot is filled."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("layers: 5\n")

    out = comp.complete(app, ["myapp", "train", str(cfg), ""], cword=3)
    assert "--layers" in out
    assert "--config" in out


def test_script_command_after_override_flag_silent(app: LiquifyApp, tmp_path: Path) -> None:
    """`myapp train cfg.yaml --layers <TAB>` expects a value; we have no type
    info so we stay silent (let the shell do default filename completion)."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("layers: 5\n")

    out = comp.complete(app, ["myapp", "train", str(cfg), "--layers", ""], cword=4)
    assert out == []


def test_config_flag_value_completion(app: LiquifyApp, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("k: v\n")

    out = comp.complete(app, ["myapp", "--config", str(tmp_path) + "/"], cword=2)
    assert any(c.endswith("cfg.yaml") for c in out)


def test_scope_flag_value_no_suggestion(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "--scope", ""], cword=2)
    assert out == []


def test_install_completion_offers_shells(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "--install-completion", ""], cword=2)
    assert set(out) == {"bash", "zsh", "fish"}


def test_show_completion_filters_shells(app: LiquifyApp) -> None:
    out = comp.complete(app, ["myapp", "--show-completion", "z"], cword=2)
    assert out == ["zsh"]


# ----------------------------- render_script ------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_render_script_substitutes_prog(shell: str) -> None:
    script = comp.render_script("marainer", shell)
    assert "_marainer_completion" in script or "__fish_marainer_complete" in script
    # Fast-path: must invoke the lightweight liquifai-complete entry, not the slow app.
    assert "liquifai-complete marainer" in script
    # Should not contain the literal placeholder tokens after rendering.
    assert "{prog}" not in script


def test_render_script_unknown_shell_raises() -> None:
    with pytest.raises(ValueError):
        comp.render_script("x", "tcsh")


def test_bash_script_suppresses_trailing_space_for_directories() -> None:
    """Bash auto-inserts a space after every completion; for directory
    candidates we need `compopt -o nospace` so the user can keep tabbing in.
    """
    script = comp.render_script("marainer", "bash")
    assert "compopt -o nospace" in script
    # Guard: the suppression must be conditional on the candidate ending in `/`
    # (suppressing unconditionally would break file completion).
    assert "*/" in script
    # macOS ships bash 3.2 which has no `compopt` builtin — the call MUST be
    # guarded so old-bash users don't see "compopt: command not found" on
    # every TAB. The fix degrades gracefully (trailing space returns).
    assert "command -v compopt" in script


def test_zsh_script_suppresses_trailing_space_for_directories() -> None:
    """Zsh's `compadd` adds a trailing space by default; for directory
    candidates we need `-S ''` so the user can keep tabbing in.
    """
    script = comp.render_script("marainer", "zsh")
    assert "compadd -U -S '' --" in script
    # Conditional on `*/` so non-directory candidates still get a trailing
    # space (the normal "ready for next arg" UX).
    assert "*/" in script


# ----------------------------- install_script -----------------------------


def test_install_script_bash_embeds_function_directly(tmp_path: Path) -> None:
    home = tmp_path
    rc = comp.install_script("marainer", "bash", home=home)
    contents = rc.read_text()
    assert rc == home / ".bashrc"
    # Function body must be embedded — no `eval "$(marainer --show-completion ...)"`,
    # otherwise every shell startup re-invokes the slow app.
    assert "_marainer_completion()" in contents
    assert "liquifai-complete marainer" in contents
    assert "marainer --show-completion" not in contents


def test_install_script_bash_replaces_old_block(tmp_path: Path) -> None:
    home = tmp_path
    rc = home / ".bashrc"
    rc.write_text(
        "# unrelated\n"
        "\n# >>> liquifai completion for marainer >>>\n"
        'eval "$(marainer --show-completion bash)"\n'
        "# <<< liquifai completion for marainer <<<\n"
        "# tail\n"
    )
    comp.install_script("marainer", "bash", home=home)
    contents = rc.read_text()
    # Old eval-style line is gone, new fast-path content is in.
    assert 'eval "$(marainer --show-completion bash)"' not in contents
    assert "liquifai-complete marainer" in contents
    # Surrounding unrelated content is preserved.
    assert "# unrelated" in contents
    assert "# tail" in contents


def test_install_script_zsh_idempotent(tmp_path: Path) -> None:
    home = tmp_path
    rc = comp.install_script("annotaide", "zsh", home=home)
    assert rc == home / ".zshrc"
    first = rc.read_text()
    comp.install_script("annotaide", "zsh", home=home)
    assert rc.read_text() == first


def test_install_script_fish(tmp_path: Path) -> None:
    home = tmp_path
    target = comp.install_script("fluxstudio", "fish", home=home)
    assert target == home / ".config" / "fish" / "completions" / "fluxstudio.fish"
    assert "liquifai-complete fluxstudio" in target.read_text()


def test_install_script_unknown_shell_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        comp.install_script("x", "tcsh", home=tmp_path)


# ----------------------------- alias helpers ------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_render_helpers_includes_bind_alias(shell: str) -> None:
    body = comp.render_helpers(shell)
    assert "liquifai-bind-alias" in body
    assert "_liquifai_alias_complete" in body
    assert "liquifai-complete" in body


def test_render_helpers_fish_is_empty() -> None:
    assert comp.render_helpers("fish") == ""


def test_install_writes_shared_helpers_block_once(tmp_path: Path) -> None:
    comp.install_script("marainer", "bash", home=tmp_path)
    comp.install_script("annotaide", "bash", home=tmp_path)
    contents = (tmp_path / ".bashrc").read_text()
    # Helpers block should appear exactly once even after installing two apps.
    assert contents.count(comp._HELPERS_MARKER) == 1
    assert contents.count(comp._HELPERS_END_MARKER) == 1
    # Both per-app blocks should be present.
    assert "_marainer_completion()" in contents
    assert "_annotaide_completion()" in contents


def test_install_zsh_writes_shared_helpers_block(tmp_path: Path) -> None:
    comp.install_script("marainer", "zsh", home=tmp_path)
    contents = (tmp_path / ".zshrc").read_text()
    assert "liquifai-bind-alias" in contents
    assert "_liquifai_alias_complete" in contents


def test_install_fish_does_not_write_helpers(tmp_path: Path) -> None:
    comp.install_script("marainer", "fish", home=tmp_path)
    target = tmp_path / ".config" / "fish" / "completions" / "marainer.fish"
    assert "liquifai-bind-alias" not in target.read_text()


# ------------------------- detect_shell -----------------------------------


def test_detect_shell_recognized(monkeypatch: Any) -> None:
    monkeypatch.setenv("SHELL", "/usr/local/bin/zsh")
    assert comp.detect_shell() == "zsh"


def test_detect_shell_unknown_falls_back(monkeypatch: Any) -> None:
    monkeypatch.setenv("SHELL", "/bin/tcsh")
    assert comp.detect_shell() == "bash"


# ----------------- serialize_app + cache round-trip ----------------------


def test_serialize_app_round_trip(app: LiquifyApp) -> None:
    tree = comp.serialize_app(app)
    assert tree["name"] == "myapp"
    assert set(tree["commands"]) == {"greet", "train"}
    assert set(tree["script_cmds"]) == {"train"}
    assert set(tree["sub_apps"].keys()) == {"group"}
    sub = tree["sub_apps"]["group"]
    assert set(sub["commands"]) == {"alpha", "beta"}
    assert set(sub["script_cmds"]) == {"beta"}


def test_write_then_read_cache(app: LiquifyApp, tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    target = comp.write_cache(app)
    assert target == tmp_path / "liquifai" / "myapp.json"
    payload = json.loads(target.read_text())
    assert payload["version"] == comp.CACHE_VERSION

    tree = comp.read_cache("myapp")
    assert tree is not None
    assert tree["name"] == "myapp"
    assert "greet" in tree["commands"]


def test_read_cache_missing_returns_none(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert comp.read_cache("nonexistent") is None


def test_read_cache_wrong_version_returns_none(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    target = tmp_path / "liquifai" / "stale.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"version": 9999, "tree": {}}))
    assert comp.read_cache("stale") is None


def test_complete_from_tree_works_without_app(app: LiquifyApp) -> None:
    tree = comp.serialize_app(app)
    out = comp.complete_from_tree(tree, ["myapp", "tr"], cword=1)
    assert out == ["train"]


# ------------------------ end-to-end via LiquifyApp ----------------------


def test_app_emits_completion_via_env(app: LiquifyApp, capsys: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("_MYAPP_COMPLETE", "complete_bash")
    monkeypatch.setenv("COMP_WORDS", "myapp gr")
    monkeypatch.setenv("COMP_CWORD", "1")
    monkeypatch.setattr(sys, "argv", ["myapp"])
    set_context(None)  # type: ignore[arg-type]

    with pytest.raises(SystemExit) as exc:
        app.run()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert "greet" in lines
    assert "group" in lines
    assert "train" not in lines


def test_app_show_completion_prints_script(app: LiquifyApp, capsys: Any, monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "argv", ["myapp", "--show-completion", "bash"])
    monkeypatch.delenv("_MYAPP_COMPLETE", raising=False)
    set_context(None)  # type: ignore[arg-type]

    app.run()
    captured = capsys.readouterr()
    assert "_myapp_completion" in captured.out
    assert "liquifai-complete myapp" in captured.out
    # --show-completion must also prime the per-app cache. Without this,
    # liquifai-install-completions's auto-discovery probe leaves apps
    # registered for tab completion but with no command tree to suggest
    # from, so TAB silently returns nothing.
    cache = tmp_path / "cache" / "liquifai" / "myapp.json"
    assert cache.exists()


def test_app_show_completion_tolerates_cache_write_failure(app: LiquifyApp, capsys: Any, monkeypatch: Any) -> None:
    """If write_cache raises (e.g. read-only XDG_CACHE_HOME), the script
    must still be printed — script output is the primary contract,
    cache-priming is a best-effort side effect."""
    monkeypatch.setattr(sys, "argv", ["myapp", "--show-completion", "bash"])
    monkeypatch.delenv("_MYAPP_COMPLETE", raising=False)
    set_context(None)  # type: ignore[arg-type]

    def boom(_self: Any) -> Path:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(comp, "write_cache", boom)
    app.run()
    captured = capsys.readouterr()
    assert "_myapp_completion" in captured.out


def test_app_install_completion_writes_rc_and_cache(
    app: LiquifyApp, capsys: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "argv", ["myapp", "--install-completion", "zsh"])
    monkeypatch.delenv("_MYAPP_COMPLETE", raising=False)
    set_context(None)  # type: ignore[arg-type]

    app.run()
    rc = tmp_path / ".zshrc"
    assert rc.exists()
    assert "liquifai-complete myapp" in rc.read_text()
    cache = tmp_path / "cache" / "liquifai" / "myapp.json"
    assert cache.exists()


def test_completion_env_var_normalizes_dashes() -> None:
    a = LiquifyApp(name="my-app")
    assert a._completion_env_var() == "_MY_APP_COMPLETE"


# ------------- install_script with target_rc (workspace-local rc) -------------


def test_install_script_target_rc_writes_target_not_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target_rc = tmp_path / "ws" / ".project.bashrc.completion"
    rc = comp.install_script("marainer", "bash", home=home, target_rc=target_rc)
    assert rc == target_rc
    assert target_rc.exists()
    # ~/.bashrc must remain pristine — this is the whole point of target_rc.
    assert not (home / ".bashrc").exists()
    body = target_rc.read_text()
    assert "_marainer_completion()" in body
    assert comp._HELPERS_MARKER in body


def test_install_script_target_rc_idempotent(tmp_path: Path) -> None:
    target_rc = tmp_path / ".project.bashrc.completion"
    comp.install_script("marainer", "bash", target_rc=target_rc)
    comp.install_script("marainer", "bash", target_rc=target_rc)
    body = target_rc.read_text()
    assert body.count(comp._HELPERS_MARKER) == 1
    assert body.count("# >>> liquifai completion for marainer >>>") == 1


def test_install_script_target_rc_two_apps_share_helpers(tmp_path: Path) -> None:
    target_rc = tmp_path / ".project.bashrc.completion"
    comp.install_script("marainer", "bash", target_rc=target_rc)
    comp.install_script("annotaide", "bash", target_rc=target_rc)
    body = target_rc.read_text()
    assert body.count(comp._HELPERS_MARKER) == 1
    assert "_marainer_completion()" in body
    assert "_annotaide_completion()" in body


def test_install_script_target_rc_creates_parent(tmp_path: Path) -> None:
    target_rc = tmp_path / "nested" / "dir" / "rc"
    comp.install_script("marainer", "bash", target_rc=target_rc)
    assert target_rc.exists()


# --------------------- install_for_apps + auto-discovery ----------------------


def test_install_for_apps_explicit_list(tmp_path: Path) -> None:
    target_rc = tmp_path / "rc"
    installed = comp.install_for_apps(target_rc=target_rc, apps=["foo", "bar"], shell="bash")
    assert installed == ["foo", "bar"]
    body = target_rc.read_text()
    assert "_foo_completion()" in body
    assert "_bar_completion()" in body
    # Single helpers block even with multiple apps.
    assert body.count(comp._HELPERS_MARKER) == 1


def test_install_for_apps_empty_list_is_noop(tmp_path: Path) -> None:
    target_rc = tmp_path / "rc"
    installed = comp.install_for_apps(target_rc=target_rc, apps=[], shell="bash")
    assert installed == []
    assert not target_rc.exists()


def _make_stub_script(path: Path, exit_code: int, stdout: str) -> None:
    """Write a tiny POSIX shell stub that mimics a Liquifai --show-completion probe."""
    body = "#!/bin/sh\n"
    if stdout:
        body += f'printf "%s" "{stdout}"\n'
    body += f"exit {exit_code}\n"
    path.write_text(body)
    path.chmod(0o755)


def test_discover_liquifai_apps_filters_by_probe_response(tmp_path: Path) -> None:
    prefix = tmp_path / "venv"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    # A real Liquifai-shaped responder — output must contain the marker
    # `liquifai-complete <name>` that render_script always emits.
    _make_stub_script(
        bindir / "marainer",
        exit_code=0,
        stdout="_marainer_completion() { :; }; liquifai-complete marainer 2>/dev/null",
    )
    # Non-Liquifai CLI (exits non-zero on --show-completion).
    _make_stub_script(bindir / "some-other-tool", exit_code=2, stdout="")
    # Click/Typer-style responder: exits 0 with output but NO liquifai marker
    # — must be filtered out.
    _make_stub_script(
        bindir / "click-app",
        exit_code=0,
        stdout="_CLICK_APP_COMPLETE=complete_bash click-app",
    )
    # Liquifai responder but in the skip-list — must still be excluded.
    _make_stub_script(bindir / "python3.12", exit_code=0, stdout="liquifai-complete python3.12")
    # liquifai-* helpers must also be excluded.
    _make_stub_script(bindir / "liquifai-complete", exit_code=0, stdout="liquifai-complete liquifai-complete")
    found = comp.discover_liquifai_apps(prefix=prefix)
    assert found == ["marainer"]


def test_install_for_apps_auto_discover(tmp_path: Path) -> None:
    prefix = tmp_path / "venv"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    _make_stub_script(
        bindir / "marainer",
        exit_code=0,
        stdout="_marainer_completion() { :; }; liquifai-complete marainer",
    )
    _make_stub_script(
        bindir / "annotaide",
        exit_code=0,
        stdout="_annotaide_completion() { :; }; liquifai-complete annotaide",
    )
    _make_stub_script(bindir / "ls-fake", exit_code=1, stdout="")

    target_rc = tmp_path / "rc"
    installed = comp.install_for_apps(target_rc=target_rc, shell="bash", prefix=prefix)
    assert sorted(installed) == ["annotaide", "marainer"]
    body = target_rc.read_text()
    assert "_marainer_completion()" in body
    assert "_annotaide_completion()" in body
    assert "_ls-fake_completion" not in body


def test_discover_liquifai_apps_missing_bindir(tmp_path: Path) -> None:
    assert comp.discover_liquifai_apps(prefix=tmp_path / "does-not-exist") == []


# --------------------------- CLI: --target-rc entry ---------------------------


def test_cli_install_completions_explicit_apps(tmp_path: Path, capsys: Any) -> None:
    target_rc = tmp_path / "rc"
    rc = comp._cli_install_completions(["--target-rc", str(target_rc), "--shell", "bash", "marainer", "annotaide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "marainer" in out and "annotaide" in out
    body = target_rc.read_text()
    assert "_marainer_completion()" in body
    assert "_annotaide_completion()" in body


def test_cli_install_completions_auto_discover_empty(tmp_path: Path, capsys: Any) -> None:
    target_rc = tmp_path / "rc"
    # No apps to discover (no prefix override; sys.prefix's bin probably has
    # non-Liquifai stuff that exits non-zero on --show-completion bash). We
    # only assert the no-op message + clean exit, not the specific contents.
    rc = comp._cli_install_completions(["--target-rc", str(target_rc), "--shell", "bash"])
    assert rc == 0
    out = capsys.readouterr().out
    # Either some apps were installed (rare in test env) or the no-op message
    # was printed. Both are acceptable; assert clean exit.
    if not target_rc.exists():
        assert "no Liquifai apps found" in out


def test_cli_install_completions_requires_target_rc() -> None:
    with pytest.raises(SystemExit):
        comp._cli_install_completions(["--shell", "bash"])


# ----------------- _fast_complete (the standalone entry) -----------------


def test_fast_complete_main_serves_from_cache(app: LiquifyApp, capsys: Any, monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    comp.write_cache(app)

    monkeypatch.setattr(sys, "argv", ["liquifai-complete", "myapp"])
    monkeypatch.setenv("COMP_WORDS", "myapp gr")
    monkeypatch.setenv("COMP_CWORD", "1")

    from liquifai import _fast_complete

    _fast_complete.main()
    out = capsys.readouterr().out.splitlines()
    assert "greet" in out
    assert "group" in out


def test_fast_complete_main_silent_on_cache_miss(capsys: Any, monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["liquifai-complete", "nonexistent"])
    monkeypatch.setenv("COMP_WORDS", "nonexistent ")
    monkeypatch.setenv("COMP_CWORD", "1")

    from liquifai import _fast_complete

    _fast_complete.main()
    assert capsys.readouterr().out == ""


def test_help_refreshes_stale_completion_cache(capsys: Any, monkeypatch: Any, tmp_path: Path) -> None:
    """Adding a new command then running --help must update the on-disk cache.

    Regression test: prior to this fix, --help short-circuited before the
    cache write at the end of run(), so freshly added commands didn't appear
    under TAB until a real (non-help) invocation succeeded.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    set_context(None)  # type: ignore

    # 1) Build a smaller app and prime the cache with just its commands.
    small = LiquifyApp(name="freshapp")

    @small.command()
    def alpha() -> None:
        pass

    comp.write_cache(small)
    tree_before = comp.read_cache("freshapp")
    assert tree_before is not None
    assert set(tree_before["commands"]) == {"alpha"}

    # 2) Build a larger app under the same name (simulates adding a command).
    big = LiquifyApp(name="freshapp")

    @big.command()
    def alpha2() -> None:
        pass

    @big.command()
    def beta() -> None:
        pass

    # 3) Invoking --help on the larger app should refresh the cache.
    monkeypatch.setattr(sys, "argv", ["freshapp", "--help"])
    big.run()

    tree_after = comp.read_cache("freshapp")
    assert tree_after is not None
    assert set(tree_after["commands"]) == {"alpha2", "beta"}
