"""The scopes of the config files a command runs (``@app.command(config_files=...)``).

A command that hands a YAML of its own choosing to another tool names that file; liquifai reads it (raw, includes
followed) and answers from it: the scopes ``--help`` lists, the ones ``--scope <TAB>`` offers, and the refusal of a
typed scope the file does not declare. A bare scope (``!scope:onnx``) is offered as its name, a keyed one
(``!scope:task=seg``) as ``KEY=VAL``.

Stdlib-only at import (completion imports it from the cache-refresh path): confluid is imported inside the functions.
"""

import os
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Sequence, Set, Union

from liquifai.completion.tree import _provider_arity
from liquifai.exceptions import UnknownScopeError

#: What a ``config_files=`` provider returns: the config files the command runs.
ConfigFiles = Sequence[Union[str, "os.PathLike[str]"]]


def files_for(provider: Callable[..., ConfigFiles], typed: Mapping[str, str]) -> List[Path]:
    """The files ``provider`` names for the positionals typed so far (``{}``: none typed — the command's defaults)."""
    files = provider(dict(typed)) if _provider_arity(provider) else provider()
    return list(dict.fromkeys(Path(f) for f in (files or [])))


def scopes_of(path: Path) -> Set[str]:
    """Every scope ``--scope`` may name in ``path``: its bare scopes, and its keyed ones as ``KEY=VAL``."""
    import confluid

    raw = confluid.load(path, until="raw")
    keyed = {f"{key}={value}" for key, values in confluid.discover_dimension_values(raw).items() for value in values}
    return set(confluid.discover_boolean_scopes(raw)) | keyed


def display(path: Path) -> str:
    """``path`` as a person reads it: relative to the working directory when it lies under it."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def offered(files: Sequence[Path]) -> Dict[str, List[str]]:
    """``{scope: [the files declaring it]}`` over ``files``, the scopes sorted; a missing file declares none."""
    found: Dict[str, List[str]] = {}
    for path in files:
        if not path.is_file():
            continue
        for scope in sorted(scopes_of(path)):
            found.setdefault(scope, []).append(display(path))
    return dict(sorted(found.items()))


def check_declared(typed: Sequence[str], raw: object, path: Path) -> None:
    """Refuse a typed BARE scope the config at ``path`` (read raw: ``raw``) does not declare.

    A bare name must be a bare scope of the config or the name of one of its keyed dimensions (whose own bare-use
    refusal is confluid's). A keyed ``dim=value`` is not judged here: an undeclared dimension is an inert no-op, so a
    CLI can pass one to every config (confluid's §1), and a declared dimension's value is confluid's check.

    Raises:
        UnknownScopeError: naming the file, the scope and the scopes the file does declare.
    """
    import confluid

    bare = [scope for scope in typed if scope and "=" not in scope]
    if not bare:
        return
    values = confluid.discover_dimension_values(raw)
    names = set(confluid.discover_boolean_scopes(raw)) | set(values)
    for scope in bare:
        if scope not in names:
            keyed = {f"{key}={value}" for key, found in values.items() for value in found}
            listed = ", ".join(sorted(set(confluid.discover_boolean_scopes(raw)) | keyed)) or "none"
            raise UnknownScopeError(f"{display(path)} has no scope '{scope}' (its scopes: {listed})")


def check_typed(typed: Sequence[str], files: Sequence[Path]) -> None:
    """Refuse a typed scope one of ``files`` does not declare — the command runs every one of them with it.

    Raises:
        UnknownScopeError: naming the file, the scope and the scopes the file does declare.
    """
    if not typed:
        return
    for path in files:
        if not path.is_file():
            continue  # the command names a file it then refuses or creates: its own message is the better one
        declared = scopes_of(path)
        for scope in typed:
            if scope not in declared:
                listed = ", ".join(sorted(declared)) or "none"
                raise UnknownScopeError(f"{display(path)} has no scope '{scope}' (its scopes: {listed})")
