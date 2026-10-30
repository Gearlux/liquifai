"""Global-flag parsing — phase 3 of the bootstrap lifecycle.

Two passes over the tokens the router did not consume, both driven by
declarations rather than hand-written flag lists:

* :func:`parse_globals` extracts the bootstrap-relevant global flags
  (``--config``, ``--scope``, ``--debug``, the log-level knobs) straight from
  :data:`liquifai.grammar.GLOBAL_FLAG_SPECS`, so the parser can never drift
  from what ``--help`` renders or what completion offers.
* :func:`bind_dimension_flags` promotes ``--KEY VAL`` into an active scope when
  ``KEY`` is a scope dimension declared by the loaded YAML. It runs *second*
  because it needs the config the first pass located — the one genuine ordering
  constraint between them.

Both consume and return :class:`~liquifai.walk.Token`\\ s and skip literals
(anything after ``--``), so a protected value can never be mistaken for a flag
and still reaches the phases downstream.

This module imports confluid (``discover_dimensions``) and is therefore NOT
fast-path safe — unlike :mod:`liquifai.grammar`, which owns the stdlib-only
vocabulary these functions read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import confluid

from liquifai.exceptions import MissingOptionValueError
from liquifai.grammar import GLOBAL_FLAG_SPECS
from liquifai.walk import Token

#: Dests whose value is a filesystem path (wrapped in ``Path`` on the way out).
_PATH_DESTS = frozenset({"config_path", "log_dir"})

#: The dests phase 3 actually consumes. Help/completion flags are handled by
#: their own short-circuits in ``run()`` and must fall through to ``remaining``.
_BOOTSTRAP_DESTS = frozenset(
    {"config_path", "scopes", "log_level", "console_level", "file_level", "log_dir", "save_config"}
)


@dataclass
class GlobalFlags:
    """What phase 3 extracted from the command line.

    A named record rather than the historical 5-tuple: every field is read at
    a different point in :meth:`liquifai.core.LiquifyApp._prepare`, and a
    positional unpack there was a standing invitation to swap two of them.
    """

    #: ``--config PATH``, unresolved (the caller runs it through confluid's tiers).
    config_path: Path | None = None
    #: Scope activations from ``--scope`` (comma-separated values already split).
    scopes: List[str] = field(default_factory=list)
    #: ``--debug`` / ``-d``.
    debug: bool = False
    #: Log knobs, keyed by their spec ``dest`` — passed straight to LiquifyContext.
    log_overrides: Dict[str, Any] = field(default_factory=dict)
    #: ``--dump-config``: write the resolved configuration and run nothing.
    dump_config: bool = False
    #: ``--dump-config=PATH``: the file it goes to; ``None`` prints it.
    dump_config_path: Path | None = None
    #: ``--save-config PATH``: write the resolved configuration there, then run the command.
    save_config: Path | None = None
    #: Tokens phase 3 did not consume, literals included, in order.
    remaining: List[Token] = field(default_factory=list)


def parse_globals(tokens: Sequence[Token]) -> GlobalFlags:
    """Extract the bootstrap global flags declared in :mod:`liquifai.grammar`.

    Table-driven off :data:`~liquifai.grammar.GLOBAL_FLAG_SPECS` — the same
    declaration ``--help`` and completion render from — so the three surfaces
    can never drift. Only the bootstrap-relevant dests are consumed; everything
    else (including every literal) lands in :attr:`GlobalFlags.remaining`.

    Example::

        flags = parse_globals(tokenize(["--config", "a.yaml", "-d", "--lr", "1"]))
        flags.config_path            # Path("a.yaml")
        flags.debug                  # True
        [t.text for t in flags.remaining]   # ["--lr", "1"]
    """
    out = GlobalFlags()

    value_specs = {
        flag: spec
        for spec in GLOBAL_FLAG_SPECS
        if spec.takes_value and spec.dest in _BOOTSTRAP_DESTS
        for flag in spec.flags
    }
    debug_flags = {flag for spec in GLOBAL_FLAG_SPECS if spec.dest == "debug" for flag in spec.flags}
    dump_flags = {flag for spec in GLOBAL_FLAG_SPECS if spec.dest == "dump_config" for flag in spec.flags}
    save_flags = {flag for spec in GLOBAL_FLAG_SPECS if spec.dest == "save_config" for flag in spec.flags}

    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.literal:  # protected by `--` — never an option
            out.remaining.append(tok)
            i += 1
            continue

        flag, equals, path = tok.text.partition("=")
        if flag in dump_flags:
            if equals and not path:
                raise MissingOptionValueError(
                    f"{flag}= names no file — write `{flag}=PATH` to write it there, or `{flag}` alone to print it."
                )
            out.dump_config = True
            out.dump_config_path = Path(path) if path else None
            i += 1
            continue
        if tok.text in save_flags and (i + 1 == n or tokens[i + 1].literal or tokens[i + 1].is_flag_like()):
            # Read as an override, a bare `--save-config` would become the config key `save_config: true` and the
            # command would run with nothing saved.
            raise MissingOptionValueError(f"{tok.text} needs a file: `{tok.text} PATH`.")

        spec = value_specs.get(tok.text)
        if spec is not None and i + 1 < n and not tokens[i + 1].literal:
            value = tokens[i + 1].text
            if spec.dest == "save_config":
                out.save_config = Path(value)
            elif spec.dest == "config_path":
                out.config_path = Path(value)
            elif spec.dest == "scopes":
                out.scopes.extend(value.split(","))
            elif spec.dest in _PATH_DESTS:
                out.log_overrides[spec.dest] = Path(value)
            else:
                out.log_overrides[spec.dest] = value
            i += 2
        elif tok.text in debug_flags:
            out.debug = True
            i += 1
        else:
            out.remaining.append(tok)
            i += 1

    return out


def bind_dimension_flags(
    scopes: List[str],
    raw_config: Any,
    tokens: Sequence[Token],
) -> Tuple[List[str], List[Token]]:
    """Promote ``--KEY VAL`` / ``--KEY=VAL`` into ``scopes`` for declared dimensions.

    The raw YAML is walked once by :func:`confluid.discover_dimensions` to learn
    which keys appear in a ``!scope:KEY=VAL`` / ``!scope:KEY(VAL)`` block. Those
    keys bind to implicit CLI flags, so a user may write ``--task classification``
    as well as ``--scope task=classification``. Non-dimension flags pass through
    untouched and continue down the ordinary CLI-override path.

    Returns ``(scopes, remaining_tokens)``; ``scopes`` is extended in place-ish
    (a new list is not made) so the caller's earlier ``--scope`` values are kept.
    """
    dimensions = confluid.discover_dimensions(raw_config)
    if not dimensions:
        return scopes, list(tokens)

    remaining: List[Token] = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.literal and tok.text.startswith("--"):
            if "=" in tok.text:  # ``--KEY=VAL``
                key, value = tok.text[2:].split("=", 1)
                if key in dimensions:
                    scopes.append(f"{key}={value}")
                    i += 1
                    continue
            else:  # ``--KEY VAL`` — requires a non-flag, non-literal follower
                key = tok.text[2:]
                if key in dimensions and i + 1 < n and not tokens[i + 1].is_flag_like() and not tokens[i + 1].literal:
                    scopes.append(f"{key}={tokens[i + 1].text}")
                    i += 2
                    continue
        remaining.append(tok)
        i += 1
    return scopes, remaining
