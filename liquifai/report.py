from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

from liquifai.grammar import GLOBAL_FLAG_SPECS, flag_display


def show_configuration(
    target: Any,
    config_map: Optional[Dict[str, Any]] = None,
    title: str = "Available Configuration Options",
    layout: str = "table",
    positionals: Optional[List[str]] = None,
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

    ``positionals`` (the command's declared positional names, in order) are
    rendered as their own "Positional Arguments" block and EXCLUDED from the
    options — mirroring completion, which never offers a positional as its
    ``--flag`` spelling (the spelling still parses; it just isn't advertised).
    """
    from confluid import get_hierarchy, get_hierarchy_from_instance

    pos_names = list(positionals or [])
    _render_positionals(target, pos_names, layout)

    if _looks_like_flowed_graph(config_map):
        hierarchy = _drop_positional_paths(get_hierarchy_from_instance(config_map), pos_names)
        if layout == "lines":
            _render_lines(hierarchy, title, flowed=True)
        else:
            _render_flowed_table(hierarchy, title)
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

    for path in sorted_paths:
        short_path = display_map[path]
        type_str, default, doc = hierarchy[path]
        current_val = _get_from_config(config_map, path) if config_map else None
        display_val = current_val if current_val is not None else default
        val_str = str(display_val)
        if len(val_str) > 50:
            val_str = val_str[:47] + "..."
        table.add_row(f"--{short_path}", type_str, val_str, doc)
    console.print(table)


def _drop_positional_paths(hierarchy: Dict[str, Any], positionals: List[str]) -> Dict[str, Any]:
    """Remove entries rooted at a declared positional from an options hierarchy."""
    if not positionals:
        return hierarchy
    roots = set(positionals)
    return {p: v for p, v in hierarchy.items() if p.split(".", 1)[0] not in roots}


def _render_positionals(target: Any, positionals: List[str], layout: str) -> None:
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
        from rich.markup import escape

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
        table.add_row(f"<{p}>", str(type_str), doc or "")
    console.print(table)


def _render_flowed_table(hierarchy: Dict[str, Any], title: str) -> None:
    """Render the flowed-instance hierarchy with shortest-unique paths and a host-class column."""
    from confluid import shortest_unique_paths

    all_paths = list(hierarchy.keys())
    display_map = shortest_unique_paths(all_paths)

    console = Console()
    table = Table(title=title, box=None, show_header=True, header_style="bold cyan")
    table.add_column("Option", style="bold white")
    table.add_column("Applies to", style="cyan")
    table.add_column("Type", style="dim cyan")
    table.add_column("Current Value", style="green")
    table.add_column("Description", style="dim white")

    sorted_paths = sorted(all_paths, key=lambda p: (display_map[p].count("."), display_map[p]))

    for path in sorted_paths:
        short_path = display_map[path]
        type_str, value, doc = hierarchy[path]
        # Host class is the second-to-last segment of the full path. For a
        # path like "processor.DatasetProcessor.show_progress" the host is
        # "DatasetProcessor".
        parts = path.split(".")
        host = parts[-2] if len(parts) >= 2 else ""
        val_str = _short_repr(value)
        table.add_row(f"--{short_path}", host, type_str, val_str, doc)
    console.print(table)


def _render_lines(
    hierarchy: Dict[str, Any],
    title: str,
    *,
    flowed: bool,
    config_map: Optional[Dict[str, Any]] = None,
) -> None:
    """Render code-extracted option docs one option per line (the ``--docs`` view).

    Same data as the tables (``confluid.get_hierarchy`` → type / default-or-value /
    docstring), shortest-unique flag form, value/markup escaped. Aligned columns
    keep it readable while staying a single physical line per option (greppable).
    """
    from confluid import shortest_unique_paths
    from rich.markup import escape

    console = Console()
    console.print(f"\n[bold cyan]{title}[/bold cyan]")

    all_paths = list(hierarchy.keys())
    if not all_paths:
        console.print("[dim]  (no configurable options)[/dim]")
        return

    display_map = shortest_unique_paths(all_paths)
    sorted_paths = sorted(all_paths, key=lambda p: (display_map[p].count("."), display_map[p]))
    flags = {p: f"--{display_map[p]}" for p in sorted_paths}
    flag_w = max(len(f) for f in flags.values())
    type_w = max((len(hierarchy[p][0]) for p in sorted_paths), default=0)

    for path in sorted_paths:
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
        desc = f"[bold]Group:[/bold] {sub_app.description}" if sub_app.description else "Group."
        table.add_row(label, desc)

    for name, func in sorted(app._commands.items()):
        desc = func.__doc__.strip().split("\n")[0] if func.__doc__ else "No description."
        table.add_row(name, desc)

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
