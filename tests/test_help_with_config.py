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
    """Wrapper whose toggle is set by setattr after __init__ (a config layer injecting a key)."""

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
    wrapper.visualize = True  # type: ignore[attr-defined]  # post-construction, undeclared
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


def _version_create_cmd(
    name: str = "",
    source_version: str = "",
    target_version: str = "",
    append: bool = False,
) -> None:
    """Create a dataset version.

    Args:
        name: Dataset name.
        source_version: Version to create the new version from.
        target_version: Version string for the new version.
        append: Append to the source version's files.
    """
    return None


def test_show_configuration_positionals_block_and_exclusion() -> None:
    """Declared positionals render as their own block and vanish from the
    options table — mirroring completion, which never offers a positional as
    its ``--flag`` spelling (the spelling still parses at runtime)."""
    out = _capture(show_configuration, _version_create_cmd, positionals=["name", "source_version"])
    assert "Positional Arguments" in out
    assert "<name>" in out
    assert "<source_version>" in out
    assert "Version to create the new version from." in out  # doc carried into the block
    assert "--source_version" not in out
    # Real options are untouched.
    assert "--target_version" in out
    assert "--append" in out


def test_show_configuration_positionals_lines_layout_matches() -> None:
    """The ``--docs`` lines layout carries the SAME positional block/exclusion."""
    out = _capture(show_configuration, _version_create_cmd, layout="lines", positionals=["name", "source_version"])
    assert "Positional Arguments" in out
    assert "<source_version>" in out
    assert "--source_version" not in out
    assert "--target_version" in out


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

    # Use `Any` annotation to match real commands (matrainer's `process(processor: Any)`).
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


def _render_help(renderer: Any, *args: Any) -> str:
    """Run a report help-renderer (which takes an explicit ``console``) and
    capture its output."""
    from io import StringIO

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    renderer(*args, console)
    return buf.getvalue()


def test_show_command_index_lists_commands_and_groups() -> None:
    from liquifai.report import show_command_index

    root = LiquifyApp(name="myapp")

    @root.command()
    def greet(target: str = "world") -> None:
        """Say hello to someone."""

    sub = LiquifyApp(name="data", description="Data ops")

    @sub.command()
    def alpha() -> None:
        pass

    root.add_app(sub, "data")

    out = _render_help(show_command_index, root)
    assert "greet" in out
    assert "Say hello to someone" in out  # first docstring line
    assert "data" in out
    assert "Data ops" in out  # group description


def test_show_command_index_folds_aliases_into_canonical_row() -> None:
    from liquifai.report import show_command_index

    root = LiquifyApp(name="myapp")
    sub = LiquifyApp(name="dataset", description="Dataset ops")
    root.add_app(sub, "dataset", aliases=["ds"])

    out = _render_help(show_command_index, root)
    # Canonical group carries the alias in-line; no separate `ds` group row.
    assert "dataset (ds)" in out
    assert "\nds " not in out  # alias is not its own row


def test_show_global_options_renders_every_visible_flag() -> None:
    from liquifai.grammar import GLOBAL_FLAG_SPECS, flag_display
    from liquifai.report import show_global_options

    out = _render_help(show_global_options)
    assert "Global Options" in out
    for spec in GLOBAL_FLAG_SPECS:
        if spec.hidden:
            continue
        assert flag_display(spec) in out, f"visible flag {spec!r} missing from help"
    # The implicit per-dimension flag note is present.
    assert "--KEY VAL" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# --------------------------------------------------------------------------- #
# Scope dimensions block — what a config's `!scope:KEY=VAL` blocks offer, and the
# default `default_scopes:` names for a dimension the caller leaves unset.
# --------------------------------------------------------------------------- #
def _capture_console(renderer: Any, *args: Any, **kwargs: Any) -> str:
    from io import StringIO

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    renderer(*args, console=console, **kwargs)
    return buf.getvalue()


def test_scope_dimensions_block_lists_values_and_the_default(tmp_path: Path) -> None:
    """`--framework <keras|lightning|torch>  (default: lightning)` — values from the document's
    blocks, the default from its `default_scopes:`; a dimension without a default shows none."""
    import confluid

    from liquifai.report import show_scope_dimensions

    path = _write_yaml(
        tmp_path,
        """
        default_scopes: [framework=lightning]
        lightning: !scope:framework=lightning
          runnable: L
        torch: !scope:framework=torch
          runnable: T
        keras: !scope:framework=keras
          runnable: K
        convnet: !scope:model=convnet
          model: C
        """,
    )
    out = _capture_console(show_scope_dimensions, confluid.load(path, until="raw"), source=path.name)
    assert "cfg.yaml" in out
    assert "--framework <keras|lightning|torch>" in out  # sorted, one flag per dimension
    assert "(default: lightning)" in out
    assert "--model <convnet>" in out
    assert out.count("(default:") == 1  # `model` has no default — nothing is invented for it


def test_scope_dimensions_block_is_silent_for_a_document_without_dimensions(tmp_path: Path) -> None:
    import confluid

    from liquifai.report import show_scope_dimensions

    path = _write_yaml(tmp_path, "x: 1\n")
    assert _capture_console(show_scope_dimensions, confluid.load(path, until="raw"), source=path.name) == ""


def test_scope_dimensions_block_names_a_negation_only_dimension_without_values(tmp_path: Path) -> None:
    """A dimension declared only by `!notscope:` offers nothing to SELECT — confluid reports an
    empty value set — so the flag is shown with a `<value>` placeholder, not an empty `<>`."""
    import confluid

    from liquifai.report import show_scope_dimensions

    path = _write_yaml(tmp_path, "unless_seg: !notscope:task=segmentation\n  head: default\n")
    out = _capture_console(show_scope_dimensions, confluid.load(path, until="raw"), source=path.name)
    assert "--task <value>" in out
