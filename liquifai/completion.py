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

import concurrent.futures
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp


SHELLS: List[str] = ["bash", "zsh", "fish"]
# v3: command option flags are stored as shortest-unique-path-collapsed
# ``signature_flags`` (+ raw ``signature_paths``), replacing the v2
# ``signature_keys`` ``{param: [subs]}`` map. Bumping invalidates stale caches
# so they are rewritten on the next run / ``--help``.
CACHE_VERSION: int = 3

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
    # If the sole auto-insert candidate is a directory (ends in `/`),
    # suppress bash's trailing space so the user can keep tabbing deeper.
    # `compopt` is bash 4+; macOS ships bash 3.2, so guard the call (the
    # trailing space silently returns on old bash — acceptable degradation).
    if [[ ${#COMPREPLY[@]} -eq 1 && "${COMPREPLY[0]}" == */ ]]; then
        command -v compopt >/dev/null 2>&1 && compopt -o nospace
    fi
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
    # Directory candidates (ending in `/`) get `-S ''` so zsh doesn't
    # add a trailing space — lets the user keep tabbing into the dir.
    local item
    for item in "${response[@]}"; do
        if [[ "$item" == */ ]]; then
            compadd -U -S '' -- "$item"
        else
            compadd -U -- "$item"
        fi
    done
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


def install_script(
    prog: str,
    shell: str,
    home: Optional[Path] = None,
    target_rc: Optional[Path] = None,
) -> Path:
    """Install completion for ``prog`` in ``shell``. Idempotent.

    Embeds the rendered script directly in the rc file (bash/zsh) or the
    fish completions directory — never an ``eval "$(prog --show-completion)"``
    callback, because that would re-invoke the (slow) app on every shell
    startup. For bash/zsh, also installs (or refreshes) a single shared
    ``# >>> liquifai shared helpers >>>`` block providing
    :func:`liquifai-bind-alias` so user aliases can opt in to completion.

    When ``target_rc`` is provided (bash/zsh only), the helpers + per-app
    block are written into that file instead of ``home/.bashrc`` (or
    ``.zshrc``). This lets a project-level bootstrap install completion
    into a workspace-local rc file (e.g. sourced from ``project.bashrc``)
    without polluting the user's global shell rc. ``target_rc`` is
    ignored for fish, which always uses the per-completion file layout
    under ``~/.config/fish/completions``.

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

    if target_rc is not None:
        rc = Path(target_rc)
        rc.parent.mkdir(parents=True, exist_ok=True)
    else:
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
# Workspace bootstrap helpers
# ---------------------------------------------------------------------------


# Entries in `sys.prefix/bin/` we never want to probe — neither plausible
# Liquifai apps nor cheap to invoke. Patterns are matched as prefixes.
_NON_APP_BIN_PREFIXES: List[str] = [
    "python",
    "pip",
    "activate",
    "deactivate",
    "liquifai-",  # liquifai-complete, liquifai-install-completions
    "uv",
    "ruff",
    "black",
    "isort",
    "flake8",
    "mypy",
    "pytest",
    "coverage",
    "wheel",
    "twine",
    "jupyter",
    "ipython",
    "tensorboard",
    "mlflow",
    "f2py",
    "normalizer",
    "httpx",
    "tqdm",
    "huggingface-cli",
    "transformers-cli",
    "torch",
    "convert-",
]


def _probe_is_liquifai_app(entry: Path, timeout: float) -> bool:
    """Return True iff executing ``entry`` identifies it as a Liquifai app.

    Runs ``<entry> --show-completion bash`` and looks for the Liquifai marker
    ``liquifai-complete <name>`` that :func:`render_script` always emits.
    A Liquifai app short-circuits that flag before Confluid bootstrap, but a
    heavy app still runs its module-import side effects first (e.g. marainer
    pulling in PyTorch Lightning at the top of ``cli.py``), so ``timeout`` is
    sized for the slowest known import chain, not the bare short-circuit cost.
    Click/Typer apps accept ``--show-completion`` too but emit their own
    dispatch logic, so the marker check filters them out. Any probe failure
    (non-zero exit, timeout, OSError) maps to False so a single bad binary
    never aborts discovery.
    """
    name = entry.name
    try:
        res = subprocess.run(
            [str(entry), "--show-completion", "bash"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if res.returncode != 0:
        return False
    # Decode loosely so binary noise from a non-Liquifai responder can't crash us.
    out = res.stdout.decode("utf-8", errors="replace")
    return f"liquifai-complete {name}" in out


def discover_liquifai_apps(prefix: Optional[Path] = None, timeout: float = 15.0) -> List[str]:
    """Return the names of Liquifai apps installed in ``prefix``'s bin dir.

    Iterates ``<prefix>/bin/*`` (defaulting to ``sys.prefix``), skips obvious
    non-CLI / non-Liquifai entries (:data:`_NON_APP_BIN_PREFIXES`), and probes
    each remaining executable with :func:`_probe_is_liquifai_app`.

    The probes run **concurrently** in a thread pool. Each probe spawns the
    real CLI, and heavy apps import their full stack (torch, Lightning, …)
    before the ``--show-completion`` short-circuit — so a *serial* walk of a
    populated ML venv costs the SUM of per-probe times (minutes: dozens of
    binaries, several hitting the 15 s timeout). The work is pure subprocess
    I/O, so a thread pool bounds the wall-clock cost to roughly the slowest
    single probe. Results preserve the sorted bin order (futures are collected
    in submission order), so discovery stays deterministic regardless of which
    thread finishes first.
    """
    bindir = Path(prefix or sys.prefix) / "bin"
    if not bindir.is_dir():
        return []
    candidates = [
        entry
        for entry in sorted(bindir.iterdir())
        if entry.is_file()
        and os.access(entry, os.X_OK)
        and not any(entry.name.startswith(p) for p in _NON_APP_BIN_PREFIXES)
    ]
    if not candidates:
        return []
    # Each probe may spawn a heavy ML import (torch/Lightning). Empirically a
    # real app imports in ~6 s alone but is slowed by concurrent imports
    # thrashing CPU/memory bandwidth: past ~8 parallel probes on a populated
    # workspace venv, genuine apps start blowing the 15 s timeout and drop out
    # of discovery (a correctness regression worse than a slower run). So cap
    # low — 8 collapses the serial timeout-sum ~5x (minutes → ~45 s) while
    # keeping every real app under the timeout and peak memory bounded. RAM,
    # not cores, is the constraint here, so this does NOT scale with cpu_count
    # (CI runners have many cores but little RAM). Never exceed candidate count.
    max_workers = min(len(candidates), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_probe_is_liquifai_app, entry, timeout) for entry in candidates]
        return [entry.name for entry, fut in zip(candidates, futures) if fut.result()]


def install_for_apps(
    target_rc: Path,
    apps: Optional[Iterable[str]] = None,
    shell: Optional[str] = None,
    prefix: Optional[Path] = None,
) -> List[str]:
    """Install completion for a set of Liquifai apps into ``target_rc``.

    When ``apps`` is ``None``, auto-discover them via
    :func:`discover_liquifai_apps` against ``prefix`` (default
    ``sys.prefix``). Returns the list of app names that were installed
    (in install order). The same ``target_rc`` accumulates one helpers
    block + one per-app completion block per call; re-running is
    idempotent because :func:`install_script` splices by markers.
    """
    shell = shell or detect_shell()
    names = list(apps) if apps is not None else discover_liquifai_apps(prefix=prefix)
    rc = Path(target_rc)
    installed: List[str] = []
    for name in names:
        install_script(name, shell, target_rc=rc)
        installed.append(name)
    return installed


def _cli_install_completions(argv: Optional[List[str]] = None) -> int:
    """Console-script entry for ``liquifai-install-completions``.

    Usage::

        liquifai-install-completions --target-rc <path> [--shell <bash|zsh|fish>] [apps...]

    With no positional ``apps``, auto-discover every Liquifai app in the
    current venv and install completion for each into ``--target-rc``.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="liquifai-install-completions",
        description="Install liquifai shell completion for one or more apps into a target rc file.",
    )
    parser.add_argument(
        "--target-rc",
        required=True,
        type=Path,
        help="Path to the rc file to write completion into (e.g. project-local .bashrc fragment).",
    )
    parser.add_argument(
        "--shell",
        choices=SHELLS,
        default=None,
        help="Shell to install completion for (default: detect from $SHELL).",
    )
    parser.add_argument(
        "apps",
        nargs="*",
        help="App names to install. Empty → auto-discover all Liquifai apps in the active venv.",
    )
    args = parser.parse_args(argv)

    shell = args.shell or detect_shell()
    apps = args.apps or None
    installed = install_for_apps(target_rc=args.target_rc, apps=apps, shell=shell)
    if not installed:
        print(f"liquifai-install-completions: no Liquifai apps found to install into {args.target_rc}")
        return 0
    for name in installed:
        print(f"installed {name} ({shell}) → {args.target_rc}")
    return 0


# ---------------------------------------------------------------------------
# Cache (static command-tree snapshot)
# ---------------------------------------------------------------------------


def serialize_app(app: "LiquifyApp") -> Dict[str, Any]:
    """Snapshot the static command tree of ``app`` to a JSON-friendly dict.

    Per command (plain ``@command`` AND ``@script_command``) we record the
    option flags inferred from the function signature — for EVERY command, not
    only script_commands: a plain ``@command`` (e.g. ``run list``) carries its
    options in its signature too, so without this TAB could only ever offer the
    global flags for it. Two parallel maps are stored:

    * ``signature_paths`` — the raw flat dotted override paths
      (``["converter", "converter.class_name", ...]``). Kept so the
      config-present completion path can collapse the UNION of these and the
      YAML's own keys in one pass.
    * ``signature_flags`` — those paths run through confluid's canonical
      :func:`confluid.shortest_unique_paths` and turned into ``--<flag>``
      candidates, so a unique leaf shows as ``--class_name`` (not the noisy
      ``--converter.class_name``), exactly matching ``--help`` /
      :mod:`liquifai.report`. Collapsing happens HERE — at snapshot time, when
      the app module (and therefore confluid) is already loaded — so the
      stdlib-only fast completion path just reads the result and never imports
      confluid.
    """
    command_paths = {cmd: _introspect_function_keys(func) for cmd, func in app._commands.items()}
    return {
        "name": app.name,
        "commands": list(app._commands.keys()),
        "script_cmds": sorted(app._script_cmds),
        # All resolvable sub-app names (canonical + aliases) — kept for descent.
        "sub_apps": {n: serialize_app(s) for n, s in app._sub_apps.items()},
        # Alias names only; excluded from TAB suggestions (they still resolve via
        # ``sub_apps`` above) so completion shows the canonical name once, not the
        # abbreviation alongside it.
        "sub_app_aliases": sorted(app._sub_app_aliases.keys()),
        "signature_paths": command_paths,
        "signature_flags": {cmd: _collapse_to_flags(paths) for cmd, paths in command_paths.items()},
    }


def _collapse_to_flags(paths: Iterable[str]) -> List[str]:
    """Collapse dotted override paths to their shortest-unique ``--flag`` form.

    Reuses confluid's canonical :func:`confluid.shortest_unique_paths` — the
    SAME function ``--help`` / :func:`liquifai.report.show_configuration` uses —
    so completion and help agree: a leaf unique across the command's override
    keys shows as ``--<leaf>`` (``--class_name``), and only a shared leaf keeps
    enough of its prefix to disambiguate (``--a.lr`` vs ``--b.lr``).

    Confluid is imported lazily: this is called at cache-build time (confluid
    already loaded in the app process) and on the config-present completion path
    (which has already imported confluid to read the YAML), so the stdlib-only
    fast path never reaches it. Returns sorted, de-duplicated flags.
    """
    from confluid import shortest_unique_paths

    unique = sorted({p for p in paths if p})
    display = shortest_unique_paths(unique)
    out: List[str] = []
    seen: Set[str] = set()
    for full in unique:
        flag = f"--{display[full]}"
        if flag not in seen:
            out.append(flag)
            seen.add(flag)
    return out


def _introspect_function_keys(func: Any) -> List[str]:
    """Return the flat list of LEAF override paths a command exposes.

    Delegates to confluid's :func:`confluid.get_hierarchy` — the SAME path
    enumerator ``--help`` / :func:`liquifai.report.show_configuration` use — so
    completion and help can never diverge. ``get_hierarchy`` walks the command
    function's signature params, recurses into each ``@configurable`` param, and
    records only LEAF scalars (never the configurable container itself):
    ``convert-ops-export(converter: TaidalOpsToHeliosConverter)`` yields
    ``["converter.class_name", "converter.dst", ...]`` — no bare ``converter``
    root. A plain ``@command`` like ``run list`` yields its bare params
    (``["experiment", "status", ...]``). It reads ``__init__``/signature
    parameters only (NOT ``dir(cls)``), so inherited framework-base attributes
    never pollute the output.

    These RAW paths are later collapsed to shortest-unique ``--flag`` form by
    :func:`_collapse_to_flags`. Called only at cache-build time
    (:func:`serialize_app`), where confluid is already loaded — never on the
    stdlib-only fast path. Returns ``[]`` on any introspection failure so a
    broken annotation never breaks completion.
    """
    try:
        from confluid import get_hierarchy

        return sorted(get_hierarchy(func).keys())
    except Exception:
        return []


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
        # Suggest canonical sub-app names only — aliases resolve (see the
        # ``tok in cur["sub_apps"]`` descent above) but are not offered, so TAB
        # shows ``dataset`` not ``dataset``+``ds``.
        aliases = set(cur.get("sub_app_aliases", []))
        sub_names = [n for n in cur["sub_apps"] if n not in aliases]
        return _filter_prefix(list(cur["commands"]) + sub_names, incomplete)

    is_script_cmd = cmd_name in cur["script_cmds"]
    # ``signature_flags``: the command's options already collapsed to
    # shortest-unique ``--flag`` form (baked at serialize time). ``signature_paths``:
    # the raw dotted paths, kept so the config-present branch can re-collapse
    # the UNION of these and the YAML's own keys in one pass.
    signature_flags = list((cur.get("signature_flags") or {}).get(cmd_name, []))
    signature_paths = list((cur.get("signature_paths") or {}).get(cmd_name, []))

    # The previous token is a value-taking ``--flag`` (and not one of the
    # globals whose values we resolved at the top): its value comes next and
    # we can't know the type, so stay silent and let the shell's default
    # filename completion kick in. Checked FIRST so a value slot
    # (``--converter.src <TAB>``) stays silent even for a script_command that
    # hasn't consumed a config yet — otherwise the config-file branch below
    # would hijack the flag's value position. Applies to both command kinds.
    if prev.startswith("--") and prev not in GLOBAL_VALUE_FLAGS:
        return []

    # A script_command's first positional is its YAML config path, so before
    # one is consumed (and the user isn't already typing a flag) offer
    # config-file candidates — but UNION the command's own option flags so a
    # bare ``<cmd> <TAB>`` also reveals the overrides (e.g. ``--class_name``),
    # not just files, matching the discoverability of a plain @command's
    # ``<cmd> <TAB>``. A script_command runs from CLI overrides + defaults too
    # (no config required), so these option flags must complete with or without
    # a config on the line. ``_filter_prefix`` drops every flag while the user
    # is typing a path (no flag starts with a path prefix), so the flags
    # surface only for an empty word. A plain @command has no config positional
    # and skips straight to its option flags below.
    if is_script_cmd and not consumed_config and not incomplete.startswith("-"):
        files = _file_candidates(incomplete, exts=["yaml", "yml"])
        flags = list(GLOBAL_FLAGS) + signature_flags
        return files + _filter_prefix(flags, incomplete)

    # Otherwise the user is at a flag position. Offer the global flags plus this
    # command's own option flags. Empty ``incomplete`` is included so bare
    # ``<cmd> <TAB>`` reveals the options instead of falling back to filename
    # completion. When a script_command has a config on the line, collapse the
    # UNION of the signature paths and the YAML's own override keys to
    # shortest-unique form in a single pass (confluid is already loaded to read
    # the YAML) so completion and ``--help`` agree; otherwise use the flags
    # collapsed at serialize time (the stdlib-only fast path — no confluid).
    candidates = list(GLOBAL_FLAGS)
    if is_script_cmd and config_path is not None and config_path.exists():
        try:
            yaml_paths = _resolve_override_keys(config_path)
        except Exception:
            yaml_paths = []
        candidates.extend(_collapse_to_flags(signature_paths + yaml_paths))
    else:
        candidates.extend(signature_flags)
    return _filter_prefix(candidates, incomplete)


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

    Also surfaces scope-dimension keys (any ``KEY`` mentioned by a
    ``!scope:KEY=VAL`` block) so the implicit ``--KEY VAL`` activation form
    completes alongside config overrides.

    Lazily imports confluid so the fast path stays ~stdlib-only when no
    config is on the command line.
    """
    import confluid

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        raw = confluid.load_config(config_path)
        dimensions = confluid.discover_dimensions(raw)
        cfg = confluid.load(raw, flow=False)
    keys: List[str] = list(dimensions)
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
