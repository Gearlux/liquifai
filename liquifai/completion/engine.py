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

from liquifai.grammar import GLOBAL_FLAGS, GLOBAL_VALUE_FLAGS, PATH_VALUE_FLAGS, SHELL_VALUE_FLAGS, stops_positional

from . import cache, tree
from .shells import SHELLS

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp

# ---------------------------------------------------------------------------
# Candidate computation
# ---------------------------------------------------------------------------


def complete(app: "LiquifyApp", words: List[str], cword: int) -> List[str]:
    """Convenience wrapper: snapshot ``app`` then call :func:`complete_from_tree`."""
    return complete_from_tree(tree.serialize_app(app), words, cword)


def complete_from_tree(
    tree: Dict[str, Any],
    words: List[str],
    cword: int,
    lazy_refresh: Optional[Callable[[str, Dict[str, str]], None]] = None,
) -> List[str]:
    """Compute completion candidates from a serialized command tree.

    Args:
        tree: A dict produced by :func:`serialize_app`.
        words: Tokenized command line including the program name at index 0.
        cword: Index of the word being completed (0-based).
        lazy_refresh: Optional ``(cache_key, inputs)`` callback invoked when a
            DEPENDENT positional's per-input cache is missing or stale, so the fast
            path can self-heal it in the background (see
            :func:`make_lazy_refresh_spawner`). Whatever is cached now (or the
            placeholder) is still returned immediately; this never blocks. ``None``
            (the default, used by tests / in-process completion) disables it.

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
        # Find where the command token sits in ``parsed`` (words[1:cword]).
        cmd_idx = next((j for j, t in enumerate(parsed) if t == cmd_name), None)
        consumed_tokens: List[str] = []
        if cmd_idx is not None:
            for tok in parsed[cmd_idx + 1 :]:
                # Stop at the first flag-like or key=value token — the SAME
                # classifier core's dispatcher uses (grammar is stdlib-only,
                # so the fast path may import it).
                if stops_positional(tok):
                    break
                consumed_tokens.append(tok)
        n_consumed = len(consumed_tokens)
        if n_consumed < len(cmd_positionals):
            pos_name = cmd_positionals[n_consumed]
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
                    if lazy_refresh is not None:
                        age = cache._dependent_value_cache_age(app_name, info["key"], inputs)
                        if age is None or age > cache.DEPENDENT_REFRESH_TTL:
                            lazy_refresh(info["key"], inputs)
            elif info.get("key"):
                cached_values = cache.read_value_cache(app_name, info["key"])
                # Self-heal STATIC positionals too: a missing/stale name list (e.g. a
                # brand-new positional that has never been refreshed) populates itself in
                # the background on first TAB, so it appears on the next one — no manual
                # `--refresh-completions` needed. Empty ``inputs`` marks a static refresh.
                if lazy_refresh is not None:
                    age = cache._value_cache_age(app_name, info["key"])
                    if age is None or age > cache.DEPENDENT_REFRESH_TTL:
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
    if is_script_cmd and config_path is not None and config_path.exists():
        # Local import: the module-level ``tree`` name is shadowed here by the
        # ``tree`` parameter (the serialized command dict), and this branch has
        # already left the stdlib-only fast path (it reads the YAML).
        from .tree import _collapse_to_flags

        try:
            yaml_paths = _resolve_override_keys(config_path)
        except Exception:
            yaml_paths = []
        candidates.extend(_collapse_to_flags(signature_paths + yaml_paths))
    else:
        candidates.extend(signature_flags)
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
