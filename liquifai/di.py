"""Dependency injection: resolve a command's parameters from the loaded config.

Extracted from ``core.py`` in the consolidation split: this module owns the
annotation-driven resolution (:func:`resolve_kwargs`), the recursive Fluid
flowing walker (:func:`deep_flow`), and the confluid active-context shim
(:func:`confluid_active_context`). ``core.py`` re-exports the historical
underscore-prefixed names for existing callers (fluxstudio, tests).
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, Optional, Set

import confluid
from confluid import active_context, flow, get_registry, materialize
from confluid.fluid import Fluid
from confluid.fluid import Lazy as LazyFluid
from confluid.lazy import lazy_param_names

if TYPE_CHECKING:
    from liquifai.context import LiquifyContext


def resolve_kwargs(context: "LiquifyContext", func: Callable[..., Any]) -> Dict[str, Any]:
    """DI-resolve ``func``'s parameters against ``context.config_data``.

    Shared between :meth:`liquifai.core.LiquifyApp.run_command` and
    :meth:`liquifai.core.LiquifyApp.liquify` — the latter needs the same live
    instances DI would produce, but without actually invoking the command.
    """
    context.logger.debug(f"DI: Resolving arguments for {func.__name__}")
    # config_data may be a Fluid when the YAML's root is a single
    # `!class:` document — guard the introspection so DI stays usable
    # for commands that don't depend on top-level keys.
    cfg = context.config_data
    cfg_keys = list(cfg.keys()) if isinstance(cfg, dict) else "<root-Fluid>"
    context.logger.trace(f"DI: Global config keys: {cfg_keys}")

    reg = get_registry()
    sig = inspect.signature(func)
    kwargs: Dict[str, Any] = {}

    for name, param in sig.parameters.items():
        if reg.is_configurable(param.annotation):
            cls_name = getattr(param.annotation, "__confluid_name__", param.annotation.__name__)
            if isinstance(cfg, dict):
                # Membership checks, NOT truthiness: a present-but-empty block
                # (``trainer: {}`` or YAML-null ``trainer:``) means "construct
                # with defaults". The old ``cfg.get(a) or cfg.get(b) or cfg``
                # chain falsy-fell-through an empty block and splatted the
                # ENTIRE top-level config into the instance's kwargs.
                if cls_name in cfg:
                    config_block = cfg[cls_name]
                elif name in cfg:
                    config_block = cfg[name]
                else:
                    # Whole-config fallback — load-bearing for flat configs
                    # (top-level keys broadcast into the injected class).
                    config_block = cfg
                if config_block is None:
                    config_block = {}
            else:
                # Root-level Fluid: there is no surrounding dict to look
                # up by class- or param-name, so the Fluid itself is the
                # candidate block.
                config_block = cfg

            context.logger.debug(
                f"DI: Resolving {name} ({cls_name}). Block keys: "
                f"{list(config_block.keys()) if isinstance(config_block, dict) else 'N/A'}"
            )

            if isinstance(config_block, Fluid):
                # User wrote `name: !class:...` — the Fluid already carries
                # the full kwargs; materialize it directly so its payload
                # isn't discarded by the synthesized-Instance path below.
                kwargs[name] = materialize(config_block, context=context.config_data)
            else:
                # Synthesize an Instance Fluid for the annotated class
                # (kwargs assigned post-construction so a config key
                # literally named ``target`` can't collide with the Fluid
                # ctor's own parameter). Confluid's IR is Fluid objects —
                # the legacy ``{"_confluid_class_": ...}`` marker dicts
                # are gone.
                instance = confluid.Instance(cls_name)
                if isinstance(config_block, dict):
                    instance.kwargs.update(config_block)
                kwargs[name] = materialize(instance, context=context.config_data)
        else:
            # Non-configurable: Resolve from context data or use default
            if isinstance(cfg, dict) and name in cfg:
                kwargs[name] = cfg[name]
            elif param.default is not inspect.Parameter.empty:
                kwargs[name] = param.default

    return kwargs


@contextmanager
def confluid_active_context(context_data: Dict[str, Any]) -> Iterator[None]:
    """Activate confluid's context so bare ``flow()`` resolves ``!ref:``.

    ``materialize()`` already does this internally, but liquifai's deep-flow
    runs *after* :func:`resolve_kwargs` has returned (with confluid's context
    restored). For non-configurable parameters whose YAML values contain
    nested ``!ref:`` markers, we need the context active again during the
    deep-flow walk — otherwise references silently fail to resolve.

    Thin wrapper over the public :func:`confluid.active_context` (which this
    helper predates — it used to reach into confluid's engine state directly).
    Kept under its historical name (``core._confluid_active_context``) for
    existing callers/tests.
    """
    with active_context(context_data):
        yield


def deep_flow(value: Any, _visited: Optional[Set[int]] = None) -> Any:
    """Recursively flow any ``Fluid`` stubs embedded in ``value``.

    Walks lists, tuples, dicts, and live instances' ``vars()``; any attribute
    that is still a ``Fluid`` is replaced in-place with the flowed instance.
    Cycle-safe via ``id(obj)`` tracking. Primitives pass through unchanged.

    Skips dunder attrs (``__*__``) on instances — those are framework
    bookkeeping (e.g. confluid's ``__confluid_kwargs__`` round-trip mirror,
    Python internals) that shouldn't be re-flowed by an external walker.
    Honors :func:`confluid.lazy.lazy_param_names` to leave attrs marked
    ``Lazy[T]`` deferred.

    ``confluid.fluid.Lazy`` (YAML ``!lazy:``) Fluids are likewise left
    deferred at every level — they are runtime-injection points (e.g. an
    optimizer needing ``params=model.parameters()``) and must be flowed
    later by domain code with the missing runtime kwargs.
    """
    if _visited is None:
        _visited = set()

    if isinstance(value, LazyFluid):
        return value

    if isinstance(value, Fluid):
        return deep_flow(flow(value), _visited)

    if isinstance(value, (list, tuple)):
        out = [deep_flow(v, _visited) for v in value]
        if isinstance(value, tuple):
            # NamedTuple subclasses take their fields as POSITIONAL args, not
            # as a single iterable. Without the splat, e.g.
            # ``Sample([input, target, metadata])`` wraps the entire triplet
            # into the ``input`` field with target/metadata at their defaults
            # — silently breaking any dataset whose elements are NamedTuples
            # (most notably ``sampleflux.sample.Sample``).
            if hasattr(type(value), "_fields"):
                return type(value)(*out)
            return type(value)(out)
        return out

    if type(value) is dict:
        return {k: deep_flow(v, _visited) for k, v in value.items()}

    # Live instance: walk its __dict__ and replace any Fluid attrs in place.
    if hasattr(value, "__dict__") and not isinstance(value, type):
        vid = id(value)
        if vid in _visited:
            return value
        _visited.add(vid)

        lazy = lazy_param_names(type(value))
        for attr_name, attr_value in list(vars(value).items()):
            if attr_name.startswith("__") and attr_name.endswith("__"):
                continue  # framework bookkeeping (e.g. __confluid_kwargs__)
            if attr_name in lazy:
                continue  # honor Lazy[T]: leave runtime-injection attrs deferred
            if isinstance(attr_value, LazyFluid):
                continue  # YAML !lazy: stays deferred even without the Lazy[T] mirror
            resolved = deep_flow(attr_value, _visited)
            if resolved is not attr_value:
                try:
                    setattr(value, attr_name, resolved)
                except (AttributeError, TypeError):
                    pass
    return value
