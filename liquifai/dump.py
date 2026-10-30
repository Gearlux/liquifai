"""The configuration a run resolves to, as a file — what ``--dump-config`` and ``--save-config`` write.

The text is the document after includes, scopes, positionals and command-line keys, every object in it built and
dumped with each of its settings — written or defaulted — so a reader sees every option of every class the run
involves, and the file runs again as it is. Objects are built the way dependency injection builds them
(``confluid.load(node, context=document)``, so top-level keys broadcast), without ``solidify()``: a constructor only
stores values (the workspace's lazy-initialization rule), so nothing is downloaded or materialized. Classes are named
by their importable dotted path (``qualified=True``), so the file reloads in a process that registered nothing.

Example::

    text = resolved_config_text(context.config_data, context.host_facts)
    # epochs: 3
    # runnable:
    #   _target_: mypkg.Trainer
    #   optimizer: {_target_: mypkg.Optimizer, lr: 0.05, momentum: 0.9, nesterov: false}
    #   epochs: 3
    #   seed: 0
"""

from pathlib import Path
from typing import Any, Mapping

import confluid

from liquifai import host


def resolved_config_text(document: Mapping[str, Any], host_facts: Mapping[str, str]) -> str:
    """Build every object in ``document`` (no ``solidify()``) and dump the result with every setting.

    The ``platform:`` block liquifai merged into the document (:func:`liquifai.host.inject_facts`) is left out when
    it holds only the detected ``host_facts``: the values that depend on it are already resolved, and a file carrying
    it would pin this machine's facts wherever it runs. A block holding a value the config wrote stays.
    """
    data = dict(document)
    built = confluid.load(data, context=data, solidify=False)
    if isinstance(built, dict) and built.get(host.NAMESPACE) == dict(host_facts):
        built.pop(host.NAMESPACE)
    return str(confluid.dump(built, qualified=True))


def write_config_text(text: str, path: Path) -> Path:
    """Write ``text`` to ``path``, creating its folder; returns the path written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
