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
    from confluid import Fluid, get_registry

    reg = get_registry()

    for attr_name in dir(obj):
        if attr_name.startswith("_"):
            continue

        try:
            attr_val = getattr(obj, attr_name)

            # Check visibility markers on class member and instance value
            member = getattr(cls, attr_name, None)
            if member and getattr(member, "__confluid_ignore__", False):
                continue
            if getattr(attr_val, "__confluid_ignore__", False):
                continue

            # 1. Handle Configurable Instances (Existing logic)
            if hasattr(attr_val.__class__, "__confluid_configurable__"):
                paths.update(get_configurable_paths(attr_val, current_prefix, visited))

            # 2. Handle Deferred Classes (type objects registered in Confluid)
            elif isinstance(attr_val, type) and reg.is_configurable(attr_val):
                from confluid import get_hierarchy

                hierarchy = get_hierarchy(attr_val)
                for h_path, _ in hierarchy.items():
                    # Hierarchy returns 'ClassName.param', we want 'current_prefix.param'
                    # Strip the ClassName and prepend current_prefix
                    param_name = h_path.split(".", 1)[-1] if "." in h_path else h_path
                    full_p = f"{current_prefix}.{param_name}" if current_prefix else param_name
                    paths[full_p] = None  # Value is unknown for a class-only default

            # 3. Handle Fluid Proxies
            elif isinstance(attr_val, Fluid):
                # Recurse into the Fluid's target class
                from confluid import get_hierarchy

                target = attr_val.target
                cls_to_inspect = reg.get_class(target) if isinstance(target, str) else target

                if cls_to_inspect:
                    hierarchy = get_hierarchy(cls_to_inspect)
                    for h_path, _ in hierarchy.items():
                        param_name = h_path.split(".", 1)[-1] if "." in h_path else h_path
                        full_p = f"{current_prefix}.{param_name}" if current_prefix else param_name
                        # Use Fluid's kwarg value if present, otherwise None
                        paths[full_p] = attr_val.kwargs.get(param_name)

            elif not callable(attr_val):
                # Leaf attribute
                full_path = f"{current_prefix}.{attr_name}" if current_prefix else attr_name
                paths[full_path] = attr_val

        except Exception:
            continue

    return paths
