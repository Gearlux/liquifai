"""CLI-facing glue between ``argv`` and the shell-completion machinery.

These are the argv-guard + console-output handlers that intercept the
completion flags (``--show-completion`` / ``--install-completion`` /
``--refresh-completions`` / ``--refresh-completion-value``) before the bootstrap
lifecycle, plus the two cache-refresh helpers run after a command.

They live HERE — a top-level module, not inside :mod:`liquifai.completion` —
because they import Rich (``console``) and must stay off the completion
fast-path. Every submodule of :mod:`liquifai.completion` MUST stay stdlib-only
at import time (the ``liquifai-complete`` hot path imports the package), so the
Rich-using CLI glue cannot live inside it. The real work is still delegated to
:mod:`liquifai.completion`; these functions only parse argv and print.

Each takes the ``LiquifyApp`` as ``app`` (typed ``Any`` to avoid a circular
import — ``core`` imports this module) and is called by a thin same-named
private method on :class:`liquifai.core.LiquifyApp` so existing call sites and
tests keep working unchanged.
"""

import os
from typing import Any, List

from rich.console import Console

console = Console()


def handle_completion_install(app: Any, argv: List[str]) -> bool:
    """Handle ``--show-completion`` / ``--install-completion`` early.

    Both must run before Confluid bootstrap (no config required) and
    before help rendering. ``--install-completion`` also primes the
    on-disk command-tree cache so the very first TAB after installing
    is fast (the user does not have to invoke the slow app once first).
    Returns True if one was handled.
    """
    for special in ("--show-completion", "--install-completion"):
        if special not in argv:
            continue
        from liquifai.completion import SHELLS, detect_shell, install_script, render_script, write_cache

        idx = argv.index(special)
        shell = argv[idx + 1] if idx + 1 < len(argv) and argv[idx + 1] in SHELLS else detect_shell()
        if special == "--show-completion":
            print(render_script(app.name, shell))
            # Side effect: prime the cache while the app is loaded.
            # liquifai-install-completions auto-discovers apps by
            # probing them with `<app> --show-completion bash`; the
            # cache is what makes the resulting `complete` calls
            # actually return suggestions, so we MUST seed it here
            # — otherwise tab-completion is registered but silent.
            # Best-effort: never fail the script output on a cache
            # write error.
            try:
                write_cache(app)
            except Exception:
                pass
        else:
            target = install_script(app.name, shell)
            cache_target = write_cache(app)
            console.print(f"[green]Installed[/green] {app.name} {shell} completion in [cyan]{target}[/cyan]")
            console.print(f"[dim]Cached command tree: {cache_target}[/dim]")
            console.print(f"[dim]Restart your shell or `source {target}` to activate.[/dim]")
        return True
    return False


def refresh_completion_cache(app: Any) -> None:
    """Best-effort refresh of the on-disk command-tree cache."""
    try:
        from liquifai.completion import write_cache

        write_cache(app)
    except Exception:
        pass


def handle_refresh_completions(app: Any, argv: List[str]) -> bool:
    """Handle ``--refresh-completions`` early (before bootstrap).

    Runs every registered positional value provider (``@command(...,
    completions=...)``) in THIS process — providers may import the heavy SDK
    and hit the network — and writes each result to its value cache so TAB
    can offer real values for a ``<name>`` slot. Returns True if handled.
    """
    if "--refresh-completions" not in argv:
        return False
    from liquifai.completion import refresh_value_caches, write_cache

    try:
        write_cache(app)  # keep the command tree fresh too
    except Exception:
        pass
    written = refresh_value_caches(app)
    total = sum(written.values())
    if written:
        console.print(
            f"[green]Refreshed[/green] {len(written)} completion value cache(s) "
            f"([cyan]{total}[/cyan] values) for [cyan]{app.name}[/cyan]."
        )
    else:
        console.print(f"[dim]No positional completion providers registered for {app.name}.[/dim]")
    return True


def handle_refresh_completion_value(app: Any, argv: List[str]) -> bool:
    """Handle ``--refresh-completion-value '<json>'`` early (background self-heal).

    The detached helper the fast path spawns to refresh ONE dependent positional's
    cache for a single input combo (``{"key": ..., "inputs": {...}}``). Runs the
    targeted provider and writes the cache, then exits — no command, no bootstrap.
    Silent and best-effort (it runs in the background). Returns True if handled.
    """
    if "--refresh-completion-value" not in argv:
        return False
    import json

    from liquifai.completion import refresh_one

    idx = argv.index("--refresh-completion-value")
    try:
        spec = json.loads(argv[idx + 1]) if idx + 1 < len(argv) else {}
        refresh_one(app, str(spec.get("key", "")), dict(spec.get("inputs", {}) or {}))
    except Exception:
        pass
    return True


def maybe_background_refresh_values(app: Any, ttl: float = 600.0) -> None:
    """Opportunistically refresh stale positional value caches in the background.

    **Opt-in** — does nothing unless ``$LIQUIFAI_BG_REFRESH`` is set. When
    enabled, after a successful command, if any registered provider's value
    cache is missing or older than ``ttl`` seconds, the providers run in a
    detached daemon thread so the next TAB sees fresh values without blocking
    the command that just ran. Best-effort: any failure is swallowed.

    It is OFF by default because a provider may hit the network (e.g. query a
    platform), and a normal run should never trigger a surprise background
    request — the deterministic path is ``--refresh-completions``. Set
    ``LIQUIFAI_BG_REFRESH=1`` (e.g. in a project rc) to keep caches warm for free.
    """
    if not os.environ.get("LIQUIFAI_BG_REFRESH"):
        return
    try:
        import threading

        from liquifai.completion import has_stale_value_caches, refresh_value_caches

        if not has_stale_value_caches(app, ttl):
            return

        def _bg() -> None:
            try:
                refresh_value_caches(app)
            except Exception:
                pass

        threading.Thread(target=_bg, daemon=True, name=f"liquifai-refresh-{app.name}").start()
    except Exception:
        pass
