"""Fast standalone entry point for shell tab completion.

Registered as the ``liquifai-complete`` console script. Imports only
stdlib + :mod:`liquifai.completion` (which itself avoids touching
``liquifai.core`` so loggair / confluid / rich never load on the hot path).

Wire protocol: the shell wrapper sets either ``COMP_LINE`` + ``COMP_POINT`` (the
raw command line + cursor offset — bash, quote-aware) OR ``COMP_WORDS`` +
``COMP_CWORD`` (the pre-split word array — zsh/fish, already quote-aware, and old
bash installs), and passes the target app name as ``argv[1]``. We read the on-disk
command-tree cache and emit candidates one per line. Cache miss → silent exit (the
user gets one slow completion the first time they actually run the app, then the
cache is populated and subsequent TABs are fast).

``COMP_LINE`` is preferred when present because bash's ``COMP_WORDS`` splits on
whitespace WITHOUT honoring quotes/escapes — so a value like ``"Helios Base Model"``
is shattered into three words. Re-tokenizing ``COMP_LINE`` (quote-aware) keeps it as
one token; the ``COMP_WORDS`` path stays as a fallback for zsh/fish/old-bash.
"""

from __future__ import annotations

import os
import sys

from liquifai.completion import (
    complete_from_tree,
    escape_candidate,
    make_lazy_refresh_spawner,
    read_cache,
    split_comp_words,
    wants_forced_refresh,
    words_from_comp_line,
)


def main() -> None:
    if len(sys.argv) < 2:
        return
    app_name = sys.argv[1]
    tree = read_cache(app_name)
    if tree is None:
        return

    # Prefer COMP_LINE/COMP_POINT (bash: quote-aware re-tokenization keeps
    # "Helios Base Model" as ONE word); fall back to the pre-split COMP_WORDS
    # (zsh/fish, already quote-aware, and old bash installs not yet re-sourced).
    # escape_candidate emits each candidate so the shell inserts it as a single
    # argument. The lazy-refresh spawner self-heals stale/missing DEPENDENT caches
    # (e.g. a new dataset version) in a detached background process. A repeated/second
    # TAB (bash $COMP_TYPE) forces that refresh even for a fresh-by-age cache — the
    # user's "this list looks wrong, refresh it" signal.
    force_refresh = wants_forced_refresh(os.environ.get("COMP_TYPE"))
    comp_line = os.environ.get("COMP_LINE")
    comp_point = os.environ.get("COMP_POINT")
    if comp_line:  # non-empty ⇒ present (an empty/unset COMP_LINE falls back below)
        try:
            point = int(comp_point) if comp_point else len(comp_line)
        except ValueError:
            point = len(comp_line)
        words, cword = words_from_comp_line(comp_line, point)
    else:
        words = split_comp_words(os.environ.get("COMP_WORDS", ""))
        try:
            cword = int(os.environ.get("COMP_CWORD", "0"))
        except ValueError:
            cword = 0
    for cand in complete_from_tree(
        tree, words, cword, lazy_refresh=make_lazy_refresh_spawner(app_name), force_refresh=force_refresh
    ):
        print(escape_candidate(cand))


if __name__ == "__main__":
    main()
