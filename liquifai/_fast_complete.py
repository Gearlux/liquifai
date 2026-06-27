"""Fast standalone entry point for shell tab completion.

Registered as the ``liquifai-complete`` console script. Imports only
stdlib + :mod:`liquifai.completion` (which itself avoids touching
``liquifai.core`` so logflow / confluid / rich never load on the hot path).

Wire protocol: the shell wrapper sets ``COMP_WORDS`` and ``COMP_CWORD`` and
passes the target app name as ``argv[1]``. We read the on-disk command-tree
cache and emit candidates one per line. Cache miss → silent exit (the user
will get one slow completion the first time they actually run the app, then
the cache is populated and subsequent TABs are fast).
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
)


def main() -> None:
    if len(sys.argv) < 2:
        return
    app_name = sys.argv[1]
    tree = read_cache(app_name)
    if tree is None:
        return

    # split_comp_words preserves tokens with embedded spaces (e.g. a dataset name
    # "Test Script VB"); escape_candidate emits each candidate so the shell inserts
    # it as a single argument. The lazy-refresh spawner self-heals stale/missing
    # DEPENDENT caches (e.g. a new dataset version) in a detached background process.
    words = split_comp_words(os.environ.get("COMP_WORDS", ""))
    try:
        cword = int(os.environ.get("COMP_CWORD", "0"))
    except ValueError:
        cword = 0
    for cand in complete_from_tree(tree, words, cword, lazy_refresh=make_lazy_refresh_spawner(app_name)):
        print(escape_candidate(cand))


if __name__ == "__main__":
    main()
