"""What a config on the command line says about its settings — the help a TAB listing and ``--help`` show for it.

A script command's config holds the settings its author put first: the top-level keys written in that file (never
its includes') and the scope blocks it declares. :func:`read_config_help` reads them with their help — the comment
lines directly above each key (else the comment at the end of its line), else the docstring of the class parameter
the key feeds (through ``!ref:`` or by its name) — and every other key the document holds with its class's
parameter help. Class docstrings are read from source (:func:`confluid.source_param_docs`), never by importing the
class, so a TAB stays fast.

Stdlib-only at import, like every ``liquifai.completion`` module: confluid is imported inside the function.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

#: The widest value a focused key's row shows; a longer one (a list of markers) shows none.
_VALUE_MAX = 60


@dataclass
class FocusedKey:
    """A top-level key written in the config file itself."""

    #: The key, as written (a dotted key keeps its dots).
    path: str
    #: Its help: its comment, else the docstring of the parameter it feeds.
    help: str = ""
    #: Its value after interpolation, when it is short enough to show; else empty.
    value: str = ""


@dataclass
class ConfigScope:
    """A scope block the config file declares."""

    #: What ``--scope`` takes to activate it (``quick``, ``task=classification``).
    activation: str
    #: Its comment, else empty.
    help: str = ""


@dataclass
class ConfigHelp:
    """The settings a config offers on the command line, the ones its author wrote first."""

    #: The config file's name, which the groups are titled by.
    source: str
    #: Every dotted key the document holds, its scope dimensions included — what ``--<key>`` overrides.
    keys: List[str] = field(default_factory=list)
    #: The top-level keys of the file itself, in file order.
    focused: List[FocusedKey] = field(default_factory=list)
    #: The scope blocks of the file itself, in file order, one per activation.
    scopes: List[ConfigScope] = field(default_factory=list)
    #: ``{dotted key: help}`` for the document's other keys: an included file's top-level keys and the parameters
    #: of the markers it builds.
    docs: Dict[str, str] = field(default_factory=dict)
    #: ``{dotted key: value}`` of a marker's parameters: the value the document writes when it is short enough to
    #: show, else the default its class's source writes.
    values: Dict[str, str] = field(default_factory=dict)
    #: The yes/no parameters among the markers' (they take no value).
    switches: Set[str] = field(default_factory=set)
    #: ``(path, class name)`` of every ``!class:`` block, the shallowest first, then in document order.
    blocks: List[Tuple[str, str]] = field(default_factory=list)


def read_config_help(config_path: Path, exclude: Iterable[str] = (), with_docs: bool = True) -> ConfigHelp:
    """The keys of the config at ``config_path``, its own top-level keys and scopes first, each with its help.

    ``exclude`` names keys that belong to the command (its parameters and positionals — ``runnable`` is what
    ``run`` takes, not a setting of the file); they are never focused. ``with_docs=False`` reads the keys alone (the
    candidates of a TAB that lists nothing). A file a key's comment or docstring cannot be read from leaves that help
    empty; the keys are read as :func:`confluid.load` reads them, and its error propagates.
    """
    import confluid

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        raw, paths = confluid.load(config_path, until="raw", return_paths=True)
        dimensions = confluid.discover_dimensions(raw)
        document = confluid.load(raw, until="document")
    keys: List[str] = list(dimensions)
    targets: Dict[str, str] = {}
    values: Dict[str, str] = {}
    _walk_keys(document, "", keys, targets, values)
    # Every parameter of a block's class is an option, written or not (``--overwrite`` with no ``overwrite:``),
    # read from the class's source — nothing is imported.
    switches: Set[str] = set()
    written = set(keys)
    for path, target in targets.items():
        for param in _params_of(confluid.source_params, target):
            key = f"{path}.{param.name}"
            if key not in written and param.default:
                values[key] = param.default
            if param.switch:
                switches.add(key)
            keys.append(key)
    order = {path: index for index, path in enumerate(targets)}
    blocks = [(path, _class_name(targets[path])) for path in sorted(targets, key=lambda p: (p.count("."), order[p]))]
    found = ConfigHelp(source=config_path.name, keys=sorted(set(keys)), values=values, switches=switches, blocks=blocks)
    if not with_docs:
        return found

    class_docs: Dict[str, Dict[str, str]] = {}

    def docs_of(target: str) -> Dict[str, str]:
        if target not in class_docs:
            try:
                class_docs[target] = confluid.source_param_docs(target)
            except Exception:  # an unreadable source: no help, never a failed TAB
                class_docs[target] = {}
        return class_docs[target]

    def help_of(note: Any) -> str:
        if note.comment:
            return str(note.comment)
        for target, param in note.feeds:
            text = docs_of(target).get(param, "")
            if text:
                return text
        for target in targets.values():  # a key named like a parameter broadcasts to it: that parameter's help
            text = docs_of(target).get(note.name, "")
            if text:
                return text
        return ""

    entry = Path(config_path).resolve()
    skipped = set(exclude)
    for read in paths or [entry]:
        notes = _notes(confluid.document_notes, read)
        if notes is None:
            continue
        own = Path(read).resolve() == entry
        for note in notes.keys:
            if own and note.name not in skipped:
                value = document.get(note.name) if isinstance(document, dict) else None
                found.focused.append(FocusedKey(note.name, help_of(note), _shown(value)))
            elif note.name not in found.docs:
                found.docs[note.name] = help_of(note)
        if own:
            seen: Dict[str, ConfigScope] = {}
            for scope in notes.scopes:
                if scope.activation not in seen:
                    seen[scope.activation] = ConfigScope(scope.activation, scope.comment)
                    found.scopes.append(seen[scope.activation])
                elif not seen[scope.activation].help:
                    seen[scope.activation].help = scope.comment
    for key in found.keys:
        head, _, leaf = key.rpartition(".")
        owner = targets.get(head)
        if owner is not None and key not in found.docs:
            found.docs[key] = docs_of(owner).get(leaf, "")
    return found


def _params_of(reader: Callable[[str], Any], target: str) -> Any:
    """``reader(target)`` — a class's parameters from its source — or none when the source cannot be read."""
    try:
        return reader(target)
    except Exception:  # an unreadable source: its written keys only, never a failed TAB
        return ()


def _class_name(target: str) -> str:
    """The class's own name in a dotted ``!class:`` spelling (``pkg.mod.Builder()`` → ``Builder``)."""
    return target.split("@", 1)[0].split("(", 1)[0].strip().rpartition(".")[2]


def _notes(reader: Callable[[Any], Any], path: Any) -> Optional[Any]:
    """``reader(path)``, or None when the file cannot be read back."""
    try:
        return reader(path)
    except Exception:
        return None


def _shown(value: Any) -> str:
    """``value`` as a row shows it: a scalar, or a list of scalars, written out when short; else empty."""
    if isinstance(value, bool):
        text = str(value).lower()
    elif isinstance(value, (str, int, float)):
        text = str(value)
    elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
        text = "[" + ", ".join(str(v) for v in value) + "]"
    else:
        return ""
    return text if len(text) <= _VALUE_MAX else ""


def _walk_keys(
    obj: Any,
    prefix: str,
    out: List[str],
    targets: Dict[str, str],
    values: Dict[str, str],
    depth: int = 0,
    max_depth: int = 8,
    in_block: bool = False,
) -> None:
    """Every dotted key under ``obj`` into ``out``; ``targets`` gets each marker's path and the class it names, and
    ``values`` the value of each marker parameter short enough to show."""
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue
            full = f"{prefix}.{k}" if prefix else k
            out.append(full)
            if in_block and _shown(v):
                values[full] = _shown(v)
            _walk_keys(v, full, out, targets, values, depth + 1, max_depth)
        return
    kwargs = getattr(obj, "kwargs", None)
    if isinstance(kwargs, dict):
        target = getattr(obj, "target", None)
        is_block = bool(prefix) and type(obj).__name__ != "Reference" and target is not None
        if is_block:
            targets[prefix] = (
                target if isinstance(target, str) else f"{target.__module__}.{getattr(target, '__qualname__', '')}"
            )
        _walk_keys(kwargs, prefix, out, targets, values, depth + 1, max_depth, in_block=is_block)
