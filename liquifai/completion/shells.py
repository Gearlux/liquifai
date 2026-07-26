"""Shell-facing layer of liquifai completion: wire protocol + script templates.

Owns everything that talks TO a shell — shell detection, the quote/escape-aware
word transport (``split_comp_words`` / ``words_from_comp_line`` /
``escape_candidate``), and the bash/zsh/fish completion-script and
``liquifai-bind-alias`` helper templates rendered by :func:`render_script` /
:func:`render_helpers`.

Pure-stdlib module (fast-path safe — see the completion mandate).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from liquifai.exceptions import UnsupportedShellError

SHELLS: List[str] = ["bash", "zsh", "fish"]

#: bash sets ``$COMP_TYPE`` inside a completion function to the ASCII code of the
#: readline completion type: ``9`` (TAB) is a normal first completion, while
#: ``63`` (``?`` — list after successive TABs), ``33`` (``!``) and ``64`` (``@``)
#: are "list the candidates" requests — i.e. a repeated/second TAB. ``37`` (``%``
#: menu-complete) is a continuous single-key cycle, NOT a repeat request. We treat
#: the listing family as the user's signal to FORCE a value-cache refresh (see
#: :func:`wants_forced_refresh`). Only bash exposes this; zsh/fish leave it unset.
_LISTING_COMP_TYPES = frozenset({"33", "63", "64"})


def wants_forced_refresh(comp_type: Optional[str]) -> bool:
    """True when ``$COMP_TYPE`` marks a repeated/list TAB (bash only).

    A second consecutive TAB (or a readline show-all variant) asks to LIST
    candidates; liquifai reads that as "the user suspects the cache is stale and
    wants it refreshed now", so the fast path force-refreshes the positional's
    value cache (bypassing the age gate). Absent/unset (zsh, fish, or a bash install
    whose wrapper predates COMP_TYPE forwarding) or a normal first TAB → ``False``,
    keeping the age-gated self-heal behaviour unchanged.
    """
    return comp_type in _LISTING_COMP_TYPES


# ---------------------------------------------------------------------------
# Shell detection + script templates
# ---------------------------------------------------------------------------


def detect_shell() -> str:
    """Return the basename of ``$SHELL`` if recognized, else ``"bash"``."""
    name = Path(os.environ.get("SHELL", "/bin/bash")).name
    return name if name in SHELLS else "bash"


# ---------------------------------------------------------------------------
# Wire-protocol helpers — preserve & escape words/candidates containing spaces
# ---------------------------------------------------------------------------
# The shell wrappers join ``$COMP_WORDS`` with a NEWLINE so a value like
# ``Test Script VB`` survives transport as ONE token (older wrappers space-join;
# we detect and tolerate both). Candidate values are then backslash-escaped on
# output so the shell inserts e.g. ``Test\ Script\ VB`` as a single argument.

#: Shell-special chars that must be backslash-escaped in an emitted candidate so
#: it inserts as one token (whitespace + word-splitting / globbing / quoting metas).
_SHELL_SPECIAL = re.compile(r"([ \t\"'()&;|<>$`\\*?\[\]{}])")


def _unescape_word(word: str) -> str:
    """Strip one layer of shell backslash-escaping (``Test\\ Scr`` -> ``Test Scr``)."""
    return re.sub(r"\\(.)", r"\1", word)


def split_comp_words(comp_words: str) -> List[str]:
    """Split the shell's ``$COMP_WORDS`` into tokens, PRESERVING embedded spaces.

    The wrappers newline-join the word array; older space-joined ones lack a
    newline — so split on newline when present, else fall back to whitespace
    (migration-safe: bash already newline-joins, so this fixes spaces with no
    re-install; old zsh/fish installs keep working, unbroken). Each token is then
    unescaped so a half-typed ``Test\\ Scr`` matches the logical value ``Test Scr``.
    """
    parts = comp_words.split("\n") if "\n" in comp_words else comp_words.split()
    return [_unescape_word(p) for p in parts]


def words_from_comp_line(comp_line: str, comp_point: int) -> Tuple[List[str], int]:
    """Tokenize a shell command line up to the cursor, QUOTE/ESCAPE-aware.

    Bash's raw ``$COMP_WORDS`` array splits ``$COMP_LINE`` on ``$COMP_WORDBREAKS``
    (which includes space) WITHOUT honoring quotes or backslash escapes, so a value
    like ``"Helios Base Model"`` (or ``Helios\\ Base\\ Model``) is shattered into
    several words — which corrupts positional counting (the shell then falls back to
    filename completion). Re-tokenizing the raw ``$COMP_LINE`` here — as bash's own
    parser would — keeps such a value as ONE token. Tolerant of an unterminated final
    quote (the user is still typing), which runs to the end as one token.

    (zsh's ``$words`` and fish's ``commandline -opc`` are already quote-aware, so only
    bash needs this; those wrappers keep using the ``COMP_WORDS`` path.)

    Returns ``(words, cword)`` where ``words[0]`` is the program name and
    ``words[cword]`` is the (possibly empty) word under the cursor.
    """
    if comp_point < 0 or comp_point > len(comp_line):
        comp_point = len(comp_line)
    text = comp_line[:comp_point]
    words: List[str] = []
    cur: List[str] = []
    in_word = False  # a token has started (incl. an empty quoted "")
    quote = ""  # "" | "'" | '"'
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            # Inside "..." a backslash escapes ", \, $ and ` (bash rules); inside
            # '...' nothing is special. An unterminated quote falls through to EOF.
            if quote == '"' and c == "\\" and i + 1 < n and text[i + 1] in '"\\$`':
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
                i += 1
                continue
            cur.append(c)
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            in_word = True
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            cur.append(text[i + 1])
            in_word = True
            i += 2
            continue
        if c in (" ", "\t"):
            if in_word:
                words.append("".join(cur))
                cur = []
                in_word = False
            i += 1
            continue
        cur.append(c)
        in_word = True
        i += 1
    # Flush the final token. If the line ends on unquoted whitespace, the current
    # word is empty (bare TAB after a space) — represent it as a trailing "".
    words.append("".join(cur))
    return words, len(words) - 1


def escape_candidate(value: str) -> str:
    """Backslash-escape a candidate so the shell inserts it as ONE token.

    ``Test Script VB`` -> ``Test\\ Script\\ VB`` (so bash/zsh don't split it into
    three args). Placeholders like ``<name>`` are left verbatim — they are display
    hints, never inserted as real values, and escaping would render ``\\<name\\>``.
    Values with no special chars (``alpha``, ``--path``) pass through unchanged.
    """
    if value.startswith("<") and value.endswith(">"):
        return value
    return _SHELL_SPECIAL.sub(r"\\\1", value)


_BASH_TEMPLATE = """\
_{prog}_completion() {
    local IFS=$'\\n'
    local raw
    # Pass COMP_LINE/COMP_POINT (the raw line + cursor) so liquifai-complete can
    # re-tokenize quote-aware — bash's own COMP_WORDS splits "Helios Base Model"
    # on spaces. COMP_WORDS/COMP_CWORD are forwarded too as a fallback. COMP_TYPE
    # (readline completion type) lets liquifai-complete detect a repeated/second
    # TAB and force-refresh a possibly-stale value cache.
    raw=$(env COMP_LINE="$COMP_LINE" COMP_POINT="$COMP_POINT" \\
        COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD \\
        COMP_TYPE="$COMP_TYPE" \\
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
    # `${(F)words}` joins the word array with NEWLINES so a value with embedded
    # spaces (e.g. a name "Test Script VB") survives transport as one token.
    response=("${(@f)$(env COMP_WORDS=\"${(F)words}\" \\
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
    # Join with NEWLINE so a value with embedded spaces survives as one token.
    set -l joined (string join \\n -- $all_words)
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
# Shared body invoked by every per-alias delegator. Delegates to the fast
# `liquifai-complete` entry as if the app itself had been typed. Prefers a
# rewritten COMP_LINE (alias token -> `<app> <prefix>`, cursor shifted) so
# liquifai-complete re-tokenizes quote-aware and a value like "Helios Base
# Model" stays ONE word; also builds the legacy COMP_WORDS/COMP_CWORD as a
# fallback (space-joined; shatters spaces — old liquifai-complete only). We
# iterate COMP_WORDS manually instead of ${arr[*]:n} because bash 3.2 leaks a
# stray \x7f byte there for empty trailing elements.
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
    # Quote-aware path: rewrite the raw line's leading alias token to
    # `<app> <prefix...>` and shift the cursor by the length delta.
    local line_env="" point_env=""
    if [ -n "$COMP_LINE" ]; then
        local alias_tok="${COMP_WORDS[0]}"
        line_env="${prefix_str}${COMP_LINE:${#alias_tok}}"
        point_env=$((COMP_POINT - ${#alias_tok} + ${#prefix_str}))
    fi
    local raw
    raw=$(env COMP_LINE="$line_env" COMP_POINT="$point_env" \
        COMP_WORDS="$words" COMP_CWORD="$cword" COMP_TYPE="$COMP_TYPE" \
        liquifai-complete "$app" 2>/dev/null)
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
    # zsh's $words is already quote-aware; NEWLINE-join (prefix words + the typed
    # rest) so split_comp_words preserves a value with embedded spaces ("Helios
    # Base Model") as ONE token — matching the main zsh wrapper's ${(F)words}.
    local -a merged_arr
    merged_arr=(${=prefix_str} "${(@)words[2,-1]}")
    local merged="${(F)merged_arr}"
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


def _bindir_env_prefix() -> str:
    """Return PATH="$PATH:<bindir>" env prefix if bindir exists and contains liquifai-complete."""
    bindir = Path(sys.executable).parent
    if bindir.exists() and (bindir / "liquifai-complete").exists():
        return f'PATH="$PATH:{bindir}" '
    return ""


def render_script(prog: str, shell: str) -> str:
    """Render the shell completion script for ``prog`` in ``shell``."""
    if shell not in SHELLS:
        raise UnsupportedShellError(f"Unsupported shell {shell!r}; expected one of {SHELLS}")
    template = {"bash": _BASH_TEMPLATE, "zsh": _ZSH_TEMPLATE, "fish": _FISH_TEMPLATE}[shell]
    path_prefix = _bindir_env_prefix()
    return template.replace("{prog}", prog).replace("env ", f"env {path_prefix}")


def render_helpers(shell: str) -> str:
    """Render the shared shell helpers (``liquifai-bind-alias`` etc.)."""
    if shell == "fish":
        return ""
    if shell not in ("bash", "zsh"):
        raise UnsupportedShellError(f"Unsupported shell {shell!r}; expected one of {SHELLS}")
    template = _BASH_HELPERS if shell == "bash" else _ZSH_HELPERS
    path_prefix = _bindir_env_prefix()
    return template.replace("env ", f"env {path_prefix}")
