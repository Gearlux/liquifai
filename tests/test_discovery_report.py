from typing import Any, Optional

import confluid

from liquifai.discovery import get_configurable_paths
from liquifai.report import show_configuration


@confluid.configurable
class SubModel:
    def __init__(self, size: int = 10):
        self.size = size
        self.name = "sub"  # Used by discovery.py if prefix is not empty


@confluid.configurable
class RootModel:
    def __init__(self, sub: SubModel, lr: float = 0.01):
        self.sub = sub
        self.lr = lr
        self.threshold = 0.5


def test_discovery() -> None:
    sub = SubModel(size=20)
    root = RootModel(sub=sub)

    paths = get_configurable_paths(root)

    # Discovery starts with the class name for the root
    assert "RootModel.lr" in paths
    assert "RootModel.threshold" in paths
    assert "RootModel.sub.size" in paths
    assert paths["RootModel.sub.size"] == 20


def test_discovery_extended() -> None:
    @confluid.configurable
    class IgnoredModel:
        def __init__(self, val: int = 1, other: int = 2) -> None:
            self.val = val
            self.other = other
            self._internal = 3

    # 1. Ignore via class member
    class MockMember:
        __confluid_ignore__ = True

        def __init__(self) -> None:
            pass

    setattr(IgnoredModel, "val", MockMember())

    # 2. Ignore via value
    class IgnoredVal:
        __confluid_ignore__ = True

        def __init__(self) -> None:
            pass

    obj = IgnoredModel()
    obj.other = IgnoredVal()  # type: ignore

    paths = get_configurable_paths(obj)
    # val should be ignored (class member ignore), other should be ignored (value ignore)
    # _internal ignored (starts with _)
    assert "IgnoredModel.val" not in paths
    assert "IgnoredModel.other" not in paths
    assert "IgnoredModel._internal" not in paths


def test_discovery_named_objects() -> None:
    @confluid.configurable
    class Child:
        def __init__(self, name: str, value: int) -> None:
            self.name = name
            self.value = value

    @confluid.configurable
    class Parent:
        def __init__(self, child: Child) -> None:
            self.child = child

    parent = Parent(child=Child(name="mychild", value=42))
    paths = get_configurable_paths(parent)

    # child has a .name, so it should be used in the path
    assert "Parent.mychild.value" in paths
    assert paths["Parent.mychild.value"] == 42


def test_discovery_cycle() -> None:
    @confluid.configurable
    class Node:
        def __init__(self, name: str) -> None:
            self.name = name
            self.child: Optional["Node"] = None

    a = Node("a")
    b = Node("b")
    a.child = b
    b.child = a  # Cycle

    paths = get_configurable_paths(a)
    assert "Node.name" in paths


def test_discovery_exception() -> None:
    @confluid.configurable
    class BadModel:
        def __init__(self) -> None:
            pass

        @property
        def boom(self) -> Any:
            raise ValueError("Boom")

    paths = get_configurable_paths(BadModel())
    assert "boom" not in paths


def test_report_truncation(capsys: Any) -> None:
    @confluid.configurable
    class LongModel:
        def __init__(self, val: str = "x" * 60) -> None:
            self.val = val

    show_configuration(LongModel())
    captured = capsys.readouterr()
    # Support both triple dot and ellipsis character
    assert "..." in captured.out or "…" in captured.out


def test_report_with_config_map(capsys: Any) -> None:
    @confluid.configurable
    class Simple:
        def __init__(self, x: int = 1) -> None:
            self.x = x

    show_configuration(Simple(), config_map={"Simple": {"x": 42}})
    captured = capsys.readouterr()
    assert "42" in captured.out
