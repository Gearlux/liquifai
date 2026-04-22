from typing import Any, Dict, Optional

from rich.console import Console
from rich.table import Table


def show_configuration(
    target: Any,
    config_map: Optional[Dict[str, Any]] = None,
    title: str = "Available Configuration Options",
) -> None:
    """Display configuration options using the shortest possible unique paths.

    Two modes:

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
    """
    from confluid import get_hierarchy, get_hierarchy_from_instance

    if _looks_like_flowed_graph(config_map):
        hierarchy = get_hierarchy_from_instance(config_map)
        _render_flowed_table(hierarchy, title)
        return

    # Static-type path (legacy behaviour)
    hierarchy = get_hierarchy(target)
    all_paths = list(hierarchy.keys())
    display_map = _shortest_unique_paths(all_paths)

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


def _render_flowed_table(hierarchy: Dict[str, Any], title: str) -> None:
    """Render the flowed-instance hierarchy with shortest-unique paths and a host-class column."""
    all_paths = list(hierarchy.keys())
    display_map = _shortest_unique_paths(all_paths)

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


def _shortest_unique_paths(all_paths: list) -> Dict[str, str]:
    """For each full path, pick the shortest trailing dotted-suffix that is unique across ``all_paths``."""
    display_map: Dict[str, str] = {}
    for full_path in all_paths:
        parts = full_path.split(".")
        for i in range(1, len(parts) + 1):
            suffix = ".".join(parts[-i:])
            matches = [p for p in all_paths if p.endswith(f".{suffix}") or p == suffix]
            if len(matches) == 1:
                display_map[full_path] = suffix
                break
        else:
            display_map[full_path] = full_path
    return display_map


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
