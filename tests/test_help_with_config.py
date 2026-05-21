"""Tests for config-aware --help (flowed-instance path)."""

import textwrap
from pathlib import Path
from typing import Any

import pytest
from confluid import configurable
from rich.console import Console

from liquifai import LiquifyApp
from liquifai.report import show_configuration


@configurable
class _Leaf:
    """Leaf configurable.

    Args:
        count: How many widgets.
        label: Display label.
    """

    def __init__(self, count: int = 7, label: str = "widget") -> None:
        self.count = count
        self.label = label


@configurable
class _Parent:
    """Parent with a child configurable.

    Args:
        leaf: The leaf to display.
        title: A title.
    """

    def __init__(self, leaf: _Leaf, title: str = "untitled") -> None:
        self.leaf = leaf
        self.title = title


@configurable
class _Wrapper:
    """Wrapper that sets a post-construction toggle (Enable-style)."""

    def __init__(self, inner: _Leaf) -> None:
        self.inner = inner


def _capture(renderer: Any, *args: Any, **kwargs: Any) -> str:
    """Run a Rich-using helper and capture its stdout as a string."""
    from io import StringIO

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    # Monkey-patch the module's singleton `Console` temporarily
    import liquifai.report as report_mod

    original = report_mod.Console
    try:
        report_mod.Console = lambda *a, **kw: console  # type: ignore[misc, assignment]
        renderer(*args, **kwargs)
    finally:
        report_mod.Console = original  # type: ignore[misc]
    return buf.getvalue()


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_show_configuration_flowed_graph_lists_ctor_params() -> None:
    graph = {"parent": _Parent(leaf=_Leaf(count=42, label="q"), title="T")}

    def cmd(parent: _Parent) -> None:
        return None

    out = _capture(show_configuration, cmd, config_map=graph, title="Test")
    # Current values reflect live instance attributes
    assert "42" in out
    assert "'q'" in out or "q" in out
    assert "T" in out
    # Shortest-unique names surface
    assert "--count" in out
    assert "--label" in out
    assert "--title" in out
    # Host class appears in the Applies-to column
    assert "Parent" in out
    assert "Leaf" in out


def test_show_configuration_flowed_graph_surfaces_post_construction_toggle() -> None:
    inner = _Leaf(count=3)
    wrapper = _Wrapper(inner=inner)
    wrapper.visualize = True  # type: ignore[attr-defined]  # Enable pattern
    graph = {"wrapper": wrapper}

    def cmd(wrapper: _Wrapper) -> None:
        return None

    out = _capture(show_configuration, cmd, config_map=graph)
    assert "--visualize" in out
    assert "True" in out


def test_show_configuration_static_path_untouched() -> None:
    """Falls back to the classic walker when config_map has no live objects."""

    def cmd(parent: _Parent) -> None:
        return None

    out = _capture(show_configuration, cmd, title="Static")
    # Static walker still finds ctor leaves via the type annotation
    assert "--count" in out or "--label" in out or "--title" in out


def test_liquify_and_show_end_to_end(tmp_path: Path) -> None:
    """LiquifyApp.liquify + show_configuration produce the expected options."""
    # Confluid can only `!class:` resolve a module-importable path. Alias
    # this test module under a stable name so the YAML's `!class:...` works.
    import sys

    sys.modules["test_help_with_config_module"] = sys.modules[__name__]

    yaml = _write_yaml(
        tmp_path,
        """\
        parent:
          !class:test_help_with_config_module._Parent
          title: "from YAML"
          leaf:
            !class:test_help_with_config_module._Leaf
            count: 99
            label: "gadget"
        """,
    )

    app = LiquifyApp(name="test-app")

    # Use `Any` annotation to match real commands (marainer's `process(processor: Any)`).
    @app.command()
    def dummy(parent: Any) -> None:
        return None

    kwargs = app.liquify(dummy, config_path=yaml)
    assert isinstance(kwargs["parent"], _Parent)
    assert kwargs["parent"].title == "from YAML"
    assert kwargs["parent"].leaf.count == 99
    assert kwargs["parent"].leaf.label == "gadget"

    out = _capture(show_configuration, dummy, config_map=kwargs)
    assert "99" in out
    assert "gadget" in out
    assert "from YAML" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
