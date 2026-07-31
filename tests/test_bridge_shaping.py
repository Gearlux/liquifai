"""Tests for liquifai.bridge.shaping — generic record serialization, dry-run
descriptors, and CLI-string input adapters."""

import dataclasses
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

import pytest

from liquifai.bridge.shaping import (
    DEFAULT_ADAPTERS,
    attr_str,
    coerce_scalar,
    dry_descriptor,
    filter_records,
    format_call,
    items_result,
    jsonify,
    parse_csv,
    parse_kv,
    parse_tags,
    record_to_dict,
    records_of,
    require,
)

# ---------------------------------------------------------------------------
# record_to_dict — the reflection ladder
# ---------------------------------------------------------------------------


class _PydanticV2Like:
    def __init__(self, name: str) -> None:
        self._name = name

    def model_dump(self, mode: str = "python") -> Dict[str, Any]:
        assert mode == "json"
        return {"name": self._name, "kind": "v2"}


class _PydanticV1Like:
    def dict(self) -> Dict[str, Any]:
        return {"kind": "v1"}


@dataclasses.dataclass
class _RecordDC:
    name: str
    count: int


class _PlainRecord:
    def __init__(self) -> None:
        self.name = "plain"
        self._private = "hidden"


class _SlottedRecord:
    """No __dict__ — exercises the dir() fallback."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "slotted"

    def method(self) -> None:  # callables must be skipped
        pass


def test_record_to_dict_none() -> None:
    assert record_to_dict(None) == {}


def test_record_to_dict_dict_passthrough_jsonified() -> None:
    assert record_to_dict({"when": date(2026, 1, 2)}) == {"when": "2026-01-02"}


def test_record_to_dict_pydantic_v2() -> None:
    assert record_to_dict(_PydanticV2Like("x")) == {"name": "x", "kind": "v2"}


def test_record_to_dict_pydantic_v1() -> None:
    assert record_to_dict(_PydanticV1Like()) == {"kind": "v1"}


def test_record_to_dict_dataclass() -> None:
    assert record_to_dict(_RecordDC(name="dc", count=2)) == {"name": "dc", "count": 2}


def test_record_to_dict_vars_skips_private() -> None:
    assert record_to_dict(_PlainRecord()) == {"name": "plain"}


def test_record_to_dict_dir_fallback() -> None:
    assert record_to_dict(_SlottedRecord()) == {"name": "slotted"}


def test_record_to_dict_model_dump_raises_falls_through() -> None:
    class R:
        def model_dump(self, mode: Any = None) -> Dict[str, Any]:
            raise RuntimeError("boom")

        def dict(self) -> Dict[str, Any]:
            return {"fallback": True}

    assert record_to_dict(R()) == {"fallback": True}


def test_record_to_dict_nested_to_dict_records() -> None:
    """Nested records exposing to_dict() serialize as real JSON, not str() reprs."""

    class _Linked:
        def to_dict(self) -> Dict[str, Any]:
            return {"models": [{"id": "m1"}], "datasets": []}

    class Rec:
        linked_resources = _Linked()

    assert record_to_dict(Rec()) == {"linked_resources": {"models": [{"id": "m1"}], "datasets": []}}


# ---------------------------------------------------------------------------
# jsonify
# ---------------------------------------------------------------------------


class _Color(Enum):
    RED = "red"


class _WithToDict:
    def to_dict(self) -> Dict[str, Any]:
        return {"nested": True}


def test_jsonify_primitives_and_none() -> None:
    for v in (None, "s", 1, 1.5, True):
        assert jsonify(v) == v


def test_jsonify_datetime_date_enum_path() -> None:
    assert jsonify(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    assert jsonify(date(2026, 1, 2)) == "2026-01-02"
    assert jsonify(_Color.RED) == "red"
    assert jsonify(Path("/tmp/x")) == "/tmp/x"


def test_jsonify_containers_recurse() -> None:
    assert jsonify({"k": [_Color.RED, (1, 2)]}) == {"k": ["red", [1, 2]]}
    assert sorted(jsonify({1, 2})) == [1, 2]  # sets become lists


def test_jsonify_to_dict_recursion() -> None:
    assert jsonify(_WithToDict()) == {"nested": True}


def test_jsonify_fallback_str() -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    assert jsonify(Opaque()) == "<opaque>"


# ---------------------------------------------------------------------------
# records_of / items_result / attr_str / filter_records
# ---------------------------------------------------------------------------


def test_records_of_container_attr_and_bare_list() -> None:
    class Page:
        records = [1, 2]

    assert records_of(Page()) == [1, 2]
    assert records_of([3]) == [3]
    assert records_of(None) == []


def test_records_of_custom_attr() -> None:
    class Page:
        rows = ["a"]

    assert records_of(Page(), attr="rows") == ["a"]


def test_items_result_wraps_full_records() -> None:
    out = items_result([{"name": "a"}, {"name": "b"}])
    assert out == {"items": [{"name": "a"}, {"name": "b"}], "count": 2}


def test_attr_str_dict_and_object() -> None:
    assert attr_str({"name": 3}, "name") == "3"

    class R:
        name = "x"

    assert attr_str(R(), "name") == "x"
    assert attr_str(R(), "missing", "d") == "d"


def test_filter_records_case_insensitive_default() -> None:
    recs = [{"name": "Alpha"}, {"name": "beta"}]
    assert filter_records(recs, "name", "ALPHA", case_sensitive=False) == [{"name": "Alpha"}]
    assert filter_records(recs, "name", "Alpha", case_sensitive=True) == [{"name": "Alpha"}]
    assert filter_records(recs, "name", "alpha", case_sensitive=True) == []
    assert filter_records(recs, "name", "", case_sensitive=False) == recs


# ---------------------------------------------------------------------------
# require / format_call / dry_descriptor
# ---------------------------------------------------------------------------


def test_require_raises_listing_all_missing() -> None:
    with pytest.raises(ValueError, match="name, version"):
        require(name="", version="")
    require(name="ok")  # no raise


def test_format_call_shapes() -> None:
    assert format_call("client.f") == "client.f()"
    rendered = format_call("client.f", "a", key=1)
    assert rendered.startswith("client.f(\n")
    assert "'a'" in rendered and "key=1" in rendered


def test_dry_descriptor_contract() -> None:
    d = dry_descriptor("widget", "list", "client.widget.list_widgets()")
    assert d["dry_run"] is True
    assert d == {
        "dry_run": True,
        "command": "widget",
        "action": "list",
        "call": "client.widget.list_widgets()",
    }


# ---------------------------------------------------------------------------
# input adapters
# ---------------------------------------------------------------------------


def test_parse_tags_and_csv() -> None:
    assert parse_tags(" a, b ,,c ") == ["a", "b", "c"]
    assert parse_tags("") == []
    assert parse_csv("x , y") == ["x", "y"]
    assert parse_csv("") == []


def test_coerce_scalar() -> None:
    assert coerce_scalar("true") is True
    assert coerce_scalar("False") is False
    assert coerce_scalar("3") == 3
    assert coerce_scalar("3.5") == 3.5
    assert coerce_scalar("abc") == "abc"


def test_parse_kv_coerced_and_raw() -> None:
    assert parse_kv("a=1,b=true, c = x ") == {"a": 1, "b": True, "c": "x"}
    assert parse_kv("a=1", coerce=False) == {"a": "1"}
    assert parse_kv("junk,=v,a=") == {"a": ""}


def test_default_adapters_table() -> None:
    assert DEFAULT_ADAPTERS["list"]("a,b") == ["a", "b"]
    assert DEFAULT_ADAPTERS["csv"]("a,b") == ["a", "b"]
    assert DEFAULT_ADAPTERS["kv"]("a=1") == {"a": 1}
    assert DEFAULT_ADAPTERS["props"]("a=1") == {"a": "1"}
    assert DEFAULT_ADAPTERS["str"](1.0) == "1.0"
    assert DEFAULT_ADAPTERS["str"]("") == ""


def test_records_of_non_list_iterable() -> None:
    recs: List[Any] = records_of(iter([1, 2]), attr="records")  # iterator has no .records
    assert recs == [1, 2]
