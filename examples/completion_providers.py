"""Dynamic positional completion — the runnable companion to ``docs/shell-completion.md``.

Wires a STATIC provider (0-arg) and a DEPENDENT provider (1-arg, receiving the
already-typed earlier positionals) onto a command's positionals. Providers run
only at refresh time (never on the TAB hot path); here we call them directly to
show the candidates each would contribute.

Run with no arguments for the demo; with arguments to act as the app itself.
"""

import os
import sys
import tempfile
from typing import Dict, List

from liquifai import LiquifyApp
from liquifai.completion.tree import refresh_value_caches

app = LiquifyApp(name="completion-demo")

_DATASETS: Dict[str, List[str]] = {
    "cifar10": ["1.0", "2.0"],
    "rfuav": ["v1", "v2", "v3"],
}


def dataset_names() -> List[str]:
    """STATIC provider (0-arg): one global value cache."""
    return sorted(_DATASETS)


def dataset_versions(inputs: Dict[str, str]) -> List[str]:
    """DEPENDENT provider (1-arg): receives the typed earlier positionals."""
    return _DATASETS.get(inputs.get("name", ""), [])


def formats() -> List[str]:
    """An OPTION's provider: ``download cifar10 --format <TAB>`` completes the option's value."""
    return ["parquet", "arrow", "csv"]


@app.command(
    "download",
    positionals=["name", "version"],
    completions={"name": dataset_names, "version": dataset_versions, "format": formats},
)
def download(
    name: str = "", version: str = "latest", path: str = ".", overwrite: bool = False, format: str = "parquet"
) -> None:
    """Pretend-download a dataset version.

    Args:
        name: Dataset name — TAB-completes from dataset_names().
        version: Version — TAB-completes per the typed name via dataset_versions().
        path: Destination directory.
        overwrite: Replace an existing download.
        format: File format — TAB-completes from formats(); a comma list's next word too (parquet,<TAB>).
    """
    print(f"RESULT name={name!r} version={version!r} path={path!r} overwrite={overwrite} format={format!r}")


def demo() -> None:
    print("static provider  (download <TAB>):          ", dataset_names())
    print("dependent provider (download rfuav <TAB>):   ", dataset_versions({"name": "rfuav"}))
    print("dependent provider (download cifar10 <TAB>): ", dataset_versions({"name": "cifar10"}))
    print()
    print("At refresh time (`completion-demo --refresh-completions`) liquifai calls the")
    print("static provider once and PRE-ENUMERATES the dependent one per prior value;")
    print("the TAB hot path only ever reads the JSON caches under ~/.cache/liquifai/.")
    print()

    # Candidate semantics (engine is pure — drive it directly):
    # positionals are hinted, never offered as flags; a bool flag doesn't open
    # a value slot; already-typed flags drop out of the suggestions.
    from liquifai import completion as comp

    line = ["completion-demo", "download", "rfuav", "v1", ""]
    flags = [
        c
        for c in comp.complete(app, line, cword=4)
        if not c.startswith("-") or c in ("--path", "--overwrite", "--name", "--version")
    ]
    print("after both positionals (download rfuav v1 <TAB>):", flags)
    print("  -> --name/--version are POSITIONALS: hinted, never offered as flags")
    line = ["completion-demo", "download", "rfuav", "v1", "--overwrite", ""]
    out = comp.complete(app, line, cword=5)
    print("after the bool flag (… --overwrite <TAB>):", [c for c in out if c in ("--path", "--overwrite")])
    print("  -> --overwrite is bool (no value slot) and, once typed, not re-offered")
    print()
    os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()  # a throwaway cache: the demo never touches yours
    refresh_value_caches(app)
    line = ["completion-demo", "download", "cifar10", "--format", ""]
    print("an option's value (… --format <TAB>):", comp.complete(app, line, cword=4))
    line = ["completion-demo", "download", "cifar10", "--format", "parquet,"]
    print("a list's next word (… --format parquet,<TAB>):", comp.complete(app, line, cword=4))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        app.run()
    else:
        demo()
