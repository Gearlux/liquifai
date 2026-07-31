"""On-disk caches for liquifai completion (command tree + positional values).

Owns the XDG cache locations, the per-positional value caches (STATIC —
one global list — and DEPENDENT — per-input-combination, hash-keyed), their
freshness/changed-at bookkeeping, the refresh-policy constants, and the
detached lazy self-heal spawner. The command-TREE cache read/write lives in
:mod:`liquifai.completion.tree` next to ``serialize_app`` (its format owner).

Pure-stdlib module (fast-path safe — see the completion mandate).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

#: Versioned envelope for the per-positional value caches (Q2). Separate from
#: CACHE_VERSION so a value-format change doesn't invalidate the command tree.
VALUE_CACHE_VERSION: int = 1

#: A dependent positional's per-input cache older than this (seconds) is refreshed
#: lazily on use (in the background) so new/changed values self-heal; a missing one
#: is always refreshed. :data:`LAZY_REFRESH_THROTTLE` bounds how often the same
#: input combo re-spawns a refresh so rapid TABbing can't fork a storm.
DEPENDENT_REFRESH_TTL: float = 300.0
LAZY_REFRESH_THROTTLE: float = 30.0

#: After a lazy self-heal actually CHANGES a dependent positional's values, TAB shows
#: a transient ``<<positional>-updated>`` hint alongside them for this many seconds, so
#: the user knows the background refresh added/changed values (and need not wonder why
#: the list grew). Change-only — an unchanged refresh shows nothing.
DEPENDENT_NOTICE_WINDOW: float = 60.0


def cache_dir() -> Path:
    """Per-XDG cache directory for liquifai completion data."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "liquifai"


def cache_path(app_name: str) -> Path:
    return cache_dir() / f"{app_name}.json"


# ---------------------------------------------------------------------------
# Per-positional value caches (Q2 — dynamic completion of a positional's value)
# ---------------------------------------------------------------------------
# A command may register a value PROVIDER for a positional via
# ``@command(..., completions={"name": provider})`` (provider is a
# ``Callable[[], List[str]]``). At refresh time the provider runs IN the app
# process (it may import the heavy SDK / hit the network) and its result is
# cached to ``<cache_dir>/<app>.values/<key>.json``. The stdlib-only fast path
# then reads that cache and offers the real values for the ``<positional>``
# slot — never running the provider itself, so TAB stays fast and offline-safe.


def values_cache_dir(app_name: str) -> Path:
    """Directory holding an app's per-positional value caches."""
    return cache_dir() / f"{app_name}.values"


def _sanitize_key(cache_key: str) -> str:
    """Make a value-cache key safe to use as a filename."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", cache_key)


def value_cache_key(path: Tuple[str, ...], cmd: str, positional: str) -> str:
    """Deterministic key for one (command-path, command, positional) value cache.

    The app name is NOT part of the key — value caches are already namespaced by
    :func:`values_cache_dir`. Sub-app aliases collapse to their canonical name
    (handled by callers) so ``dataset`` and its alias ``ds`` share one cache.
    """
    return "__".join((*path, cmd, positional))


def value_cache_path(app_name: str, cache_key: str) -> Path:
    return values_cache_dir(app_name) / f"{_sanitize_key(cache_key)}.json"


def write_value_cache(app_name: str, cache_key: str, values: List[str]) -> Path:
    """Write a positional's candidate values to its cache (timestamped). Best-effort."""
    target = value_cache_path(app_name, cache_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": VALUE_CACHE_VERSION, "ts": time.time(), "values": [str(v) for v in values]}
    target.write_text(json.dumps(payload))
    return target


def read_value_cache(app_name: str, cache_key: str) -> Optional[List[str]]:
    """Read a positional's cached values. None if missing/unreadable/stale-format."""
    target = value_cache_path(app_name, cache_key)
    if not target.exists():
        return None
    try:
        with target.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("version") != VALUE_CACHE_VERSION:
        return None
    vals = data.get("values")
    return [str(v) for v in vals] if isinstance(vals, list) else None


def _value_cache_age(app_name: str, cache_key: str) -> Optional[float]:
    """Seconds since a value cache was written, or None if missing/unreadable."""
    target = value_cache_path(app_name, cache_key)
    if not target.exists():
        return None
    try:
        with target.open() as f:
            ts = json.load(f).get("ts")
    except (OSError, ValueError):
        return None
    return (time.time() - ts) if isinstance(ts, (int, float)) else None


# --- Dependent positional caches (values keyed by the earlier positionals) ----
# A dependent positional's candidates depend on what was already typed (e.g.
# ``download <name> <version>`` — versions depend on the dataset name). At refresh
# time we enumerate the cross-product of the prior positionals' static values and
# cache the dependent provider's output per input combination under
# ``<app>.values/<key>/<inputs-hash>.json``; the fast path reads the cache for the
# exact typed inputs.


def _inputs_sig(inputs: Dict[str, str]) -> str:
    """Stable short hash of a dependent positional's input combination."""
    raw = json.dumps(inputs, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def dependent_value_cache_path(app_name: str, cache_key: str, inputs: Dict[str, str]) -> Path:
    return values_cache_dir(app_name) / _sanitize_key(cache_key) / f"{_inputs_sig(inputs)}.json"


def write_dependent_value_cache(
    app_name: str,
    cache_key: str,
    inputs: Dict[str, str],
    values: List[str],
    changed_at: Optional[float] = None,
) -> Path:
    """Cache a dependent positional's candidate values for one input combination.

    ``changed_at`` (set by :func:`refresh_one` only when the values actually
    changed) timestamps the change so TAB can briefly flag "<…>-updated" (see
    :data:`DEPENDENT_NOTICE_WINDOW`). The bulk refresh leaves it None — no notice.
    """
    target = dependent_value_cache_path(app_name, cache_key, inputs)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "version": VALUE_CACHE_VERSION,
        "ts": time.time(),
        "inputs": inputs,
        "values": [str(v) for v in values],
    }
    if changed_at is not None:
        payload["changed_at"] = changed_at
    target.write_text(json.dumps(payload))
    return target


def read_dependent_value_cache(app_name: str, cache_key: str, inputs: Dict[str, str]) -> Optional[List[str]]:
    """Read a dependent positional's cached values for ``inputs``. None if missing/stale-format."""
    target = dependent_value_cache_path(app_name, cache_key, inputs)
    if not target.exists():
        return None
    try:
        with target.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("version") != VALUE_CACHE_VERSION:
        return None
    vals = data.get("values")
    return [str(v) for v in vals] if isinstance(vals, list) else None


def _dependent_value_cache_age(app_name: str, cache_key: str, inputs: Dict[str, str]) -> Optional[float]:
    """Seconds since a dependent positional's per-input cache was written (None if missing)."""
    target = dependent_value_cache_path(app_name, cache_key, inputs)
    if not target.exists():
        return None
    try:
        with target.open() as f:
            ts = json.load(f).get("ts")
    except (OSError, ValueError):
        return None
    return (time.time() - ts) if isinstance(ts, (int, float)) else None


def _dependent_changed_at(app_name: str, cache_key: str, inputs: Dict[str, str]) -> Optional[float]:
    """The ``changed_at`` stamp of a dependent per-input cache (None if missing/unset)."""
    target = dependent_value_cache_path(app_name, cache_key, inputs)
    if not target.exists():
        return None
    try:
        with target.open() as f:
            ca = json.load(f).get("changed_at")
    except (OSError, ValueError):
        return None
    return ca if isinstance(ca, (int, float)) else None


def _dependent_changed_recently(app_name: str, cache_key: str, inputs: Dict[str, str], window: float) -> bool:
    """True if a dependent per-input cache's values changed within ``window`` seconds."""
    ca = _dependent_changed_at(app_name, cache_key, inputs)
    return ca is not None and (time.time() - ca) < window


def make_lazy_refresh_spawner(app_name: str) -> Callable[[str, Dict[str, str]], None]:
    """Return a callback that DETACHES a targeted refresh for one positional's cache.

    Passed to :func:`complete_from_tree` by the fast path so a positional with a
    missing/stale value cache self-heals for next time WITHOUT blocking TAB: it spawns
    ``<app> --refresh-completion-value '<json>'`` in a new session (output discarded),
    throttled per (key, inputs) by :data:`LAZY_REFRESH_THROTTLE`. Handles BOTH a STATIC
    positional (empty ``inputs`` → refreshes the whole name list) and a DEPENDENT one
    (``inputs`` = the earlier positionals → refreshes that combo). Opt out entirely with
    ``$LIQUIFAI_NO_LAZY_COMPLETE``. Best-effort — any error is swallowed (completion still
    returned whatever was cached / the placeholder).

    The throttle marker applies to EVERY caller, including a forced double-TAB refresh
    (:func:`liquifai.completion.wants_forced_refresh`): forcing bypasses the *age gate*
    in :func:`complete_from_tree` (so a fresh-but-wrong cache still refreshes) but NOT
    this throttle, so hammering TAB spawns the refresh at most once per throttle window
    — one refresh per completion session, not one per keystroke.
    """

    def spawn(cache_key: str, inputs: Dict[str, str]) -> None:
        if os.environ.get("LIQUIFAI_NO_LAZY_COMPLETE"):
            return
        base = (
            dependent_value_cache_path(app_name, cache_key, inputs) if inputs else value_cache_path(app_name, cache_key)
        )
        marker = base.with_suffix(".pending")
        try:
            if marker.exists() and (time.time() - marker.stat().st_mtime) < LAZY_REFRESH_THROTTLE:
                return  # a refresh for this combo was attempted very recently
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError:
            return
        payload = json.dumps({"key": cache_key, "inputs": inputs})
        try:
            subprocess.Popen(
                [app_name, "--refresh-completion-value", payload],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass

    return spawn
