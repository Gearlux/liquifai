"""Shell completion for :class:`liquifai.core.LiquifyApp`.

Implements a Typer/Click-shaped wire protocol so any LiquifyApp gets
bash/zsh/fish tab completion for free.

Architecture (fast path):
    1. ``--install-completion`` snapshots the static command tree to
       ``~/.cache/liquifai/<app>.json`` and embeds a tiny shell function in
       the user's rc file.
    2. On TAB the rc function calls the standalone ``liquifai-complete``
       binary (registered by liquifai) — NOT the app — so the heavy
       app-side imports (torch, ultralytics, plugins, …) never load.
    3. ``liquifai-complete`` reads the JSON cache and computes candidates
       via :func:`complete_from_tree`. Override-key suggestions lazily
       import confluid only when needed.
    4. Every successful ``app.run()`` rewrites the cache so plugin/command
       changes propagate.

Package layout (every submodule stays stdlib-only at import time — the
completion mandate):

* :mod:`~liquifai.completion.shells` — shell detection, quote-aware word
  transport, script/helper templates.
* :mod:`~liquifai.completion.cache` — XDG cache locations, static/dependent
  positional value caches, refresh-policy constants, lazy self-heal spawner.
* :mod:`~liquifai.completion.tree` — command-tree snapshot (+ its cache
  read/write), provider specs, bulk/targeted value refresh.
* :mod:`~liquifai.completion.engine` — the TAB hot-path candidate computation.
* :mod:`~liquifai.completion.install` — rc-file splicing + the
  ``liquifai-install-completions`` entry.
* :mod:`~liquifai.completion.discover` — installed-app discovery
  (``liquifai.apps`` entry-point group, probe fallback).

This ``__init__`` re-exports the full historical surface so
``from liquifai.completion import X`` (core, ``_fast_complete``, tests,
consumers) keeps working unchanged. The global flag sets come from
:mod:`liquifai.grammar` — the single source of truth shared with the parser
and ``--help``.
"""

from __future__ import annotations

# Dependency order matters: later modules do `from . import <earlier>`.
from liquifai.grammar import (  # noqa: F401 — re-exported for existing importers
    GLOBAL_FLAGS,
    GLOBAL_VALUE_FLAGS,
    PATH_VALUE_FLAGS,
    SHELL_VALUE_FLAGS,
    stops_positional,
)

from .cache import (  # noqa: F401
    DEPENDENT_NOTICE_WINDOW,
    DEPENDENT_REFRESH_TTL,
    LAZY_REFRESH_THROTTLE,
    VALUE_CACHE_VERSION,
    _dependent_changed_at,
    _dependent_changed_recently,
    cache_dir,
    cache_path,
    dependent_value_cache_path,
    make_lazy_refresh_spawner,
    read_dependent_value_cache,
    read_value_cache,
    value_cache_key,
    value_cache_path,
    values_cache_dir,
    write_dependent_value_cache,
    write_value_cache,
)
from .discover import declared_liquifai_apps, discover_liquifai_apps  # noqa: F401
from .engine import complete, complete_from_tree  # noqa: F401
from .install import (  # noqa: F401
    _HELPERS_END_MARKER,
    _HELPERS_MARKER,
    _cli_install_completions,
    install_for_apps,
    install_script,
)
from .shells import (  # noqa: F401
    SHELLS,
    detect_shell,
    escape_candidate,
    render_helpers,
    render_script,
    split_comp_words,
    wants_forced_refresh,
    words_from_comp_line,
)
from .tree import (  # noqa: F401
    CACHE_VERSION,
    _collapse_to_flags,
    _introspect_function_keys,
    _provider_arity,
    has_stale_value_caches,
    iter_completion_providers,
    read_cache,
    refresh_one,
    refresh_value_caches,
    serialize_app,
    write_cache,
)
