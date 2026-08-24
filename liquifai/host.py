"""Host facts every run carries: the ``os`` / ``device`` scopes and the ``platform`` namespace.

A config almost always has one or two lines that depend on the machine it runs on —
a loader knob that misbehaves on one OS, a device string, a path segment. Liquifai
detects those facts once per run and offers them TWICE, because a config needs them
in two different positions:

* as a **scope activation** (``os=darwin``, ``device=mps``), so a document can carry
  ``!scope:os=darwin`` blocks that override whole keys;
* as a **document key** (``platform: {os: darwin, device: mps}``), so a value can be
  read inline with ordinary interpolation — ``logdir: /runs/${platform.os}``.

The namespace is what makes the second one possible. Confluid dispatches a
``${...}`` placeholder on the NAME SHAPE (``confluid/resolver.py:161``): a dotted
name reads the config tree, a bare one reads ``os.getenv``. So ``${platform.os}``
resolves against the injected key while a bare ``${os}`` would look for an
environment variable — and liquifai deliberately sets none.

Two rules keep this from surprising anyone:

* **The document always wins.** The namespace is merged UNDER the document, key by
  key, so ``platform: {device: gpu}`` re-spells one fact for a framework that
  needs another word and keeps ``platform.os``.
* **The activation is advisory.** Confluid rejects an undeclared value for a
  dimension a document declares (``No scope block matches device='cpu'``), which is
  the typo guard a typed ``--scope`` must keep. A DETECTED value is not a typo, so
  :func:`auto_scopes` drops one the document cannot use — a linux-only document
  runs on a Mac instead of failing. Known limit: a dimension carrying BOTH a
  positive block and a ``!notscope:`` block is filtered on its positive values
  alone, so the auto value is dropped there and the ``!notscope:`` block stays
  active. Confluid's public discovery surface reports the positive values only
  (``discover_dimension_values``); the alternative was a second walker here, and
  two walkers over the same node kinds is the drift that confluid's own scope
  module warns about.
"""

import importlib.util
import platform
import sys
from typing import Any, Dict, Iterable, List, Literal, Mapping, Tuple

import confluid
from loggair import get_logger

logger = get_logger("liquifai.host")

#: The document key liquifai injects. What a config author writes is
#: ``${platform.os}`` — hence "platform" here even though the module is about the host.
NAMESPACE = "platform"

#: The facts, in the order they are detected. Each is a scope dimension name AND a key
#: inside :data:`NAMESPACE`, so ``!scope:device=mps`` and ``${platform.device}`` name
#: the same thing — one word, two positions.
DIMENSIONS: Tuple[str, ...] = ("os", "device")

#: What :func:`detect_device` may answer. Closed on purpose: liquifai decides these three
#: words, so a form-spec / widget can enumerate them. (``detect_os`` has no Literal twin —
#: ``platform.system()`` is open-ended and answers ``freebsd`` / ``aix`` on machines that
#: run neither of the three common values.)
Device = Literal["cuda", "mps", "cpu"]


def detect_os() -> str:
    """The operating system as ``platform.system()`` spells it, lowered.

    ``darwin`` / ``linux`` / ``windows`` on the three common platforms — Python's own
    word, not a friendlier translation, so ``!scope:os=darwin`` matches what every
    other tool on the machine reports.
    """
    return platform.system().lower()


def detect_device() -> Device:
    """The compute device torch would pick: ``cuda`` > ``mps`` > ``cpu``.

    Torch is the authority when it is installed and the answer is ``cpu`` when it is
    not — liquifai never depends on it. The check is explicit (``find_spec``) rather
    than a caught ``ImportError``, and it runs at most once per invocation: a
    dimension the caller forced with ``--scope device=…`` is never detected at all
    (:func:`host_facts`), so a run that has already made the choice does not pay
    torch's import.
    """
    if "torch" not in sys.modules and importlib.util.find_spec("torch") is None:
        return "cpu"
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def forced_values(scopes: Iterable[str]) -> Dict[str, str]:
    """The ``{dimension: value}`` an activation list already fixes for our dimensions.

    Reads ``--scope os=linux`` and any ``--KEY VAL`` a document's own dimension
    declaration promoted. Last entry wins per dimension, mirroring confluid's
    activation map (``confluid.scopes.normalize_active``).
    """
    forced: Dict[str, str] = {}
    for entry in scopes:
        key, sep, value = entry.partition("=")
        if sep and key.strip() in DIMENSIONS:
            forced[key.strip()] = value.strip()
    return forced


def host_facts(forced: Mapping[str, str]) -> Dict[str, str]:
    """The effective ``{dimension: value}`` map for this run — forced values win over detection."""
    detectors = {"os": detect_os, "device": detect_device}
    return {dim: forced[dim] if dim in forced else detectors[dim]() for dim in DIMENSIONS}


def auto_scopes(facts: Mapping[str, str], raw_config: Any) -> List[str]:
    """The activations to prepend for ``facts`` — dropping any the document cannot use.

    Prepended (never appended) so a value the user typed later in the list wins, and
    filtered against the RAW document's declared values so a detected fact never turns
    a document that simply does not mention this machine into a ``ScopeError``. A value
    the USER typed is not filtered here and keeps that error.
    """
    declared = confluid.discover_dimension_values(raw_config) if raw_config is not None else {}
    scopes: List[str] = []
    for dim, value in facts.items():
        values = declared.get(dim)
        if values and value not in values:
            logger.debug(
                f"Host fact {dim}={value} is not among the values this document declares "
                f"({', '.join(sorted(values))}) — not activating it."
            )
            continue
        scopes.append(f"{dim}={value}")
    return scopes


def inject_facts(data: Any, facts: Mapping[str, str]) -> Any:
    """Return ``data`` with ``platform: {…}`` merged UNDER it, so the document wins per key.

    Runs on the RAW document, before confluid's interpolation pass, which is what lets
    ``${platform.os}`` resolve. A document that already spells the namespace something
    other than a mapping keeps its own value untouched — liquifai never overwrites a key
    an author wrote — and says so, because every ``${platform.…}`` in that file then
    reads the author's value rather than the detected one.
    """
    if not isinstance(data, dict):
        return data
    existing = data.get(NAMESPACE)
    if existing is not None and not isinstance(existing, dict):
        logger.warning(
            f"Config key `{NAMESPACE}:` is a {type(existing).__name__}, not a mapping — leaving it as "
            f"written. The host facts ({', '.join(f'{k}={v}' for k, v in facts.items())}) are NOT "
            f"available as ${{{NAMESPACE}.<name>}} in this document."
        )
        return data
    merged = dict(data)
    merged[NAMESPACE] = {**facts, **(existing or {})}
    return merged
