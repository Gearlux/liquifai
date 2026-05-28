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

    # Top-level call returns shortest-unique paths — non-colliding leaves
    # reduce to just the leaf name.
    assert "lr" in paths
    assert "threshold" in paths
    assert "size" in paths
    assert paths["size"] == 20
    assert paths["lr"] == 0.01
    assert paths["threshold"] == 0.5
    # Inherited / class-level prefix is gone.
    assert not any(k.startswith("RootModel.") for k in paths)


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
    # val ignored (class member), other ignored (value), _internal ignored (private).
    assert "val" not in paths
    assert "other" not in paths
    assert "_internal" not in paths
    assert not any(k.endswith(".val") for k in paths)
    assert not any(k.endswith(".other") for k in paths)


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

    # Child's .name is used as the path segment internally; shortest-unique
    # reduces the leaf to just "value" since nothing else collides.
    assert "value" in paths
    assert paths["value"] == 42


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
    # Two ``name`` leaves collide → shortest-unique extends the suffix.
    assert any("name" in k for k in paths)
    assert paths.get("Node.name") == "a" or paths.get("a.name") == "a"


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


def test_inherited_class_constants_not_surfaced() -> None:
    """Class-level constants on a non-``@configurable`` parent must not leak."""

    class NonConfigBase:
        LEAKY_CONSTANT = "noise"
        OTHER_CONSTANT = 42

    @confluid.configurable
    class ChildConfig(NonConfigBase):
        def __init__(self, real_param: int = 7) -> None:
            self.real_param = real_param

    paths = get_configurable_paths(ChildConfig(real_param=99))
    assert "real_param" in paths
    assert paths["real_param"] == 99
    assert not any("LEAKY_CONSTANT" in k for k in paths)
    assert not any("OTHER_CONSTANT" in k for k in paths)


def test_parent_init_instance_attrs_filtered() -> None:
    """Non-underscore instance attrs planted by a non-``@configurable`` parent's ``__init__``
    must not leak — mimics ``torch.nn.Module.__init__`` setting ``self.training = True``."""

    class LeakyParent:
        def __init__(self) -> None:
            self.training = True  # parent plants this on every instance
            self.parent_only = "should_not_appear"

    @confluid.configurable
    class ChildConfig(LeakyParent):
        def __init__(self, user_param: str = "x") -> None:
            super().__init__()
            self.user_param = user_param
            self.post_init = "user_set"

    paths = get_configurable_paths(ChildConfig(user_param="abc"))
    assert "user_param" in paths
    assert "post_init" in paths
    assert paths["user_param"] == "abc"
    assert paths["post_init"] == "user_set"
    assert not any("training" in k for k in paths)
    assert not any("parent_only" in k for k in paths)


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
