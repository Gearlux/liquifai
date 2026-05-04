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

from liquifai.completion import complete_from_tree, read_cache


def main() -> None:
    if len(sys.argv) < 2:
        return
    app_name = sys.argv[1]
    tree = read_cache(app_name)
    if tree is None:
        return

    words = os.environ.get("COMP_WORDS", "").split()
    try:
        cword = int(os.environ.get("COMP_CWORD", "0"))
    except ValueError:
        cword = 0
    for cand in complete_from_tree(tree, words, cword):
        print(cand)


if __name__ == "__main__":
    main()
