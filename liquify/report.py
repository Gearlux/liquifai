from typing import Any, Dict, Optional

from rich.console import Console
from rich.table import Table


def show_configuration(
    target: Any, config_map: Optional[Dict[str, Any]] = None, title: str = "Available Configuration Options"
) -> None:
    """
    Display configuration options using the shortest possible unique paths.
    """
    from confluid import get_hierarchy

    # 1. Get the architectural hierarchy from Confluid
    hierarchy = get_hierarchy(target)
    all_paths = list(hierarchy.keys())

    # 2. Build the display map (Shortest Unique Path logic)
    display_map = {}
    for full_path in all_paths:
        parts = full_path.split(".")
        # Try suffixes of increasing length
        for i in range(1, len(parts) + 1):
            suffix = ".".join(parts[-i:])
            # Count how many other paths share this suffix
            matches = [p for p in all_paths if p.endswith(f".{suffix}") or p == suffix]
            if len(matches) == 1:
                display_map[full_path] = suffix
                break
        else:
            display_map[full_path] = full_path

    console = Console()
    table = Table(title=title, box=None, show_header=True, header_style="bold cyan")

    table.add_column("Option (Shortest Unique)", style="bold white")
    table.add_column("Type", style="dim cyan")
    table.add_column("Current/Default Value", style="green")

    # Sort by short path
    sorted_paths = sorted(all_paths, key=lambda p: (display_map[p].count("."), display_map[p]))

    for path in sorted_paths:
        short_path = display_map[path]
        type_str, default, doc = hierarchy[path]

        # Use live value if available in config_map, else use default
        current_val = _get_from_config(config_map, path) if config_map else None
        display_val = current_val if current_val is not None else default

        val_str = str(display_val)
        if len(val_str) > 50:
            val_str = val_str[:47] + "..."

        table.add_row(f"--{short_path}", type_str, val_str)

    console.print(table)


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
