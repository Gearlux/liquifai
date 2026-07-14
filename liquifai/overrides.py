"""CLI override grammar and its application to the loaded config tree.

Extracted from ``core.py`` in the consolidation split: this module owns
everything between "a list of leftover CLI tokens" and "the config tree has
the overrides merged in" — parsing (:func:`parse_override_args`), Fluid
broadcast (:func:`merge_overrides_into_fluids`), deletions
(:func:`delete_dotted_key`), and env/``~`` expansion
(:func:`expand_strings`). ``core.py`` re-exports the historical
underscore-prefixed names for existing callers (fluxstudio, tests).

Token *classification* lives in :mod:`liquifai.grammar` (stdlib-only, shared
with the completion fast path); this module may import confluid freely.
"""

from __future__ import annotations

import inspect
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from confluid import parse_value
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


def merge_overrides_into_fluids(data: Any, overrides: Dict[str, Any]) -> None:
    """Merge CLI overrides into Fluid kwargs throughout the config tree."""
    if isinstance(data, Fluid):
        accepted = accepted_override_keys(data.target)
        # If this Fluid has a YAML-set `name: "<id>"`, dotted keys like
        # `"overlay.visualize"` land here by suffix — targeting this
        # instance only. Flat keys still broadcast as before.
        fluid_name = data.kwargs.get("name") if isinstance(data.kwargs, dict) else None
        for k, v in overrides.items():
            if fluid_name and "." in k:
                head, _, tail = k.partition(".")
                if head == str(fluid_name) and (tail in data.kwargs or tail in accepted):
                    data.kwargs[tail] = v
                    continue  # dotted form handled — don't also broadcast-match.
            # Flat form: apply when the kwarg is already in YAML (catches the
            # post-construction setattr pattern like `Enable.visualize`) OR
            # when the target class accepts it (ctor params always; for
            # ``@configurable`` classes, also public class-level attributes
            # that Confluid would setattr at flow time — e.g. @property
            # setters, plain class attrs).
            if k in data.kwargs or k in accepted:
                data.kwargs[k] = v
        for v in data.kwargs.values():
            merge_overrides_into_fluids(v, overrides)
    elif isinstance(data, dict):
        for v in data.values():
            merge_overrides_into_fluids(v, overrides)
    elif isinstance(data, list):
        for item in data:
            merge_overrides_into_fluids(item, overrides)


def accepted_override_keys(target: Any) -> Set[str]:
    """Return every attribute name ``target`` accepts as an override.

    For any class: the set of ``__init__`` parameter names.

    For ``@configurable`` classes additionally: every public class-level
    attribute — that is, any non-dunder, non-underscore name on the class
    that is not a method, is not a read-only ``@property``, and is not
    ``__confluid_ignore__``'d. This mirrors Confluid's post-construction
    setattr pattern — ``flow()`` accepts any extra kwarg that targets a
    public attribute, so overrides must too.

    ``target`` can be a class, an instance, or the dotted string Confluid
    uses for deferred class resolution (``!class:module.Cls``). Returns an
    empty set if the target can't be resolved or introspected.
    """
    cls: Any = target
    if isinstance(cls, str):
        cls = resolve_class(cls)
    if cls is None:
        return set()
    if not isinstance(cls, type):
        cls = cls.__class__
    init = getattr(cls, "__init__", None)
    if init is None:
        return set()
    try:
        sig = inspect.signature(init)
    except (ValueError, TypeError):
        return set()
    accepted: Set[str] = {p for p in sig.parameters if p not in ("self", "cls", "args", "kwargs")}

    if not getattr(cls, "__confluid_configurable__", False):
        return accepted

    # @configurable: Confluid setattr-applies any extra kwarg whose target is
    # a public class attribute. Include those in the accepted set.
    for name in dir(cls):
        if name.startswith("_"):
            continue
        member = getattr(cls, name, None)
        if getattr(member, "__confluid_ignore__", False):
            continue
        if callable(member) and not isinstance(member, property):
            continue  # skip bound methods / functions
        if isinstance(member, property) and member.fset is None:
            continue  # read-only properties can't accept overrides
        accepted.add(name)
    return accepted


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
