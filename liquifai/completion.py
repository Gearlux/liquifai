"""Shell completion for :class:`liquifai.core.LiquifyApp`.

Implements a Typer/Click-shaped wire protocol so any LiquifyApp gets
bash/zsh/fish tab completion for free.

Architecture (fast path):
    1. ``--install-completion`` snapshots the static command tree to
       ``~/.cache/liquifai/<app>.json`` and embeds a tiny shell function in
       the user's rc file.
    2. On TAB the rc function calls the standalone ``liquifai-complete``
       binary (registered by liquifai) — NOT the app — so the heavy
       app-side imports (torch, ultralytics, plugins, …) never load.
    3. ``liquifai-complete`` reads the JSON cache and computes candidates
       via :func:`complete_from_tree`. Override-key suggestions lazily
       import confluid only when needed.
    4. Every successful ``app.run()`` rewrites the cache so plugin/command
       changes propagate.

This module imports only stdlib at module level. confluid is imported
lazily inside :func:`_resolve_override_keys`.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp


SHELLS: List[str] = ["bash", "zsh", "fish"]
CACHE_VERSION: int = 1

GLOBAL_FLAGS: List[str] = [
    "--config",
    "-c",
    "--scope",
    "-s",
    "--debug",
    "-d",
    "--level",
    "--console-level",
    "--file-level",
    "--log-dir",
    "--help",
    "--install-completion",
    "--show-completion",
]

PATH_VALUE_FLAGS: Set[str] = {"--config", "-c", "--log-dir"}
SHELL_VALUE_FLAGS: Set[str] = {"--install-completion", "--show-completion"}
GLOBAL_VALUE_FLAGS: Set[str] = (
    PATH_VALUE_FLAGS | SHELL_VALUE_FLAGS | {"--scope", "-s", "--level", "--console-level", "--file-level"}
)


# ---------------------------------------------------------------------------
# Shell detection + script templates
# ---------------------------------------------------------------------------


def detect_shell() -> str:
    """Return the basename of ``$SHELL`` if recognized, else ``"bash"``."""
    name = Path(os.environ.get("SHELL", "/bin/bash")).name
    return name if name in SHELLS else "bash"


def cache_dir() -> Path:
    """Per-XDG cache directory for liquifai completion data."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "liquifai"


def cache_path(app_name: str) -> Path:
    return cache_dir() / f"{app_name}.json"


_BASH_TEMPLATE = """\
_{prog}_completion() {
    local IFS=$'\\n'
    local raw
    raw=$(env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD \\
        liquifai-complete {prog} 2>/dev/null)
    COMPREPLY=()
    for item in $raw; do
        COMPREPLY+=("$item")
    done
    return 0
}
complete -o default -F _{prog}_completion {prog}
"""

_ZSH_TEMPLATE = """\
#compdef {prog}

# Self-bootstrap compinit so this works even in vanilla zsh setups that
# never ran `autoload -Uz compinit; compinit` themselves.
if ! whence compdef >/dev/null 2>&1; then
    autoload -Uz compinit
    compinit -u 2>/dev/null
fi

_{prog}_completion() {
    local -a response
    response=("${(@f)$(env COMP_WORDS=\"${words[*]}\" \\
        COMP_CWORD=$((CURRENT-1)) \\
        liquifai-complete {prog} 2>/dev/null)}")
    if (( ${#response[@]} == 0 )); then
        _files
        return
    fi
    compadd -U -- "${response[@]}"
}
compdef _{prog}_completion {prog}
"""

_FISH_TEMPLATE = """\
function __fish_{prog}_complete
    set -l prev_words (commandline -opc)
    set -l cur_word (commandline -ct)
    set -l all_words $prev_words $cur_word
    set -l joined (string join " " -- $all_words)
    set -l cword (count $prev_words)
    env COMP_WORDS="$joined" COMP_CWORD="$cword" liquifai-complete {prog} 2>/dev/null
end
complete -c {prog} -f -a "(__fish_{prog}_complete)"
"""

# Shared helpers (one block per shell, defined once even when multiple
# liquifai apps install). `liquifai-bind-alias <alias> <app> [<prefix>...]`
# wires shell completion for an alias by rewriting COMP_WORDS / CURRENT
# before delegating to the standard `liquifai-complete` entry.
_BASH_HELPERS = r"""
# Shared body invoked by every per-alias delegator. Builds COMP_WORDS as
# `<prefix> <typed-rest>`, recomputes COMP_CWORD, and delegates to the
# fast `liquifai-complete` entry. We iterate manually instead of using
# ${arr[*]:n} because bash 3.2 leaks a stray \x7f byte there for empty
# trailing elements (becomes a bogus incomplete prefix).
_liquifai_alias_complete() {
    local prefix_str="$1"
    local prefix_len="$2"
    local app="$3"
    local cur=""
    local _i _n=${#COMP_WORDS[@]}
    for ((_i=1; _i<_n; _i++)); do
        if [ $_i -eq 1 ]; then
            cur="${COMP_WORDS[_i]}"
        else
            cur="$cur ${COMP_WORDS[_i]}"
        fi
    done
    local words="$prefix_str $cur"
    local cword=$((COMP_CWORD + prefix_len - 1))
    local raw
    raw=$(env COMP_WORDS="$words" COMP_CWORD="$cword" liquifai-complete "$app" 2>/dev/null)
    COMPREPLY=()
    local line
    while IFS= read -r line; do
        [ -n "$line" ] && COMPREPLY+=("$line")
    done <<< "$raw"
    return 0
}

# Public helper. Usage:
#   alias mt='marainer train'
#   liquifai-bind-alias mt marainer train
liquifai-bind-alias() {
    if [ "$#" -lt 2 ]; then
        echo "usage: liquifai-bind-alias <alias-name> <app> [<prefix-args>...]" >&2
        return 1
    fi
    local alias_name="$1"
    local app="$2"
    shift 2
    local prefix_args=("$@")
    local prefix_len=$((${#prefix_args[@]} + 1))
    local prefix_str="$app"
    local arg
    for arg in "${prefix_args[@]}"; do
        prefix_str="$prefix_str $arg"
    done
    eval "
    _liquifai_alias_${alias_name}() {
        _liquifai_alias_complete '${prefix_str}' ${prefix_len} '${app}'
    }
    complete -o default -F _liquifai_alias_${alias_name} ${alias_name}
    "
}
"""

_ZSH_HELPERS = r"""
_liquifai_alias_complete() {
    local prefix_str="$1"
    local prefix_len="$2"
    local app="$3"
    local cur="${(j: :)words[2,-1]}"
    local merged="$prefix_str $cur"
    local cword=$((CURRENT + prefix_len - 2))
    local -a response
    response=("${(@f)$(env COMP_WORDS="$merged" COMP_CWORD="$cword" liquifai-complete "$app" 2>/dev/null)}")
    if (( ${#response[@]} == 0 )); then
        _files
        return
    fi
    compadd -U -- "${response[@]}"
}

# Public helper. Usage:
#   alias mt='marainer train'
#   liquifai-bind-alias mt marainer train
liquifai-bind-alias() {
    if (( $# < 2 )); then
        echo "usage: liquifai-bind-alias <alias-name> <app> [<prefix-args>...]" >&2
        return 1
    fi
    local alias_name="$1"
    local app="$2"
    shift 2
    local prefix_args=("$@")
    local prefix_len=$((${#prefix_args[@]} + 1))
    local prefix_str="$app"
    local arg
    for arg in "${prefix_args[@]}"; do
        prefix_str="$prefix_str $arg"
    done
    eval "
    _liquifai_alias_${alias_name}() {
        _liquifai_alias_complete '${prefix_str}' ${prefix_len} '${app}'
    }
    compdef _liquifai_alias_${alias_name} ${alias_name}
    "
}
"""

_HELPERS_MARKER = "# >>> liquifai shared helpers >>>"
_HELPERS_END_MARKER = "# <<< liquifai shared helpers <<<"


def render_script(prog: str, shell: str) -> str:
    """Render the shell completion script for ``prog`` in ``shell``."""
    if shell not in SHELLS:
        raise ValueError(f"Unsupported shell {shell!r}; expected one of {SHELLS}")
    template = {"bash": _BASH_TEMPLATE, "zsh": _ZSH_TEMPLATE, "fish": _FISH_TEMPLATE}[shell]
    return template.replace("{prog}", prog)


def render_helpers(shell: str) -> str:
    """Render the shared shell helpers (``liquifai-bind-alias`` etc.)."""
    if shell == "fish":
        return ""
    if shell not in ("bash", "zsh"):
        raise ValueError(f"Unsupported shell {shell!r}; expected one of {SHELLS}")
    return _BASH_HELPERS if shell == "bash" else _ZSH_HELPERS


def _splice_block(text: str, start_marker: str, end_marker: str, new_block: str) -> str:
    """Replace an existing ``start_marker``..``end_marker`` block, or append it."""
    if start_marker in text and end_marker in text:
        start = text.index(start_marker)
        end = text.index(end_marker) + len(end_marker)
        if end < len(text) and text[end] == "\n":
            end += 1
        replacement = new_block
        if start > 0 and text[start - 1] != "\n":
            replacement = "\n" + replacement
        return text[:start] + replacement + text[end:]
    prefix = "" if not text or text.endswith("\n") else "\n"
    return text + prefix + "\n" + new_block


def install_script(prog: str, shell: str, home: Optional[Path] = None) -> Path:
    """Install completion for ``prog`` in ``shell``. Idempotent.

    Embeds the rendered script directly in the rc file (bash/zsh) or the
    fish completions directory — never an ``eval "$(prog --show-completion)"``
    callback, because that would re-invoke the (slow) app on every shell
    startup. For bash/zsh, also installs (or refreshes) a single shared
    ``# >>> liquifai shared helpers >>>`` block providing
    :func:`liquifai-bind-alias` so user aliases can opt in to completion.

    Returns the path that was created or modified.
    """
    if shell not in SHELLS:
        raise ValueError(f"Unsupported shell {shell!r}; expected one of {SHELLS}")
    home = home or Path.home()

    if shell == "fish":
        target = home / ".config" / "fish" / "completions" / f"{prog}.fish"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_script(prog, shell))
        return target

    rc = home / (".bashrc" if shell == "bash" else ".zshrc")
    existing = rc.read_text() if rc.exists() else ""

    helpers_body = render_helpers(shell).rstrip("\n")
    helpers_block = f"{_HELPERS_MARKER}\n{helpers_body}\n{_HELPERS_END_MARKER}\n"
    existing = _splice_block(existing, _HELPERS_MARKER, _HELPERS_END_MARKER, helpers_block)

    marker = f"# >>> liquifai completion for {prog} >>>"
    end_marker = f"# <<< liquifai completion for {prog} <<<"
    body = render_script(prog, shell).rstrip("\n")
    app_block = f"{marker}\n{body}\n{end_marker}\n"
    existing = _splice_block(existing, marker, end_marker, app_block)

    rc.write_text(existing)
    return rc


# ---------------------------------------------------------------------------
# Cache (static command-tree snapshot)
# ---------------------------------------------------------------------------


def serialize_app(app: "LiquifyApp") -> Dict[str, Any]:
    """Snapshot the static command tree of ``app`` to a JSON-friendly dict."""
    return {
        "name": app.name,
        "commands": list(app._commands.keys()),
        "script_cmds": sorted(app._script_cmds),
        "sub_apps": {n: serialize_app(s) for n, s in app._sub_apps.items()},
    }


def write_cache(app: "LiquifyApp") -> Path:
    """Write the static command tree for ``app`` to disk. Best-effort."""
    target = cache_path(app.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CACHE_VERSION, "tree": serialize_app(app)}
    target.write_text(json.dumps(payload))
    return target


def read_cache(app_name: str) -> Optional[Dict[str, Any]]:
    """Read the static command tree. Returns None if missing or unreadable."""
    target = cache_path(app_name)
    if not target.exists():
        return None
    try:
        with target.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    tree = data.get("tree")
    return tree if isinstance(tree, dict) else None


# ---------------------------------------------------------------------------
# Candidate computation
# ---------------------------------------------------------------------------


def complete(app: "LiquifyApp", words: List[str], cword: int) -> List[str]:
    """Convenience wrapper: snapshot ``app`` then call :func:`complete_from_tree`."""
    return complete_from_tree(serialize_app(app), words, cword)


def complete_from_tree(tree: Dict[str, Any], words: List[str], cword: int) -> List[str]:
    """Compute completion candidates from a serialized command tree.

    Args:
        tree: A dict produced by :func:`serialize_app`.
        words: Tokenized command line including the program name at index 0.
        cword: Index of the word being completed (0-based).

    Returns:
        Candidates, one per line. Empty list means "no suggestion".
    """
    parsed = words[1:cword]
    incomplete = words[cword] if 0 <= cword < len(words) else ""
    prev = words[cword - 1] if cword - 1 >= 1 else ""

    if prev in PATH_VALUE_FLAGS:
        return _file_candidates(incomplete, exts=None)
    if prev in SHELL_VALUE_FLAGS:
        return [s for s in SHELLS if s.startswith(incomplete)]
    if prev in GLOBAL_VALUE_FLAGS:
        return []

    cur = tree
    cmd_name: Optional[str] = None
    config_path: Optional[Path] = None
    consumed_config = False

    i = 0
    while i < len(parsed):
        tok = parsed[i]
        if cmd_name is None and tok in cur["sub_apps"]:
            cur = cur["sub_apps"][tok]
            i += 1
            continue
        if cmd_name is None and tok in cur["commands"]:
            cmd_name = tok
            i += 1
            if cmd_name in cur["script_cmds"] and i < len(parsed) and not parsed[i].startswith("-"):
                p = Path(parsed[i])
                if not p.suffix:
                    p = p.with_suffix(".yaml")
                config_path = p
                consumed_config = True
                i += 1
            continue
        if tok in PATH_VALUE_FLAGS and i + 1 < len(parsed):
            if tok in ("--config", "-c"):
                config_path = Path(parsed[i + 1])
            i += 2
            continue
        if tok in GLOBAL_VALUE_FLAGS and i + 1 < len(parsed):
            i += 2
            continue
        i += 1

    if cmd_name is None:
        if incomplete.startswith("-"):
            return _filter_prefix(GLOBAL_FLAGS, incomplete)
        return _filter_prefix(list(cur["commands"]) + list(cur["sub_apps"].keys()), incomplete)

    is_script_cmd = cmd_name in cur["script_cmds"]

    if is_script_cmd and not consumed_config and not incomplete.startswith("-"):
        return _file_candidates(incomplete, exts=["yaml", "yml"])

    # Once a script_command has consumed its config (or any time the user
    # is typing a flag), suggest globals + dotted override keys. Empty
    # incomplete is included so the user gets a hint even before typing
    # the first dash.
    if is_script_cmd and consumed_config and prev.startswith("--") and prev not in GLOBAL_VALUE_FLAGS:
        # User just typed `--<key>`; the next token is its value. We don't
        # know the value type, so stay silent and let the shell's default
        # filename completion kick in.
        return []

    if incomplete.startswith("-") or (is_script_cmd and consumed_config):
        candidates = list(GLOBAL_FLAGS)
        if is_script_cmd and config_path is not None and config_path.exists():
            try:
                candidates.extend(f"--{k}" for k in _resolve_override_keys(config_path))
            except Exception:
                pass
        return _filter_prefix(candidates, incomplete)

    return []


def _filter_prefix(items: List[str], prefix: str) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for it in items:
        if it.startswith(prefix) and it not in seen:
            out.append(it)
            seen.add(it)
    return out


def _file_candidates(incomplete: str, exts: Optional[List[str]]) -> List[str]:
    """List filesystem entries that match ``incomplete``.

    Directories always pass through (so the user can drill into them).
    Regular files are kept iff they end with one of ``exts`` (when given).
    """
    if "/" in incomplete:
        dirpath, partial = os.path.split(incomplete)
        dirpath = dirpath or "/"
    else:
        dirpath, partial = ".", incomplete
    try:
        entries = os.listdir(dirpath)
    except OSError:
        return []
    out: List[str] = []
    for entry in sorted(entries):
        if not entry.startswith(partial):
            continue
        full = entry if dirpath == "." else os.path.join(dirpath, entry)
        if os.path.isdir(full):
            out.append(full + "/")
            continue
        if exts is None or any(entry.endswith("." + e) for e in exts):
            out.append(full)
    return out


def _resolve_override_keys(config_path: Path) -> List[str]:
    """Walk ``config_path`` and return dotted keys for ``--<key>`` overrides.

    Lazily imports confluid so the fast path stays ~stdlib-only when no
    config is on the command line.
    """
    import confluid

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        raw = confluid.load_config(config_path)
        cfg = confluid.load(raw, flow=False)
    keys: List[str] = []
    _walk_keys(cfg, prefix="", out=keys)
    return sorted(set(keys))


def _walk_keys(obj: Any, prefix: str, out: List[str], depth: int = 0, max_depth: int = 8) -> None:
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue
            full = f"{prefix}.{k}" if prefix else k
            out.append(full)
            _walk_keys(v, full, out, depth + 1, max_depth)
        return
    kwargs = getattr(obj, "kwargs", None)
    if isinstance(kwargs, dict):
        _walk_keys(kwargs, prefix, out, depth + 1, max_depth)
