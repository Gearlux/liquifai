"""Generic result-shaping and input-parsing helpers for bridged SDK operations.

These are the SDK-agnostic building blocks policies and consumers compose:
serializing arbitrary SDK records to JSON-safe dicts, rendering dry-run
descriptors, parsing CLI string inputs into structured SDK values, and simple
client-side record filtering. Nothing in this module knows any particular SDK.

Deliberately stdlib-only so a consumer's policy module can import it without
pulling in the rest of liquifai.

NOTE: no ``from __future__ import annotations`` anywhere in ``liquifai.bridge``
— liquifai/confluid/FastMCP introspect live annotation objects on synthesized
operation signatures.
"""

import dataclasses
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, cast


def dry_descriptor(command: str, action: str, call: str) -> Dict[str, Any]:
    """Build the dry-run descriptor returned in place of a real SDK call.

    This is the bridge's dry-run CONTRACT: a policy must check
    ``conn.dry_run`` **before** calling ``conn.get_client()`` and return this
    descriptor instead — so ``--help``, examples, MCP schemas, and tests run
    without credentials. Presenters and tests detect dry-run via
    ``result.get("dry_run") is True``.
    """
    return {"dry_run": True, "command": command, "action": action, "call": call}


def format_call(fn: str, *args: Any, **kwargs: Any) -> str:
    """Render a readable ``fn(arg, key=value, ...)`` string for dry-run output."""
    parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
    if not parts:
        return f"{fn}()"
    body = ",\n".join(f"    {p}" for p in parts)
    return f"{fn}(\n{body},\n)"


def jsonify(value: Any) -> Any:
    """Coerce an arbitrary value into a JSON-serializable form.

    Primitives pass through; datetimes/dates → ISO strings; enums → their value;
    Paths → str; dict/list/tuple/set recurse; objects exposing a JSON-shaped
    ``to_dict()`` recurse through it; anything else → ``str(value)``.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return jsonify(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonify(v) for v in value]
    # Some SDK records (e.g. attrs classes) expose a JSON-shaped ``to_dict()``.
    # Recurse through it so nested records serialize as real JSON instead of a
    # ``str(...)`` repr — otherwise ``--json`` consumers (jq) can't read them.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return jsonify(to_dict())
        except Exception:  # pragma: no cover - defensive
            pass
    return str(value)


def record_to_dict(obj: Any) -> Dict[str, Any]:
    """Serialize an SDK record into a complete, JSON-safe dict.

    Handles (in order) pydantic v2 (``model_dump(mode="json")``), pydantic v1
    (``.dict()``), dataclasses, plain dicts, and finally any object's public
    instance attributes (``vars``) or public non-callable attributes (``dir``).
    Returns EVERY field the SDK exposes — this is what makes both the CLI and
    MCP returns complete instead of a hand-picked subset.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {str(k): jsonify(v) for k, v in obj.items()}

    dump = getattr(obj, "model_dump", None)  # pydantic v2
    if callable(dump):
        try:
            return cast(Dict[str, Any], jsonify(dump(mode="json")))
        except Exception:  # pragma: no cover - defensive: non-pydantic model_dump
            pass

    as_dict = getattr(obj, "dict", None)  # pydantic v1
    if callable(as_dict) and not isinstance(obj, type):
        try:
            return cast(Dict[str, Any], jsonify(as_dict()))
        except Exception:  # pragma: no cover - defensive
            pass

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return cast(Dict[str, Any], jsonify(dataclasses.asdict(obj)))

    if hasattr(obj, "__dict__"):
        data = {k: jsonify(v) for k, v in vars(obj).items() if not k.startswith("_")}
        if data:
            return data

    out: Dict[str, Any] = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(obj, name)
        except Exception:  # pragma: no cover - defensive
            continue
        if callable(attr):
            continue
        out[name] = jsonify(attr)
    return out


def records_of(container: Any, attr: str = "records") -> List[Any]:
    """Pull the record list out of a paginated response (``.records``) or a bare list."""
    inner = getattr(container, attr, container)
    return list(inner or [])


def items_result(records: List[Any]) -> Dict[str, Any]:
    """Wrap full-record dicts as a ``{"items": [...], "count": N}`` list result."""
    rows = [record_to_dict(r) for r in records]
    return {"items": rows, "count": len(rows)}


def attr_str(obj: Any, name: str, default: str = "") -> str:
    """Read ``name`` off a record (object or dict) as a string — used for filtering."""
    val = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    return str(val)


def filter_records(records: List[Any], attr: str, needle: str, case_sensitive: bool) -> List[Any]:
    """Client-side substring filter on ``attr`` of each record."""
    if not needle:
        return records
    if case_sensitive:
        return [r for r in records if needle in attr_str(r, attr)]
    low = needle.lower()
    return [r for r in records if low in attr_str(r, attr).lower()]


def require(**named: str) -> None:
    """Raise ``ValueError`` if any named argument is empty."""
    missing = [name for name, value in named.items() if not value]
    if missing:
        raise ValueError(f"Missing required argument(s): {', '.join(missing)}")


def parse_tags(tags: str) -> List[str]:
    """Split a comma-separated tags string into a clean list."""
    if not tags:
        return []
    return [t.strip() for t in tags.split(",") if t.strip()]


def parse_csv(value: str) -> List[str]:
    """Split a comma-separated string into a clean list (artifacts, etc.)."""
    if not value:
        return []
    return [a.strip() for a in value.split(",") if a.strip()]


def coerce_scalar(text: str) -> Any:
    """Best-effort scalar coercion of a string: bool, int, float, else str."""
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def parse_kv(text: str, *, coerce: bool = True) -> Dict[str, Any]:
    """Parse ``"k=v,k2=v2"`` into a dict.

    Values are scalar-coerced (bool/int/float) when ``coerce`` is True; kept as
    strings when False (for APIs that type the mapping as ``Dict[str, str]``).
    """
    out: Dict[str, Any] = {}
    for pair in text.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        if key:
            out[key] = coerce_scalar(value.strip()) if coerce else value.strip()
    return out


#: Built-in input adapters: a CLI string -> the structured value an SDK kwarg
#: expects, keyed by the name used in :attr:`liquifai.bridge.P.adapt`. Consumers
#: extend (or override) this table per-bridge via ``SdkBridge(adapters={...})``.
DEFAULT_ADAPTERS: Dict[str, Callable[[Any], Any]] = {
    "list": parse_tags,  # "a,b,c" -> ["a","b","c"]
    "csv": parse_csv,  # "a,b" -> ["a","b"]
    "kv": parse_kv,  # "k=v,..." -> {coerced}
    "props": lambda s: parse_kv(s, coerce=False),  # "k=v,..." -> {str: str}
    # Values read as ``--flag 1.0`` are liquifai-coerced to a float; an SDK that
    # wants the string gets it back via ``str`` (empty string stays empty).
    "str": lambda v: "" if v == "" else str(v),
}
