"""Single source of truth for liquifai's CLI grammar.

This module holds the ONE declaration of the global CLI flags
(:data:`GLOBAL_FLAG_SPECS`) plus the token classifiers shared by the
dispatcher and the completion engine. Every other surface DERIVES from it:

* ``core._parse_globals`` iterates the spec table to route flag values.
* ``core._show_help`` renders the "Global Options" block from it.
* ``completion.py`` re-exports the derived flag sets for TAB candidates.

Never restate a flag list elsewhere — the pre-consolidation code kept three
hand-maintained copies (parser handlers, completion constants, help strings)
which had already drifted (``--log-dir`` / ``--docs`` / ``--refresh-completions``
were missing from ``--help``).

Pure-stdlib module — like :mod:`liquifai.exceptions`, it is safe for the
``liquifai-complete`` fast path, which must never pull in confluid / loggair /
rich (see the completion mandate in ``CLAUDE.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Set, Tuple

#: What kind of value a value-taking global flag expects. Drives completion:
#: ``path`` → filesystem candidates, ``shell`` → bash/zsh/fish, everything
#: else → no suggestion (free-form value).
ValueKind = Literal["path", "shell", "scope", "level", "json", "none"]


@dataclass(frozen=True)
class GlobalFlag:
    """Declaration of one global CLI flag (all spellings + metadata)."""

    #: All spellings, long form first (``("--config", "-c")``).
    flags: Tuple[str, ...]
    #: Logical destination the parser routes the value to.
    dest: str
    #: One-line help text rendered by ``--help``.
    help: str
    #: True when the flag consumes the next token (or ``=value``) as its value.
    takes_value: bool = False
    #: Completion hint for the value (see :data:`ValueKind`).
    value_kind: ValueKind = "none"
    #: Placeholder shown after the flag in help (``PATH``, ``[SHELL]``, …).
    metavar: str = ""
    #: Hidden flags are internal plumbing: excluded from help AND from the
    #: derived completion sets (never advertised, never suggested).
    hidden: bool = False


#: The complete global-flag vocabulary, in display order. ``--KEY VAL``
#: dimension flags are NOT here — they are config-dependent (any key declared
#: by a ``!scope:KEY=…`` block in the loaded YAML) and documented as a
#: pattern in the help footer instead.
GLOBAL_FLAG_SPECS: Tuple[GlobalFlag, ...] = (
    GlobalFlag(
        ("--config", "-c"), "config_path", "Configuration file.", takes_value=True, value_kind="path", metavar="PATH"
    ),
    GlobalFlag(
        ("--scope", "-s"),
        "scopes",
        "Active boolean scope(s); accepts `NAME` or `KEY=VAL`.",
        takes_value=True,
        value_kind="scope",
        metavar="NAME",
    ),
    GlobalFlag(("--debug", "-d"), "debug", "Enable debug mode."),
    GlobalFlag(
        ("--level",),
        "log_level",
        "Set log level for both sinks (TRACE, DEBUG, INFO).",
        takes_value=True,
        value_kind="level",
        metavar="LEVEL",
    ),
    GlobalFlag(
        ("--console-level",),
        "console_level",
        "Set console log level (overrides --level).",
        takes_value=True,
        value_kind="level",
        metavar="LEVEL",
    ),
    GlobalFlag(
        ("--file-level",),
        "file_level",
        "Set file log level (overrides --level).",
        takes_value=True,
        value_kind="level",
        metavar="LEVEL",
    ),
    GlobalFlag(
        ("--log-dir",),
        "log_dir",
        "Directory to write log files into.",
        takes_value=True,
        value_kind="path",
        metavar="PATH",
    ),
    # ``-h`` is the universal convention, and its absence was not a smaller vocabulary but a TRAP:
    # a single-dash token is no override form, so ``-h`` fell through to the parser's dropped list
    # (a warning) and execution CONTINUED into the command. Measured 2026-08-26 on a live CLI:
    # `streamstudio restart -h` restarted the server instead of describing it.
    GlobalFlag(("--help", "-h"), "help", "Show this help."),
    GlobalFlag(("--docs",), "docs", "Render the same option docs as --help, one per line (greppable)."),
    GlobalFlag(
        ("--install-completion",),
        "install_completion",
        "Install tab completion (bash/zsh/fish).",
        takes_value=True,
        value_kind="shell",
        metavar="[SHELL]",
    ),
    GlobalFlag(
        ("--show-completion",),
        "show_completion",
        "Print the completion script to stdout.",
        takes_value=True,
        value_kind="shell",
        metavar="[SHELL]",
    ),
    GlobalFlag(
        ("--refresh-completions",),
        "refresh_completions",
        "Run positional completion providers and refresh their value caches.",
    ),
    GlobalFlag(
        ("--refresh-completion-value",),
        "refresh_completion_value",
        "Internal: targeted background refresh of one completion value cache.",
        takes_value=True,
        value_kind="json",
        metavar="JSON",
        hidden=True,
    ),
)

# ---------------------------------------------------------------------------
# Derived flag sets — computed from the table, never restated. Hidden flags
# are excluded everywhere: they are spawned programmatically, never typed.
# ---------------------------------------------------------------------------

#: Every visible flag spelling, in display order (TAB candidate list).
GLOBAL_FLAGS: List[str] = [f for spec in GLOBAL_FLAG_SPECS if not spec.hidden for f in spec.flags]

#: Every spelling that asks for help — DERIVED, so the short-circuit in ``core.run`` can never
#: drift from the table that ``--help`` and completion render. A hard-coded ``"--help"`` there
#: is what let ``-h`` fall through into the command.
HELP_FLAGS: frozenset = frozenset(f for spec in GLOBAL_FLAG_SPECS if spec.dest == "help" for f in spec.flags)

#: Flags whose value is a filesystem path (completion offers files/dirs).
PATH_VALUE_FLAGS: Set[str] = {
    f for spec in GLOBAL_FLAG_SPECS if not spec.hidden and spec.value_kind == "path" for f in spec.flags
}

#: Flags whose value is a shell name (completion offers bash/zsh/fish).
SHELL_VALUE_FLAGS: Set[str] = {
    f for spec in GLOBAL_FLAG_SPECS if not spec.hidden and spec.value_kind == "shell" for f in spec.flags
}

#: Every visible value-taking flag (completion stays silent after these).
GLOBAL_VALUE_FLAGS: Set[str] = {
    f for spec in GLOBAL_FLAG_SPECS if not spec.hidden and spec.takes_value for f in spec.flags
}


def flag_display(spec: GlobalFlag) -> str:
    """Render a spec's spellings for help output (``-c, --config PATH``)."""
    parts = ", ".join(sorted(spec.flags, key=len))
    return f"{parts} {spec.metavar}".rstrip()


# ---------------------------------------------------------------------------
# Token classifiers — the shared micro-grammar for positional/override
# tokenization. Used by core's dispatcher AND completion's positional
# counting, which previously mirrored these rules by hand (drift hazard).
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"[A-Za-z_][\w.\-]*")


def looks_like_arg(token: str) -> bool:
    """True if the token looks like the *start* of another CLI option, so
    it should NOT be consumed as the value for a preceding ``--key``.

    Catches ``--foo``, ``+foo=bar``, ``~foo`` — anything that
    :func:`liquifai.overrides.parse_override_args` would itself parse as a
    new option in the next iteration.
    """
    if not token:
        return False
    return token.startswith("--") or token.startswith("+") or token.startswith("~")


def stops_positional(token: str) -> bool:
    """True if ``token`` must NOT be consumed as a positional value.

    Positional consumption halts at the first flag-like token (a ``-`` / ``+`` /
    ``~`` prefix — covers short ``-c`` and long ``--config`` options as well as
    the ``+add`` / ``~delete`` override forms) or any ``key=value`` token. This
    lets a user supply positionals (``info foo``), the equals form
    (``info name=foo``), or trailing flags (``download foo 1.0 --path /tmp``)
    interchangeably without the parser mistaking one for another.
    """
    if not token:
        return True
    if token[0] in ("-", "+", "~"):
        return True
    return "=" in token


def looks_like_key(token: str) -> bool:
    """Conservative shape check for the bare ``key=value`` form.

    Keys are word characters + dots (``trainer.max_epochs``). Anything
    else (slashes, colons inside the head) probably isn't an override.
    """
    return bool(_KEY_RE.fullmatch(token))
