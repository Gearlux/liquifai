import inspect
from typing import Any, Dict, Optional, Set


def get_configurable_paths(obj: Any, prefix: str = "", visited: Optional[Set[int]] = None) -> Dict[str, Any]:
    """Recursively discover the configurable surface of an object as ``{dotted_path: live_value}``.

    Iteration is bounded to attributes the user actually declared:

    * Constructor parameters from ``inspect.signature(cls.__init__)`` (skipping
      ``self`` / ``cls`` / ``args`` / ``kwargs`` / ``name`` per the schema-walker
      convention).
    * Live instance attributes from :func:`confluid.get_configurable_attrs`
      — ``vars(obj)`` minus everything non-``@configurable`` ancestors
      contributed (class annotations like ``training: bool`` on
      ``torch.nn.Module``, class-level constants like
      ``CHECKPOINT_HYPER_PARAMS_KEY`` on
      ``pytorch_lightning.LightningModule``, and ``__init__``-body setattrs
      like ``self.prepare_data_per_node: bool = True`` on
      ``pytorch_lightning.core.hooks.DataHooks``). What's left is the
      ``@configurable`` class's own constructor params, its own post-init
      setattrs, and any post-construction setattrs done externally by
      Confluid's broadcast mechanism or the user's own code (the Enable
      wrapper pattern).

    :class:`confluid.Fluid` proxies (``Class`` / ``Lazy`` / ``Instance`` /
    ``Reference`` / ``Clone``) are handled specially: the walker descends into
    the underlying *target* class's hierarchy with values from
    ``fluid.kwargs`` rather than surfacing the Fluid's own ``target`` /
    ``kwargs`` scaffolding fields.

    At the top of the recursion (``prefix == ""``) the resulting paths are
    rewritten to their shortest unique trailing suffix via
    :func:`confluid.shortest_unique_paths`, so the noisy root-class prefix
    (e.g. ``LightningTrainer.``) is dropped unless it is needed to
    disambiguate two values.
    """
    if visited is None:
        visited = set()

    obj_id = id(obj)
    if obj_id in visited:
        return {}
    visited.add(obj_id)

    from confluid import Fluid, get_configurable_attrs, get_hierarchy, get_registry, shortest_unique_paths
    from confluid.registry import resolve_class

    reg = get_registry()
    cls = obj.__class__

    if not prefix:
        node_name = getattr(cls, "__confluid_name__", cls.__name__)
    else:
        node_name = getattr(obj, "name", None)
    current_prefix = f"{prefix}.{node_name}" if prefix and node_name else (node_name or prefix)

    paths: Dict[str, Any] = {}

    if isinstance(obj, Fluid):
        target_cls = resolve_class(obj.target)
        if isinstance(target_cls, type):
            hierarchy = get_hierarchy(target_cls)
            for h_path in hierarchy.keys():
                param_name = h_path.split(".", 1)[-1] if "." in h_path else h_path
                full_p = f"{current_prefix}.{param_name}" if current_prefix else param_name
                paths[full_p] = obj.kwargs.get(param_name)
        return _maybe_shorten(paths, prefix, shortest_unique_paths)

    try:
        sig = inspect.signature(cls.__init__)
        init_params = {p for p in sig.parameters if p not in ("self", "cls", "args", "kwargs", "name")}
    except (ValueError, TypeError):
        init_params = set()

    instance_attrs = get_configurable_attrs(obj)
    attrs_to_walk = init_params | instance_attrs

    for attr_name in attrs_to_walk:
        if attr_name.startswith("_"):
            continue
        try:
            attr_val = getattr(obj, attr_name, None)
            member = getattr(cls, attr_name, None)
            if member is not None and getattr(member, "__confluid_ignore__", False):
                continue
            if attr_val is not None and getattr(attr_val, "__confluid_ignore__", False):
                continue

            attr_prefix = f"{current_prefix}.{attr_name}" if current_prefix else attr_name

            if isinstance(attr_val, Fluid):
                # Walk the Fluid's target class with values from .kwargs, using
                # this attribute name (e.g. "optimizer", "train_loader") as the
                # path segment so siblings stay distinguishable.
                target_cls = resolve_class(attr_val.target)
                if isinstance(target_cls, type):
                    hierarchy = get_hierarchy(target_cls)
                    for h_path in hierarchy.keys():
                        param_name = h_path.split(".", 1)[-1] if "." in h_path else h_path
                        paths[f"{attr_prefix}.{param_name}"] = attr_val.kwargs.get(param_name)
            elif attr_val is not None and hasattr(attr_val.__class__, "__confluid_configurable__"):
                paths.update(get_configurable_paths(attr_val, current_prefix, visited))
            elif isinstance(attr_val, type) and reg.is_configurable(attr_val):
                hierarchy = get_hierarchy(attr_val)
                for h_path in hierarchy.keys():
                    param_name = h_path.split(".", 1)[-1] if "." in h_path else h_path
                    paths[f"{attr_prefix}.{param_name}"] = None
            elif attr_val is None or not callable(attr_val):
                paths[attr_prefix] = attr_val
        except Exception:
            continue

    return _maybe_shorten(paths, prefix, shortest_unique_paths)


def _maybe_shorten(paths: Dict[str, Any], prefix: str, shortener: Any) -> Dict[str, Any]:
    """Rewrite keys to their shortest unique suffix when we are at the top of the recursion."""
    if prefix:
        return paths
    short = shortener(list(paths.keys()))
    return {short[p]: v for p, v in paths.items()}
