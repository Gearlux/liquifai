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


def test_app_show_completion_prints_script(app: LiquifyApp, capsys: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["myapp", "--show-completion", "bash"])
    monkeypatch.delenv("_MYAPP_COMPLETE", raising=False)
    set_context(None)  # type: ignore[arg-type]

    app.run()
    captured = capsys.readouterr()
    assert "_myapp_completion" in captured.out
    assert "liquifai-complete myapp" in captured.out


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
