"""Discovery of installed liquifai apps (entry-point group + probe fallback).

An app declares itself in the ``liquifai.apps`` entry-point group
(``[project.entry-points."liquifai.apps"] <name> = "pkg.cli:app"``) — the
idiomatic workspace pattern (like ``confluid.configurables``) — making
discovery an instant, deterministic metadata read. Binaries NOT declared are
probed by executing ``<bin> --show-completion bash`` (slow: a heavy app
imports its full stack first), kept only as a fallback for apps that haven't
opted in.

Pure-stdlib module (fast-path safe — see the completion mandate).
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Workspace bootstrap helpers
# ---------------------------------------------------------------------------


# Entries in `sys.prefix/bin/` we never want to probe — neither plausible
# Liquifai apps nor cheap to invoke. Patterns are matched as prefixes.
_NON_APP_BIN_PREFIXES: List[str] = [
    "python",
    "pip",
    "activate",
    "deactivate",
    "liquifai-",  # liquifai-complete, liquifai-install-completions
    "uv",
    "ruff",
    "black",
    "isort",
    "flake8",
    "mypy",
    "pytest",
    "coverage",
    "wheel",
    "twine",
    "jupyter",
    "ipython",
    "tensorboard",
    "mlflow",
    "f2py",
    "normalizer",
    "httpx",
    "tqdm",
    "huggingface-cli",
    "transformers-cli",
    "torch",
    "convert-",
]


def _probe_is_liquifai_app(entry: Path, timeout: float) -> bool:
    """Return True iff executing ``entry`` identifies it as a Liquifai app.

    Runs ``<entry> --show-completion bash`` and looks for the Liquifai marker
    ``liquifai-complete <name>`` that :func:`render_script` always emits.
    A Liquifai app short-circuits that flag before Confluid bootstrap, but a
    heavy app still runs its module-import side effects first (e.g. matrainer
    pulling in PyTorch Lightning at the top of ``cli.py``), so ``timeout`` is
    sized for the slowest known import chain, not the bare short-circuit cost.
    Click/Typer apps accept ``--show-completion`` too but emit their own
    dispatch logic, so the marker check filters them out. Any probe failure
    (non-zero exit, timeout, OSError) maps to False so a single bad binary
    never aborts discovery.
    """
    name = entry.name
    try:
        res = subprocess.run(
            [str(entry), "--show-completion", "bash"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if res.returncode != 0:
        return False
    # Decode loosely so binary noise from a non-Liquifai responder can't crash us.
    out = res.stdout.decode("utf-8", errors="replace")
    return f"liquifai-complete {name}" in out


def declared_liquifai_apps() -> List[str]:
    """App names declared in the ``liquifai.apps`` entry-point group (sorted).

    Instant, deterministic discovery: any installed distribution may declare

    .. code-block:: toml

        [project.entry-points."liquifai.apps"]
        sonair = "sonair.cli:app"

    where the NAME is the app's CLI/binary name (== ``LiquifyApp.name``) and
    the value points at the ``LiquifyApp`` instance. Entry points come from
    installed dist metadata, so a declared app is by construction installed in
    the active environment. Best-effort: a metadata read failure yields ``[]``
    (discovery then falls back to probing everything).
    """
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="liquifai.apps")
    except Exception:
        return []
    return sorted({ep.name for ep in eps})


def discover_liquifai_apps(prefix: Optional[Path] = None, timeout: float = 15.0) -> List[str]:
    """Return the names of Liquifai apps installed in ``prefix``'s bin dir.

    Two tiers:

    1. **Declared** (:func:`declared_liquifai_apps`): apps registered in the
       ``liquifai.apps`` entry-point group are returned WITHOUT probing —
       an instant metadata read, immune to probe timeouts.
    2. **Probe fallback**: remaining ``<prefix>/bin/*`` entries (defaulting to
       ``sys.prefix``; obvious non-apps skipped via
       :data:`_NON_APP_BIN_PREFIXES`, declared names skipped as already
       found) are executed with :func:`_probe_is_liquifai_app` — kept only
       for apps that haven't opted into the entry-point group.

    The probes run **concurrently** in a thread pool. Each probe spawns the
    real CLI, and heavy apps import their full stack (torch, Lightning, …)
    before the ``--show-completion`` short-circuit — so a *serial* walk of a
    populated ML venv costs the SUM of per-probe times (minutes: dozens of
    binaries, several hitting the 15 s timeout). The work is pure subprocess
    I/O, so a thread pool bounds the wall-clock cost to roughly the slowest
    single probe. Results preserve sorted-name order (declared names merged
    with probed bin entries), so discovery stays deterministic regardless of
    which thread finishes first.
    """
    declared = declared_liquifai_apps()

    bindir = Path(prefix or sys.prefix) / "bin"
    if not bindir.is_dir():
        return declared
    candidates = [
        entry
        for entry in sorted(bindir.iterdir())
        if entry.is_file()
        and os.access(entry, os.X_OK)
        and entry.name not in declared
        and not any(entry.name.startswith(p) for p in _NON_APP_BIN_PREFIXES)
    ]
    if not candidates:
        return declared
    # Each probe may spawn a heavy ML import (torch/Lightning). Empirically a
    # real app imports in ~6 s alone but is slowed by concurrent imports
    # thrashing CPU/memory bandwidth: past ~8 parallel probes on a populated
    # workspace venv, genuine apps start blowing the 15 s timeout and drop out
    # of discovery (a correctness regression worse than a slower run). So cap
    # low — 8 collapses the serial timeout-sum ~5x (minutes → ~45 s) while
    # keeping every real app under the timeout and peak memory bounded. RAM,
    # not cores, is the constraint here, so this does NOT scale with cpu_count
    # (CI runners have many cores but little RAM). Never exceed candidate count.
    max_workers = min(len(candidates), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_probe_is_liquifai_app, entry, timeout) for entry in candidates]
        probed = [entry.name for entry, fut in zip(candidates, futures) if fut.result()]
    return sorted({*declared, *probed})
