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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from liquifai.grammar import (
    GLOBAL_FLAGS,
    GLOBAL_VALUE_FLAGS,
    PATH_VALUE_FLAGS,
    SHELL_VALUE_FLAGS,
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
    parsed = words[1:cword]
    incomplete = words[cword] if 0 <= cword < len(words) else ""
    prev = words[cword - 1] if cword - 1 >= 1 else ""
    app_name = str(tree.get("name", ""))

    if prev in PATH_VALUE_FLAGS:
        return _file_candidates(incomplete, exts=None)
    if prev in SHELL_VALUE_FLAGS:
        return [s for s in SHELLS if s.startswith(incomplete)]
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
        if takes_args and len(parsed) == walk.args_index:
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
            )
            return own + [n for n in names if n not in own]
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
    )


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
) -> List[str]:
    """Candidates once the line is inside a command: its config, positionals, flags.

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
        flags = list(GLOBAL_FLAGS) + [f for f in signature_flags if _not_used(f)]
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
    resolved_config = _resolve_config_path(config_path) if (is_script_cmd and config_path is not None) else None
    if resolved_config is not None:
        # Local import: the module-level ``tree`` name is shadowed here by the
        # ``tree`` parameter (the serialized command dict), and this branch has
        # already left the stdlib-only fast path (it reads the YAML).
        from .tree import _collapse_to_flags

        try:
            yaml_paths = _resolve_override_keys(resolved_config)
        except Exception:
            yaml_paths = []
        candidates.extend(f for f in _collapse_to_flags(signature_paths + yaml_paths) if _not_used(f))
    else:
        candidates.extend(f for f in signature_flags if _not_used(f))
    # Prepend positional hint (if any) before flag candidates; _filter_prefix
    # naturally drops it when the user has started typing a non-matching prefix.
    return _filter_prefix(positional_hint + candidates, incomplete)


def _filter_prefix(items: List[str], prefix: str) -> List[str]:
    """Keep items that start with ``prefix``, CASE-INSENSITIVELY, preserving order + de-duping.

    Case-insensitive so a lowercase prefix matches server-provided values with arbitrary
    case (typing ``h`` completes ``Helios_…``) — liquifai filters server-side, so the shell's
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
