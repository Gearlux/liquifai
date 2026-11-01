"""What a config on the command line says about its settings — the help a TAB listing and ``--help`` show for it.

A script command's config holds the settings its author put first: the top-level keys written in that file, then
the scope blocks of the document — its own, then its includes' (a scope activates wherever it is declared) — then
each included file's top-level keys the file itself does not write, grouped by the include as it is written
(``pkg://other-project/config/base.yaml``). :func:`read_config_help` reads them with their help — the comment lines
directly above each key (else the comment at the end of its line, with the comment lines aligned under it), else the
docstring of the class parameter the key feeds (through ``!ref:`` or by its name) — and every other key the document
holds with its class's parameter help. Class docstrings are read from source (:func:`confluid.source_param_docs`),
never by importing the class, so a TAB stays fast.

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
    """A scope the config declares, in the file itself or in a file it includes."""

    #: What ``--scope`` takes to activate it (``quick``, ``task=classification``).
    activation: str
    #: Its comment, else empty.
    help: str = ""


@dataclass
class IncludedKeys:
    """The top-level keys an included file writes that the config file itself does not."""

    #: The include as the including file writes it (``base.yaml``, ``pkg://<distribution>/<path>``); the file's path
    #: when it cannot be read back.
    source: str
    #: Its keys, in its file order.
    keys: List[FocusedKey] = field(default_factory=list)


@dataclass
class ConfigHelp:
    """The settings a config offers on the command line, the ones its author wrote first."""

    #: The config file's name, which the groups are titled by.
    source: str
    #: Every dotted key the document holds, its scope dimensions included — what ``--<key>`` overrides.
    keys: List[str] = field(default_factory=list)
    #: The top-level keys of the file itself, in file order.
    focused: List[FocusedKey] = field(default_factory=list)
    #: The included files' top-level keys the file itself does not write, a group per file in the order they are
    #: read; a key two includes write is listed with the first.
    included: List[IncludedKeys] = field(default_factory=list)
    #: The scopes of the document, one per activation: the file's own in file order, then each include's in the
    #: order they are read; the help is the file's comment, else the first include's.
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
    #: ``{dotted key: annotation}`` of a marker's parameters, as its class's source writes it (``Optional[int]``).
    types: Dict[str, str] = field(default_factory=dict)
    #: ``{dotted key: referenced key}`` of every marker parameter written ``!ref:<key>``.
    references: Dict[str, str] = field(default_factory=dict)
    #: ``(class name, parameter)`` of every block parameter a top-level key already feeds — through ``!ref:`` or by
    #: its name — and that is therefore not listed with its block.
    fed: Set[Tuple[str, str]] = field(default_factory=set)


def read_config_help(config_path: Path, exclude: Iterable[str] = (), with_docs: bool = True) -> ConfigHelp:
    """The keys of the config at ``config_path``, its own top-level keys and every scope first, each with its help.

    ``exclude`` names keys that belong to the command (its parameters and positionals — ``runnable`` is what
    ``run`` takes, not a setting of the file); they are never focused. A key that holds an object — an excluded one,
    or a marker parameter holding a ``!class:`` block (``runnable.stream``) — is no key to type: a typed value
    replaces the object, so only its parameters are listed. ``with_docs=False`` reads the keys alone (the
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
    refs: Dict[str, str] = {}
    _walk_keys(document, "", keys, targets, values, refs)
    # Every parameter of a block's class is an option, written or not (``--overwrite`` with no ``overwrite:``),
    # read from the class's source — nothing is imported.
    switches: Set[str] = set()
    types: Dict[str, str] = {}
    written = set(keys)
    for path, target in targets.items():
        for param in _params_of(confluid.source_params, target):
            key = f"{path}.{param.name}"
            if key not in written and param.default:
                values[key] = param.default
            if param.switch:
                switches.add(key)
            if param.annotation:
                types[key] = param.annotation
            keys.append(key)
    # A block parameter a top-level key already feeds is that key's to list, not its block's: one written as
    # ``!ref:<key>``, or one the block does not write, named like a top-level key (which is broadcast into it).
    top = (
        {key for key in document if isinstance(key, str) and not key.startswith("_")}
        if isinstance(document, dict)
        else set()
    )
    fed_paths = {path for path, ref in refs.items() if ref.split(".", 1)[0].split("[", 1)[0] in top}
    for key in keys:
        head, _, leaf = key.rpartition(".")
        if head in targets and leaf in top and key not in written:
            fed_paths.add(key)
    fed = {(_class_name(targets[path.rpartition(".")[0]]), path.rpartition(".")[2]) for path in fed_paths}
    skipped = set(exclude)
    objects = {path for path in targets if "." in path or path in skipped}
    keys = [key for key in keys if key not in fed_paths and key not in objects]
    order = {path: index for index, path in enumerate(targets)}
    blocks = [(path, _class_name(targets[path])) for path in sorted(targets, key=lambda p: (p.count("."), order[p]))]
    found = ConfigHelp(
        source=config_path.name,
        keys=sorted(set(keys)),
        values=values,
        switches=switches,
        blocks=blocks,
        fed=fed,
        types=types,
        references=refs,
    )
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

    def key_of(note: Any) -> FocusedKey:
        value = document.get(note.name) if isinstance(document, dict) else None
        return FocusedKey(note.name, help_of(note), _shown(value))

    entry = Path(config_path).resolve()
    read_notes = [(Path(read).resolve(), _notes(confluid.document_notes, read)) for read in paths or [entry]]
    listed = {note.name for read, notes in read_notes if read == entry and notes is not None for note in notes.keys}
    listed |= skipped
    spellings = _include_spellings([read for read, _ in read_notes])
    seen: Dict[str, ConfigScope] = {}
    for read, notes in read_notes:
        if notes is None:
            continue
        own = read == entry
        group = IncludedKeys(spellings.get(read, str(read)))
        for note in notes.keys:
            if own and note.name not in skipped:
                found.focused.append(key_of(note))
            elif note.name not in found.docs:
                found.docs[note.name] = help_of(note)
            if not own and note.name not in listed:
                group.keys.append(key_of(note))
                listed.add(note.name)
        if group.keys:
            found.included.append(group)
        _add_scopes(notes, seen, found.scopes)
    _add_aliases(raw, seen, found.scopes)
    for key in found.keys:
        head, _, leaf = key.rpartition(".")
        owner = targets.get(head)
        if owner is not None and key not in found.docs:
            found.docs[key] = docs_of(owner).get(leaf, "")
    return found


def read_config_scopes(config_path: Path) -> List[ConfigScope]:
    """The scopes of the config at ``config_path`` — :attr:`ConfigHelp.scopes`, without reading its keys.

    What ``--scope <TAB>`` offers: the file's own in file order, then each include's, one per activation, then the
    document's ``scope_aliases:``. The files are read as :func:`confluid.load` reads them, and its error propagates.
    """
    import confluid

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        raw, paths = confluid.load(config_path, until="raw", return_paths=True)
    scopes: List[ConfigScope] = []
    seen: Dict[str, ConfigScope] = {}
    for read in paths or [Path(config_path).resolve()]:
        notes = _notes(confluid.document_notes, read)
        if notes is not None:
            _add_scopes(notes, seen, scopes)
    _add_aliases(raw, seen, scopes)
    return scopes


def _include_spellings(reads: List[Path]) -> Dict[Path, str]:
    """``{file: the include as written}`` for every file ``reads`` holds that another of them includes — the
    entry's ``include: pkg://other-project/config/base.yaml`` names that file ``pkg://other-project/config/base.yaml``.

    Read with :func:`confluid.locate.locate`, which resolves an include as :func:`confluid.load` does and imports
    nothing; a confluid without it, or a file it cannot read, leaves the file unnamed (its path is shown).
    """
    try:
        from confluid.locate import locate
    except ImportError:  # a confluid older than its go-to-definition reader
        return {}
    spellings: Dict[Path, str] = {}
    for read in reads:
        try:
            mentions = locate(read.read_text(encoding="utf-8"), read)
        except Exception:  # an unreadable file: its includes stay unnamed, never a failed TAB
            continue
        for mention in mentions:
            if mention.kind == "include" and mention.target is not None:
                spellings.setdefault(Path(mention.target.path).resolve(), mention.text)
    return spellings


def _add_scopes(notes: Any, seen: Dict[str, ConfigScope], scopes: List[ConfigScope]) -> None:
    """Append one file's scopes to ``scopes``: a scope activates for the whole document wherever it is declared, so an
    include's are the config's too; one declared again keeps its first place and gains a comment it lacked."""
    for scope in notes.scopes:
        if scope.activation not in seen:
            seen[scope.activation] = ConfigScope(scope.activation, scope.comment)
            scopes.append(seen[scope.activation])
        elif not seen[scope.activation].help:
            seen[scope.activation].help = scope.comment


def _add_aliases(raw: Any, seen: Dict[str, ConfigScope], scopes: List[ConfigScope]) -> None:
    """Append the document's ``scope_aliases:`` — its includes' too, merged into the raw document — after its scopes,
    each helped by what it activates; an alias named like a scope is that scope's row already."""
    aliases = raw.get("scope_aliases") if isinstance(raw, dict) else None
    if not isinstance(aliases, dict):
        return
    for name, targets in aliases.items():
        if not isinstance(name, str) or name in seen:
            continue
        names = targets if isinstance(targets, list) else [targets]
        seen[name] = ConfigScope(name, f"Alias of {', '.join(str(each) for each in names)}.")
        scopes.append(seen[name])


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
    refs: Dict[str, str],
    depth: int = 0,
    max_depth: int = 8,
    in_block: bool = False,
) -> None:
    """Every dotted key under ``obj`` into ``out``; ``targets`` gets each marker's path and the class it names,
    ``values`` the value of each marker parameter short enough to show, and ``refs`` the path each marker parameter
    written as a reference points at."""
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
            if in_block and type(v).__name__ == "Reference" and isinstance(getattr(v, "target", None), str):
                refs[full] = v.target
            _walk_keys(v, full, out, targets, values, refs, depth + 1, max_depth)
        return
    kwargs = getattr(obj, "kwargs", None)
    if isinstance(kwargs, dict):
        target = getattr(obj, "target", None)
        is_block = bool(prefix) and type(obj).__name__ != "Reference" and target is not None
        if is_block:
            targets[prefix] = (
                target if isinstance(target, str) else f"{target.__module__}.{getattr(target, '__qualname__', '')}"
            )
        _walk_keys(kwargs, prefix, out, targets, values, refs, depth + 1, max_depth, in_block=is_block)
