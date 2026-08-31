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

import inspect
import os
from typing import AbstractSet, Any, Dict, List, Optional, Set, Tuple

from confluid import accepts_any_key, accepts_broadcast, accepts_key, deep_merge, expand_dotted_keys, parse_value
from confluid.fluid import Fluid
from confluid.registry import resolve_class
from confluid.report import ConfigurationReport
from loggair import get_logger

from liquifai.grammar import looks_like_arg, looks_like_key

logger = get_logger(__name__)


def _normalize_key(key: str) -> str:
    """``--custom-node`` addresses the parameter ``custom_node``.

    Every CLI spells a multi-word option with hyphens; Python spells the parameter it binds to with
    underscores. Normalising is safe rather than a guess: a hyphen CANNOT appear in a Python
    identifier, so a hyphenated override key could not have been addressing a parameter under any
    spelling — there is nothing for this to shadow. Dotted keys normalise per segment, so
    ``--my-opt.learning-rate`` reaches ``my_opt.learning_rate``.

    Callers MUST read the polarity suffix (``--key-`` / ``--key+``) before calling this: the
    trailing hyphen that means "false" is a value, not a word separator.
    """
    return key.replace("-", "_")


def str_param_names(func: Any) -> Set[str]:
    """Parameter names ``func`` annotates as ``str`` (or ``Optional[str]``).

    Their CLI values bypass :func:`confluid.parse_value`: the command has already said
    it wants text, so YAML typing can only corrupt it — folding a multi-line value onto
    one line, reading ``#…`` as a comment, ``3:30`` as sexagesimal, ``012`` as octal,
    ``yes`` as True. Anything NOT annotated ``str`` still coerces, which is what makes
    ``--limit 5`` an int and ``+trainer.lr=0.01`` a float.

    Best-effort: an unintrospectable callable yields an empty set, so the caller simply
    keeps the historical behaviour.
    """
    if func is None:
        return set()
    try:
        params = inspect.signature(func).parameters
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return set()
    names: Set[str] = set()
    for name, param in params.items():
        annotation = param.annotation
        if annotation is str or annotation == "str":
            names.add(name)
            continue
        # ``Optional[str]`` / ``Union[str, None]`` — a declared-text parameter either way.
        args = getattr(annotation, "__args__", ())
        if args and all(a is str or a is type(None) for a in args) and str in args:
            names.add(name)
    return names


def parse_override_args(
    args: List[str], verbatim_keys: AbstractSet[str] = frozenset()
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Tokenize ``args`` into an ``(overrides, deletions, dropped)`` triple.

    Supported forms (order-independent; longest match wins per token):

    * ``--key value``           — legacy space-separated form (still primary).

    A key may be spelled with hyphens OR underscores — ``--custom-node`` and
    ``--custom_node`` are the same key (:func:`_normalize_key`). VALUES are never
    touched, so a URL or a negative number keeps its hyphens.
    * ``--key=value``           — equals form.
    * ``key=value``             — bare equals form, no ``--`` prefix.
    * ``--key+`` / ``--key-``   — polarity (True / False).
    * ``--key``                 — implicit ``True`` flag.
    * ``+key=value`` / ``+--key=value`` — add a new key (today merged with
      same semantics as a normal override; future: fail if key exists).
    * ``~key`` / ``~--key``     — delete the dotted key from the config.

    ``verbatim_keys`` names the parameters the active command declares as ``str``
    (see :func:`str_param_names`). A value landing on one of those BARE keys is taken
    exactly as typed instead of being coerced through ``confluid.parse_value`` — YAML
    typing is right for an untyped config override and wrong for declared text. A DOTTED
    key is never verbatim: it addresses a nested config object, not the signature.

    Any token that doesn't match a recognised form is collected into
    ``dropped`` (it is NOT applied). Callers surface these — a typo'd
    override that silently vanishes can cost an entire training run, so
    :meth:`liquifai.core.LiquifyApp._apply_overrides` logs one warning per
    dropped token.
    """
    overrides: Dict[str, Any] = {}

    def _coerce(key: str, raw: str) -> Any:
        """Verbatim for a declared-``str`` bare key; YAML-typed otherwise."""
        if "." not in key and key in verbatim_keys:
            return raw
        return parse_value(raw)

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
                deletions.append(_normalize_key(key))
            i += 1
            continue

        if arg.startswith("+"):
            body = arg[1:]
            if body.startswith("--"):
                body = body[2:]
            if "=" in body:
                k, v = body.split("=", 1)
                if k:
                    overrides[_normalize_key(k)] = _coerce(_normalize_key(k), v)
            elif body and i + 1 < len(args) and not looks_like_arg(args[i + 1]):
                overrides[_normalize_key(body)] = _coerce(_normalize_key(body), args[i + 1])
                i += 1
            elif body:
                overrides[_normalize_key(body)] = True
            i += 1
            continue

        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                if k:
                    overrides[_normalize_key(k)] = _coerce(_normalize_key(k), v)
                i += 1
                continue
            # Polarity is read FIRST: the trailing ``-`` means False, it is not a word separator.
            if key.endswith("+"):
                overrides[_normalize_key(key[:-1])] = True
                i += 1
                continue
            if key.endswith("-"):
                overrides[_normalize_key(key[:-1])] = False
                i += 1
                continue
            if i + 1 < len(args) and not looks_like_arg(args[i + 1]):
                overrides[_normalize_key(key)] = _coerce(_normalize_key(key), args[i + 1])
                i += 2
                continue
            overrides[_normalize_key(key)] = True
            i += 1
            continue

        # Bare ``key=value`` (no ``--``). Lets users drop the dashes when
        # they want — common ergonomics ask from the user. A token whose head
        # isn't shaped like a config key (JSON-ish blobs, URLs, file paths)
        # falls through to the dropped list below.
        if "=" in arg and not arg.startswith("="):
            k, v = arg.split("=", 1)
            if k and looks_like_key(k):
                overrides[_normalize_key(k)] = _coerce(_normalize_key(k), v)
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
       then re-seat every override key's top-level head at the END of the
       document in typed order, so confluid's document-order precedence sees
       the CLI keys as appended lines, in the order the user typed them
       (:func:`_move_cli_keys_last`),
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
        _move_cli_keys_last(data, parsed)

    for path in deletions:
        delete_dotted_key(data, path)

    merge_overrides_into_fluids(data, parsed)
    # "Did this override reach anything?" is deliberately NOT judged here — this
    # runs BEFORE materialization, where the question can only be guessed at (the
    # deleted local heuristic guessed wrong twice: glob heads and multi-hop paths
    # were reported ignored while the value landed). Confluid answers it
    # authoritatively after DI materializes: see :func:`warn_unused_overrides`,
    # called from ``core.run_command`` with the pass's ``ConfigurationReport``.
    return data


def _move_cli_keys_last(data: Dict[str, Any], overrides: Dict[str, Any]) -> None:
    """Re-seat every override key's top-level HEAD at the END, in typed order.

    Confluid has ONE precedence rule — document order, last spec wins, with no
    specificity tiers — so a key's POSITION decides whether it beats a value
    addressed at a node, and the CLI's only lever is where its keys sit. The
    honest encoding of "the user typed these after the whole file" is: CLI
    flags behave exactly as if their keys were APPENDED to the end of the
    document, in the order typed. ``deep_merge`` alone does not produce that —
    it replaces a key the document ALREADY declares *in place* (a top-level
    ``run_name:`` written on line 1 keeps line 1's precedence), and
    ``expand_dotted_keys`` folds a dotted override into a pre-existing block at
    the BLOCK's position (an early ``Trainer:`` block plus a late bare ``lr:``
    silently beat a typed ``--Trainer.lr``, measured 2026-08-13).

    So every override key's head — bare keys and dotted-expanded heads alike —
    is re-seated at the end, iterating in typed CLI order. This function may
    NEVER reorder flags relative to each other (the previous version moved
    only BARE keys, forcing every bare flag after every dotted flag: a
    ``--lr 0.2 --Trainer.lr 0.1`` pair produced 0.2 on Trainer in BOTH flag
    orders). A head mentioned twice seats at its LAST mention — the user's
    final word about that block is where it sits.

    Accepted consequence: re-seating a PRE-EXISTING block moves its unrelated
    keys' precedence with it (``--Trainer.lr`` re-seats the whole ``Trainer:``
    block, so its ``layers: 8`` now beats a later bare ``layers: 4`` the
    document used to win with). The alternative — leaving pre-existing blocks
    in place — keeps the silent-loss case above, which is worse. Both sides
    are pinned in ``tests/test_override_broadcast.py`` (the seating group).
    """
    for key in overrides:
        head = key.split(".", 1)[0]
        if head in data:
            data[head] = data.pop(head)


def warn_unused_overrides(overrides: Dict[str, Any], report: ConfigurationReport) -> None:
    """Warn for each CLI override the materialization REPORT says matched nothing.

    This asks confluid instead of guessing: ``report.unused`` is authoritative
    across every delivery mechanism (bare cascade, addressed blocks, glob
    riders, the nested-marker cascade into deferred slots), so the verdicts
    the deleted pre-materialization heuristic got wrong — a glob head, a
    multi-hop path, each reported "ignored" while the value landed — are right
    by construction. It also covers what the heuristic could not see at all: a
    BARE override no object accepts.

    The report's candidate spelling for an override key: a glob-headed
    override keeps its full key (a ``'**'`` block registers per leaf as
    ``**.lr``), a named dotted override is its HEAD block (``--opt.lr``
    expands to a top-level ``opt:`` mapping), a bare override is the key
    itself. A command that materializes nothing registers no candidates, so
    the check degrades to silence rather than to guessing.

    Called from ``core.run_command`` after DI materializes and BEFORE the
    command body runs — a doomed multi-hour run gets its warning up front.
    Do NOT downgrade to debug/trace: an override the operator typed and did
    not get is exactly the actionable condition ``warning`` is reserved for.
    """
    unused = set(report.unused)
    for key in overrides:
        head, dot, tail = key.partition(".")
        dotted_at_name = bool(dot) and head not in ("*", "**")
        candidate = head if dotted_at_name else key
        if candidate not in unused:
            continue
        if dotted_at_name:
            logger.warning(
                f"Override {key!r} matched nothing and was ignored: no configured object is named "
                f"{head!r}, and {head!r} is not a key in the configuration. The dotted form addresses "
                f"an instance by its YAML 'name:'; for a slot declared in code, use the bare form "
                f"('--{tail} <value>') or set it inside that object's config block."
            )
        else:
            logger.warning(
                f"Override {key!r} matched nothing and was ignored: no configured object accepts "
                f"{(tail or key)!r}. Check the spelling against the declared parameters "
                f"(`--help` lists them)."
            )


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

    Whether an override REACHED anything is deliberately not judged here (nor
    returned): confluid's report answers that after materialization — see
    :func:`warn_unused_overrides`.
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
                if head == str(fluid_name):
                    # The head names THIS instance. A multi-hop tail (``opt.lr``)
                    # is confluid's to route (its first segment floats, later
                    # ones are strict hops); only a single-segment tail this
                    # target takes is written here.
                    if tail in data.kwargs or accepts_key(cls, tail):
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

    ``target`` may be a class, a plain callable (a registered builder
    FUNCTION), an instance, or the dotted string Confluid uses for deferred
    resolution (``!class:module.Cls``). ``None`` means "not introspectable" —
    the caller falls back to the already-in-YAML rule. A routine is returned
    AS-IS: taking ``type(fn)`` (= ``function``, whose ``__init__`` takes
    ``**kwargs``) would hand the predicates a target that accepts everything —
    the exact degradation confluid's own target normalizer exists to prevent.
    """
    cls: Any = resolve_class(target) if isinstance(target, str) else target
    if cls is None:
        return None
    if isinstance(cls, type) or inspect.isroutine(cls):
        return cls
    return type(cls)


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
