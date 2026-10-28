"""Candidate computation for liquifai completion (the TAB hot path).

Owns ``complete_from_tree`` — the pure function from (serialized command
tree, typed words, cursor) to candidate strings — plus its filesystem/prefix
helpers and the lazy config-override key resolver. Reads value caches through
the :mod:`liquifai.completion.cache` MODULE (attribute access, so tests can
patch ``cache.DEPENDENT_REFRESH_TTL`` etc. and this module sees it).

Pure-stdlib module on the hot path; confluid is imported only inside
``_resolve_override_keys`` (the config-present branch).
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Set, Tuple

from liquifai.grammar import (
    GLOBAL_FLAG_SPECS,
    GLOBAL_FLAGS,
    GLOBAL_VALUE_FLAGS,
    PATH_VALUE_FLAGS,
    SCOPE_VALUE_FLAGS,
    SHELL_VALUE_FLAGS,
    flag_display,
    looks_like_key,
    stops_positional,
)
from liquifai.walk import Nav, tokenize, walk_invocation

from . import cache, tree
from .shells import SHELLS

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp

# ---------------------------------------------------------------------------
# Candidate computation
# ---------------------------------------------------------------------------


class _TreeNav:
    """:class:`~liquifai.walk.Nav` over a serialized command-tree node.

    The completion counterpart of :class:`liquifai.router._AppNav`: same walk,
    different data shape. Stdlib-only — it reads plain JSON dicts.
    """

    def __init__(self, node: Dict[str, Any]) -> None:
        self.node = node

    def sub_app(self, token: str) -> Optional["Nav"]:
        sub = (self.node.get("sub_apps") or {}).get(token)
        return _TreeNav(sub) if sub is not None else None

    def has_command(self, token: str) -> bool:
        return token in (self.node.get("commands") or [])

    def is_script_command(self, cmd: str) -> bool:
        return cmd in (self.node.get("script_cmds") or [])

    def positionals(self, cmd: str) -> List[str]:
        return list((self.node.get("positionals") or {}).get(cmd, []))

    def default_command(self) -> Optional[str]:
        default = self.node.get("default")
        return str(default) if default else None


def _peek_config(token: str) -> Optional[Path]:
    """Consume a script-command's promoted config token WITHOUT resolving it.

    The dispatcher's counterpart resolves through confluid's search tiers, but
    that would import confluid on every TAB — so the hot path defers: the token
    is recorded as-is (bare names gain ``.yaml``) and only the branch that
    actually reads the YAML calls :func:`_resolve_config_path`, which is
    already past the stdlib-only boundary.
    """
    p = Path(token)
    return p if p.suffix else p.with_suffix(".yaml")


def _resolve_config_path(config_path: Path) -> Optional[Path]:
    """Resolve ``config_path`` through confluid's search tiers; None if absent.

    A relative config lives under ``./``, ``./config/`` or an XDG dir — the
    dispatcher resolves all four tiers, so completion must too. Testing
    ``config_path.exists()`` on the typed-as-is path (the old behaviour) made
    the whole config-present branch dead for every layout except CWD: TAB
    offered only the command's signature flags and never the YAML's own
    override keys.

    Confluid is imported lazily here — this branch already left the
    stdlib-only fast path (it is about to parse the YAML).
    """
    try:
        import confluid

        resolved = confluid.resolve_config_path(config_path)
    except Exception:
        resolved = config_path
    return resolved if resolved.exists() else None


def complete(app: "LiquifyApp", words: List[str], cword: int) -> List[str]:
    """Convenience wrapper: snapshot ``app`` then call :func:`complete_from_tree`."""
    return complete_from_tree(tree.serialize_app(app), words, cword)


#: One row of the listing: a candidate and the line shown for it. A spelling that completes but shares another
#: spelling's row (``-c`` beside ``-c, --config PATH``) has the line ``""``; a line that is no candidate (an argument
#: already typed, or still missing) has the candidate ``""``.
ListingRow = Tuple[str, str]
#: A group of the listing: its header and its rows.
ListingGroup = Tuple[str, List[ListingRow]]
#: The groups a second TAB shows: the commands, the command's arguments, the command's own options, the global ones.
Listing = List[ListingGroup]

#: The header of the global options' group; the others are ``commands of <app> <sub-app …>``, ``arguments of <app>
#: <command path>`` and ``options of <app> <command path>``.
GLOBAL_HEADER = "global options"
#: The help of a script command's config slot (``<config>``) in the arguments group.
CONFIG_HELP = "the YAML config it runs"
#: Marks the argument to type next, in place of the row's two-space indent.
NEXT_MARK = "▸ "

#: What a piece of a row is, which decides its colour.
Role = Literal["title", "command", "option", "metavar", "given", "missing", "help"]
#: Each role's terminal colour (SGR codes): titles bold cyan, commands blue, options bold in the terminal's own
#: colour, a value's placeholder dim cyan, a typed argument green, a missing one yellow, help dim (user choice
#: 2026-10-28; the palette of ``--help``, commands set apart in blue).
COLOURS: Dict[Role, str] = {
    "title": "1;36",
    "command": "34",
    "option": "1",
    "metavar": "2;36",
    "given": "32",
    "missing": "33",
    "help": "2",
}
#: A run of text and its role (None: no colour).
_Piece = Tuple[str, Optional[Role]]


def _paint(text: str, role: Optional[Role], colour: bool) -> str:
    return f"\x1b[{COLOURS[role]}m{text}\x1b[0m" if colour and role is not None and text else text


#: A listing row's spelling column is as wide as the widest spelling, but never wider than this.
_LEFT_MAX = 40


@dataclass
class _OptionList:
    """What the walk knew when it made the candidates: the node and command they belong to, what was typed."""

    #: The tree node the command lives in.
    node: Dict[str, Any]
    #: The command, or None before one is typed (the global options alone).
    cmd_name: Optional[str] = None
    #: ``<app> <sub-app ...> <command>``, the command's groups' name.
    path: str = ""
    #: ``--flag`` → dotted path, for a config on the line whose keys re-collapse the spellings; None: the cache's.
    flag_paths: Optional[Dict[str, str]] = None
    #: ``<app> <sub-app ...>`` when the node's command and sub-app names are among the candidates (a bare prompt).
    commands_of: Optional[str] = None
    #: The positionals typed, in order (a script command's config left out).
    typed: List[str] = field(default_factory=list)
    #: The positionals given in their ``--flag`` / ``key=value`` spelling.
    given_by_flag: Set[str] = field(default_factory=set)
    #: A script command's config on the line, as typed; None while it is still to come.
    config: Optional[str] = None


def complete_with_listing(
    tree: Dict[str, Any],
    words: List[str],
    cword: int,
    width: int = 80,
    lazy_refresh: Optional[Callable[..., None]] = None,
    force_refresh: bool = False,
    colour: bool = False,
    codes_count: bool = False,
) -> Tuple[List[str], Optional[Listing]]:
    """The candidates :func:`complete_from_tree` returns, and the grouped listing a second TAB shows for them.

    The listing exists wherever more than one candidate is offered but at an option's value. Its groups hold exactly
    the candidates: the commands and sub-apps (their first docstring line), the command's arguments — each typed one
    with its value, each missing one with its help, the next one's values or config files under it — the command's
    own options (its signature's ``Args:`` help and its default; a config's keys with none), then the global options
    (``grammar.GLOBAL_FLAG_SPECS``, one row per option, its help beside it). Rows are cut to ``width - 1`` visible
    characters. With ``colour`` each title and piece of a row carries its colour (:data:`COLOURS`); the text is the
    same once the codes are taken out. ``codes_count`` (zsh, which measures a row's codes as characters) counts the
    codes against ``width`` too, so the row still fits where zsh measures it.
    """
    width = max(width, 20)
    found: List[_OptionList] = []
    candidates = _complete(tree, words, cword, lazy_refresh, force_refresh, found)
    if not found or len(candidates) < 2:
        return candidates, None
    return candidates, _listing(candidates, found[0], width, colour, codes_count)


def complete_from_tree(
    tree: Dict[str, Any],
    words: List[str],
    cword: int,
    lazy_refresh: Optional[Callable[..., None]] = None,
    force_refresh: bool = False,
) -> List[str]:
    """Compute completion candidates from a serialized command tree.

    Args:
        tree: A dict produced by :func:`serialize_app`.
        words: Tokenized command line including the program name at index 0.
        cword: Index of the word being completed (0-based).
        lazy_refresh: Optional ``(cache_key, inputs)`` callback invoked when a
            positional's value cache should be refreshed, so the fast path can
            self-heal it in the background (see :func:`make_lazy_refresh_spawner`).
            Whatever is cached now (or the placeholder) is still returned immediately;
            this never blocks. ``None`` (the default, used by tests / in-process
            completion) disables it.
        force_refresh: When True (a repeated/second TAB — see
            :func:`liquifai.completion.wants_forced_refresh`), the value cache for the
            current positional is refreshed REGARDLESS of its age, so a cache that is
            fresh-by-age but wrong (e.g. an item deleted upstream) still updates. Only
            the age gate is bypassed — the spawner's throttle still applies, so a burst
            of double-TABs triggers at most one refresh per session. The refresh is
            detached, so the corrected list appears on the NEXT TAB.

    Returns:
        Candidates, one per line. Empty list means "no suggestion".
    """
    return _complete(tree, words, cword, lazy_refresh, force_refresh, None)


def _complete(
    tree: Dict[str, Any],
    words: List[str],
    cword: int,
    lazy_refresh: Optional[Callable[..., None]],
    force_refresh: bool,
    found: Optional[List[_OptionList]],
) -> List[str]:
    """:func:`complete_from_tree`'s body; a branch whose candidates are options alone appends to ``found``."""
    parsed = words[1:cword]
    incomplete = words[cword] if 0 <= cword < len(words) else ""
    prev = words[cword - 1] if cword - 1 >= 1 else ""
    app_name = str(tree.get("name", ""))

    if prev in PATH_VALUE_FLAGS:
        return _file_candidates(incomplete, exts=None)
    if prev in SHELL_VALUE_FLAGS:
        return [s for s in SHELLS if s.startswith(incomplete)]
    if prev in SCOPE_VALUE_FLAGS:
        return _scope_candidates(tree, parsed, incomplete, app_name, lazy_refresh, force_refresh)
    if prev in GLOBAL_VALUE_FLAGS:
        return []

    # The descent to (sub-app, command, promoted config, positionals) is the
    # SAME walk the dispatcher runs — shared via liquifai.walk so the two can
    # no longer drift (they did: the old copy here never resolved a promoted
    # config through confluid's search tiers). `_TreeNav` is the adapter that
    # teaches the walker to read a serialized tree instead of live app objects.
    walk = walk_invocation(tokenize(parsed), _TreeNav(tree), _peek_config)
    cur = walk.nav.node if isinstance(walk.nav, _TreeNav) else tree
    cmd_name = walk.cmd_name
    config_path: Optional[Path] = walk.config_path
    consumed_config = walk.consumed_config

    # An explicit ``--config PATH`` outranks a promoted token for the
    # override-key branch below (the walk leaves the flag in `remaining`).
    leftovers = [t.text for t in walk.remaining]
    for i, tok in enumerate(leftovers):
        if tok in ("--config", "-c") and i + 1 < len(leftovers):
            config_path = Path(leftovers[i + 1])

    if cmd_name is None:
        if incomplete.startswith("-"):
            if found is not None:
                found.append(_OptionList(node=cur))
            return _filter_prefix(GLOBAL_FLAGS, incomplete)
        # Suggest canonical sub-app names only — aliases resolve (see the
        # ``tok in cur["sub_apps"]`` descent above) but are not offered, so TAB
        # shows ``dataset`` not ``dataset``+``ds``.
        aliases = set(cur.get("sub_app_aliases", []))
        sub_names = [n for n in cur["sub_apps"] if n not in aliases]
        names = _filter_prefix(list(cur["commands"]) + sub_names, incomplete)
        # Nothing typed at this level yet and the default command takes arguments:
        # the word may still become a command name OR the default command's first
        # argument (the walk binds a LEADING token that way), so offer both — its
        # argument hint / config files / flags first, the names after.
        default = cur.get("default")
        takes_args = bool(default) and (default in cur["script_cmds"] or (cur.get("positionals") or {}).get(default))
        level = _command_path(tree, parsed[: walk.args_index], None)
        if takes_args and len(parsed) == walk.args_index:
            mine: Optional[List[_OptionList]] = [] if found is not None else None
            own = _command_candidates(
                cur,
                str(default),
                walk.args_index,
                parsed,
                prev,
                incomplete,
                config_path,
                consumed_config,
                app_name,
                lazy_refresh,
                force_refresh,
                None if mine is None else (mine, f"{level} {default}"),
            )
            if found is not None:
                found += mine or [_OptionList(node=cur)]
                found[-1].commands_of = level
            return own + [n for n in names if n not in own]
        if found is not None:
            found.append(_OptionList(node=cur, commands_of=level))
        return names

    return _command_candidates(
        cur,
        cmd_name,
        walk.args_index,
        parsed,
        prev,
        incomplete,
        config_path,
        consumed_config,
        app_name,
        lazy_refresh,
        force_refresh,
        None if found is None else (found, _command_path(tree, parsed[: walk.args_index], cmd_name)),
    )


def _command_path(tree: Dict[str, Any], leading: List[str], cmd_name: Optional[str]) -> str:
    """``<app> <sub-app ...> <command>``: the canonical names of the sub-apps ``leading`` enters, then the command
    (none: the level alone)."""
    names = [str(tree.get("name", ""))]
    node = tree
    for tok in leading:
        sub = (node.get("sub_apps") or {}).get(tok)
        if sub is not None:
            node = sub
            names.append(str(sub.get("name", tok)))
    return " ".join([*names, cmd_name] if cmd_name else names)


def _typed_positionals(cur: Dict[str, Any], cmd_name: str, parsed: List[str], args_index: int) -> Dict[str, str]:
    """The positionals typed after the command, by name — leading words up to the first flag-like token."""
    names = list((cur.get("positionals") or {}).get(cmd_name, []))
    typed: List[str] = []
    for tok in parsed[args_index:]:
        if stops_positional(tok):
            break
        typed.append(tok)
    return dict(zip(names, typed))


def _slot_candidates(
    info: Dict[str, Any],
    typed: Dict[str, str],
    incomplete: str,
    app_name: str,
    lazy_refresh: Optional[Callable[..., None]],
    force_refresh: bool,
) -> List[str]:
    """A non-positional slot's cached values (an option's value, a command's config scopes), self-healing."""
    inputs = cache.slot_inputs(info, typed)
    if inputs is None:
        return []
    values = cache.read_slot_values(app_name, info, typed)
    if lazy_refresh is not None and (force_refresh or cache.slot_is_stale(app_name, info, inputs)):
        lazy_refresh(info["key"], inputs)
    return _list_candidates(list(values or []), incomplete)


def _list_candidates(values: List[str], incomplete: str) -> List[str]:
    """``values`` matching ``incomplete`` — or, after a comma, the list's next word: ``a,`` → ``a,b``, ``a,c``.

    A comma separates a list's items in a positional (``pictures all a,d``), an option's value and ``--scope``; the
    words already in the list are left out. Shells keep a comma inside the word, so the whole list is the candidate.
    """
    if "," not in incomplete:
        return _filter_prefix(values, incomplete)
    head, _, tail = incomplete.rpartition(",")
    chosen = set(head.split(","))
    return [f"{head},{value}" for value in _filter_prefix(values, tail) if value not in chosen]


def _scope_candidates(
    tree: Dict[str, Any],
    parsed: List[str],
    incomplete: str,
    app_name: str,
    lazy_refresh: Optional[Callable[..., None]],
    force_refresh: bool,
) -> List[str]:
    """``--scope <TAB>``: the scopes of the config files a command runs (``@command(config_files=...)``).

    Read from the cache for the positionals typed before the flag (``{}`` when none is). Any other command's
    ``--scope`` activates the app's own config, whose scopes this path cannot know without loading it: nothing is
    offered, as before. A missing or stale cache self-heals like a dependent positional's.
    """
    walk = walk_invocation(tokenize(parsed), _TreeNav(tree), _peek_config)
    cur = walk.nav.node if isinstance(walk.nav, _TreeNav) else tree
    cmd_name = walk.cmd_name or ""
    info = (cur.get("scope_completions") or {}).get(cmd_name)
    if not info:
        return []
    typed = _typed_positionals(cur, cmd_name, parsed, walk.args_index)
    return _slot_candidates(info, typed, incomplete, app_name, lazy_refresh, force_refresh)


def _command_candidates(
    cur: Dict[str, Any],
    cmd_name: str,
    args_index: int,
    parsed: List[str],
    prev: str,
    incomplete: str,
    config_path: Optional[Path],
    consumed_config: bool,
    app_name: str,
    lazy_refresh: Optional[Callable[..., None]],
    force_refresh: bool,
    found: Optional[Tuple[List[_OptionList], str]],
) -> List[str]:
    """Candidates once the line is inside a command: its config, positionals, flags.

    ``found`` (the listing's out-list and the command's path) is appended to when the candidates are its flags.

    ``args_index`` is where the command's arguments start in ``parsed`` (the walk
    reports it — for a default command bound without a name token no token equals
    ``cmd_name``, so the old "first token equal to the name" lookup would miss).
    """
    is_script_cmd = cmd_name in cur["script_cmds"]
    # ``signature_flags``: the command's options already collapsed to
    # shortest-unique ``--flag`` form (baked at serialize time). ``signature_paths``:
    # the raw dotted paths, kept so the config-present branch can re-collapse
    # the UNION of these and the YAML's own keys in one pass.
    # ``signature_bool_flags``: every spelling of the command's bool-typed
    # flags — they take no value, so they never open a value slot.
    signature_flags = list((cur.get("signature_flags") or {}).get(cmd_name, []))
    signature_paths = list((cur.get("signature_paths") or {}).get(cmd_name, []))
    bool_flags = set((cur.get("signature_bool_flags") or {}).get(cmd_name, []))

    # Option keys already consumed on the line (after the command token), in
    # any of the override grammar's spellings: ``--key value``, ``--key=value``,
    # ``--key+``/``--key-``, ``+key[=v]``, bare ``key=value``. Used to (a) skip
    # flag-provided positionals in the hint below and (b) drop already-typed
    # flags from the candidates — offering ``--target_version`` again after it
    # was consumed is noise (a repeat parses, but last-write-wins).
    used_keys: Set[str] = set()
    for tok in parsed[args_index:]:
        if tok.startswith("--"):
            key = tok[2:].split("=", 1)[0]
            if key.endswith(("+", "-")):
                key = key[:-1]
            if key:
                used_keys.add(key)
        elif tok.startswith("+"):
            body = tok[1:]
            if body.startswith("--"):
                body = body[2:]
            key = body.split("=", 1)[0]
            if key:
                used_keys.add(key)
        elif "=" in tok and not tok.startswith("="):
            head = tok.split("=", 1)[0]
            if looks_like_key(head):
                used_keys.add(head)

    def _not_used(flag: str) -> bool:
        # A candidate is "used" when its key was typed exactly, or when a full
        # dotted path ending in the candidate's collapsed leaf was typed
        # (``--converter.dry`` consumed ⇒ drop the collapsed ``--dry``).
        key = flag[2:]
        return not any(k == key or k.endswith("." + key) for k in used_keys)

    # The previous token is a value-taking ``--flag`` (and not one of the
    # globals whose values we resolved at the top): its value comes next and
    # we can't know the type, so stay silent and let the shell's default
    # filename completion kick in. Checked FIRST so a value slot
    # (``--converter.src <TAB>``) stays silent even for a script_command that
    # hasn't consumed a config yet — otherwise the config-file branch below
    # would hijack the flag's value position. Applies to both command kinds.
    # NOT a value slot (fall through to hints/flags instead of silence):
    # a global non-value flag (``--debug``), a self-contained ``--key=value``
    # or polarity ``--key+``/``--key-`` token, and a known bool-typed command
    # flag (``--append`` is store-true; its "value" is the next option).
    if (
        prev.startswith("--")
        and "=" not in prev
        and not prev.endswith(("+", "-"))
        and prev not in GLOBAL_FLAGS
        and prev not in bool_flags
    ):
        # An option whose value has a provider (``--library <TAB>``) offers its cached values.
        info = ((cur.get("flag_completions") or {}).get(cmd_name) or {}).get(prev[2:].replace("-", "_"))
        if info:
            typed = _typed_positionals(cur, cmd_name, parsed, args_index)
            return _slot_candidates(info, typed, incomplete, app_name, lazy_refresh, force_refresh)
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
    def listed(flag_paths: Optional[Dict[str, str]] = None) -> None:
        """Hand the listing what this command's candidates are made of: what was typed, whose they are."""
        if found is None:
            return
        tokens = list(parsed[args_index:])
        if consumed_config and tokens:
            tokens = tokens[1:]  # the config, shown in its own row
        typed: List[str] = []
        for tok in tokens:
            if stops_positional(tok):
                break
            typed.append(tok)
        names = list((cur.get("positionals") or {}).get(cmd_name, []))
        config = (parsed[args_index] if args_index < len(parsed) else str(config_path)) if consumed_config else None
        found[0].append(
            _OptionList(
                node=cur,
                cmd_name=cmd_name,
                path=found[1],
                flag_paths=flag_paths,
                typed=typed,
                given_by_flag={p for p in names if p in used_keys},
                config=config,
            )
        )

    if is_script_cmd and not consumed_config and not incomplete.startswith("-"):
        files = _file_candidates(incomplete, exts=["yaml", "yml"])
        flags = list(GLOBAL_FLAGS) + [f for f in signature_flags if _not_used(f)]
        listed()
        return files + _filter_prefix(flags, incomplete)

    # Positional hints: when the cursor is at an unfilled positional slot, emit
    # ``<name>`` as the first candidate so the user sees what to type without
    # consulting ``--help``.  The angle-bracket format is a conventional
    # "placeholder, not a literal" signal — shells show it in the completion
    # list but don't expand it.  The hint appears only when ``incomplete`` is
    # empty (bare TAB); while the user is actively typing it is filtered out by
    # ``_filter_prefix`` naturally.
    positional_hint: List[str] = []
    cmd_positionals = list((cur.get("positionals") or {}).get(cmd_name, []))
    if cmd_positionals:
        consumed_tokens: List[str] = []
        for tok in parsed[args_index:]:
            # Stop at the first flag-like or key=value token — the SAME
            # classifier core's dispatcher uses (grammar is stdlib-only,
            # so the fast path may import it).
            if stops_positional(tok):
                break
            consumed_tokens.append(tok)
        n_consumed = len(consumed_tokens)
        # A positional supplied in its (still valid) ``--flag`` / ``key=value``
        # spelling counts as filled — the hint moves past it. Positionally
        # typed tokens keep binding to the declared order (unchanged).
        unfilled = [p for p in cmd_positionals if p not in used_keys]
        if n_consumed < len(unfilled):
            pos_name = unfilled[n_consumed]
            # If this positional registered a value provider AND its cache is
            # populated, offer the real cached values; otherwise fall back to the
            # ``<name>`` placeholder. The cache is stdlib-JSON — the provider never
            # runs on this hot path.
            info = ((cur.get("positional_completions") or {}).get(cmd_name) or {}).get(pos_name) or {}
            cached_values: Optional[List[str]] = None
            show_notice = False
            if info.get("kind") == "dependent":
                # Resolve this slot's values from the cache keyed by the ALREADY-TYPED
                # earlier positionals (e.g. <version> from the cache for <name>).
                inputs: Dict[str, str] = {}
                for dep in info.get("depends_on", []):
                    di = cmd_positionals.index(dep) if dep in cmd_positionals else -1
                    if 0 <= di < len(consumed_tokens):
                        inputs[dep] = consumed_tokens[di]
                if len(inputs) == len(info.get("depends_on", [])) and inputs:
                    cached_values = cache.read_dependent_value_cache(app_name, info["key"], inputs)
                    # Notify (only when it matters): a lazy self-heal that actually
                    # changed the values flags ``<…-updated>`` for a short window so the
                    # user sees the background refresh took effect.
                    if cached_values and cache._dependent_changed_recently(
                        app_name, info["key"], inputs, cache.DEPENDENT_NOTICE_WINDOW
                    ):
                        show_notice = True
                    # Self-heal: if this combo's cache is missing or stale, kick off a
                    # background refresh for it (non-blocking) so new datasets / new
                    # versions / beyond-the-cap names become current on the NEXT TAB.
                    # ``force_refresh`` (a double-TAB) refreshes regardless of age; the
                    # spawner's own throttle still bounds it to once per session.
                    if lazy_refresh is not None:
                        age = cache._dependent_value_cache_age(app_name, info["key"], inputs)
                        if force_refresh or age is None or age > cache.DEPENDENT_REFRESH_TTL:
                            lazy_refresh(info["key"], inputs)
            elif info.get("key"):
                cached_values = cache.read_value_cache(app_name, info["key"])
                # Self-heal STATIC positionals too: a missing/stale name list (e.g. a
                # brand-new positional that has never been refreshed) populates itself in
                # the background on first TAB, so it appears on the next one — no manual
                # `--refresh-completions` needed. Empty ``inputs`` marks a static refresh.
                # ``force_refresh`` (a double-TAB) refreshes regardless of age — the fix
                # for a fresh-by-age but wrong list (e.g. an item deleted upstream); the
                # spawner's throttle still bounds it to once per session.
                if lazy_refresh is not None:
                    age = cache._value_cache_age(app_name, info["key"])
                    if force_refresh or age is None or age > cache.DEPENDENT_REFRESH_TTL:
                        lazy_refresh(info["key"], {})
            positional_hint = list(cached_values) if cached_values else [f"<{pos_name}>"]
            # The notice is a ``<…>`` hint: shown at a bare TAB (alongside the values),
            # space-free so it stays one token, and dropped by _filter_prefix the moment
            # the user types a real value prefix.
            if show_notice:
                positional_hint = [f"<{pos_name}-updated>"] + positional_hint

    # Otherwise the user is at a flag position. Offer the global flags plus this
    # command's own option flags. Empty ``incomplete`` is included so bare
    # ``<cmd> <TAB>`` reveals the options instead of falling back to filename
    # completion. When a script_command has a config on the line, collapse the
    # UNION of the signature paths and the YAML's own override keys to
    # shortest-unique form in a single pass (confluid is already loaded to read
    # the YAML) so completion and ``--help`` agree; otherwise use the flags
    # collapsed at serialize time (the stdlib-only fast path — no confluid).
    candidates = list(GLOBAL_FLAGS)
    flag_paths: Optional[Dict[str, str]] = None
    resolved_config = _resolve_config_path(config_path) if (is_script_cmd and config_path is not None) else None
    if resolved_config is not None:
        # Local import: the module-level ``tree`` name is shadowed here by the
        # ``tree`` parameter (the serialized command dict), and this branch has
        # already left the stdlib-only fast path (it reads the YAML).
        from .tree import _collapse_to_flags, _flag_display_map

        try:
            yaml_paths = _resolve_override_keys(resolved_config)
        except Exception:
            yaml_paths = []
        candidates.extend(f for f in _collapse_to_flags(signature_paths + yaml_paths) if _not_used(f))
        flag_paths = {flag: path for path, flag in _flag_display_map(signature_paths + yaml_paths).items()}
    else:
        candidates.extend(f for f in signature_flags if _not_used(f))
    # Prepend positional hint (if any) before flag candidates; _filter_prefix
    # naturally drops it when the user has started typing a non-matching prefix.
    if "," in incomplete and positional_hint and not positional_hint[-1].startswith("<"):
        return _list_candidates([v for v in positional_hint if not v.startswith("<")], incomplete)
    listed(flag_paths)
    return _filter_prefix(positional_hint + candidates, incomplete)


def _listing(
    candidates: List[str], options: _OptionList, width: int, colour: bool = False, codes_count: bool = False
) -> Listing:
    """Group ``candidates`` into the commands, the command's arguments, the command's own options and the global ones.

    A global option's spellings share one row (``-c, --config PATH``), shown at the first of them that is a
    candidate; the others complete but show nothing. A command option's row carries its declared short spelling,
    when it takes a value its name as the placeholder (``--port PORT``), and its default after the help; a config's
    own key shows bare. The arguments group says each argument's state: a typed one with its value, a missing one as
    ``<name>`` with its help, the next one marked ``▸`` with its values (or a script command's config files) indented
    under it. The spelling column is as wide as the widest spelling the command could offer, so rows keep their place
    as the word is typed.
    """
    visible = [spec for spec in GLOBAL_FLAG_SPECS if not spec.hidden]
    spec_of = {flag: spec for spec in visible for flag in spec.flags}
    cmd = options.cmd_name or ""
    node = options.node
    docs = [list(entry) + ["", ""][len(entry) - 2 :] for entry in (node.get("option_docs") or {}).get(cmd, [])]
    doc_of_path = {str(entry[0]): str(entry[2]) for entry in docs}
    default_of_path = {str(entry[0]): str(entry[3]) for entry in docs if len(entry) > 3}
    flag_paths = options.flag_paths if options.flag_paths is not None else {str(e[1]): str(e[0]) for e in docs}
    short_of = {param: letter for letter, param in ((node.get("option_shorts") or {}).get(cmd) or {}).items()}
    bools = set((node.get("signature_bool_flags") or {}).get(cmd, []))
    aliases = set(node.get("sub_app_aliases") or [])
    sub_apps = {name: sub for name, sub in (node.get("sub_apps") or {}).items() if name not in aliases}
    command_help = {
        **{n: str(s.get("description") or "") for n, s in sub_apps.items()},
        **(node.get("command_docs") or {}),
    }
    names = set(node.get("commands") or []) | set(sub_apps) if options.commands_of is not None else set()
    positional_help = (node.get("positional_docs") or {}).get(cmd, {})
    is_script = bool(cmd) and cmd in (node.get("script_cmds") or [])
    slots = (["config"] if is_script else []) + list((node.get("positionals") or {}).get(cmd, []))

    def command_left(flag: str) -> List[_Piece]:
        key = flag[2:]
        pieces: List[_Piece] = [(f"-{short_of[key]}, {flag}" if key in short_of else flag, "option")]
        path = flag_paths.get(flag)
        if path in doc_of_path and flag not in bools:
            pieces.append((" " + key.rsplit(".", 1)[-1].upper().replace("-", "_"), "metavar"))
        return pieces

    def global_left(spec: Any) -> List[_Piece]:
        spellings = ", ".join(sorted(spec.flags, key=len))
        return [(spellings, "option"), (f" {spec.metavar}", "metavar")] if spec.metavar else [(spellings, "option")]

    def option_help(flag: str) -> str:
        path = flag_paths.get(flag, "")
        help_text, default = doc_of_path.get(path, ""), default_of_path.get(path, "")
        return f"{help_text} ({default})".strip() if default else help_text

    lefts = [flag_display(spec) for spec in visible] + ["".join(t for t, _ in command_left(str(e[1]))) for e in docs]
    lefts += list(names) + [f"<{slot}>" for slot in slots]
    left_w = min(max(len(left) for left in lefts), _LEFT_MAX)

    def row(left: List[_Piece], doc: str = "", doc_role: Optional[Role] = "help", lead: str = "  ") -> str:
        """``lead``, the spelling column padded to ``left_w``, two spaces, ``doc``: cut to ``width - 1`` visible
        characters, each piece painted by its role."""
        lead_role: Optional[Role] = "missing" if lead == NEXT_MARK else None
        shown = sum(len(t) for t, _ in left)
        pieces: List[_Piece] = [(lead, lead_role), *left]
        if doc:
            pieces += [(" " * max(left_w - shown, 0) + "  ", None), (doc, doc_role)]
        out: List[str] = []
        room = width - 1
        for text, role in pieces:
            codes = len(_paint(text, role, colour)) - len(text) if codes_count else 0
            if len(text) + codes > room:  # cut: the row ends in "…", in the colour of the piece it cuts
                keep = room - codes - 1
                if keep >= 0:
                    out.append(_paint(text[:keep] + "…", role, colour))
                break
            out.append(_paint(text, role, colour))
            room -= len(text) + codes
        return "".join(out).rstrip()

    command_rows: List[ListingRow] = []
    values: List[str] = []
    global_rows: List[ListingRow] = []
    option_rows: List[ListingRow] = []
    shown_specs: Set[str] = set()
    for cand in candidates:
        spec = spec_of.get(cand)
        if spec is not None:
            global_rows.append((cand, "" if spec.dest in shown_specs else row(global_left(spec), spec.help)))
            shown_specs.add(spec.dest)
        elif cand in names:
            command_rows.append((cand, row([(cand, "command")], str(command_help.get(cand, "")))))
        elif cand.startswith("-"):
            option_rows.append((cand, row(command_left(cand), option_help(cand))))
        else:
            values.append(cand)

    hints = [v for v in values if v.startswith("<") and v.endswith(">")]
    offered = [v for v in values if v not in hints]
    argument_rows: List[ListingRow] = []
    typed = list(options.typed)
    current_done = False
    for slot in slots:
        is_config = slot == "config" and is_script and not argument_rows
        help_text = CONFIG_HELP if is_config else str(positional_help.get(slot, ""))
        if is_config and options.config is not None:
            argument_rows.append(("", row([(slot, "given")], options.config, "given")))
        elif not is_config and slot in options.given_by_flag:
            argument_rows.append(("", row([(slot, "given")], f"given as --{slot}", "given")))
        elif not is_config and typed:
            argument_rows.append(("", row([(slot, "given")], typed.pop(0), "given")))
        elif not current_done:  # the next argument, marked: its hint, or its values under it
            current_done = True
            own = [h for h in hints if h == f"<{slot}>"]
            marked = row([(f"<{slot}>", "missing")], help_text, lead=NEXT_MARK)
            argument_rows.append((own[0], marked) if own else ("", marked))
            argument_rows += [(h, row([(h, "missing")])) for h in hints if h not in own]
            argument_rows += [(v, row([(f"  {v}", None)])) for v in offered]
            hints, offered = [], []
        else:
            argument_rows.append(("", row([(f"<{slot}>", "missing")], help_text)))
    argument_rows += [(v, row([(v, None)])) for v in hints + offered]  # values no slot claimed: still offered

    def title(text: str) -> str:
        return _paint(text, "title", colour)

    groups: Listing = []
    if command_rows:
        groups.append((title(f"commands of {options.commands_of}"), command_rows))
    if argument_rows:
        groups.append((title(f"arguments of {options.path}"), argument_rows))
    if option_rows:
        groups.append((title(f"options of {options.path}"), option_rows))
    if global_rows:  # last: what every command takes (user correction 2026-10-28)
        groups.append((title(GLOBAL_HEADER), global_rows))
    return groups


def _filter_prefix(items: List[str], prefix: str) -> List[str]:
    """Keep items that start with ``prefix``, CASE-INSENSITIVELY, preserving order + de-duping.

    Case-insensitive so a lowercase prefix matches server-provided values with arbitrary
    case (typing ``h`` completes ``Raidar_…``) — liquifai filters server-side, so the shell's
    own ``completion-ignore-case`` never gets a chance to apply. Harmless for the lowercase
    command/flag candidates. Placeholders (``<name>``) start with ``<`` so a real prefix drops
    them either way.
    """
    low = prefix.lower()
    seen: Set[str] = set()
    out: List[str] = []
    for it in items:
        if it.lower().startswith(low) and it not in seen:
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
        raw = confluid.load(config_path, until="raw")
        dimensions = confluid.discover_dimensions(raw)
        cfg = confluid.load(raw, until="document")
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
