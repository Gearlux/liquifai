"""Tokenization and the ONE argv walk shared by dispatch and completion.

Two things live here:

* :func:`tokenize` — the front of every command line. It handles the POSIX
  ``--`` end-of-options separator once, marking every following token
  ``literal`` so no later phase can mistake it for an option. Without it a
  dash-leading positional (``seek -5 /tmp/x``) is unrepresentable: positional
  consumption halts at the first ``-``, the tokens fall through to the
  override parser, and the command silently runs on defaults.
* :func:`walk_invocation` — the descent from raw tokens to
  ``(sub-app, command, promoted config, positionals, leftovers)``.
  :mod:`liquifai.router` and :mod:`liquifai.completion.engine` both need this
  walk, over two different data shapes (live ``LiquifyApp`` objects vs. the
  serialized JSON command tree). They used to implement it twice, and the
  copies drifted — the completion copy never resolved a promoted config path
  through confluid's search tiers, so a ``./config/foo.yaml`` layout that
  dispatch happily consumed was invisible to TAB. The :class:`Nav` protocol is
  what lets ONE walk serve both: each side supplies a ~15-line adapter.

Pure-stdlib module (fast-path safe — see the completion mandate in
``CLAUDE.md``): the completion hot path imports it on every TAB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Sequence

from liquifai.grammar import GLOBAL_VALUE_FLAGS, stops_positional

#: The POSIX end-of-options separator. Everything after the first bare ``--``
#: is a literal value, never an option — the escape hatch for positionals that
#: start with ``-``.
END_OF_OPTIONS = "--"


@dataclass(frozen=True)
class Token:
    """One command-line token plus whether it sits after ``--``."""

    #: The verbatim token text (the separator itself is never kept).
    text: str
    #: True when the token followed a bare ``--``: it can never be an option,
    #: never stops positional consumption, and is never parsed as an override.
    literal: bool = False

    def stops_positional(self) -> bool:
        """True if this token must NOT be bound as a positional value.

        A literal token never stops consumption — that is the whole point of
        ``--``. Otherwise this defers to
        :func:`liquifai.grammar.stops_positional`.
        """
        return False if self.literal else stops_positional(self.text)

    def is_flag_like(self) -> bool:
        """True if the token opens an option (and so is not a promoted config path)."""
        return not self.literal and self.text.startswith("-")


def tokenize(argv: Sequence[str]) -> List[Token]:
    """Split ``argv`` at the first bare ``--``; mark the remainder literal.

    The separator itself is dropped. A second ``--`` is an ordinary literal
    token (it is already past the separator), matching POSIX behaviour.

    Example::

        tokenize(["seek", "--", "-5"]) == [Token("seek"), Token("-5", literal=True)]
    """
    out: List[Token] = []
    literal = False
    for arg in argv:
        if not literal and arg == END_OF_OPTIONS:
            literal = True
            continue
        out.append(Token(arg, literal=literal))
    return out


def option_texts(tokens: Sequence[Token]) -> List[str]:
    """The non-literal token texts — the input every option parser may consume."""
    return [t.text for t in tokens if not t.literal]


def literal_texts(tokens: Sequence[Token]) -> List[str]:
    """The post-``--`` token texts, which no option parser may touch."""
    return [t.text for t in tokens if t.literal]


class Nav(Protocol):
    """Structure-agnostic view of one command-tree node.

    Implemented twice — over a live ``LiquifyApp`` (:mod:`liquifai.router`) and
    over a serialized tree dict (:mod:`liquifai.completion.engine`) — so
    :func:`walk_invocation` never learns either shape.
    """

    def sub_app(self, token: str) -> Optional["Nav"]:
        """The child node ``token`` names, or None if it names no sub-app."""
        ...  # pragma: no cover - protocol

    def has_command(self, token: str) -> bool:
        """True if ``token`` names a command registered on this node."""
        ...  # pragma: no cover - protocol

    def is_script_command(self, cmd: str) -> bool:
        """True if ``cmd`` supports config promotion (its first positional is a YAML path)."""
        ...  # pragma: no cover - protocol

    def positionals(self, cmd: str) -> List[str]:
        """Declared positional-argument names for ``cmd``, in order."""
        ...  # pragma: no cover - protocol

    def default_command(self) -> Optional[str]:
        """Name of the command that runs when no command token is given, or None."""
        ...  # pragma: no cover - protocol


@dataclass
class Walk:
    """Everything the descent through argv determined."""

    #: The node the command tokens descended into.
    nav: Nav
    #: Matched command name, or None when no command was found. Also set to the
    #: DEFAULT command's name when its arguments were bound without a name token
    #: (``app w.yaml`` for a default command declaring a positional / a script
    #: default command) — then no token equals it; see :attr:`args_index`.
    cmd_name: Optional[str] = None
    #: Index of the first token that belongs to the command's ARGUMENTS: right
    #: after the command name, or — for the default command bound without a
    #: name token — the first token after the last sub-app descent. Equals
    #: ``len(tokens)`` when the line ends at the command name.
    args_index: int = 0
    #: Config path consumed by script-command promotion (already resolved by
    #: the caller's ``resolve_config``), or None.
    config_path: Optional[Path] = None
    #: The RAW token that promotion consumed, before resolution. Kept so the
    #: caller can report provenance — ``run demo`` resolving to
    #: ``~/.config/app/demo.yaml`` is legal but surprising, and the typed token
    #: is the only way to say which spelling produced which file.
    config_token: Optional[str] = None
    #: True when a token was consumed as the promoted config.
    consumed_config: bool = False
    #: Declared positional names for the matched command, in order.
    positional_names: List[str] = field(default_factory=list)
    #: Leading positional tokens actually consumed (may be fewer than names).
    positional_values: List[str] = field(default_factory=list)
    #: Every token not consumed by the walk.
    remaining: List[Token] = field(default_factory=list)


def walk_invocation(
    tokens: Sequence[Token],
    nav: Nav,
    resolve_config: Callable[[str], Optional[Path]],
) -> Walk:
    """Descend ``tokens`` through sub-apps to a command, its config and positionals.

    ``resolve_config`` decides whether the token after a ``script_command`` is
    its promoted config: return the resolved :class:`~pathlib.Path` to consume
    it, or None to leave it for the positional/override parsers. This is the
    ONE place the two callers differ — dispatch resolves through confluid's
    search tiers and requires the file to exist; completion resolves lazily
    (it must not import confluid on the hot path).

    Everything unconsumed lands in :attr:`Walk.remaining`, tokens intact, so
    downstream phases can still tell an option from a post-``--`` literal.
    """
    cur = nav
    walk = Walk(nav=cur)
    i, n = 0, len(tokens)

    while i < n:
        tok = tokens[i]
        # A value-taking global flag owns the next token, so `app --level run`
        # is "--level=run", NOT the `run` command. Both tokens still land in
        # `remaining` for `_parse_globals` to consume — the walk only declines
        # to interpret the value.
        if not tok.literal and tok.text in GLOBAL_VALUE_FLAGS and i + 1 < n and not tokens[i + 1].literal:
            walk.remaining.append(tok)
            walk.remaining.append(tokens[i + 1])
            i += 2
            continue
        if walk.cmd_name is None and not tok.literal:
            sub = cur.sub_app(tok.text)
            if sub is not None:
                cur = sub
                walk.nav = cur
                i += 1
                walk.args_index = i
                continue
            if cur.has_command(tok.text):
                walk.cmd_name = tok.text
                i += 1
                walk.args_index = i
                i = _bind_arguments(walk, cur, tokens, i, resolve_config)
                continue
        # No command token — but the DEFAULT command may take this token as its
        # promoted config or first positional, exactly as if its name had been
        # typed. Only as a LEADING token (``i == args_index``: nothing at this
        # level was skipped into `remaining` yet), mirroring an explicit
        # command whose positionals stop binding at the first flag; and only
        # when the default command has arguments to bind — otherwise the token
        # stays for the override parser, unchanged. A token that binds NOTHING
        # (a script default whose config does not resolve) is left alone too.
        if walk.cmd_name is None and i == walk.args_index:
            default = cur.default_command()
            if default is not None and (cur.is_script_command(default) or cur.positionals(default)):
                walk.cmd_name = default
                bound = _bind_arguments(walk, cur, tokens, i, resolve_config)
                if bound > i:
                    i = bound
                    continue
                walk.cmd_name = None
                walk.positional_names = []
        walk.remaining.append(tok)
        i += 1

    return walk


def _bind_arguments(
    walk: Walk,
    cur: Nav,
    tokens: Sequence[Token],
    i: int,
    resolve_config: Callable[[str], Optional[Path]],
) -> int:
    """Consume the command's promoted config (script commands) and its leading positionals.

    ``walk.cmd_name`` is already set; ``i`` indexes the first argument token.
    Returns the index of the first token NOT consumed.
    """
    n = len(tokens)
    cmd = walk.cmd_name or ""
    if cur.is_script_command(cmd) and i < n and not tokens[i].is_flag_like():
        resolved = resolve_config(tokens[i].text)
        if resolved is not None:
            walk.config_path = resolved
            walk.config_token = tokens[i].text
            walk.consumed_config = True
            i += 1
    walk.positional_names = list(cur.positionals(cmd))
    for _ in walk.positional_names:
        if i < n and not tokens[i].stops_positional():
            walk.positional_values.append(tokens[i].text)
            i += 1
        else:
            break
    return i
