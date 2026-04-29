"""Scope-block resolution for liquifai-driven configs.

Scopes are a CLI/runtime concern: a user passes ``--scope <name>`` (or
multiple) to a liquifai-built application; the matching scope blocks in
the configuration YAML are unwrapped at THEIR ORIGINAL POSITION. Confluid
itself knows nothing about scopes — by the time it sees the config, the
active scope blocks have already been spliced in.

Unwrap rule:
    For each active scope name (after alias resolution and hierarchy
    expansion), if the top-level dict has a dict-valued key matching that
    name, replace the wrapper key with its contents at the SAME position.
    Other keys (including inactive scope blocks like ``prod:`` when only
    ``debug`` is active) are left as-is — liquifai cannot tell whether a
    given dict-valued top-level key is a scope or something domain-meaningful,
    so it does not guess.

``not <name>:`` blocks are applied (in-place) when the named scope is NOT
active and dropped otherwise.

The metadata keys ``scope_aliases`` and ``scopes`` are always stripped.
"""

from typing import Any, Dict, List, Set

from logflow import get_logger

logger = get_logger("liquifai.scopes")


def resolve_scopes(config: Dict[str, Any], active_scopes: List[str]) -> Dict[str, Any]:
    """Splice active scope blocks into the top-level dict at their original positions.

    Args:
        config: The raw config dict (post-include, pre-flow). May contain
            scope blocks (top-level dict-valued keys whose names match
            entries in ``active_scopes`` after alias + hierarchy resolution),
            ``not <name>:`` blocks, ``scope_aliases``, and a ``scopes`` list.
        active_scopes: Names of the scopes the user activated (typically
            from ``--scope`` on the CLI).

    Returns:
        A new dict with active scope blocks unwrapped in place, ``not <name>:``
        blocks applied when ``<name>`` is not active, and scope metadata
        stripped. Inactive scope blocks (e.g. ``prod:`` when only ``debug``
        is active) are preserved verbatim — liquifai does not strip them.
    """
    logger.debug(f"Resolving scopes: {active_scopes}")

    aliases = config.get("scope_aliases", {})
    resolved = _resolve_aliases(active_scopes, aliases) if active_scopes else []

    # Build the hierarchy-expanded ordered list of active scope names.
    all_active: List[str] = []
    seen: Set[str] = set()
    for s in resolved:
        for h in _expand_hierarchy(s):
            if h not in seen:
                seen.add(h)
                all_active.append(h)

    # Apply each active scope in order, in place at the wrapper key's slot.
    result: Dict[str, Any] = dict(config)
    for scope in all_active:
        block = result.get(scope)
        if isinstance(block, dict):
            result = _splice_in_place(result, scope, block)

    # Apply ``not <name>:`` blocks at their slot when ``<name>`` is not
    # active; drop the wrapper key in either case (it's metadata, not data).
    not_keys = [k for k in result if isinstance(k, str) and k.startswith("not ") and len(k) > 4]
    for key in not_keys:
        block = result.get(key)
        target = key[4:]
        if target not in seen and isinstance(block, dict):
            result = _splice_in_place(result, key, block)
        else:
            new_result: Dict[str, Any] = {k: v for k, v in result.items() if k != key}
            result = new_result

    result.pop("scope_aliases", None)
    result.pop("scopes", None)
    return result


def _splice_in_place(top: Dict[str, Any], wrapper_key: str, block: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict with ``top[wrapper_key]``'s contents replacing the
    wrapper at the same position. If a content key collides with an existing
    top-level key, Python dict re-assignment keeps the original position."""
    out: Dict[str, Any] = {}
    for k, v in top.items():
        if k == wrapper_key:
            for bk, bv in block.items():
                out[bk] = bv
        else:
            out[k] = v
    return out


def _resolve_aliases(requested: List[str], aliases: Dict[str, Any]) -> List[str]:
    """Recursively expand alias chains. Detects circular references."""
    resolved: List[str] = []
    seen: Set[str] = set()

    def expand(name: str, path: List[str]) -> None:
        if name in path:
            raise ValueError(f"Circular scope alias detected: {' -> '.join(path + [name])}")
        if name in seen:
            return
        seen.add(name)
        if name in aliases:
            target = aliases[name]
            new_path = path + [name]
            if isinstance(target, str):
                expand(target, new_path)
            elif isinstance(target, list):
                for t in target:
                    expand(t, new_path)
        else:
            resolved.append(name)

    for r in requested:
        seen.clear()
        expand(r, [])
    return resolved


def _expand_hierarchy(scope_name: str) -> List[str]:
    """``"prod.gpu"`` -> ``["prod", "prod.gpu"]``. Each hierarchy level is
    applied in turn, so deeper levels override their parents."""
    parts = scope_name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]
