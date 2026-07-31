"""Command-tree snapshot + provider refresh for liquifai completion.

Owns ``serialize_app`` (the JSON command-tree snapshot the fast path reads),
the tree cache read/write (``CACHE_VERSION`` envelope), the positional
value-PROVIDER machinery (spec derivation, bulk ``refresh_value_caches``,
targeted ``refresh_one``, staleness checks), and the confluid-delegating
flag introspection (``_introspect_function_keys`` / ``_collapse_to_flags``).

Runs in the APP process (cache-build/refresh time) — confluid is imported
lazily inside functions, so importing this module stays stdlib-only and the
fast path may reach it (it never calls the confluid-touching paths).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from . import cache

if TYPE_CHECKING:
    from liquifai.core import LiquifyApp

# v6: declared positionals are EXCLUDED from ``signature_paths`` /
# ``signature_flags`` (a required positional was also offered as its ``--flag``
# spelling — the spelling still parses, it is just no longer advertised), and
# the new ``signature_bool_flags`` map records every spelling (collapsed + full
# path) of a command's bool-typed flags so the fast path can tell a store-true
# flag (``--append``) from a value-taking one and keep offering flags after it
# instead of falling silent (which made the shell complete filenames).
# v5: ``positional_completions`` entries are now ``{positional: {key, kind,
# depends_on}}`` (kind ``static``|``dependent``) so a positional's values can
# depend on EARLIER positionals (e.g. ``download <name> <version>`` — version
# depends on name). v4 stored only ``{positional: key}`` (static).
# v4: added ``positional_completions`` so the fast path can offer real cached
# values for a positional (dynamic completion), falling back to the ``<name>``
# placeholder when no cache exists.
# v3: command option flags are stored as shortest-unique-path-collapsed
# ``signature_flags`` (+ raw ``signature_paths``), replacing the v2
# ``signature_keys`` ``{param: [subs]}`` map. Bumping invalidates stale caches
# so they are rewritten on the next run / ``--help``.
CACHE_VERSION: int = 6

# ---------------------------------------------------------------------------
# Cache (static command-tree snapshot)
# ---------------------------------------------------------------------------


def serialize_app(app: "LiquifyApp", _path: Tuple[str, ...] = ()) -> Dict[str, Any]:
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

    Declared positionals (``__liquifai_positionals__``) are EXCLUDED from both
    maps: a required positional would otherwise also be advertised as its
    ``--flag`` spelling (which still parses — positional / ``key=value`` /
    ``--flag`` forms interoperate — but must not be offered by TAB or listed
    as an option by ``--help``). A third map, ``signature_bool_flags``, records
    every spelling of the command's ``bool``-typed flags (the collapsed form
    AND the full ``--<path>`` form) so the fast path knows they take no value.
    """
    positionals_map = {cmd: list(getattr(func, "__liquifai_positionals__", [])) for cmd, func in app._commands.items()}
    command_paths: Dict[str, List[str]] = {}
    signature_flags: Dict[str, List[str]] = {}
    signature_bool_flags: Dict[str, List[str]] = {}
    for cmd, func in app._commands.items():
        hierarchy = _introspect_function_hierarchy(func)
        pos = set(positionals_map[cmd])
        paths = sorted(p for p in hierarchy if p.split(".", 1)[0] not in pos)
        command_paths[cmd] = paths
        display = _flag_display_map(paths)
        signature_flags[cmd] = list(dict.fromkeys(display[p] for p in paths))
        bools: List[str] = []
        for p in paths:
            type_str = hierarchy[p][0] if isinstance(hierarchy[p], (tuple, list)) else None
            if type_str != "bool":
                continue
            for spelling in (display[p], f"--{p}"):
                if spelling not in bools:
                    bools.append(spelling)
        signature_bool_flags[cmd] = bools
    # Per-command completion info for positionals that registered a provider
    # (``@command(..., completions={...})``): ``{positional: {key, kind, depends_on}}``.
    # ``kind`` is ``dependent`` when the provider takes an argument (it receives the
    # earlier positionals) else ``static``; ``depends_on`` lists the prior positionals
    # a dependent provider is enumerated against. complete_from_tree reads this to
    # offer cached real values instead of the ``<name>`` placeholder.
    positional_completions: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for cmd, func in app._commands.items():
        providers = getattr(func, "__liquifai_completions__", {}) or {}
        if not providers:
            continue
        pos_list = list(getattr(func, "__liquifai_positionals__", []))
        positional_completions[cmd] = {
            pos: {
                k: v
                for k, v in _provider_spec(_path, cmd, pos, provider, pos_list).items()
                if k in ("key", "kind", "depends_on")
            }
            for pos, provider in providers.items()
        }
    return {
        "name": app.name,
        "commands": list(app._commands.keys()),
        "script_cmds": sorted(app._script_cmds),
        # All resolvable sub-app names (canonical + aliases) — kept for descent.
        # The path threaded into a sub-app uses its CANONICAL name (aliases map
        # back) so a sub-app and its alias share one value-cache key namespace.
        "sub_apps": {n: serialize_app(s, (*_path, app._sub_app_aliases.get(n, n))) for n, s in app._sub_apps.items()},
        # Alias names only; excluded from TAB suggestions (they still resolve via
        # ``sub_apps`` above) so completion shows the canonical name once, not the
        # abbreviation alongside it.
        "sub_app_aliases": sorted(app._sub_app_aliases.keys()),
        "signature_paths": command_paths,
        "signature_flags": signature_flags,
        # {cmd: [--flag, ...]} — every spelling of the command's bool-typed
        # flags; the engine consults this before treating the previous token
        # as a value slot.
        "signature_bool_flags": signature_bool_flags,
        # Ordered positional-argument names per command; used by complete_from_tree
        # to emit ``<name>`` placeholder hints before flags when the cursor is at
        # an unfilled positional slot.
        "positionals": positionals_map,
        # {cmd: {positional: value-cache-key}} — only positionals with a provider.
        "positional_completions": positional_completions,
    }


def _provider_arity(provider: Callable[..., Any]) -> int:
    """Number of REQUIRED positional params of a completion provider.

    ``0`` → static (zero-arg, globally cached); ``≥1`` → dependent (receives a dict
    of the earlier positionals). Best-effort: an un-introspectable callable counts
    as static. ``inspect`` is imported lazily so it never loads on the fast path.
    """
    import inspect

    try:
        params = inspect.signature(provider).parameters.values()
    except (TypeError, ValueError):
        return 0
    return sum(
        1
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty
    )


def _provider_spec(
    path: Tuple[str, ...],
    cmd: str,
    pos: str,
    provider: Callable[..., Any],
    pos_list: List[str],
) -> Dict[str, Any]:
    """The ONE derivation of a positional provider's spec.

    ``{"key", "provider", "kind", "depends_on", "prior_keys"}`` — ``kind`` by
    :func:`_provider_arity` (0-arg → ``static``, else ``dependent``),
    ``depends_on`` the prior positionals a dependent provider is keyed by,
    ``prior_keys`` their own value-cache keys (so refresh can read their static
    values). :func:`serialize_app` projects the JSON-safe subset into the tree;
    :func:`iter_completion_providers` yields it whole. Previously each computed
    this independently — a drift hazard between the tree and the refreshers.
    """
    key = cache.value_cache_key(path, cmd, pos)
    if _provider_arity(provider) == 0:
        return {"key": key, "provider": provider, "kind": "static", "depends_on": [], "prior_keys": {}}
    idx = pos_list.index(pos) if pos in pos_list else 0
    deps = pos_list[:idx]
    return {
        "key": key,
        "provider": provider,
        "kind": "dependent",
        "depends_on": deps,
        "prior_keys": {d: cache.value_cache_key(path, cmd, d) for d in deps},
    }


def iter_completion_providers(app: "LiquifyApp", _path: Tuple[str, ...] = ()) -> Iterator[Dict[str, Any]]:
    """Yield the :func:`_provider_spec` for every registered positional provider.

    Walks commands + CANONICAL sub-apps (aliases skipped → each provider once),
    computing the same specs :func:`serialize_app` bakes into the tree.
    """
    for cmd, func in app._commands.items():
        providers = getattr(func, "__liquifai_completions__", {}) or {}
        pos_list = list(getattr(func, "__liquifai_positionals__", []))
        for pos, provider in providers.items():
            yield _provider_spec(_path, cmd, pos, provider, pos_list)
    for name, sub in app._sub_apps.items():
        if name in app._sub_app_aliases:
            continue  # canonical only — aliases share the same keys
        yield from iter_completion_providers(sub, (*_path, name))


def _cross_product(prior: List[Tuple[str, List[str]]], cap: int) -> Iterator[Dict[str, str]]:
    """Yield up to ``cap`` input dicts — the cross-product of the prior positionals' values."""
    import itertools

    names = [d for d, _ in prior]
    value_lists = [vals for _, vals in prior]
    for i, combo in enumerate(itertools.product(*value_lists)):
        if i >= cap:
            return
        yield dict(zip(names, combo))


def refresh_value_caches(app: "LiquifyApp", max_combos: int = 200) -> Dict[str, int]:
    """Run every registered positional value-provider and cache its results.

    Runs IN the app process (providers may import the SDK / hit the network). Each
    provider is best-effort: a failure (offline, auth, exception) is skipped. Returns
    ``{cache_key: n_values}`` for the ones that succeeded.

    Static providers (zero-arg) are run first and cached globally. DEPENDENT providers
    are then enumerated against the cross-product of their prior positionals' static
    values — up to ``max_combos`` combinations (a ``<version>`` that depends on
    ``<name>`` is pre-cached for every cached name) — and cached per input combo.
    """
    specs = list(iter_completion_providers(app))
    written: Dict[str, int] = {}
    for s in specs:  # static first, so dependent enumeration can read their caches
        if s["kind"] != "static":
            continue
        try:
            values = [str(v) for v in (s["provider"]() or [])]
        except Exception:
            continue
        try:
            cache.write_value_cache(app.name, s["key"], values)
        except Exception:
            continue
        written[s["key"]] = len(values)
    for s in specs:  # dependent — cross-product of prior positionals' cached values
        if s["kind"] != "dependent":
            continue
        prior: List[Tuple[str, List[str]]] = []
        ok = True
        for dep in s["depends_on"]:
            vals = cache.read_value_cache(app.name, s["prior_keys"].get(dep, ""))
            if vals is None:
                ok = False
                break
            prior.append((dep, vals))
        if not ok or not prior:
            continue
        total = 0
        for inputs in _cross_product(prior, max_combos):
            try:
                values = [str(v) for v in (s["provider"](inputs) or [])]
            except Exception:
                continue
            try:
                cache.write_dependent_value_cache(app.name, s["key"], inputs, values)
            except Exception:
                continue
            total += len(values)
        written[s["key"]] = total
    return written


def refresh_one(app: "LiquifyApp", cache_key: str, inputs: Dict[str, str]) -> Optional[int]:
    """Refresh ONE positional's value cache — static (empty ``inputs``) or dependent.

    The targeted counterpart to :func:`refresh_value_caches`, run by the detached
    ``--refresh-completion-value`` helper the fast path spawns to self-heal a missing/stale
    cache. Finds the provider whose value-cache key is ``cache_key`` and whose kind matches
    ``inputs`` (empty → static, non-empty → dependent), runs it, and writes the cache. A
    dependent refresh also stamps ``changed_at`` when the values changed (drives the
    ``<…>-updated`` notice). Returns the number of values written, or None on no match /
    provider failure.
    """
    want_dependent = bool(inputs)
    for s in iter_completion_providers(app):
        if s["key"] != cache_key or (s["kind"] == "dependent") != want_dependent:
            continue
        try:
            values = [str(v) for v in ((s["provider"](inputs) if want_dependent else s["provider"]()) or [])]
        except Exception:
            return None
        try:
            if want_dependent:
                old = cache.read_dependent_value_cache(app.name, cache_key, inputs)
                # Stamp a change (drives the "<…>-updated" notice) on first population or
                # change; keep the prior stamp on an unchanged refresh so it ages out.
                changed_at = (
                    time.time()
                    if (old is None or old != values)
                    else cache._dependent_changed_at(app.name, cache_key, inputs)
                )
                cache.write_dependent_value_cache(app.name, cache_key, inputs, values, changed_at=changed_at)
            else:
                cache.write_value_cache(app.name, cache_key, values)
        except Exception:
            return None
        return len(values)
    return None


def has_stale_value_caches(app: "LiquifyApp", ttl: float) -> bool:
    """True if any STATIC positional cache is missing or older than ``ttl``.

    Dependent caches are refreshed alongside the static ones, so staleness is gauged
    on the static caches (a refresh redoes both).
    """
    for s in iter_completion_providers(app):
        if s["kind"] != "static":
            continue
        age = cache._value_cache_age(app.name, s["key"])
        if age is None or age > ttl:
            return True
    return False


def _flag_display_map(paths: Iterable[str]) -> Dict[str, str]:
    """Map each dotted override path to its shortest-unique ``--flag`` spelling.

    Reuses confluid's canonical :func:`confluid.shortest_unique_paths` — the
    SAME function ``--help`` / :func:`liquifai.report.show_configuration` uses —
    so completion and help agree: a leaf unique across the command's override
    keys shows as ``--<leaf>`` (``--class_name``), and only a shared leaf keeps
    enough of its prefix to disambiguate (``--a.lr`` vs ``--b.lr``).

    Confluid is imported lazily: this is called at cache-build time (confluid
    already loaded in the app process) and on the config-present completion path
    (which has already imported confluid to read the YAML), so the stdlib-only
    fast path never reaches it.
    """
    from confluid import shortest_unique_paths

    unique = sorted({p for p in paths if p})
    return {p: f"--{d}" for p, d in shortest_unique_paths(unique).items()}


def _collapse_to_flags(paths: Iterable[str]) -> List[str]:
    """Collapse dotted override paths to sorted, de-duplicated ``--flag`` form.

    Thin wrapper over :func:`_flag_display_map` (one collapse pass shared with
    the bool-flag spelling derivation in :func:`serialize_app`).
    """
    display = _flag_display_map(paths)
    out: List[str] = []
    seen: Set[str] = set()
    for full in sorted(display):
        flag = display[full]
        if flag not in seen:
            out.append(flag)
            seen.add(flag)
    return out


def _introspect_function_hierarchy(func: Any) -> Dict[str, Any]:
    """Return the ``{path: (type_str, default, doc)}`` hierarchy a command exposes.

    Delegates to confluid's :func:`confluid.get_hierarchy` — the SAME path
    enumerator ``--help`` / :func:`liquifai.report.show_configuration` use — so
    completion and help can never diverge. ``get_hierarchy`` walks the command
    function's signature params, recurses into each ``@configurable`` param, and
    records only LEAF scalars (never the configurable container itself):
    ``convert-ops-export(converter: TaidalOpsToHeliosConverter)`` yields
    ``{"converter.class_name": ..., "converter.dst": ..., ...}`` — no bare
    ``converter`` root. A plain ``@command`` like ``run list`` yields its bare
    params (``experiment``, ``status``, ...). It reads ``__init__``/signature
    parameters only (NOT ``dir(cls)``), so inherited framework-base attributes
    never pollute the output. The values keep the ``type_str`` so
    :func:`serialize_app` can flag ``bool`` params (store-true flags).

    Called only at cache-build time (:func:`serialize_app`), where confluid is
    already loaded — never on the stdlib-only fast path. Returns ``{}`` on any
    introspection failure so a broken annotation never breaks completion.
    """
    try:
        from confluid import get_hierarchy

        return dict(get_hierarchy(func))
    except Exception:
        return {}


def _introspect_function_keys(func: Any) -> List[str]:
    """Return the flat sorted list of LEAF override paths a command exposes.

    Kept as the historical key-only view over
    :func:`_introspect_function_hierarchy` (re-exported by
    ``liquifai.completion``).
    """
    return sorted(_introspect_function_hierarchy(func).keys())


def write_cache(app: "LiquifyApp") -> Path:
    """Write the static command tree for ``app`` to disk. Best-effort."""
    target = cache.cache_path(app.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CACHE_VERSION, "tree": serialize_app(app)}
    target.write_text(json.dumps(payload))
    return target


def read_cache(app_name: str) -> Optional[Dict[str, Any]]:
    """Read the static command tree. Returns None if missing or unreadable."""
    target = cache.cache_path(app_name)
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
