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
    def greet(target: str = "world") -> None:
        # NB: a param literally named ``name`` is the confluid instance-identity
        # key and is skipped by ``get_hierarchy`` (hence excluded from both
        # ``--help`` and completion), so this fixture uses ``target`` to model a
        # normal plain-command option.
        print(f"hi {target}")

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


def test_script_command_empty_word_lists_files_and_flags(app: LiquifyApp, tmp_path: Path, monkeypatch: Any) -> None:
    """`myapp train <TAB>` (no config yet, empty word) reveals the command's
    override flags ALONGSIDE config-file candidates — not files only.

    Regression: a script_command (e.g. `waivefront-helios convert-ops-export`)
    hid its `--<key>` options behind a `--`, so a bare TAB showed only the
    cwd's files/dirs and never the command's own options."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cfg.yaml").write_text("layers: 5\n")

    out = comp.complete(app, ["myapp", "train", ""], cword=2)
    assert "cfg.yaml" in out  # config-file candidate still offered
    assert "--layers" in out  # ...AND the command's own option flag
    assert "--config" in out  # ...AND the globals


def test_script_command_path_prefix_lists_files_only(app: LiquifyApp, tmp_path: Path) -> None:
    """While typing a path (a non-empty, non-dash word), only file candidates
    come back — the unioned option flags are prefix-filtered out so they don't
    pollute path completion."""
    (tmp_path / "cfg.yaml").write_text("layers: 5\n")
    out = comp.complete(app, ["myapp", "train", str(tmp_path) + "/"], cword=2)
    assert any(p.endswith("cfg.yaml") for p in out)
    assert not any(p.startswith("--") for p in out)


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


def test_script_command_no_config_override_flag_silent(app: LiquifyApp) -> None:
    """`myapp train --layers <TAB>` (NO config on the line) also expects a
    value — stay silent so the shell does default filename completion.

    Regression: the no-config config-file+flags branch used to hijack the
    flag's value slot, returning files + every override flag instead of
    nothing."""
    out = comp.complete(app, ["myapp", "train", "--layers", ""], cword=3)
    assert out == []


# --------- plain @command option flags (the `sairen run list` case) -------
#
# A plain @command (no config-file positional) must still surface its own
# option flags from its signature, not just the global flags. Regression:
# `sairen run list <TAB>` fell back to file completion and
# `sairen run list --<TAB>` offered only the globals.


def test_plain_command_empty_word_lists_flags(app: LiquifyApp) -> None:
    """`myapp greet <TAB>` (empty incomplete) should reveal the command's own
    options + globals, instead of falling back to filename completion."""
    out = comp.complete(app, ["myapp", "greet", ""], cword=2)
    assert "--target" in out  # from `greet(target: str = "world")`
    assert "--config" in out  # a global flag


def test_plain_command_dashdash_lists_flags(app: LiquifyApp) -> None:
    """`myapp greet --<TAB>` should list the command's flags alongside globals."""
    out = comp.complete(app, ["myapp", "greet", "--"], cword=2)
    assert "--target" in out
    assert "--config" in out
    assert "--help" in out


def test_plain_command_prefix_filters_signature_flag(app: LiquifyApp) -> None:
    """A `--` prefix filters down to the matching signature flag."""
    out = comp.complete(app, ["myapp", "greet", "--ta"], cword=2)
    assert out == ["--target"]


def test_plain_command_after_flag_silent(app: LiquifyApp) -> None:
    """`myapp greet --target <TAB>` expects a value; stay silent so the shell's
    default filename completion handles it."""
    out = comp.complete(app, ["myapp", "greet", "--target", ""], cword=3)
    assert out == []


def test_plain_subapp_command_lists_globals(app: LiquifyApp) -> None:
    """A plain command in a sub-app with no params still offers the globals
    (and doesn't crash for the empty-signature case)."""
    out = comp.complete(app, ["myapp", "group", "alpha", ""], cword=3)
    assert "--config" in out
    assert "--help" in out


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


def test_bash_script_forwards_comp_line_for_quote_aware_tokenizing() -> None:
    """The bash wrapper must forward $COMP_LINE/$COMP_POINT so liquifai-complete can
    re-tokenize quote-aware (bash's own $COMP_WORDS splits "Helios Base Model")."""
    script = comp.render_script("marainer", "bash")
    assert 'COMP_LINE="$COMP_LINE"' in script
    assert 'COMP_POINT="$COMP_POINT"' in script
    # COMP_WORDS stays too, as the fallback for old installs.
    assert "COMP_WORDS=" in script


def test_bash_alias_helper_forwards_rewritten_comp_line() -> None:
    """The alias delegator (liquifai-bind-alias) must also rewrite the raw line's
    alias token to `<app> <prefix>` and forward COMP_LINE/COMP_POINT, so a bound
    alias (e.g. `melody` -> `sairen`) is quote-aware like the app's own wrapper."""
    helpers = comp.render_helpers("bash")
    assert "_liquifai_alias_complete" in helpers
    assert 'COMP_LINE="$line_env"' in helpers
    assert 'COMP_POINT="$point_env"' in helpers
    assert "${COMP_LINE:${#alias_tok}}" in helpers  # alias token -> app+prefix


def test_zsh_script_suppresses_trailing_space_for_directories() -> None:
    """Zsh's `compadd` adds a trailing space by default; for directory
    candidates we need `-S ''` so the user can keep tabbing in.
    """
    script = comp.render_script("marainer", "zsh")
    assert "compadd -U -S '' --" in script
    # Conditional on `*/` so non-directory candidates still get a trailing
    # space (the normal "ready for next arg" UX).
    assert "*/" in script


# ------------------------- words_from_comp_line ---------------------------
# Quote/escape-aware re-tokenization of bash's raw $COMP_LINE (bash's own
# $COMP_WORDS splits "Helios Base Model" on spaces — see the mandate).


@pytest.mark.parametrize(
    "line,expected_words,expected_cword",
    [
        # bare trailing space -> empty current word
        ("app cmd arg ", ["app", "cmd", "arg", ""], 3),
        # double-quoted value with spaces stays ONE word
        ('app cmd "a b c" ', ["app", "cmd", "a b c", ""], 3),
        # single-quoted value with spaces stays ONE word
        ("app cmd 'a b' ", ["app", "cmd", "a b", ""], 3),
        # backslash-escaped spaces stay ONE word
        ("app cmd a\\ b\\ c ", ["app", "cmd", "a b c", ""], 3),
        # completing the value itself (no trailing space) — unterminated quote
        ('app cmd "a b', ["app", "cmd", "a b"], 2),
        # partial command word
        ("app cm", ["app", "cm"], 1),
        # a following positional after a quoted spaced value
        ('app cmd "a b" ver', ["app", "cmd", "a b", "ver"], 3),
    ],
)
def test_words_from_comp_line(line: str, expected_words: list, expected_cword: int) -> None:
    words, cword = comp.words_from_comp_line(line, len(line))
    assert words == expected_words
    assert cword == expected_cword


def test_words_from_comp_line_respects_comp_point() -> None:
    # Cursor mid-line: only text before the cursor is tokenized.
    line = "app cmd xyz"
    words, cword = comp.words_from_comp_line(line, 5)  # -> "app c"
    assert words == ["app", "c"]
    assert cword == 1


def test_words_from_comp_line_out_of_range_point_clamps() -> None:
    words, cword = comp.words_from_comp_line("app x", 999)
    assert words == ["app", "x"] and cword == 1


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
    # EVERY command gets signature entries — a plain @command carries its
    # options in its signature too, so completion needs them to offer anything
    # beyond the global flags (regression: `run list --<TAB>` used to surface
    # only globals). Two parallel maps: raw `signature_paths` + the
    # shortest-unique-collapsed `signature_flags`.
    assert set(tree["signature_paths"].keys()) == {"greet", "train"}
    assert set(tree["signature_flags"].keys()) == {"greet", "train"}
    # `train(layers: int = 1)` — `int` isn't @configurable, so the only path is
    # the param itself; it collapses to the `--layers` flag.
    assert tree["signature_paths"]["train"] == ["layers"]
    assert tree["signature_flags"]["train"] == ["--layers"]
    # `greet(target: str = "world")` — likewise plain, contributing `--target`.
    assert tree["signature_paths"]["greet"] == ["target"]
    assert tree["signature_flags"]["greet"] == ["--target"]


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


# ----------------- signature-derived completion (no config) --------------
#
# Helper @configurable classes are declared at module scope (not inside the
# test bodies) because `typing.get_type_hints` resolves PEP 563 string
# annotations against the function's __globals__ — local-scoped classes
# inside a test wouldn't be reachable, but real-world Liquifai apps always
# import their @configurable types at the top of their CLI module (e.g.
# marainer/cli.py imports LightningTrainer), so module-scope helpers mirror
# the production shape exactly.

from confluid import configurable as _configurable  # noqa: E402


@_configurable
class _CompTestOptim:
    def __init__(self, lr: float = 1e-3, weight_decay: float = 0.0) -> None:
        self.lr = lr
        self.weight_decay = weight_decay


@_configurable
class _CompTestTrainer:
    def __init__(self, optimizer: _CompTestOptim, epochs: int = 10, name: str = "run") -> None:
        self.optimizer = optimizer
        self.epochs = epochs
        self.name = name


def _configurable_app() -> LiquifyApp:
    """An app whose script_command's annotated arg is a @configurable class.

    Mirrors the marainer pattern: ``train(trainer: LightningTrainer)``.
    """
    root = LiquifyApp(name="myapp")

    @root.script_command()
    def train(trainer: _CompTestTrainer) -> None:
        pass

    return root


def _walking_cmd(outer: _CompTestTrainer) -> None:
    pass


def _plain_cmd(layers: int = 1, name: str = "x") -> None:
    pass


def test_introspect_function_keys_walks_configurable() -> None:
    """Signature introspection (via confluid `get_hierarchy`) descends into
    @configurable types and emits the flat list of LEAF override paths — no
    configurable container roots/intermediates, and the instance-identity
    `name` param is skipped (`_CompTestTrainer.name` does not appear)."""
    keys = comp._introspect_function_keys(_walking_cmd)
    assert keys == [
        "outer.epochs",
        "outer.optimizer.lr",
        "outer.optimizer.weight_decay",
    ]


def test_introspect_function_keys_plain_annotation_lists_params() -> None:
    """Plain params are leaves; the special `name` param is skipped (matching
    `--help`/`get_hierarchy`), so `_plain_cmd(layers, name)` yields just
    `layers`."""
    keys = comp._introspect_function_keys(_plain_cmd)
    assert keys == ["layers"]


def test_complete_signature_keys_shortest_unique_paths() -> None:
    """`myapp train --` with no YAML surfaces the command's options collapsed to
    their SHORTEST-UNIQUE paths (confluid `shortest_unique_paths`), exactly like
    `--help` — a unique leaf shows as `--lr`, NOT `--trainer.optimizer.lr`.

    This is the regression the user hit on `convert-ops-export <TAB>`: the
    options were emitted verbatim as `--converter.class_name` instead of the
    unique-leaf `--class_name`."""
    app = _configurable_app()
    out = comp.complete(app, ["myapp", "train", "--"], cword=2)
    # Every leaf here is unique, so each collapses to its bare name:
    assert "--epochs" in out
    assert "--lr" in out
    assert "--weight_decay" in out
    # Configurable container roots/intermediates are NOT leaves → not offered
    # (matching `--help`); neither is the un-collapsed long form:
    assert "--trainer" not in out
    assert "--optimizer" not in out
    assert "--trainer.optimizer.lr" not in out
    assert "--trainer.epochs" not in out
    # Globals remain available alongside signature flags.
    assert "--config" in out


def test_complete_signature_keys_shared_leaf_keeps_prefix() -> None:
    """When two params share a leaf, `shortest_unique_paths` keeps enough prefix
    to disambiguate — completion must reflect that (no ambiguous bare `--lr`)."""
    root = LiquifyApp(name="myapp")

    @root.command()
    def tune(model: _CompTestOptim, optim: _CompTestOptim) -> None:  # both carry lr/weight_decay
        pass

    out = comp.complete(root, ["myapp", "tune", "--"], cword=2)
    # `lr` is shared by model + optim, so it stays prefixed both ways:
    assert "--model.lr" in out
    assert "--optim.lr" in out
    assert "--lr" not in out
    assert "--model.weight_decay" in out
    assert "--optim.weight_decay" in out


def test_complete_signature_keys_filtered_by_prefix() -> None:
    """Prefix filtering applies to (collapsed) signature flags like global flags."""
    app = _configurable_app()
    out = comp.complete(app, ["myapp", "train", "--weight"], cword=2)
    assert "--weight_decay" in out
    # `--epochs` / `--lr` start with `--` but not `--weight`:
    assert "--epochs" not in out
    assert "--lr" not in out


def test_complete_signature_union_with_config(tmp_path: Path) -> None:
    """When a YAML is on the line, completion collapses the UNION of config keys
    + signature keys to shortest-unique form in one pass.

    The YAML sets `trainer.epochs`; signature introspection knows
    `trainer.optimizer.lr` exists even though the YAML doesn't set it. Both must
    be offered (so the user can override either), each at its shortest-unique
    form.
    """
    app = _configurable_app()
    cfg = tmp_path / "train.yaml"
    cfg.write_text("trainer:\n  epochs: 5\n")
    out = comp.complete(app, ["myapp", "train", str(cfg), "--"], cword=3)
    # From the YAML (via _resolve_override_keys), unique leaf → bare:
    assert "--epochs" in out
    # From the signature (not in YAML), unique leaf → bare:
    assert "--lr" in out
    # Long forms are not offered:
    assert "--trainer.optimizer.lr" not in out


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
    _make_stub_script(
        bindir / "liquifai-complete",
        exit_code=0,
        stdout="liquifai-complete liquifai-complete",
    )
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


def test_discover_liquifai_apps_returns_sorted_order(tmp_path: Path) -> None:
    """Concurrent probing must still yield names in deterministic (sorted) bin
    order, regardless of which probe thread finishes first."""
    prefix = tmp_path / "venv"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    # Created out of alphabetical order; all are valid Liquifai responders.
    for app in ("zebra", "alpha", "mid"):
        _make_stub_script(
            bindir / app,
            exit_code=0,
            stdout=f"_{app}_completion() {{ :; }}; liquifai-complete {app}",
        )
    assert comp.discover_liquifai_apps(prefix=prefix) == ["alpha", "mid", "zebra"]


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


def test_cli_install_completions_auto_discover_empty(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    """Auto-discover finding nothing → clean no-op message, no rc written.

    Discovery is mocked to empty so this never probes the REAL venv. Probing it
    spawned every console-script with ``--show-completion`` — and the heavy ML
    CLIs (marainer/navigaitor/raidar/…) import torch before the short-circuit,
    several hitting the 15 s timeout — which made this single test ~225 s (99%
    of the suite). The real probe loop is covered deterministically by
    ``test_discover_liquifai_apps_*`` / ``test_install_for_apps_auto_discover``
    via controlled stub prefixes.
    """
    monkeypatch.setattr(comp, "discover_liquifai_apps", lambda *a, **k: [])
    target_rc = tmp_path / "rc"
    rc = comp._cli_install_completions(["--target-rc", str(target_rc), "--shell", "bash"])
    assert rc == 0
    assert "no Liquifai apps found" in capsys.readouterr().out
    assert not target_rc.exists()


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


def test_fast_complete_main_prefers_comp_line(app: LiquifyApp, capsys: Any, monkeypatch: Any, tmp_path: Path) -> None:
    # When COMP_LINE/COMP_POINT are present they win over COMP_WORDS (the deliberately
    # wrong COMP_WORDS below would yield nothing), so bash's quote-aware line drives it.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    comp.write_cache(app)
    monkeypatch.setattr(sys, "argv", ["liquifai-complete", "myapp"])
    monkeypatch.setenv("COMP_LINE", "myapp gr")
    monkeypatch.setenv("COMP_POINT", str(len("myapp gr")))
    monkeypatch.setenv("COMP_WORDS", "myapp zzz")
    monkeypatch.setenv("COMP_CWORD", "1")

    from liquifai import _fast_complete

    _fast_complete.main()
    out = capsys.readouterr().out.splitlines()
    assert "greet" in out and "group" in out


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


# ---------------------------------------------------------------------------
# Positional hints in complete_from_tree
# ---------------------------------------------------------------------------


def _app_with_positionals() -> LiquifyApp:
    """A one-sub-app, one-command app where 'download' has two positionals."""
    root = LiquifyApp(name="sairen")
    sub = LiquifyApp(name="dataset")

    @sub.command("download", positionals=["name", "version"])
    def download(name: str = "", version: str = "", path: str = "") -> None:
        pass

    root.add_app(sub, "dataset")
    return root


def test_positionals_in_serialized_tree() -> None:
    root = _app_with_positionals()
    tree = comp.serialize_app(root)
    sub_tree = tree["sub_apps"]["dataset"]
    assert "download" in sub_tree.get("positionals", {})
    assert sub_tree["positionals"]["download"] == ["name", "version"]


def test_positional_hint_at_first_slot() -> None:
    root = _app_with_positionals()
    # "sairen dataset download <TAB>" — cursor at position 3, no tokens after cmd yet
    out = comp.complete(root, ["sairen", "dataset", "download", ""], cword=3)
    assert out[0] == "<name>"


def test_positional_hint_advances_after_first_consumed() -> None:
    root = _app_with_positionals()
    # "sairen dataset download mydata <TAB>" — one positional consumed
    out = comp.complete(root, ["sairen", "dataset", "download", "mydata", ""], cword=4)
    assert out[0] == "<version>"


def test_no_positional_hint_after_all_consumed() -> None:
    root = _app_with_positionals()
    # Both positionals consumed — only flags remain
    out = comp.complete(root, ["sairen", "dataset", "download", "mydata", "1.0", ""], cword=5)
    assert not any(c.startswith("<") for c in out)


def test_positional_hint_stops_at_flag() -> None:
    root = _app_with_positionals()
    # "--path" was given before the positionals, stops counting
    out = comp.complete(root, ["sairen", "dataset", "download", "--path", "/tmp", ""], cword=5)
    # "--path /tmp" are consumed as a flag+value pair, so n_consumed=0 → hint is <name>
    assert out[0] == "<name>"


def test_no_hint_for_command_with_no_positionals() -> None:
    root = LiquifyApp(name="myapp")

    @root.command("list")
    def lst() -> None:
        pass

    out = comp.complete(root, ["myapp", "list", ""], cword=2)
    assert not any(c.startswith("<") for c in out)


def test_positional_hint_filtered_by_partial_input() -> None:
    root = _app_with_positionals()
    # User is typing "my" — "<name>" doesn't start with "my", so it's filtered out
    out = comp.complete(root, ["sairen", "dataset", "download", "my"], cword=3)
    assert "<name>" not in out


# ---------------------------------------------------------------------------
# Q2 — dynamic positional value completion (cached value providers)
# ---------------------------------------------------------------------------


@pytest.fixture
def iso_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate the liquifai cache dir under tmp_path for value-cache tests."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _app_with_provider(values: Any) -> LiquifyApp:
    """root(sairen) → dataset(ds) → download <name> whose name has a value provider."""
    root = LiquifyApp(name="sairen")
    sub = LiquifyApp(name="dataset")

    @sub.command("download", positionals=["name", "version"], completions={"name": lambda: list(values)})
    def download(name: str = "", version: str = "", path: str = "") -> None:
        pass

    @sub.command("create", positionals=["name"])  # no provider → placeholder only
    def create(name: str = "") -> None:
        pass

    root.add_app(sub, "dataset", aliases=["ds"])
    return root


def test_value_cache_roundtrip(iso_cache: Path) -> None:
    comp.write_value_cache("sairen", "k", ["a", "b"])
    assert comp.read_value_cache("sairen", "k") == ["a", "b"]
    assert comp.read_value_cache("sairen", "missing") is None


def test_serialized_tree_records_positional_completions() -> None:
    tree = comp.serialize_app(_app_with_provider(["x"]))
    sub = tree["sub_apps"]["dataset"]
    # Only the positional with a provider gets an entry; static, no dependencies.
    assert sub["positional_completions"]["download"] == {
        "name": {"key": "dataset__download__name", "kind": "static", "depends_on": []}
    }
    assert "create" not in sub["positional_completions"]


def test_completion_uses_cached_values(iso_cache: Path) -> None:
    root = _app_with_provider(["alpha", "beta", "gamma"])
    # Before refresh → placeholder.
    assert comp.complete(root, ["sairen", "dataset", "download", ""], cword=3)[0] == "<name>"
    comp.refresh_value_caches(root)
    # After refresh → real values lead the candidate list.
    out = comp.complete(root, ["sairen", "dataset", "download", ""], cword=3)
    assert out[:3] == ["alpha", "beta", "gamma"]
    # Prefix-filtered like any candidate.
    assert comp.complete(root, ["sairen", "dataset", "download", "al"], cword=3) == ["alpha"]
    # Second positional (no provider) stays a placeholder.
    assert comp.complete(root, ["sairen", "dataset", "download", "alpha", ""], cword=4)[0] == "<version>"


def test_completion_values_shared_via_alias(iso_cache: Path) -> None:
    root = _app_with_provider(["alpha", "beta"])
    comp.refresh_value_caches(root)
    # The `ds` alias resolves to the same sub-app and the same value cache key.
    out = comp.complete(root, ["sairen", "ds", "download", ""], cword=3)
    assert out[:2] == ["alpha", "beta"]


def test_refresh_value_caches_counts_and_walks_canonical_only(iso_cache: Path) -> None:
    root = _app_with_provider(["a", "b", "c"])
    written = comp.refresh_value_caches(root)
    # Exactly one provider, visited once (not twice for the alias).
    assert written == {"dataset__download__name": 3}
    specs = list(comp.iter_completion_providers(root))
    assert [s["key"] for s in specs] == ["dataset__download__name"]
    assert specs[0]["kind"] == "static"


def test_refresh_swallows_provider_errors(iso_cache: Path) -> None:
    root = LiquifyApp(name="sairen")

    def boom() -> Any:
        raise RuntimeError("offline")

    @root.command("download", positionals=["name"], completions={"name": boom})
    def download(name: str = "") -> None:
        pass

    # A failing provider is skipped, not raised; no cache written.
    assert comp.refresh_value_caches(root) == {}
    assert comp.read_value_cache("sairen", "download__name") is None
    # Completion still works, falling back to the placeholder.
    assert comp.complete(root, ["sairen", "download", ""], cword=2)[0] == "<name>"


def test_has_stale_value_caches(iso_cache: Path) -> None:
    root = _app_with_provider(["a"])
    # Missing cache → stale.
    assert comp.has_stale_value_caches(root, ttl=600.0) is True
    comp.refresh_value_caches(root)
    # Fresh cache, generous ttl → not stale.
    assert comp.has_stale_value_caches(root, ttl=600.0) is False
    # Zero ttl → everything counts as stale.
    assert comp.has_stale_value_caches(root, ttl=0.0) is True


def test_docs_and_refresh_flags_are_completable() -> None:
    root = _app_with_provider(["a"])
    out = comp.complete(root, ["sairen", "--"], cword=1)
    assert "--docs" in out and "--refresh-completions" in out


# ---------------------------------------------------------------------------
# Q2 — core integration: completions plumbing + run() flags
# ---------------------------------------------------------------------------


def test_command_stores_completions_on_function() -> None:
    root = LiquifyApp(name="sairen")

    @root.command("download", positionals=["name"], completions={"name": lambda: ["a"]})
    def download(name: str = "") -> None:
        pass

    assert set(getattr(download, "__liquifai_completions__", {})) == {"name"}


def test_operation_completions_flow_to_generated_handler() -> None:
    # @operation(completions=...) → build_commands() → the generated CLI handler
    # carries the provider so serialize_app/refresh see it.
    app = LiquifyApp(name="dataset")

    @app.operation(presentation="fields", completions={"name": lambda: ["alpha", "beta"]})
    def dataset_info(conn: Any, *, name: str) -> dict:
        return {"name": name}

    app.set_context_factory(lambda: None)
    app.set_presenter(lambda *a, **k: None)
    app.build_commands()

    handler = app._commands["info"]
    assert set(getattr(handler, "__liquifai_completions__", {})) == {"name"}
    tree = comp.serialize_app(app)
    assert tree["positional_completions"]["info"] == {"name": {"key": "info__name", "kind": "static", "depends_on": []}}


def test_run_refresh_completions_flag(iso_cache: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _app_with_provider(["alpha", "beta", "gamma"])
    monkeypatch.setattr(sys, "argv", ["sairen", "--refresh-completions"])
    set_context(None)  # type: ignore[arg-type]
    root.run()
    out = capsys.readouterr().out
    assert "Refreshed" in out and "3 values" in out
    assert comp.read_value_cache("sairen", "dataset__download__name") == ["alpha", "beta", "gamma"]


def test_run_docs_flag_renders_lines(iso_cache: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    root = LiquifyApp(name="sairen")
    sub = LiquifyApp(name="dataset")

    @sub.command("download", positionals=["name"])
    def download(name: str = "", path: str = ".") -> None:
        """Download a dataset.

        Args:
            path: Destination directory.
        """

    root.add_app(sub, "dataset")
    monkeypatch.setattr(sys, "argv", ["sairen", "dataset", "download", "--docs"])
    set_context(None)  # type: ignore[arg-type]
    root.run()
    out = capsys.readouterr().out
    assert "--path" in out and "Destination directory." in out
    assert "Current/Default Value" not in out  # lines layout, not the table


def test_background_refresh_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _app_with_provider(["a"])
    monkeypatch.delenv("LIQUIFAI_BG_REFRESH", raising=False)
    called = []

    def _spy(*a: Any, **k: Any) -> bool:
        called.append(True)
        return True

    # Background refresh is opt-in: without LIQUIFAI_BG_REFRESH it must not even
    # consult staleness (no provider can fire as a side effect of a normal run).
    monkeypatch.setattr(comp, "has_stale_value_caches", _spy)
    root._maybe_background_refresh_values()
    assert called == []


def test_background_refresh_opt_in(iso_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _app_with_provider(["a"])
    monkeypatch.setenv("LIQUIFAI_BG_REFRESH", "1")
    seen = []

    def _not_stale(*a: Any, **k: Any) -> bool:
        seen.append(True)
        return False  # report fresh → opt-in path consults staleness but spawns no thread

    monkeypatch.setattr(comp, "has_stale_value_caches", _not_stale)
    root._maybe_background_refresh_values()
    assert seen == [True]


# ---------------------------------------------------------------------------
# Spaces in candidates — wire-protocol robustness (split / unescape / escape)
# ---------------------------------------------------------------------------


def test_split_comp_words_preserves_spaces_newline_join() -> None:
    # Newline-joined transport (bash IFS=$'\n', zsh ${(F)words}, fish join \n):
    # an embedded space stays in one token; a half-typed backslash-escape unescapes.
    assert comp.split_comp_words("myapp\ndataset\ndownload\nTest Scr") == [
        "myapp",
        "dataset",
        "download",
        "Test Scr",
    ]
    assert comp.split_comp_words("myapp\ndataset\ndownload\nTest\\ Scr")[-1] == "Test Scr"
    # Trailing empty (bare TAB) is preserved.
    assert comp.split_comp_words("myapp\ndownload\n")[-1] == ""


def test_split_comp_words_old_space_join_fallback() -> None:
    # An old (pre-upgrade) space-joined wrapper has no newline → fall back to
    # whitespace split (no regression for installs that haven't been refreshed).
    assert comp.split_comp_words("myapp download foo") == ["myapp", "download", "foo"]


def test_escape_candidate() -> None:
    assert comp.escape_candidate("alpha") == "alpha"  # no specials → unchanged
    assert comp.escape_candidate("Test Script VB") == "Test\\ Script\\ VB"
    assert comp.escape_candidate("a(b)c") == "a\\(b\\)c"
    assert comp.escape_candidate("--path") == "--path"
    assert comp.escape_candidate("<name>") == "<name>"  # placeholder left verbatim


def test_completion_matches_space_containing_prefix(iso_cache: Path) -> None:
    root = _app_with_provider(["Test Script VB", "Test Other", "alpha"])
    comp.refresh_value_caches(root)
    tree = comp.serialize_app(root)
    # "download Test <TAB>" — the typed prefix has a space (one logical token);
    # it must match BOTH "Test …" names (not split into "Test" + "").
    both = [c for c in comp.complete_from_tree(tree, ["sairen", "dataset", "download", "Test "], cword=3)]
    assert both == ["Test Script VB", "Test Other"]
    # "download Test S<TAB>" narrows to the one whose space-containing name matches.
    one = [c for c in comp.complete_from_tree(tree, ["sairen", "dataset", "download", "Test S"], cword=3)]
    assert one == ["Test Script VB"]


# ---------------------------------------------------------------------------
# Dependent positionals — <version> values depend on the typed <name>
# ---------------------------------------------------------------------------


def _app_with_dependent() -> LiquifyApp:
    """download <name> <version> where version depends on the chosen name."""
    versions = {"alpha": ["1.0", "1.1"], "Test Script VB": ["2.0"]}
    root = LiquifyApp(name="sairen")
    sub = LiquifyApp(name="dataset")

    @sub.command(
        "download",
        positionals=["name", "version"],
        completions={
            "name": lambda: list(versions),
            "version": lambda inputs: versions.get(inputs.get("name", ""), []),
        },
    )
    def download(name: str = "", version: str = "", path: str = "") -> None:
        pass

    root.add_app(sub, "dataset", aliases=["ds"])
    return root


def test_dependent_kind_recorded_in_tree() -> None:
    sub = comp.serialize_app(_app_with_dependent())["sub_apps"]["dataset"]
    pc = sub["positional_completions"]["download"]
    assert pc["name"]["kind"] == "static"
    assert pc["version"] == {"key": "dataset__download__version", "kind": "dependent", "depends_on": ["name"]}


def test_dependent_version_completes_from_typed_name(iso_cache: Path) -> None:
    root = _app_with_dependent()
    written = comp.refresh_value_caches(root)
    # Static name (2) + dependent version pre-enumerated for each name (2+1=3).
    assert written == {"dataset__download__name": 2, "dataset__download__version": 3}
    tree = comp.serialize_app(root)
    # download alpha <TAB> → alpha's versions.
    assert [c for c in comp.complete_from_tree(tree, ["sairen", "dataset", "download", "alpha", ""], cword=4)][:2] == [
        "1.0",
        "1.1",
    ]
    # download "Test Script VB" <TAB> → that dataset's versions (space-containing name).
    assert comp.complete_from_tree(tree, ["sairen", "dataset", "download", "Test Script VB", ""], cword=4)[0] == "2.0"
    # An unknown name has no dependent cache → placeholder.
    assert comp.complete_from_tree(tree, ["sairen", "dataset", "download", "nope", ""], cword=4)[0] == "<version>"


def test_dependent_refresh_respects_max_combos(iso_cache: Path) -> None:
    versions = {n: [f"{n}.v"] for n in ["a", "b", "c", "d"]}
    root = LiquifyApp(name="sairen")

    @root.command(
        "get",
        positionals=["name", "version"],
        completions={"name": lambda: list(versions), "version": lambda inputs: versions[inputs["name"]]},
    )
    def get(name: str = "", version: str = "") -> None:
        pass

    comp.refresh_value_caches(root, max_combos=2)  # only the first 2 names get a version cache
    tree = comp.serialize_app(root)
    assert comp.complete_from_tree(tree, ["sairen", "get", "a", ""], cword=3)[0] == "a.v"
    assert comp.complete_from_tree(tree, ["sairen", "get", "c", ""], cword=3)[0] == "<version>"  # beyond the cap


# ---------------------------------------------------------------------------
# Lazy self-heal of dependent caches (new/stale versions refresh in background)
# ---------------------------------------------------------------------------


def test_complete_lazy_refresh_called_on_missing_cache(iso_cache: Path) -> None:
    root = _app_with_dependent()
    comp.refresh_value_caches(root)  # caches alpha + "Test Script VB" versions
    tree = comp.serialize_app(root)
    calls = []
    # A brand-new dataset the bulk refresh never saw → no cache → lazy refresh queued,
    # placeholder returned now (non-blocking).
    out = comp.complete_from_tree(
        tree, ["sairen", "dataset", "download", "newds", ""], cword=4, lazy_refresh=lambda k, i: calls.append((k, i))
    )
    assert out[0] == "<version>"
    assert calls == [("dataset__download__version", {"name": "newds"})]


def test_complete_lazy_refresh_skipped_when_fresh(iso_cache: Path) -> None:
    root = _app_with_dependent()
    comp.refresh_value_caches(root)
    tree = comp.serialize_app(root)
    calls = []
    # alpha's version cache is fresh (just refreshed) → no lazy refresh, values served.
    out = comp.complete_from_tree(
        tree, ["sairen", "dataset", "download", "alpha", ""], cword=4, lazy_refresh=lambda k, i: calls.append((k, i))
    )
    assert out[:2] == ["1.0", "1.1"]
    assert calls == []


def test_complete_lazy_refresh_called_when_stale(iso_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _app_with_dependent()
    comp.refresh_value_caches(root)
    tree = comp.serialize_app(root)
    monkeypatch.setattr(comp, "DEPENDENT_REFRESH_TTL", -1.0)  # treat any cache as stale
    calls = []
    out = comp.complete_from_tree(
        tree, ["sairen", "dataset", "download", "alpha", ""], cword=4, lazy_refresh=lambda k, i: calls.append((k, i))
    )
    assert out[:2] == ["1.0", "1.1"]  # stale values still served immediately
    assert calls == [("dataset__download__version", {"name": "alpha"})]  # ...and a refresh queued


def test_refresh_one(iso_cache: Path) -> None:
    root = _app_with_dependent()
    key = "dataset__download__version"
    assert comp.refresh_one(root, key, {"name": "alpha"}) == 2
    assert comp.read_dependent_value_cache("sairen", key, {"name": "alpha"}) == ["1.0", "1.1"]
    # Unknown key → no match → None, nothing written.
    assert comp.refresh_one(root, "nope__nope", {"name": "alpha"}) is None


def test_run_refresh_completion_value_flag(iso_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _app_with_dependent()
    key = "dataset__download__version"
    payload = json.dumps({"key": key, "inputs": {"name": "Test Script VB"}})
    monkeypatch.setattr(sys, "argv", ["sairen", "--refresh-completion-value", payload])
    set_context(None)  # type: ignore[arg-type]
    root.run()
    # The detached-helper code path wrote that dataset's versions to its per-input cache.
    assert comp.read_dependent_value_cache("sairen", key, {"name": "Test Script VB"}) == ["2.0"]


def test_lazy_spawner_opt_out_and_throttle(iso_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    popen_calls = []
    monkeypatch.setattr(comp.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    spawn = comp.make_lazy_refresh_spawner("sairen")

    # Opt-out env → no spawn.
    monkeypatch.setenv("LIQUIFAI_NO_LAZY_COMPLETE", "1")
    spawn("dataset__download__version", {"name": "x"})
    assert popen_calls == []

    # Enabled: first call spawns; an immediate second call is throttled (marker fresh).
    monkeypatch.delenv("LIQUIFAI_NO_LAZY_COMPLETE", raising=False)
    spawn("dataset__download__version", {"name": "x"})
    spawn("dataset__download__version", {"name": "x"})
    assert len(popen_calls) == 1
    assert popen_calls[0][0][0] == "sairen" and popen_calls[0][0][1] == "--refresh-completion-value"


# ---------------------------------------------------------------------------
# "<…>-updated" notice — shown only when a lazy self-heal changed the values
# ---------------------------------------------------------------------------


def test_no_notice_after_bulk_or_unchanged_refresh(iso_cache: Path) -> None:
    root = _app_with_dependent()
    comp.refresh_value_caches(root)  # bulk refresh never stamps changed_at
    tree = comp.serialize_app(root)
    out = comp.complete_from_tree(tree, ["sairen", "dataset", "download", "alpha", ""], cword=4)
    assert "<version-updated>" not in out and out[:2] == ["1.0", "1.1"]


def test_notice_shown_when_self_heal_changes_values(iso_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"alpha": ["1.0"]}
    root = LiquifyApp(name="sairen")
    sub = LiquifyApp(name="dataset")

    @sub.command(
        "download",
        positionals=["name", "version"],
        completions={"name": lambda: list(versions), "version": lambda inp: versions[inp["name"]]},
    )
    def download(name: str = "", version: str = "", path: str = "") -> None:
        pass

    root.add_app(sub, "dataset")
    comp.refresh_value_caches(root)
    tree = comp.serialize_app(root)
    key = "dataset__download__version"

    # A new version is published; the targeted self-heal picks it up → values changed.
    versions["alpha"] = ["1.0", "2.0"]
    comp.refresh_one(root, key, {"name": "alpha"})

    out = comp.complete_from_tree(tree, ["sairen", "dataset", "download", "alpha", ""], cword=4)
    assert out[0] == "<version-updated>" and out[1:3] == ["1.0", "2.0"]  # notice leads, values follow
    # Typing a real prefix drops the notice (it's a `<…>` hint).
    assert comp.complete_from_tree(tree, ["sairen", "dataset", "download", "alpha", "1"], cword=4) == ["1.0"]
    # The notice ages out after the window.
    monkeypatch.setattr(comp, "DEPENDENT_NOTICE_WINDOW", 0.0)
    assert "<version-updated>" not in comp.complete_from_tree(
        tree, ["sairen", "dataset", "download", "alpha", ""], cword=4
    )


def test_refresh_one_stamps_changed_only_on_change(iso_cache: Path) -> None:
    versions = {"x": ["1.0"]}
    root = LiquifyApp(name="sairen")

    @root.command(
        "get", positionals=["name", "version"], completions={"name": lambda: ["x"], "version": lambda i: versions["x"]}
    )
    def get(name: str = "", version: str = "") -> None:
        pass

    key = "get__version"
    comp.refresh_one(root, key, {"name": "x"})  # first population → stamped
    assert comp._dependent_changed_recently("sairen", key, {"name": "x"}, 60.0)
    stamp1 = comp._dependent_changed_at("sairen", key, {"name": "x"})
    comp.refresh_one(root, key, {"name": "x"})  # unchanged → stamp preserved, not bumped
    assert comp._dependent_changed_at("sairen", key, {"name": "x"}) == stamp1


# ---------------------------------------------------------------------------
# Static positionals also self-heal (a never-refreshed name list fills on use)
# ---------------------------------------------------------------------------


def test_static_self_heal_triggered_when_missing(iso_cache: Path) -> None:
    root = _app_with_provider(["alpha", "beta"])  # download.name is a STATIC provider
    tree = comp.serialize_app(root)  # no refresh → cache missing
    calls = []
    out = comp.complete_from_tree(
        tree, ["sairen", "dataset", "download", ""], cword=3, lazy_refresh=lambda k, i: calls.append((k, i))
    )
    assert out[0] == "<name>"  # placeholder now (nothing cached)
    assert calls == [("dataset__download__name", {})]  # ...and a STATIC refresh (empty inputs) queued


def test_static_self_heal_skipped_when_fresh(iso_cache: Path) -> None:
    root = _app_with_provider(["alpha", "beta"])
    comp.refresh_value_caches(root)  # populate the static cache
    tree = comp.serialize_app(root)
    calls = []
    out = comp.complete_from_tree(
        tree, ["sairen", "dataset", "download", ""], cword=3, lazy_refresh=lambda k, i: calls.append((k, i))
    )
    assert out[:2] == ["alpha", "beta"] and calls == []  # served from cache, no refresh queued


def test_refresh_one_handles_static_and_dependent(iso_cache: Path) -> None:
    root = _app_with_dependent()
    # Static: empty inputs → refreshes the whole name list.
    assert comp.refresh_one(root, "dataset__download__name", {}) == 2
    assert comp.read_value_cache("sairen", "dataset__download__name") == ["alpha", "Test Script VB"]
    # Dependent: non-empty inputs → that combo only.
    assert comp.refresh_one(root, "dataset__download__version", {"name": "alpha"}) == 2
    # Kind/inputs mismatch → no match.
    assert comp.refresh_one(root, "dataset__download__name", {"name": "x"}) is None
    assert comp.refresh_one(root, "dataset__download__version", {}) is None
