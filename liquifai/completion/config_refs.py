"""The configs a block of a config starts — the runs of a workflow — found without importing a class.

A class whose parameter names a config another run reads (a workflow step that runs ``other.yaml``) is registered by
its package under the :data:`GROUP` entry-point group::

    [project.entry-points."liquifai.config_refs"]
    "mypkg.workflow.RunConfig.config" = "mypkg.workflow:RunConfig"

The name is the parameter's dotted path (``<module>.<Class>.<parameter>``, the class as a ``!class:`` tag spells it),
the value the class. :func:`referenced_configs` finds, in a config's document, every block of such a class and the
files it names (``pkg://<distribution>/<path>`` as :func:`confluid.resolve_config_path` resolves it, else a path of
the run folder), so a command's ``--help`` and TAB can list the options of the runs it starts: what ``--show_progress``
reaches there is the run's, not the workflow's.

Why an entry point: TAB may not import the class (the fast path stays light), and an entry point is found by the
standard library alone — the mechanism of ``liquifai.values`` (:mod:`liquifai.completion.config_values`).

Stdlib-only at import, like every ``liquifai.completion`` module: confluid is imported inside the functions.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config_values import _blocks, _clean

#: The entry-point group a package registers the classes whose parameter names a config under.
GROUP = "liquifai.config_refs"


def reference_names() -> Dict[str, Set[str]]:
    """``{class: {parameter, …}}`` of every parameter some installed package registers as naming a config."""
    found: Dict[str, Set[str]] = {}
    for entry in entry_points(group=GROUP):
        owner, _, parameter = entry.name.rpartition(".")
        if owner and parameter:
            found.setdefault(owner, set()).add(parameter)
    return found


def referenced_configs(document: Any) -> List[Path]:
    """The config files the blocks of ``document`` name, in document order, each once.

    A file that does not exist (or a name that resolves to none) is left out — a TAB lists what it can read.
    """
    names = reference_names()
    if not names:
        return []
    found: List[Path] = []
    for _, target, kwargs in _blocks(document, ""):
        for parameter in sorted(names.get(_clean(target), ())):
            path = _locate(kwargs.get(parameter))
            if path is not None and path not in found:
                found.append(path)
    return found


def _locate(value: Any) -> Optional[Path]:
    """The file ``value`` names (a text: ``pkg://…`` or a path of the run folder), or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    import confluid

    try:
        path = Path(confluid.resolve_config_path(value.strip())).resolve()
    except Exception:  # a distribution that is not installed, a path with `..`: no run to list
        return None
    return path if path.is_file() else None
