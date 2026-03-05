from typing import Any, Dict, Optional, Set


def get_configurable_paths(obj: Any, prefix: str = "", visited: Optional[Set[int]] = None) -> Dict[str, Any]:
    """
    Recursively discover all configurable paths in an object hierarchy.
    Returns a mapping of dotted_path -> current_value.

    Path rules:
    - Root: Class Name
    - Intermediate: .name attribute if defined, otherwise skipped.
    - Leaf: Attribute name.
    """
    if visited is None:
        visited = set()

    obj_id = id(obj)
    if obj_id in visited:
        return {}
    visited.add(obj_id)

    paths = {}
    cls = obj.__class__

    # 1. Determine the name for this node
    # Root starts with class name.
    # Sub-objects use their .name if available.
    if not prefix:
        node_name = getattr(cls, "__confluid_name__", cls.__name__)
    else:
        node_name = getattr(obj, "name", None)

    current_prefix = f"{prefix}.{node_name}" if prefix and node_name else (node_name or prefix)

    # 2. Inspect attributes
    for attr_name in dir(obj):
        if attr_name.startswith("_"):
            continue

        try:
            attr_val = getattr(obj, attr_name)

            # Check visibility markers
            member = getattr(cls, attr_name, None)
            if member and getattr(member, "__confluid_ignore__", False):
                continue
            if getattr(attr_val, "__confluid_ignore__", False):
                continue

            full_path = f"{current_prefix}.{attr_name}" if current_prefix else attr_name

            if hasattr(attr_val.__class__, "__confluid_configurable__"):
                # Recurse into configurable sub-objects
                paths.update(get_configurable_paths(attr_val, current_prefix, visited))
            elif not callable(attr_val):
                # Leaf attribute
                paths[full_path] = attr_val

        except Exception:
            continue

    return paths
