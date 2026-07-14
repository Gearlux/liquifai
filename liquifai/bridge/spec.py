"""Declarative specs for bridged SDK operations: ``P``, ``@expose``, ``@custom``.

A consumer subclasses a real SDK (sub-)client and decorates its methods:

* ``@expose`` — declarative: the method NAME is the SDK method it exposes, the
  body is a ``...`` stub the engine never runs; the spec declares how CLI
  params map onto the SDK call and how the result is shaped.
* ``@custom`` — the escape hatch for logic the declarative engine can't
  express: the method takes the connection as its first param ``conn`` (NOT
  ``self``) and IS the operation body.

Both stash their spec on the function; :meth:`liquifai.bridge.SdkBridge.group`
consumes the specs at class-decoration time.

NOTE: no ``from __future__ import annotations`` in ``liquifai.bridge`` — live
annotation objects are required by liquifai/confluid/FastMCP introspection.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from liquifai.core import Presentation

#: Marker for a required (no-default) parameter — distinct from ``None``/``""``.
_MISSING: Any = object()

#: Attribute under which ``@expose`` stashes its spec on the decorated stub method.
_EXPOSE_ATTR = "__liquifai_expose__"

#: Attribute under which ``@custom`` stashes its spec on the decorated method.
_CUSTOM_ATTR = "__liquifai_custom__"


@dataclass
class P:
    """One CLI parameter of a bridged operation, mapped onto an SDK kwarg.

    Args:
        cli: CLI parameter name (the flag / positional shown to the user).
        sdk: SDK kwarg name to send the value under. Defaults to ``cli``.
            ``None`` marks a CLIENT-SIDE-only param never forwarded to the SDK.
        default: ``_MISSING`` -> a required positional; anything else -> an
            optional flag with that default.
        adapt: a key into the bridge's adapter table (see
            :data:`liquifai.bridge.shaping.DEFAULT_ADAPTERS` and
            ``SdkBridge(adapters=...)``) parsing the CLI string into the
            structured SDK value.
        send_positional: pass to the SDK call positionally; else as a kwarg.
        omit_empty: when an optional value is empty/falsy, drop it from the SDK
            call (so the SDK default applies) instead of forwarding it.
        validate: a flag (has a default) that is nonetheless required at runtime.
    """

    cli: str
    sdk: Optional[str] = ""  # "" sentinel -> default to cli in __post_init__
    default: Any = _MISSING
    adapt: Optional[str] = None
    send_positional: bool = False
    omit_empty: bool = True
    validate: bool = False

    def __post_init__(self) -> None:
        if self.sdk == "":
            self.sdk = self.cli

    @property
    def required(self) -> bool:
        return self.default is _MISSING


@dataclass
class ExposeSpec:
    """The glue spec attached by :func:`expose` (consumed by ``SdkBridge.group``).

    The core fields are SDK-agnostic; policy-specific knobs a particular policy
    understands (e.g. a list policy's ``client_filter`` / ``extras``) travel
    untyped in :attr:`options` so the generic spec never learns any one SDK's
    dialect.
    """

    verb: str
    presentation: Presentation = "status"
    sdk_method: str = ""  # filled from the decorated function name
    policy: str = "auto"  # "auto" routes on presentation; else an explicit policy key
    # presentation metadata forwarded to @operation
    columns: Tuple[Tuple[str, str], ...] = ()
    title: str = ""
    empty: str = "No results"
    # call mapping
    params: List[P] = field(default_factory=list)
    constants: Dict[str, Any] = field(default_factory=dict)  # always-sent SDK kwargs
    # generic result shaping
    result_attr: str = "records"  # container attr holding the record list
    nullable: bool = False  # None result -> {"found": False}
    status_word: str = ""  # status presentation: {"status": <word>, ...}
    status_echo: Tuple[str, ...] = ()  # param names to echo into the status dict
    spread_result: bool = False  # status presentation: merge record_to_dict(result)
    result_full: bool = False  # status presentation: {"result": record_to_dict(result)}
    result_key: str = ""  # status presentation: {<result_key>: attr_str(result, ...)}
    result_field: str = ""  # the SDK record attr to read for result_key
    # completion + docs
    completions: Optional[Dict[str, Callable[..., Any]]] = None  # {cli_param: provider}
    doc: str = ""
    # policy-specific knobs (opaque to the generic bridge)
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomSpec:
    """Metadata for a custom-body op declared with :func:`custom` inside a group."""

    verb: str
    presentation: Presentation = "status"
    columns: Tuple[Tuple[str, str], ...] = ()
    title: str = ""
    empty: str = "No results"
    completions: Optional[Dict[str, Callable[..., Any]]] = None


def expose(
    *,
    verb: str,
    presentation: Presentation = "status",
    policy: str = "auto",
    columns: Tuple[Tuple[str, str], ...] = (),
    title: str = "",
    empty: str = "No results",
    params: Optional[List[P]] = None,
    constants: Optional[Dict[str, Any]] = None,
    result_attr: str = "records",
    nullable: bool = False,
    status_word: str = "",
    status_echo: Tuple[str, ...] = (),
    spread_result: bool = False,
    result_full: bool = False,
    result_key: str = "",
    result_field: str = "",
    completions: Optional[Dict[str, Callable[..., Any]]] = None,
    doc: str = "",
    **options: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate an SDK-named stub method, declaring how it maps to a CLI/MCP operation.

    See the module docstring and :class:`P` for the parameter model. Any extra
    keyword argument lands in :attr:`ExposeSpec.options` for the routed policy
    to consume (e.g. a list policy's ``client_filter=`` / ``extras=``). Returns
    the stub unchanged with the spec stashed; ``SdkBridge.group`` synthesizes
    the real operation from it at class-decoration time.
    """
    spec = ExposeSpec(
        verb=verb,
        presentation=presentation,
        policy=policy,
        columns=columns,
        title=title,
        empty=empty,
        params=list(params or []),
        constants=dict(constants or {}),
        result_attr=result_attr,
        nullable=nullable,
        status_word=status_word,
        status_echo=tuple(status_echo),
        spread_result=spread_result,
        result_full=result_full,
        result_key=result_key,
        result_field=result_field,
        completions=completions,
        doc=doc,
        options=dict(options),
    )

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        spec.sdk_method = fn.__name__
        setattr(fn, _EXPOSE_ATTR, spec)
        return fn

    return _decorator


def custom(
    *,
    verb: str,
    presentation: Presentation = "status",
    columns: Tuple[Tuple[str, str], ...] = (),
    title: str = "",
    empty: str = "No results",
    completions: Optional[Dict[str, Callable[..., Any]]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a custom-body method ``(conn, *, ...) -> dict`` as a CLI/MCP operation.

    Use inside a ``SdkBridge.group`` class for ops the declarative
    :func:`expose` engine can't express (path-nesting downloads, per-op result
    builders, cross-sub-client calls, ...). The method takes the connection as
    its first param ``conn`` (NOT ``self``) and IS the operation body — its
    docstring (with an ``Args:`` block) becomes the CLI ``--help`` / MCP
    description. The op is named ``<group>_<verb>`` like an :func:`expose` op.
    """
    spec = CustomSpec(
        verb=verb, presentation=presentation, columns=columns, title=title, empty=empty, completions=completions
    )

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, _CUSTOM_ATTR, spec)
        return fn

    return _decorator
