"""CLI override grammar and its application to the loaded config tree.

Extracted from ``core.py`` in the consolidation split: this module owns
everything between "a list of leftover CLI tokens" and "the config tree has
the overrides merged in" — parsing (:func:`parse_override_args`), Fluid
broadcast (:func:`merge_overrides_into_fluids`), deletions
(:func:`delete_dotted_key`), and env/``~`` expansion
(:func:`expand_strings`). ``core.py`` re-exports the historical
underscore-prefixed names for existing callers (streamstudio, tests).

Token *classification* lives in :mod:`liquifai.grammar` (stdlib-only, shared
with the completion fast path); this module may import confluid freely.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set, Tuple

from confluid import accepts_any_key, accepts_broadcast, accepts_key, deep_merge, expand_dotted_keys, parse_value
from confluid.fluid import Fluid
from confluid.registry import resolve_class

from liquifai.grammar import looks_like_arg, looks_like_key


def parse_override_args(args: List[str]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Tokenize ``args`` into an ``(overrides, deletions, dropped)`` triple.

    Supported forms (order-independent; longest match wins per token):

    * ``--key value``           — legacy space-separated form (still primary).
    * ``--key=value``           — equals form.
    * ``key=value``             — bare equals form, no ``--`` prefix.
    * ``--key+`` / ``--key-``   — polarity (True / False).
    * ``--key``                 — implicit ``True`` flag.
    * ``+key=value`` / ``+--key=value`` — add a new key (today merged with
      same semantics as a normal override; future: fail if key exists).
    * ``~key`` / ``~--key``     — delete the dotted key from the config.

    Any token that doesn't match a recognised form is collected into
    ``dropped`` (it is NOT applied). Callers surface these — a typo'd
    override that silently vanishes can cost an entire training run, so
    :meth:`liquifai.core.LiquifyApp._apply_overrides` logs one warning per
    dropped token.
    """
    overrides: Dict[str, Any] = {}
    deletions: List[str] = []
    dropped: List[str] = []
    i = 0
    while i < len(args):
        arg = args[i]

        if arg.startswith("~"):
            key = arg[1:]
            if key.startswith("--"):
                key = key[2:]
            if key:
                deletions.append(key)
            i += 1
            continue

        if arg.startswith("+"):
            body = arg[1:]
            if body.startswith("--"):
                body = body[2:]
            if "=" in body:
                k, v = body.split("=", 1)
                if k:
                    overrides[k] = parse_value(v)
            elif body and i + 1 < len(args) and not looks_like_arg(args[i + 1]):
                overrides[body] = parse_value(args[i + 1])
                i += 1
            elif body:
                overrides[body] = True
            i += 1
            continue

        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                if k:
                    overrides[k] = parse_value(v)
                i += 1
                continue
            if key.endswith("+"):
                overrides[key[:-1]] = True
                i += 1
                continue
            if key.endswith("-"):
                overrides[key[:-1]] = False
                i += 1
                continue
            if i + 1 < len(args) and not looks_like_arg(args[i + 1]):
                overrides[key] = parse_value(args[i + 1])
                i += 2
                continue
            overrides[key] = True
            i += 1
            continue

        # Bare ``key=value`` (no ``--``). Lets users drop the dashes when
        # they want — common ergonomics ask from the user. A token whose head
        # isn't shaped like a config key (JSON-ish blobs, URLs, file paths)
        # falls through to the dropped list below.
        if "=" in arg and not arg.startswith("="):
            k, v = arg.split("=", 1)
            if k and looks_like_key(k):
                overrides[k] = parse_value(v)
                i += 1
                continue

        # Unrecognised token — collect so the caller can warn about it.
        dropped.append(arg)
        i += 1

    return overrides, deletions, dropped


def apply_overrides(data: Any, overrides: Dict[str, Any], deletions: List[str]) -> Any:
    """Apply parsed CLI overrides and deletions to a loaded config tree.

    The whole "leftover tokens are parsed, now change the config" step, in
    document order:

    1. expand ``$VAR`` / ``~`` in the override VALUES (the tree was expanded at
       load time; overrides arrive later and must get the same treatment),
    2. ``deep_merge`` them into the tree and expand dotted keys into nesting,
    3. remove each deleted dotted path,
    4. push the overrides down into any ``Fluid`` kwargs via
       :func:`merge_overrides_into_fluids` — the step that reaches values
       already committed to a marker rather than sitting in a plain dict.

    Returns the new tree (the input may be replaced outright when it was
    ``None``); a no-op call returns ``data`` unchanged.
    """
    if not overrides and not deletions:
        return data

    parsed = expand_strings(overrides)
    if data is None:
        data = {}

    data = deep_merge(data, parsed)
    if isinstance(data, dict):
        data = expand_dotted_keys(data)

    for path in deletions:
        delete_dotted_key(data, path)

    merge_overrides_into_fluids(data, parsed)
    return data


def merge_overrides_into_fluids(data: Any, overrides: Dict[str, Any], _visited: Optional[Set[int]] = None) -> None:
    """Merge CLI overrides into Fluid kwargs throughout the config tree.

    Two override forms, matching Confluid's own addressing rules exactly (the
    settability question is answered by :func:`confluid.accepts_key` /
    :func:`confluid.accepts_broadcast` — liquifai does NOT re-derive an
    accept-list, see ``docs/architecture.md``):

    * ``--<name>.<key>`` where ``<name>`` matches a Fluid's YAML-set
      ``name:`` — an ADDRESSED write, targeting that instance only. Gated by
      :func:`confluid.accepts_key` (constructor params, settable properties,
      ``__init__``-body slots, ``**kwargs`` targets).
    * ``--<key>`` — a BARE broadcast reaching every Fluid in the tree that can
      take it. Gated by the stricter :func:`confluid.accepts_broadcast`, so a
      class marked ``@configurable(broadcast=False)`` or a parameter typed
      ``NoBroadcast[T]`` is skipped — the same opt-outs a bare top-level YAML
      key obeys. Addressed writes deliberately bypass them.

    A ``**kwargs`` target is DELIBERATELY NOT written here, even though it
    accepts every key: writing into a marker's own kwargs is the ADDRESSED
    channel, and confluid hands an addressed key to the CONSTRUCTOR while a bare
    one becomes a post-init attribute. Claiming a bare ``--run_name`` was
    addressed to a class that merely cannot refuse it is how run identity ended
    up as a constructor argument of a metric, which raised ``Unexpected keyword
    arguments`` from inside a library that never asked for it. Such a key is
    left alone: ``apply_overrides`` has already merged it into the document, so
    confluid's own broadcasting delivers it with the right provenance. A class
    that DECLARES the key is unaffected — it is still written here, so a
    ``--num_workers 8`` still reaches the constructor that takes it.

    A Fluid whose target class cannot be resolved (a ``!class:`` naming a
    module that is not importable yet) has no accept-list to consult, so it
    falls back to "the key is already in the YAML kwargs" — a deferred marker
    stays overridable rather than silently ignoring every override.

    Cycle-safe via ``id()`` tracking: a ``!ref:``-shared marker graph with a
    back-edge is visited once.
    """
    if _visited is None:
        _visited = set()

    if isinstance(data, Fluid):
        vid = id(data)
        if vid in _visited:
            return
        _visited.add(vid)

        cls = _resolve_target_class(data.target)
        # If this Fluid has a YAML-set `name: "<id>"`, dotted keys like
        # `"overlay.visualize"` land here by suffix — targeting this
        # instance only. Flat keys still broadcast as before.
        fluid_name = data.kwargs.get("name") if isinstance(data.kwargs, dict) else None
        for k, v in overrides.items():
            if fluid_name and "." in k:
                head, _, tail = k.partition(".")
                if head == str(fluid_name) and (tail in data.kwargs or accepts_key(cls, tail)):
                    data.kwargs[tail] = v
                    continue  # dotted form handled — don't also broadcast-match.
            if cls is None:
                if k in data.kwargs:
                    data.kwargs[k] = v
            elif accepts_any_key(cls):
                continue  # no accept-list to filter with — let it cascade as a BARE key
            elif accepts_broadcast(cls, k):
                data.kwargs[k] = v
        for v in list(data.kwargs.values()):
            merge_overrides_into_fluids(v, overrides, _visited)
    elif isinstance(data, dict):
        for v in data.values():
            merge_overrides_into_fluids(v, overrides, _visited)
    elif isinstance(data, list):
        for item in data:
            merge_overrides_into_fluids(item, overrides, _visited)


def _resolve_target_class(target: Any) -> Any:
    """Normalize a Fluid ``target`` into the class to ask about, or ``None``.

    ``target`` may be a class, an instance, or the dotted string Confluid uses
    for deferred resolution (``!class:module.Cls``). ``None`` means "not
    introspectable" — the caller falls back to the already-in-YAML rule.
    """
    cls: Any = resolve_class(target) if isinstance(target, str) else target
    if cls is None:
        return None
    return cls if isinstance(cls, type) else type(cls)


def delete_dotted_key(config: Any, path: str) -> None:
    """Best-effort deletion of ``config[path[0]][path[1]]...``.

    Walks the dotted path through nested dicts and Fluid ``kwargs``. Silent
    no-op if any segment is missing or the leaf can't be removed.
    """
    parts = path.split(".")
    current: Any = config
    for part in parts[:-1]:
        if isinstance(current, Fluid):
            current = current.kwargs
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    leaf = parts[-1]
    if isinstance(current, Fluid):
        current = current.kwargs
    if isinstance(current, dict):
        current.pop(leaf, None)


def expand_strings(data: Any, _visited: Optional[Set[int]] = None) -> Any:
    """Recursively expand environment variables and ~ in strings."""
    if isinstance(data, str):
        if "$" in data or "~" in data:
            return os.path.expanduser(os.path.expandvars(data))
        return data

    if isinstance(data, (int, float, bool, type(None))):
        return data

    if _visited is None:
        _visited = set()

    vid = id(data)
    if vid in _visited:
        return data
    _visited.add(vid)

    if isinstance(data, dict):
        return {k: expand_strings(v, _visited) for k, v in data.items()}
    if isinstance(data, list):
        return [expand_strings(v, _visited) for v in data]
    if isinstance(data, tuple):
        out = [expand_strings(v, _visited) for v in data]
        if hasattr(type(data), "_fields"):
            return type(data)(*out)
        return type(data)(out)
    if isinstance(data, Fluid):
        if isinstance(data.kwargs, dict):
            data.kwargs = {k: expand_strings(v, _visited) for k, v in data.kwargs.items()}

    return data
