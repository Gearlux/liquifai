"""A config's own settings come first, with their help — in the TAB listing and in ``--help`` alike.

The top-level keys written in the config file itself and the document's scopes (its own, then its includes') are what
its author put first; each included file's keys the config does not write follow, under the include's name. Each shows
its comment, else the help of the class parameter it feeds (through ``!ref:`` or by its name). Both listings show the
same options: a ``!class:`` block the command's objects never reach has its own group, and a key holding an object is
never offered as a value.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any, List, Optional

import pytest
from confluid import configurable

from liquifai import LiquifyApp
from liquifai import completion as comp
from liquifai.completion import config_help as config_help_mod
from liquifai.completion import tree as tree_mod
from liquifai.completion.config_help import ConfigScope, FocusedKey, IncludedKeys, read_config_help

#: The class path a ``!class:`` tag in these configs names: this module, found by its source.
HERE = __name__


@configurable
class Leaf:
    """One item of a list.

    Args:
        size: How big the item is.
    """

    def __init__(self, size: int = 1) -> None:
        self.size = size


@configurable
class Builder:
    """Build the catalog.

    Args:
        source_dirs: The folders searched.
        count: How many to build.
        items: The items read.
        verbose: Say more.
    """

    def __init__(
        self,
        source_dirs: Optional[List[str]] = None,
        count: int = 0,
        items: Optional[List[Leaf]] = None,
        verbose: bool = False,
    ) -> None:
        self.source_dirs = source_dirs
        self.count = count
        self.items = items
        self.verbose = verbose


@configurable
class Holder:
    """Hold one leaf.

    Args:
        inner: The leaf held.
        tag: Its tag.
    """

    def __init__(self, inner: Optional[Leaf] = None, tag: str = "t") -> None:
        self.inner = inner
        self.tag = tag


@pytest.fixture(autouse=True)
def iso_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LIQUIFAI_NO_LAZY_COMPLETE", "1")
    monkeypatch.delenv("LIQUIFAI_COMPLETION_LIST", raising=False)
    return tmp_path


def _write(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text(textwrap.dedent(body))
    return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A config with its own settings, a scope, an include and a builder fed by ``!ref:``."""
    _write(
        tmp_path,
        "shared.yaml",
        f"""\
        # Shared label, documented where it is written.
        label: base
        widget: !class:{HERE}.Builder
          count: !ref:width
        width: 3
        """,
    )
    return _write(
        tmp_path,
        "run.yaml",
        f"""\
        include: shared.yaml

        # The folders read.
        folders: [a, b]
        per_folder: 0    # 0 = every setup
        count: 2
        verbose: true
        long_text: {"x" * 80}

        # A quick run.
        quick: !scope:quick
          per_folder: 20
        again: !scope:quick
          count: 1

        runnable: !class:{HERE}.Builder
          source_dirs: !ref:folders
          items:
            - !class:{HERE}.Leaf {{size: 3}}
            - !class:{HERE}.Leaf {{size: 4}}
        """,
    )


def test_the_files_own_keys_are_focused_in_file_order_each_with_its_help(config: Path) -> None:
    found = read_config_help(config, exclude=["runnable"])
    assert found.source == "run.yaml"
    assert found.focused == [
        FocusedKey("folders", "The folders read.", "[a, b]"),
        FocusedKey("per_folder", "0 = every setup", "0"),
        FocusedKey("count", "How many to build.", "2"),  # named like Builder's parameter: its help
        FocusedKey("verbose", "Say more.", "true"),
        FocusedKey("long_text", "", ""),  # too long to show its value
    ]
    assert found.scopes == [ConfigScope("quick", "A quick run.")]


@pytest.fixture
def included_scopes(tmp_path: Path) -> Path:
    """A config declaring one scope of its own over two levels of includes that declare more."""
    _write(
        tmp_path,
        "deep.yaml",
        """\
        # Validation only.
        val: !scope:val
          count: 0
        """,
    )
    _write(
        tmp_path,
        "shared.yaml",
        """\
        include: deep.yaml
        count: 1
        # The include's quick run.
        quick: !scope:quick
          count: 5
        # A long run.
        long: !scope:long
          count: 500
        """,
    )
    return _write(
        tmp_path,
        "run.yaml",
        """\
        include: shared.yaml
        quick: !scope:quick
          count: 20
        # Debug output.
        debug: !scope:debug
          count: 2
        """,
    )


def test_an_included_files_scopes_are_the_configs_too_its_own_first(included_scopes: Path) -> None:
    """A scope activates for the whole document wherever it is declared: the file's own come first, then each
    include's in the order they are read; a scope declared twice is one row, the file's comment else the include's."""
    assert read_config_help(included_scopes).scopes == [
        ConfigScope("quick", "The include's quick run."),  # its own block has no comment
        ConfigScope("debug", "Debug output."),
        ConfigScope("long", "A long run."),
        ConfigScope("val", "Validation only."),  # an include's include
    ]
    assert read_config_help(included_scopes, with_docs=False).scopes == []


def test_the_tab_listing_and_help_show_an_included_files_scopes(
    included_scopes: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _text(f"helpdemo run {included_scopes} --")
    start = text.index(f"scopes of {included_scopes}") + 1
    assert [row.split()[1] for row in text[start : start + 4]] == ["quick", "debug", "long", "val"]
    out = _help(included_scopes, capsys, monkeypatch)
    scopes = out[out.index("Scopes of run.yaml") :]
    assert all(f"-s {name}" in scopes for name in ("quick", "debug", "long", "val"))
    assert "Validation only." in scopes


def test_a_key_without_a_comment_takes_the_help_of_the_parameter_its_ref_feeds(tmp_path: Path) -> None:
    config = _write(
        tmp_path,
        "run.yaml",
        f"""\
        folders: []
        runnable: !class:{HERE}.Builder
          source_dirs: !ref:folders
        """,
    )
    assert read_config_help(config, exclude=["runnable"]).focused == [
        FocusedKey("folders", "The folders searched.", "[]")
    ]


def test_an_included_files_keys_and_the_markers_parameters_are_documented_not_focused(config: Path) -> None:
    found = read_config_help(config, exclude=["runnable"])
    assert "label" not in [key.path for key in found.focused]
    assert found.docs["label"] == "Shared label, documented where it is written."
    assert found.docs["width"] == "How many to build."  # shared.yaml's !ref:width feeds Builder.count
    assert found.docs["runnable.items"] == "The items read."
    assert found.docs["widget.items"] == "The items read."


def test_a_command_parameter_is_never_one_of_the_files_settings(config: Path) -> None:
    focused = [key.path for key in read_config_help(config, exclude=["runnable", "count"]).focused]
    assert focused == ["folders", "per_folder", "verbose", "long_text"]
    assert "runnable" in [key.path for key in read_config_help(config).focused]


def test_without_docs_only_the_keys_are_read(config: Path) -> None:
    found = read_config_help(config, with_docs=False)
    assert {"folders", "label", "runnable.items", "widget.items"} <= set(found.keys)
    assert found.focused == [] and found.scopes == [] and found.docs == {}


def test_an_included_files_keys_the_config_does_not_write_are_grouped_under_the_include(config: Path) -> None:
    assert read_config_help(config, exclude=["runnable"]).included == [
        IncludedKeys(
            "shared.yaml",
            [
                FocusedKey("label", "Shared label, documented where it is written.", "base"),
                FocusedKey("widget", "", ""),  # an object: no value to show
                FocusedKey("width", "How many to build.", "3"),
            ],
        )
    ]


def test_a_key_the_config_rewrites_stays_its_own_and_one_two_includes_write_is_listed_once(tmp_path: Path) -> None:
    _write(tmp_path, "deep.yaml", "# Deep's own.\nshared: 1\ndeep_only: 2\nlabel: x\n")
    _write(tmp_path, "base.yaml", "include: deep.yaml\n# Base's own.\nshared: 3\nlabel: y\n")
    config = _write(tmp_path, "run.yaml", "include: base.yaml\nlabel: z\n")
    found = read_config_help(config)
    assert [key.path for key in found.focused] == ["label"]
    assert [(group.source, [key.path for key in group.keys]) for group in found.included] == [
        ("base.yaml", ["shared"]),
        ("deep.yaml", ["deep_only"]),
    ]
    assert found.included[0].keys[0] == FocusedKey("shared", "Base's own.", "3")  # the value the document holds


def test_a_package_include_is_named_as_the_config_writes_it(tmp_path: Path) -> None:
    config = _write(tmp_path, "run.yaml", "include: pkg://liquifai/examples/simple.yaml\nown: 1\n")
    found = read_config_help(config)
    assert [(group.source, [key.path for key in group.keys]) for group in found.included] == [
        ("pkg://liquifai/examples/simple.yaml", ["MyComponent", "debug"])
    ]


def test_the_includes_are_named_without_finding_a_classs_source(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming an include as written asks ``locate`` for the includes alone: finding each class's definition reads its
    source, and a config holds dozens (0.1 s of a four-run workflow's listing)."""
    import confluid.locate as locate_mod

    asked: List[Any] = []
    real = locate_mod.locate

    def recording(text: str, path: Any, kinds: Any = None) -> Any:
        asked.append(kinds)
        return real(text, path, kinds=kinds)

    monkeypatch.setattr(locate_mod, "locate", recording)
    assert [group.source for group in read_config_help(config).included] == ["shared.yaml"]
    assert asked and all(tuple(kinds) == ("include",) for kinds in asked)


def test_a_confluid_whose_locate_takes_no_kinds_still_names_the_includes(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import confluid.locate as locate_mod

    real = locate_mod.locate
    monkeypatch.setattr(locate_mod, "locate", lambda text, path: real(text, path))  # no `kinds`
    assert [group.source for group in read_config_help(config).included] == ["shared.yaml"]


def test_an_include_that_cannot_be_named_shows_its_files_path(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A confluid without its go-to-definition reader names no include: the file's path stands in."""
    monkeypatch.setitem(sys.modules, "confluid.locate", None)
    assert config_help_mod._include_spellings([config]) == {}
    assert [group.source for group in read_config_help(config).included] == [str((config.parent / "shared.yaml"))]


def test_an_unreadable_file_names_none_of_its_includes(tmp_path: Path) -> None:
    assert config_help_mod._include_spellings([tmp_path / "missing.yaml"]) == {}


def test_a_key_holding_an_object_is_no_key_to_type(tmp_path: Path) -> None:
    """A marker parameter holding a block (``outer.inner``) and an excluded key holding one (``runnable``) are
    listed by their parameters only; a top-level block of the file's own (``second``) stays its key."""
    config = _write(
        tmp_path,
        "run.yaml",
        f"""\
        outer: !class:{HERE}.Holder
          inner: !class:{HERE}.Leaf
        second: !class:{HERE}.Leaf
        runnable: !class:{HERE}.Holder
          inner: !class:{HERE}.Leaf
        """,
    )
    keys = read_config_help(config, exclude=["runnable"]).keys
    assert {"outer", "second", "outer.inner.size", "runnable.inner.size", "runnable.tag"} <= set(keys)
    assert not {"outer.inner", "runnable", "runnable.inner"} & set(keys)


def _app() -> LiquifyApp:
    app = LiquifyApp(name="helpdemo")

    @app.script_command("run")
    def run(runnable: Any = None, dry_run: bool = False) -> None:
        """Run the runnable.

        Args:
            runnable: The object bound under runnable.
            dry_run: Run nothing.
        """

    return app


def _text(line: str, width: int = 160) -> List[str]:
    words = line.split(" ")
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1, width=width)
    assert listing is not None
    out: List[str] = []
    for header, rows in listing:
        out.append(header)
        out.extend(display.strip() for _, display in rows if display)
    return out


def test_the_tab_listing_shows_the_configs_options_and_scopes_before_the_commands(config: Path) -> None:
    text = _text(f"helpdemo run {config} --")
    headers = [line for line in text if not line.startswith(("-", "config", "▸"))]
    assert headers == [
        "arguments of helpdemo run",
        f"options of {config}",
        f"scopes of {config}",
        "options of shared.yaml",  # the include's own keys, under its name as run.yaml writes it
        "options of helpdemo run",
        "widget (Builder)",  # included above runnable: the document writes it first
        "runnable (Builder)",
        "global options",
    ]
    own = text[text.index(f"options of {config}") + 1 : text.index(f"scopes of {config}")]
    assert [row.split()[0] for row in own] == ["--folders", "--per_folder", "--count", "--verbose", "--long_text"]
    assert own[0].endswith("The folders read. ([a, b])")
    assert text[text.index(f"scopes of {config}") + 1].split() == ["-s", "quick", "A", "quick", "run."]
    included = text[text.index("options of shared.yaml") + 1 : text.index("options of helpdemo run")]
    assert [row.split()[0] for row in included] == ["--label", "--widget", "--width"]
    assert included[0].endswith("Shared label, documented where it is written. (base)")
    command = text[text.index("options of helpdemo run") + 1 : text.index("widget (Builder)")]
    assert [row.split()[0] for row in command] == ["--dry_run"]  # never --runnable: the object the config binds


def test_tab_never_offers_the_object_a_config_binds(config: Path) -> None:
    tree = tree_mod.serialize_app(_app())
    assert tree["object_params"] == {"run": ["runnable"]}
    for line in ("helpdemo run --runn", f"helpdemo run {config} --runn"):
        words = line.split(" ")
        assert "--runnable" not in comp.complete_from_tree(tree, words, len(words) - 1), line
    words = f"helpdemo run {config} --runn".split(" ")
    assert comp.complete_from_tree(tree, words, len(words) - 1) == ["--runnable.items"]  # its parameters stay
    words = "helpdemo run --".split(" ")
    _, listing = comp.complete_with_listing(tree, words, len(words) - 1)
    assert listing is not None
    assert [candidate for candidate, _ in dict(listing)["options of helpdemo run"]] == ["--dry_run"]


def test_a_marker_parameter_holding_an_object_is_never_offered(tmp_path: Path) -> None:
    config = _write(tmp_path, "run.yaml", f"runnable: !class:{HERE}.Holder\n  inner: !class:{HERE}.Leaf\n")
    words = f"helpdemo run {config} --".split(" ")
    candidates = comp.complete_from_tree(tree_mod.serialize_app(_app()), words, len(words) - 1)
    assert "--size" in candidates and "--tag" in candidates
    assert "--inner" not in candidates and "--runnable" not in candidates


def test_a_blocks_group_lists_every_parameter_of_its_class_written_or_not(tmp_path: Path) -> None:
    config = _write(tmp_path, "run.yaml", f"runnable: !class:{HERE}.Builder\n  source_dirs: [a]\n")
    text = _text(f"helpdemo run {config} --")
    assert text[text.index("runnable (Builder)") + 1 : text.index("global options")] == [
        "--count                       How many to build. (0)",  # not written: its class's default
        "--items                       The items read.",
        "--source_dirs                 The folders searched. ([a])",  # written: its value
        "--verbose                     Say more.",  # a yes/no parameter shows no default
    ]
    words = f"helpdemo run {config} --ver".split(" ")
    assert comp.complete_from_tree(tree_mod.serialize_app(_app()), words, len(words) - 1) == ["--verbose"]


def test_a_parameter_a_top_level_key_feeds_is_listed_with_that_key_only(config: Path) -> None:
    """``source_dirs: !ref:folders`` and the top-level ``count`` / ``verbose`` (broadcast into Builder, which does
    not write them) are listed as ``--folders``, ``--count``, ``--verbose`` — never again with the block."""
    text = _text(f"helpdemo run {config} --")
    assert text[text.index("runnable (Builder)") + 1 : text.index("global options")] == [
        "--runnable.items              The items read."
    ]
    assert read_config_help(config, exclude=["runnable"]).fed == {
        ("Builder", "source_dirs"),
        ("Builder", "count"),
        ("Builder", "verbose"),
    }
    words = f"helpdemo run {config} --runnable.so".split(" ")
    assert comp.complete_from_tree(tree_mod.serialize_app(_app()), words, len(words) - 1) == []


def test_a_parameter_its_block_writes_stays_with_the_block_though_a_top_level_key_shares_its_name(
    tmp_path: Path,
) -> None:
    config = _write(tmp_path, "run.yaml", f"count: 2\nrunnable: !class:{HERE}.Builder\n  count: 5\n")
    text = _text(f"helpdemo run {config} --")
    assert "--runnable.count              How many to build. (5)" in text
    assert "--verbose                     Say more." in text  # nothing feeds it: still the block's


def test_a_written_value_is_shown_and_an_unwritten_parameter_completes_bare(tmp_path: Path) -> None:
    config = _write(tmp_path, "run.yaml", f"runnable: !class:{HERE}.Builder\n  count: 7\n")
    text = _text(f"helpdemo run {config} --")
    block = text[text.index("runnable (Builder)") + 1 : text.index("global options")]
    assert block[0] == "--count                       How many to build. (7)"
    assert "--verbose                     Say more." in block


def test_blocks_are_grouped_the_shallowest_first_then_in_document_order(tmp_path: Path) -> None:
    config = _write(
        tmp_path,
        "run.yaml",
        f"""\
        outer: !class:{HERE}.Holder
          inner: !class:{HERE}.Leaf
        second: !class:{HERE}.Leaf
        """,
    )
    text = _text(f"helpdemo run {config} --")
    assert [line for line in text if "(" in line and not line.startswith("-")] == [
        "outer (Holder)",
        "second (Leaf)",
        "outer.inner (Leaf)",
    ]
    assert text[text.index("outer.inner (Leaf)") + 1] == "--inner.size                  How big the item is. (1)"


def test_a_scope_row_is_shown_but_never_completed(config: Path) -> None:
    words = f"helpdemo run {config} --".split(" ")
    candidates, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1)
    assert listing is not None
    scopes = dict(listing)[f"scopes of {config}"]
    assert [candidate for candidate, _ in scopes] == [""]
    assert "-s quick" not in candidates


def _help(config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch, flag: str = "--help") -> str:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(sys, "argv", ["helpdemo", "run", str(config), flag])
    _app().run()
    return str(capsys.readouterr().out)


def test_help_shows_the_configs_options_then_its_scopes_then_the_flowed_options(
    config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _help(config, capsys, monkeypatch)
    own, scopes, flowed = out.index("Options of run.yaml"), out.index("Scopes of run.yaml"), out.index("flowed from")
    assert own < scopes < flowed
    assert "--per_folder" in out[own:scopes] and "0 = every setup" in out[own:scopes]
    assert "-s quick" in out[scopes:flowed] and "A quick run." in out[scopes:flowed]


def test_help_shows_a_table_per_object_titled_by_its_path_and_class(
    config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    flowed = _help(config, capsys, monkeypatch).split("flowed from", 1)[1]
    assert "runnable (Builder)" in flowed and "Applies to" not in flowed
    table = flowed.split("runnable (Builder)", 1)[1].split("widget (Builder)", 1)[0]
    assert [line.split()[0] for line in table.splitlines() if line.strip().startswith("--")] == [
        "--items"
    ]  # --count and --verbose are the config's own, shown above; --source_dirs is fed by --folders


def test_help_lists_a_list_of_objects_as_one_row_and_no_key_twice(
    config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    flowed = _help(config, capsys, monkeypatch).split("flowed from", 1)[1]
    rows = [line.split()[0] for line in flowed.splitlines() if line.strip().startswith("--")]
    assert "--items" in rows and not any(".Leaf." in row for row in rows)
    assert (
        next(line for line in flowed.splitlines() if line.strip().startswith("--items"))
        .rstrip()
        .endswith("The items read.")
    )
    assert "--count" not in rows and "--verbose" not in rows  # shown above, as the config's own


def test_help_shows_an_included_files_keys_after_the_scopes(
    config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _help(config, capsys, monkeypatch)
    scopes, included, flowed = (
        out.index("Scopes of run.yaml"),
        out.index("Options of shared.yaml"),
        out.index("flowed from"),
    )
    assert scopes < included < flowed
    table = out[included:flowed]
    assert "--label" in table and "Shared label, documented where it is written." in table and "base" in table
    assert "--width" in table and "--widget" in table


def test_help_shows_a_block_the_commands_objects_never_reach_as_tab_groups_it(
    config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    flowed = _help(config, capsys, monkeypatch).split("flowed from", 1)[1]
    assert flowed.index("runnable (Builder)") < flowed.index("widget (Builder)")
    table = flowed.split("widget (Builder)", 1)[1].split("Global Options", 1)[0]
    rows = [line.split() for line in table.splitlines() if line.strip().startswith("--")]
    # its count is fed by !ref:width and its verbose by the top-level verbose: those keys' rows, as in TAB
    assert [row[0] for row in rows] == ["--widget.items", "--source_dirs"]
    text = _text(f"helpdemo run {config} --")
    assert [row.split()[0] for row in text[text.index("widget (Builder)") + 1 : text.index("runnable (Builder)")]] == [
        "--widget.items",
        "--source_dirs",
    ]
    assert rows[1][1] == "Optional[List[str]]" and " ".join(rows[1][2:]) == "The folders searched."
    assert "Option" in table and "Type" in table  # the flowed tables' columns


def test_help_lists_a_block_the_command_reaches_by_reference_once(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write(
        tmp_path,
        "run.yaml",
        f"""\
        leaf: !class:{HERE}.Leaf
        runnable: !class:{HERE}.Holder
          inner: !ref:leaf
        """,
    )
    flowed = _help(config, capsys, monkeypatch).split("flowed from", 1)[1]
    assert "runnable.inner (Leaf)" in flowed
    assert "leaf (Leaf)" not in flowed


def test_docs_renders_the_same_blocks_one_per_line(config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _help(config, capsys, monkeypatch, flag="--docs")
    assert out.index("Options of run.yaml") < out.index("Scopes of run.yaml")
    line = next(line for line in out.splitlines() if line.strip().startswith("--per_folder"))
    assert "= 0" in line and "0 = every setup" in line
    assert any(line.strip().startswith("-s quick") and "A quick run." in line for line in out.splitlines())
    assert not any(".Leaf." in line for line in out.splitlines())
    lines = out.splitlines()
    assert lines[lines.index("runnable (Builder)") + 1].strip().startswith("--items")
    included = lines.index("Options of shared.yaml")
    assert lines[included + 1].split()[:3] == ["--label", "=", "base"]
    block = lines.index("widget (Builder)")
    assert lines[block + 2].split()[:3] == ["--source_dirs", "Optional[List[str]]", "="]


def test_help_still_renders_when_the_configs_settings_cannot_be_read(
    config: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import liquifai.core as core

    def broken(*_: Any, **__: Any) -> Any:
        raise ValueError("unreadable")

    monkeypatch.setattr(core, "read_config_help", broken)
    out = _help(config, capsys, monkeypatch)
    assert "The config's own settings are unavailable: unreadable" in out
    assert "Options of run.yaml" not in out and "flowed from" in out
