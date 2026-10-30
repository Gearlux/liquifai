from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Set, Tuple

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from liquifai.completion.config_help import ConfigHelp
from liquifai.grammar import GLOBAL_FLAG_SPECS, flag_display

#: How a command's options are rendered: the Rich grid (``--help``) or one
#: greppable line per option (``--docs``). Closed so a typo can't silently
#: fall through to the table branch. Mirrored as ``core.HelpLayout``.
HelpLayout = Literal["table", "lines"]


def show_configuration(
    target: Any,
    config_map: Optional[Dict[str, Any]] = None,
    title: str = "Available Configuration Options",
    layout: HelpLayout = "table",
    positionals: Optional[List[str]] = None,
    shorts: Optional[Dict[str, str]] = None,
    hidden: Optional[List[str]] = None,
    config_help: Optional[ConfigHelp] = None,
) -> None:
    """Display configuration options using the shortest possible unique paths.

    Two data sources:

    * **Static-type view** (``config_map`` is None or a plain mapping of
      already-live values): walks ``target``'s type annotations via
      :func:`confluid.get_hierarchy` — same behaviour as before.
    * **Flowed-graph view** (``config_map`` is a dict returned by
      :meth:`liquifai.core.LiquifyApp.liquify`): walks the concrete live
      instances produced by DI and enumerates every configurable kwarg
      reachable through them via
      :func:`confluid.get_hierarchy_from_instance`. Surfaces defaults the
      user didn't set in YAML plus post-construction setattr keys (e.g.
      Enable.visualize).

    ``layout`` chooses the renderer:

    * ``"table"`` (default): the Rich grid.
    * ``"lines"``: one option per physical line (``--flag  type  = value  doc``),
      aligned and greppable / pipe-friendly — the ``--docs`` rendering. The
      extracted documentation is identical; only the presentation differs.

    ``shorts`` (``{letter: parameter}``, from the command's ``short=``) is rendered beside the
    long spelling — ``-b, --background``. An option the user cannot see is an option they cannot
    use, which is the whole reason this column exists.

    ``positionals`` (the command's declared positional names, in order) are
    rendered as their own "Positional Arguments" block and EXCLUDED from the
    options — mirroring completion, which never offers a positional as its
    ``--flag`` spelling (the spelling still parses; it just isn't advertised).
    ``hidden`` parameters are excluded the same way without a block of their own
    (a ``config_files=`` command's ``scopes``, filled from ``--scope``).

    Every text a command's author wrote (docs, types, values) is printed ESCAPED: help is
    rendered through Rich markup, where ``[epochs=1]`` reads as a style tag and vanishes.

    ``config_help`` (what the config on the command line says about its settings) puts the
    file's own top-level keys and its scopes first, each in a block of its own, in file order —
    the groups a TAB listing shows; a flowed option those keys spell is left out of the table
    below them. A list of objects is one row (``--datasets``), never a row per field of each item.
    """
    from confluid import get_hierarchy, get_hierarchy_from_instance

    pos_names = list(positionals or [])
    _render_positionals(target, pos_names, layout)
    pos_names += list(hidden or [])
    if config_help is not None:
        _render_config_help(config_help, layout)

    if _looks_like_flowed_graph(config_map):
        hierarchy = _drop_positional_paths(get_hierarchy_from_instance(config_map), pos_names)
        hierarchy = _fold_list_items(hierarchy, config_map)
        skip = {key.path for key in config_help.focused} if config_help is not None else set()
        if config_help is not None and config_help.fed:  # a parameter a top-level key feeds is that key's row
            hierarchy = {
                path: entry for path, entry in hierarchy.items() if tuple(path.split(".")[-2:]) not in config_help.fed
            }
        if layout == "lines":
            _render_lines(hierarchy, title, flowed=True, skip=skip, root=config_map)
        else:
            _render_flowed_table(hierarchy, title, skip=skip, root=config_map)
        return

    if layout == "lines":
        _render_lines(
            _drop_positional_paths(get_hierarchy(target), pos_names), title, flowed=False, config_map=config_map
        )
        return

    # Static-type path (legacy behaviour)
    from confluid import shortest_unique_paths

    hierarchy = _drop_positional_paths(get_hierarchy(target), pos_names)
    all_paths = list(hierarchy.keys())
    display_map = shortest_unique_paths(all_paths)

    console = Console()
    table = Table(title=title, box=None, show_header=True, header_style="bold cyan")
    table.add_column("Option (Shortest Unique)", style="bold white")
    table.add_column("Type", style="dim cyan")
    table.add_column("Current/Default Value", style="green")
    table.add_column("Documentation", style="dim white")

    sorted_paths = sorted(all_paths, key=lambda p: (display_map[p].count("."), display_map[p]))
    by_param = {param: letter for letter, param in (shorts or {}).items()}

    for path in sorted_paths:
        short_path = display_map[path]
        type_str, default, doc = hierarchy[path]
        current_val = _get_from_config(config_map, path) if config_map else None
        display_val = current_val if current_val is not None else default
        val_str = str(display_val)
        if len(val_str) > 50:
            val_str = val_str[:47] + "..."
        letter = by_param.get(short_path)
        spelling = f"-{letter}, --{short_path}" if letter else f"--{short_path}"
        table.add_row(escape(spelling), escape(str(type_str)), escape(val_str), escape(doc or ""))
    console.print(table)


def _drop_positional_paths(hierarchy: Dict[str, Any], positionals: List[str]) -> Dict[str, Any]:
    """Remove entries rooted at a declared positional from an options hierarchy."""
    if not positionals:
        return hierarchy
    roots = set(positionals)
    return {p: v for p, v in hierarchy.items() if p.split(".", 1)[0] not in roots}


def _render_positionals(target: Any, positionals: List[str], layout: HelpLayout) -> None:
    """Render the command's positional arguments as their own block.

    Type/doc come from the SAME :func:`confluid.get_hierarchy` extraction the
    options use (a positional literally named ``name`` is skipped by
    ``get_hierarchy`` — the confluid instance-identity key — so it renders with
    no type/doc). Shown before the options in BOTH layouts so ``--help`` and
    ``--docs`` stay data-identical.
    """
    if not positionals:
        return
    from confluid import get_hierarchy

    try:
        hierarchy = get_hierarchy(target)
    except Exception:
        hierarchy = {}

    console = Console()
    if layout == "lines":
        console.print("\n[bold cyan]Positional Arguments[/bold cyan]")
        name_w = max(len(p) + 2 for p in positionals)
        type_w = max((len(str(hierarchy[p][0])) for p in positionals if p in hierarchy), default=0)
        for p in positionals:
            type_str, _default, doc = hierarchy.get(p, ("", None, ""))
            console.print(
                f"  [bold white]{f'<{p}>'.ljust(name_w)}[/bold white]"
                f"  [dim cyan]{escape(str(type_str)).ljust(type_w)}[/dim cyan]  [dim]{escape(doc or '')}[/dim]"
            )
        return

    table = Table(title="Positional Arguments", box=None, show_header=True, header_style="bold cyan")
    table.add_column("Argument", style="bold white")
    table.add_column("Type", style="dim cyan")
    table.add_column("Documentation", style="dim white")
    for p in positionals:
        type_str, _default, doc = hierarchy.get(p, ("", None, ""))
        table.add_row(escape(f"<{p}>"), escape(str(type_str)), escape(doc or ""))
    console.print(table)


def _render_config_help(config_help: ConfigHelp, layout: HelpLayout) -> None:
    """The config's own top-level keys, then its scopes — each with its help, in the file's order."""
    console = Console()
    source = config_help.source
    if layout == "lines":
        if config_help.focused:
            console.print(f"\n[bold cyan]Options of {escape(source)}[/bold cyan]")
            flag_w = max(len(key.path) + 2 for key in config_help.focused)
            for key in config_help.focused:
                value = f"[green]= {escape(key.value)}[/green]  " if key.value else ""
                console.print(
                    f"  [bold white]{f'--{key.path}'.ljust(flag_w)}[/bold white]  {value}[dim]{escape(key.help)}[/dim]"
                )
        if config_help.scopes:
            console.print(f"\n[bold cyan]Scopes of {escape(source)}[/bold cyan]")
            scope_w = max(len(scope.activation) + 3 for scope in config_help.scopes)
            for scope in config_help.scopes:
                spelling = f"-s {scope.activation}".ljust(scope_w)
                console.print(f"  [bold white]{escape(spelling)}[/bold white]  [dim]{escape(scope.help)}[/dim]")
        return
    if config_help.focused:
        table = Table(title=f"Options of {escape(source)}", box=None, show_header=True, header_style="bold cyan")
        table.add_column("Option", style="bold white")
        table.add_column("Current Value", style="green")
        table.add_column("Description", style="dim white")
        for key in config_help.focused:
            table.add_row(escape(f"--{key.path}"), escape(key.value), escape(key.help))
        console.print(table)
    if config_help.scopes:
        table = Table(title=f"Scopes of {escape(source)}", box=None, show_header=True, header_style="bold cyan")
        table.add_column("Scope", style="bold white")
        table.add_column("Description", style="dim white")
        for scope in config_help.scopes:
            table.add_row(escape(f"-s {scope.activation}"), escape(scope.help))
        console.print(table)


def _fold_list_items(hierarchy: Dict[str, Any], root: Any) -> Dict[str, Any]:
    """Each list (or dict) of objects as ONE row — the parameter holding it, its value and its help.

    The instance walk reports a field per item (``….datasets.0.MelodyDatasetFiles.dataset``); the
    option a user types is the parameter (``--datasets``), whose help is its class's docstring.
    """
    from confluid import parse_param_docs

    folded: Dict[str, Any] = {}
    for path, entry in hierarchy.items():
        parts = path.split(".")
        cut = next((i for i, part in enumerate(parts) if i >= 2 and part.isdigit()), None)
        if cut is None:
            folded[path] = entry
            continue
        head = ".".join(parts[:cut])
        if head in folded:
            continue
        host, value = _live_parameter(root, parts[:cut])
        doc = parse_param_docs(type(host)).get(parts[cut - 1], "") if host is not None else ""
        folded[head] = (type(value).__name__, value, doc)
    return folded


def _live_parameter(root: Any, parts: List[str]) -> Tuple[Any, Any]:
    """``(the object holding the parameter, its value)`` at an instance-walk path, or ``(None, None)``.

    A segment naming the object itself (its class or instance name, as the walk writes it) is
    skipped; every other one steps into a mapping key, a list index or an attribute.
    """
    host: Any = None
    current = root
    for part in parts:
        if isinstance(current, dict):
            host, current = None, current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            host, current = None, current[int(part)] if int(part) < len(current) else None
        elif current is None:
            return None, None
        elif part in (type(current).__name__, getattr(current, "name", None)) and host is not current:
            host = current
        else:
            host, current = current, getattr(current, part, None)
    return host, current


def _render_flowed_table(
    hierarchy: Dict[str, Any], title: str, skip: Optional[Set[str]] = None, root: Any = None
) -> None:
    """Render the flowed-instance hierarchy with shortest-unique paths: a table per object, the shallowest first.

    Each table is titled by the object's path and class (``runnable (Builder)``), as the TAB listing's groups are.
    A path whose shortest spelling is in ``skip`` (a key the config's own block shows) is left out."""
    from confluid import shortest_unique_paths

    display_map = shortest_unique_paths(list(hierarchy.keys()))
    all_paths = [path for path in hierarchy if display_map[path] not in (skip or set())]

    console = Console()
    console.print(f"[bold cyan]{escape(title)}[/bold cyan]")
    for owner, paths in _by_owner(all_paths, display_map, root):
        table = Table(title=escape(owner), box=None, show_header=True, header_style="bold cyan")
        table.add_column("Option", style="bold white")
        table.add_column("Type", style="dim cyan")
        table.add_column("Current Value", style="green")
        table.add_column("Description", style="dim white")
        for path in paths:
            type_str, value, doc = hierarchy[path]
            table.add_row(
                escape(f"--{display_map[path]}"), escape(str(type_str)), escape(_short_repr(value)), escape(doc or "")
            )
        console.print(table)


def _by_owner(paths: List[str], display_map: Dict[str, str], root: Any) -> List[Tuple[str, List[str]]]:
    """``paths`` grouped by the object each is a parameter of — ``(title, paths)``, the shallowest object first,
    then in the walk's order; each group's paths in flag order."""
    groups: Dict[str, List[str]] = {}
    depth: Dict[str, int] = {}
    for path in paths:
        owner, keys = _owner_title(root, path.split(".")[:-1])
        groups.setdefault(owner, []).append(path)
        depth.setdefault(owner, keys)
    order = {owner: index for index, owner in enumerate(groups)}
    return [
        (owner, sorted(groups[owner], key=lambda p: display_map[p]))
        for owner in sorted(groups, key=lambda o: (depth[o], order[o]))
    ]


def _owner_title(root: Any, parts: List[str]) -> Tuple[str, int]:
    """``("<path> (<class>)", depth)`` of the object at an instance-walk path, its class segments taken out.

    The walk writes an object's class (or instance) name after the key that holds it
    (``runnable.Builder.render.RenderViews``); the title is the keys a config writes, ``runnable.render
    (RenderViews)``, and the depth is how many there are."""
    keys: List[str] = []
    name = ""
    current = root
    for part in parts:
        if isinstance(current, dict):
            keys.append(part)
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            keys.append(part)
            current = current[int(part)] if int(part) < len(current) else None
        elif current is not None and (part == type(current).__name__ or not hasattr(current, part)):
            name = part  # the object's own segment
        else:
            keys.append(part)
            current = getattr(current, part, None)
    path = ".".join(keys)
    return (f"{path} ({name})" if path and name else path or name), len(keys)


def _render_lines(
    hierarchy: Dict[str, Any],
    title: str,
    *,
    flowed: bool,
    config_map: Optional[Dict[str, Any]] = None,
    skip: Optional[Set[str]] = None,
    root: Any = None,
) -> None:
    """Render code-extracted option docs one option per line (the ``--docs`` view).

    Same data as the tables (``confluid.get_hierarchy`` → type / default-or-value /
    docstring), shortest-unique flag form, value/markup escaped. Aligned columns
    keep it readable while staying a single physical line per option (greppable).
    A flowed graph (``root``) is listed per object under its title, as the tables are.
    """
    from confluid import shortest_unique_paths

    console = Console()
    console.print(f"\n[bold cyan]{title}[/bold cyan]")

    display_map = shortest_unique_paths(list(hierarchy.keys()))
    all_paths = [path for path in hierarchy if display_map[path] not in (skip or set())]
    if not all_paths:
        console.print("[dim]  (no configurable options)[/dim]")
        return

    if root is not None:
        grouped = _by_owner(all_paths, display_map, root)
    else:
        grouped = [("", sorted(all_paths, key=lambda p: (display_map[p].count("."), display_map[p])))]
    flags = {p: f"--{display_map[p]}" for p in all_paths}
    flag_w = max(len(f) for f in flags.values())
    type_w = max((len(hierarchy[p][0]) for p in all_paths), default=0)

    for owner, group in grouped:
        if owner:
            console.print(f"[cyan]{escape(owner)}[/cyan]")
        _render_line_rows(console, hierarchy, group, flags, flag_w, type_w, flowed, config_map)


def _render_line_rows(
    console: Console,
    hierarchy: Dict[str, Any],
    paths: List[str],
    flags: Dict[str, str],
    flag_w: int,
    type_w: int,
    flowed: bool,
    config_map: Optional[Dict[str, Any]],
) -> None:
    """One ``--docs`` line per path: the flag, its type, its value, its help."""
    for path in paths:
        type_str, default_or_value, doc = hierarchy[path]
        if flowed:
            value: Any = default_or_value
        else:
            current = _get_from_config(config_map, path) if config_map else None
            value = current if current is not None else default_or_value
        val_str = escape(_short_repr(value, limit=40))
        flag = flags[path].ljust(flag_w)
        type_disp = escape(str(type_str)).ljust(type_w)
        doc_disp = escape(doc) if doc else ""
        console.print(
            f"  [bold white]{flag}[/bold white]  [dim cyan]{type_disp}[/dim cyan]"
            f"  [green]= {val_str}[/green]  [dim]{doc_disp}[/dim]"
        )


def _looks_like_flowed_graph(config_map: Any) -> bool:
    """Heuristic: a flowed graph is a dict whose values include at least one user-class instance
    (i.e., not purely primitives / None / plain containers)."""
    if not isinstance(config_map, dict):
        return False
    for v in config_map.values():
        if v is None:
            continue
        if isinstance(v, (str, bytes, int, float, bool, list, tuple, dict, set)):
            continue
        return True
    return False


def _short_repr(value: Any, limit: int = 50) -> str:
    val_str = repr(value) if isinstance(value, str) else str(value)
    if len(val_str) > limit:
        val_str = val_str[: limit - 3] + "..."
    return val_str


def _get_from_config(config: Dict[str, Any], path: str) -> Any:
    """Helper to get a value from nested config using dotted path."""
    parts = path.split(".")
    current = config
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def show_command_index(app: Any, console: Console) -> None:
    """Render the top-level command/group index table (the no-target help view).

    Alias rows fold into their canonical group. This is the counterpart to
    :func:`show_configuration` (which renders a *selected* command's options):
    together they keep ALL Rich help rendering in this module, so
    :meth:`liquifai.core.LiquifyApp._show_help` stays pure orchestration.

    ``app`` is a ``LiquifyApp`` (typed ``Any`` to avoid a circular import — core
    imports this module).
    """
    table = Table(box=None, padding=(0, 2))
    table.add_column("Command/Group", style="cyan")
    table.add_column("Description")

    for name, sub_app in sorted(app._sub_apps.items()):
        if name in app._sub_app_aliases:
            continue  # alias rows fold into their canonical group below
        aliases = sorted(a for a, canon in app._sub_app_aliases.items() if canon == name)
        label = f"{name} ({', '.join(aliases)})" if aliases else name
        desc = f"[bold]Group:[/bold] {escape(sub_app.description)}" if sub_app.description else "Group."
        table.add_row(escape(label), desc)

    for name, func in sorted(app._commands.items()):
        desc = func.__doc__.strip().split("\n")[0] if func.__doc__ else "No description."
        table.add_row(escape(name), escape(desc))

    console.print(table)


def show_global_options(console: Console) -> None:
    """Render the Global Options block from the ONE grammar source of truth.

    Rendered from ``grammar.GLOBAL_FLAG_SPECS`` — the same table the parser and
    completion derive from — so ``--help`` can never drift from what the CLI
    actually accepts. Hidden specs (internal plumbing) are excluded.
    """
    console.print("\n[bold]Global Options:[/bold]")
    visible = [spec for spec in GLOBAL_FLAG_SPECS if not spec.hidden]
    width = max(len(flag_display(spec)) for spec in visible)
    for spec in visible:
        console.print(f"  {flag_display(spec).ljust(width)}  {spec.help}")
    console.print(f"  {'--KEY VAL'.ljust(width)}  Implicit per-dimension flag for any `!scope:KEY=…` block")
    console.print(f"  {''.ljust(width)}  declared in the YAML (e.g. `--task classification`).")
    console.print("")


def show_config_scopes(provider: Callable[..., Any], typed: Mapping[str, str], *, console: Console) -> None:
    """Render the "Scopes" block of a ``@command(config_files=...)`` command: the scopes of the files it runs.

    The files are the provider's for the words typed on the help line (``pictures all a --help`` reads ``a``'s), read
    here, live (help is no hot path): one line per scope with the files declaring it. A provider or file that fails
    leaves a ``<scope>`` placeholder and its reason, and the rest of the help still renders.
    """
    from liquifai import config_scopes

    console.print("\n[bold]Scopes[/bold] (-s, --scope NAME — of the config this command runs):")
    try:
        files = config_scopes.files_for(provider, typed)
        found = config_scopes.offered(files)
    except Exception as exc:  # the help must render whatever reading the files needs
        console.print(f"  <scope>  [dim]scopes unavailable: {escape(str(exc))}[/dim]")
        return
    if not found:
        named = ", ".join(config_scopes.display(f) for f in files) or "no file"
        console.print(f"  [dim](none declared in {escape(named)})[/dim]")
        return
    width = max(len(name) for name in found)
    present = [f for f in files if f.is_file()]
    for name, where in found.items():
        if len(present) > 1 and len(where) == len(present):
            said = f"every config ({len(where)} files)"
        else:
            said = ", ".join(where[:3]) + (f" (+{len(where) - 3} more)" if len(where) > 3 else "")
        console.print(f"  {escape(name).ljust(width)}  [dim]{escape(said)}[/dim]")


#: How many values a slot shows in help before it counts the rest.
SLOT_VALUES_SHOWN = 20


def show_slot_values(rows: List[Any], *, app_name: str, console: Console) -> None:
    """Render the "Values" block: per slot (``<positional>`` or ``--option``), the values TAB offers it.

    ``rows`` are ``(slot, values)``, read from the completion cache by the caller. Empty rows → the cache is not
    filled yet, and the line says how to fill it.
    """
    console.print(
        f"\n[bold]Values[/bold] (what TAB offers; [dim]{escape(app_name)} --refresh-completions[/dim] refreshes them):"
    )
    if not rows:
        console.print(f"  [dim](not cached yet — run: {escape(app_name)} --refresh-completions)[/dim]")
        return
    width = max(len(slot) for slot, _ in rows)
    for slot, values in rows:
        shown = " | ".join(values[:SLOT_VALUES_SHOWN])
        more = f" (+{len(values) - SLOT_VALUES_SHOWN} more)" if len(values) > SLOT_VALUES_SHOWN else ""
        console.print(f"  {escape(slot).ljust(width)}  {escape(shown)}[dim]{more}[/dim]")


def show_scope_dimensions(raw_config: Any, *, console: Console, source: str) -> None:
    """Render the Scope Dimensions block: what the named config's blocks offer, and the default.

    One line per keyed dimension the RAW document declares (``!scope:KEY=VAL`` blocks —
    the same walk :func:`liquifai.flags.bind_dimension_flags` uses to bind ``--KEY VAL``),
    its values sorted, and ``(default: X)`` when the document's ``default_scopes:`` names
    one — so ``--help`` shows what a bare run picks, not only what a flag could pick.
    A dimension declared only by ``!notscope:`` blocks offers nothing to select (confluid
    reports an empty set) and is shown with a ``<value>`` placeholder. A document with no
    dimensions prints nothing at all.

    Reads confluid lazily like :func:`show_configuration`: this module must import on a
    ``--help`` for an app whose command has no config.
    """
    from confluid import default_scopes, discover_dimension_values

    dimensions = discover_dimension_values(raw_config)
    if not dimensions:
        return
    defaults = default_scopes(raw_config)
    console.print(f"\n[bold]Scope Dimensions[/bold] (from {source}):")
    flags = {dim: f"--{dim} <{'|'.join(sorted(values)) or 'value'}>" for dim, values in dimensions.items()}
    width = max(len(f) for f in flags.values())
    for dim in sorted(flags):
        default = f"(default: {defaults[dim]})" if dim in defaults else ""
        console.print(f"  {flags[dim].ljust(width)}  {default}".rstrip())
    console.print("")
