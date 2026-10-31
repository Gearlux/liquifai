"""A config key's value on TAB — what the provider of the class parameter it feeds listed.

A package registers a provider for one parameter of one of its classes under the :data:`GROUP` entry-point group::

    [project.entry-points."liquifai.values"]
    "mypkg.sources.Catalog.name" = "mypkg.sources:catalog_names"

The name is the parameter's dotted path (``<module>.<Class>.<parameter>``, the class as a ``!class:`` tag spells
it), the value the provider: ``provider(settings: Mapping[str, str]) -> Iterable[str]``, where ``settings`` holds the
block's other parameters as text — the command line's, else the file's (the block's own, a ``!ref:`` target's, a
broadcast top-level key's), else the class's defaults as its source writes them.

With a config on the line, ``key=<TAB>``, ``--key <TAB>`` and ``--key=<TAB>`` look for the ``!class:`` blocks the
key feeds — by its name (broadcast), through ``!ref:<key>``, or by its dotted path — and offer what each block's
provider listed for its settings. TAB never runs a provider: it reads the per-settings cache (``cache``'s dependent
value caches, keyed by :data:`KEY_PREFIX` + the entry point's name) and, when that cache is missing or old, starts the
detached ``<app> --refresh-completion-value`` that runs it in the app's process (:func:`provide`). A key no provider
answers for gets ``None`` here and completes as before.

Why an entry point: TAB may not import the class (the fast path stays light), and an entry point is found by the
standard library alone. Rationale: ``docs/architecture.md`` §16.

Stdlib-only at import, like every ``liquifai.completion`` module: confluid is imported inside the functions.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from liquifai.grammar import looks_like_key

from . import cache

#: The entry-point group a package registers its value providers under.
GROUP = "liquifai.values"
#: The value-cache key of a provider: this prefix and its entry point's name.
KEY_PREFIX = "liquifai.values:"

#: The words a yes/no setting reads as yes, and as no (liquifai's switch rule).
_YES = frozenset({"true", "yes", "on", "1"})
_NO = frozenset({"false", "no", "off", "0"})

#: A ``!class:`` block found in the document: its dotted path (``""`` inside a list), the class it names, and the
#: settings it writes.
_Block = Tuple[str, str, Dict[str, Any]]


def value_candidates(
    app_name: str,
    config_path: Path,
    key: str,
    typed: Sequence[str],
    lazy_refresh: Optional[Callable[[str, Dict[str, str]], None]],
    force_refresh: bool,
) -> Optional[List[str]]:
    """The values TAB offers for the config key ``key``, or ``None`` when no provider answers for it.

    Args:
        app_name: The app whose value caches are read.
        config_path: The config on the line, already resolved.
        key: The key whose value is being typed (``name``, ``runnable.source.name``).
        typed: The words typed after the config, the one being completed left out.
        lazy_refresh: Called with a provider's cache key and settings when that cache is missing or old.
        force_refresh: Ask for the refresh even when the cache is fresh (a second TAB).
    """
    names = _provider_names()
    if not names:
        return None
    owners = {name.rpartition(".")[0] for name in names}
    try:
        document = _document(config_path)
    except Exception:  # a config that cannot be read completes nothing, never a failed TAB
        return None
    blocks = [block for block in _blocks(document, "") if _clean(block[1]) in owners]
    if not blocks:
        return None
    line = _typed_settings(typed)
    answered = False
    values: List[str] = []
    for path, target, written in blocks:
        params = _params(target)
        for param in _slots(key, path, written, params):
            name = f"{_clean(target)}.{param}"
            if name not in names:
                continue
            answered = True
            settings = _settings(path, written, params, param, document, line)
            cache_key = KEY_PREFIX + name
            listed = cache.read_dependent_value_cache(app_name, cache_key, settings) or []
            stale = cache.slot_is_stale(app_name, {"kind": "dependent", "key": cache_key}, settings)
            if lazy_refresh is not None and (force_refresh or stale):
                lazy_refresh(cache_key, settings)
            values.extend(v for v in listed if v not in values)
    return values if answered else None


def is_value_key(cache_key: str) -> bool:
    """Whether ``cache_key`` names a provider of this module (what ``--refresh-completion-value`` refreshes)."""
    return cache_key.startswith(KEY_PREFIX)


def provide(cache_key: str, settings: Dict[str, str]) -> Optional[List[str]]:
    """Run the provider ``cache_key`` names with ``settings`` — the refresh's side, in the app's process.

    ``None`` when no installed package registers it or the provider fails (a server down): nothing is cached then.
    """
    found = [ep for ep in entry_points(group=GROUP) if ep.name == cache_key[len(KEY_PREFIX) :]]
    if not found:
        return None
    try:
        return [str(v) for v in (_load(found[0])(dict(settings)) or [])]
    except Exception:  # a provider may query a server: a failure caches nothing, the next TAB asks again
        return None


def _load(entry: EntryPoint) -> Callable[[Dict[str, str]], Any]:
    provider: Callable[[Dict[str, str]], Any] = entry.load()
    return provider


def _provider_names() -> Set[str]:
    """The parameters some installed package registers a provider for."""
    return {ep.name for ep in entry_points(group=GROUP)}


def _document(config_path: Path) -> Any:
    """The config's document, as the help reads it (scopes not applied)."""
    import confluid

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        raw = confluid.load(config_path, until="raw")
        return confluid.load(raw, until="document")


def _clean(target: str) -> str:
    """A ``!class:`` spelling's dotted path (``pkg.mod.Builder()`` → ``pkg.mod.Builder``)."""
    return target.split("@", 1)[0].split("(", 1)[0].strip()


def _reference_target(value: Any) -> Optional[str]:
    """The key a ``!ref:`` names, or None when ``value`` is no reference."""
    target = getattr(value, "target", None)
    return target if type(value).__name__ == "Reference" and isinstance(target, str) else None


def _is_reference(value: Any) -> bool:
    return _reference_target(value) is not None


def _blocks(obj: Any, path: str, depth: int = 0) -> Iterator[_Block]:
    """Every ``!class:`` block under ``obj``; a block inside a list has no dotted path (``""``)."""
    if depth > 12:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and not k.startswith("_"):
                yield from _blocks(v, f"{path}.{k}" if path else k, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _blocks(item, "", depth + 1)
    else:
        kwargs = getattr(obj, "kwargs", None)
        target = getattr(obj, "target", None)
        if isinstance(kwargs, dict) and target is not None and not _is_reference(obj):
            spelled = target if isinstance(target, str) else f"{target.__module__}.{target.__qualname__}"
            yield path, spelled, kwargs
            for k, v in kwargs.items():
                if isinstance(k, str):
                    yield from _blocks(v, f"{path}.{k}" if path else "", depth + 1)


def _params(target: str) -> Dict[str, Tuple[Optional[str], bool]]:
    """``{parameter: (its default as text, whether it is a switch)}`` of the class, read from its source."""
    import confluid

    try:
        return {p.name: (p.default, p.switch) for p in confluid.source_params(_clean(target))}
    except Exception:  # an unreadable source: no parameters, never a failed TAB
        return {}


def _slots(key: str, path: str, written: Dict[str, Any], params: Dict[str, Tuple[Optional[str], bool]]) -> List[str]:
    """The parameters of a block the key feeds: by its dotted path, through ``!ref:<key>``, or by its name."""
    if "." in key:
        head, _, leaf = key.rpartition(".")
        return [leaf] if path and head == path and leaf in params else []
    slots = [p for p, v in written.items() if _reference_target(v) == key]
    if key in params and key not in slots:  # a bare key is broadcast: it reaches the block even where it writes it
        slots.append(key)
    return slots


def _settings(
    path: str,
    written: Dict[str, Any],
    params: Dict[str, Tuple[Optional[str], bool]],
    slot: str,
    document: Any,
    line: Dict[str, str],
) -> Dict[str, str]:
    """The block's other parameters as text: the line's, else the file's, else the class's defaults."""
    top = document if isinstance(document, dict) else {}
    settings: Dict[str, str] = {}
    for param, (default, switch) in params.items():
        if param == slot:
            continue
        value: Optional[str] = None
        given = written.get(param)
        feeder = _reference_target(given) or param
        for typed_key in ((f"{path}.{param}",) if path else ()) + (feeder,):
            if typed_key in line:
                value = line[typed_key]
                break
        if value is None and param in written and not _is_reference(given):
            value = _text(given)
        if value is None and feeder in top:
            value = _text(top[feeder])
        if value is None:
            value = default
        if value is None:
            continue
        settings[param] = _yes_no(value) if switch else value
    return settings


def _text(value: Any) -> Optional[str]:
    """A scalar setting as text (``True`` → ``true``); ``None`` for anything else."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _yes_no(value: str) -> str:
    """A switch's value as ``true`` / ``false``; anything else as written."""
    word = value.strip().lower()
    return "true" if word in _YES else "false" if word in _NO else value


def _typed_settings(typed: Sequence[str]) -> Dict[str, str]:
    """``{key: value}`` of the overrides typed — ``key=value``, ``--key=value``, ``--key value`` — the last one wins.

    A ``--key`` with no value after it (the next word a flag or another key, or the end) is a switch typed alone:
    ``true``. ``--key+`` / ``--key-`` are ``true`` / ``false``. The words after ``--`` are literal.
    """
    found: Dict[str, str] = {}
    words = list(typed)
    index = 0
    while index < len(words):
        word = words[index]
        index += 1
        if word == "--":
            break
        if word.startswith("--"):
            body = word[2:]
            if "=" in body:
                head, _, value = body.partition("=")
                found[option_key(head)] = value
            elif body.endswith(("+", "-")):
                found[option_key(body[:-1])] = "true" if body.endswith("+") else "false"
            elif index < len(words) and not _is_option(words[index]):
                found[option_key(body)] = words[index]
                index += 1
            else:
                found[option_key(body)] = "true"
        elif "=" in word and looks_like_key(word.partition("=")[0]):
            head, _, value = word.partition("=")
            found[option_key(head)] = value
    return found


def _is_option(word: str) -> bool:
    """Whether ``word`` is an option of its own rather than a value: a flag, or ``key=value``."""
    return word.startswith("-") or ("=" in word and looks_like_key(word.partition("=")[0]))


def option_key(head: str) -> str:
    """An option's key as the parser reads it: ``-`` → ``_`` in each segment."""
    return ".".join(part.replace("-", "_") for part in head.split("."))
