"""A config that starts other runs lists their options too, and keeps its own knobs in full.

A block of a class a package registers under ``liquifai.config_refs`` names a config another run reads
(``Step(config="child_a.yaml")``). Such a config's TAB listing and ``--help`` show, after the options it lists itself,
the options of those runs — their top-level keys and the parameters of their top-level blocks, BARE as they are
typed, since a key the config does not use is handed to every run. A run that cannot be read lists nothing; a key the
config lists itself is not repeated. A top-level mapping some parameter reads through ``!ref:`` is a group of knobs,
each listed under its full name (``--flow.workers``) with its own comment: a bare ``--workers`` is another key.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, List, Optional

import pytest
from confluid import configurable

from liquifai import LiquifyApp
from liquifai import completion as comp
from liquifai.completion import config_refs
from liquifai.completion import tree as tree_mod
from liquifai.completion.config_help import read_config_help

HERE = __name__

#: The registered-names reader as the package ships it, taken before ``_registered`` replaces it for each test.
_SHIPPED_REFERENCE_NAMES = config_refs.reference_names


@configurable
class Step:
    """One step of a flow: run a config.

    Args:
        config: The config the step runs.
        name: The name `only` picks the step by.
    """

    def __init__(self, config: str = "", name: str = "") -> None:
        self.config = config
        self.name = name


@configurable
class Flow:
    """Run the steps in order.

    Args:
        steps: The steps.
        only: The names of the steps to run.
    """

    def __init__(self, steps: Optional[List[Any]] = None, only: Optional[List[str]] = None) -> None:
        self.steps = steps
        self.only = only


@configurable
class Processor:
    """Process the records.

    Args:
        show_progress: Draw a progress bar.
        workers: How many processes build the records.
        limit: The most records built.
    """

    def __init__(self, show_progress: bool = False, workers: int = 1, limit: int = 0) -> None:
        self.show_progress = show_progress
        self.workers = workers
        self.limit = limit


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``Step.config`` is registered as naming a config, as an installed package's entry point does."""
    monkeypatch.setattr(config_refs, "reference_names", lambda: {f"{HERE}.Step": {"config"}})
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LIQUIFAI_NO_LAZY_COMPLETE", "1")
    monkeypatch.chdir(tmp_path)


def _write(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text(textwrap.dedent(body))
    return path


@pytest.fixture
def flow(tmp_path: Path) -> Path:
    """A flow of two runs; the first has a key (``label``) the second lacks."""
    _write(
        tmp_path,
        "child_a.yaml",
        f"""\
        # What the run is called.
        label: a
        # Written again.
        overwrite: false
        runnable: !class:{HERE}.Processor
          workers: 4
        """,
    )
    _write(
        tmp_path,
        "child_b.yaml",
        f"""\
        overwrite: false
        runnable: !class:{HERE}.Processor
        """,
    )
    return _write(
        tmp_path,
        "flow.yaml",
        f"""\
        flow:
          # The steps to make, by name.
          only: []
          # How many processes each step may use.
          workers: auto
        runnable: !class:{HERE}.Flow
          only: !ref:flow.only
          steps:
            - !class:{HERE}.Step {{config: child_a.yaml, name: a}}
            - !class:{HERE}.Step {{config: child_b.yaml, name: b}}
        """,
    )


def _groups(config: Path) -> dict:
    return {
        group.source: [(key.path, key.help, key.value) for key in group.keys] for group in read_config_help(config).runs
    }


def test_the_options_of_the_runs_are_listed_bare_with_the_help_of_their_own_files(flow: Path) -> None:
    groups = _groups(flow)
    assert list(groups) == ["the runs", "the runs' runnable (Processor)"]
    assert groups["the runs"] == [
        ("label", "What the run is called. [1 of 2 runs]", "a"),
        ("overwrite", "Written again.", "false"),
    ]
    assert groups["the runs' runnable (Processor)"] == [
        ("limit", "The most records built.", "0"),
        ("show_progress", "Draw a progress bar.", ""),
        ("workers", "How many processes build the records.", ""),  # one run writes 4, the other takes the default
    ]


def test_the_runs_options_are_read_without_their_docs_for_a_tab_that_lists_nothing(flow: Path) -> None:
    found = read_config_help(flow, with_docs=False).runs
    assert [key.path for group in found for key in group.keys] == [
        "label",
        "overwrite",
        "limit",
        "show_progress",
        "workers",
    ]
    assert all(key.help == "" or key.help.startswith("[") for group in found for key in group.keys)


def test_a_key_the_config_lists_itself_is_not_repeated_among_the_runs(flow: Path, tmp_path: Path) -> None:
    """``only`` is a parameter of the flow (``--only``); a run's own ``only`` would be the same bare flag."""
    _write(tmp_path, "child_b.yaml", f"only: [x]\nrunnable: !class:{HERE}.Processor\n")
    keys = [key.path for group in read_config_help(flow).runs for key in group.keys]
    assert "only" not in keys and "workers" in keys


def test_a_run_that_cannot_be_read_lists_nothing_and_the_others_still_do(flow: Path, tmp_path: Path) -> None:
    (tmp_path / "child_a.yaml").write_text("runnable: !class:{unterminated\n")
    keys = [key.path for group in read_config_help(flow).runs for key in group.keys]
    assert "workers" in keys and "label" not in keys


def test_a_run_that_names_the_flow_again_does_not_loop(flow: Path, tmp_path: Path) -> None:
    _write(
        tmp_path,
        "child_b.yaml",
        f"runnable: !class:{HERE}.Flow\n  steps:\n    - !class:{HERE}.Step {{config: flow.yaml}}\n",
    )
    assert "workers" in [key.path for group in read_config_help(flow).runs for key in group.keys]


def test_a_config_that_starts_nothing_lists_no_runs(tmp_path: Path) -> None:
    assert read_config_help(_write(tmp_path, "solo.yaml", f"runnable: !class:{HERE}.Processor\n")).runs == []


def test_without_a_registered_reference_the_runs_are_not_read(flow: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_refs, "reference_names", lambda: {})
    assert read_config_help(flow).runs == []


def test_a_mapping_a_parameter_reads_lists_its_keys_in_full_with_their_comments(flow: Path) -> None:
    found = read_config_help(flow, exclude=["runnable"])
    assert [(key.path, key.help, key.value) for key in found.focused] == [
        ("flow.only", "The steps to make, by name.", "[]"),
        ("flow.workers", "How many processes each step may use.", "auto"),
    ]
    assert found.knobs == {"flow", "flow.only", "flow.workers"}


def test_a_mapping_nothing_reads_stays_one_row(tmp_path: Path) -> None:
    config = _write(tmp_path, "block.yaml", f"{'Processor'}:\n  workers: 2\nrunnable: !class:{HERE}.Processor\n")
    found = read_config_help(config, exclude=["runnable"])
    assert [key.path for key in found.focused] == ["Processor"] and found.knobs == set()


def _app() -> LiquifyApp:
    app = LiquifyApp(name="flowdemo")

    @app.script_command("run")
    def run(runnable: Any = None, dry_run: bool = False) -> None:
        """Run the runnable.

        Args:
            runnable: The object bound under runnable.
            dry_run: Run nothing.
        """

    return app


def _listing(line: str) -> dict:
    words = line.split(" ")
    _, listing = comp.complete_with_listing(tree_mod.serialize_app(_app()), words, len(words) - 1, width=160)
    assert listing is not None
    return {header: [flag for flag, _ in rows] for header, rows in listing}


def test_tab_offers_the_runs_options_bare_and_the_knobs_in_full(flow: Path) -> None:
    candidates = comp.complete_from_tree(tree_mod.serialize_app(_app()), f"flowdemo run {flow} --".split(" "), 3)
    assert {"--show_progress", "--workers", "--label", "--flow.workers", "--flow.only"} <= set(candidates)
    assert "--flow" not in candidates  # the mapping is no option, its keys are
    # the knob is not shortened to the bare `--workers`, which the runs take
    assert candidates.count("--workers") == 1


def test_the_listing_groups_the_runs_options_after_the_configs_own(flow: Path) -> None:
    listing = _listing(f"flowdemo run {flow} --")
    titles = list(listing)
    assert titles.index(f"options of {flow}") < titles.index("options of the runs")
    assert titles.index("options of the runs") < titles.index("options of the runs' runnable (Processor)")
    assert listing[f"options of {flow}"] == ["--flow.only", "--flow.workers"]
    assert listing["options of the runs' runnable (Processor)"] == ["--limit", "--show_progress", "--workers"]


def test_a_run_option_the_config_already_offers_is_the_configs(flow: Path, tmp_path: Path) -> None:
    """The flow's own bare ``--only`` (a parameter of ``Flow``) wins over a run's key of that name."""
    _write(tmp_path, "child_b.yaml", f"only: [x]\nrunnable: !class:{HERE}.Processor\n")
    candidates = comp.complete_from_tree(tree_mod.serialize_app(_app()), f"flowdemo run {flow} --on".split(" "), 3)
    assert candidates.count("--only") <= 1


def test_the_installed_distributions_are_scanned_once_however_many_runs_are_read(
    flow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan opens the metadata of every installed distribution (43 ms with 2 900 of them); reading a flow reads
    the flow and each of its runs, and used to scan again for each — 0.21 s of a 0.31 s read."""
    scans: List[str] = []

    class Registered:
        name = f"{HERE}.Step.config"

    def entry_points(group: str) -> List[Registered]:
        scans.append(group)
        return [Registered()]

    monkeypatch.setattr(config_refs, "entry_points", entry_points)
    monkeypatch.setattr(config_refs, "reference_names", _SHIPPED_REFERENCE_NAMES)
    _SHIPPED_REFERENCE_NAMES.cache_clear()
    try:
        assert [key.path for group in read_config_help(flow).runs for key in group.keys]  # the runs were read
        read_config_help(flow, with_docs=False)
        assert scans == [config_refs.GROUP]
    finally:
        _SHIPPED_REFERENCE_NAMES.cache_clear()


def _counting(monkeypatch: pytest.MonkeyPatch, name: str) -> List[Any]:
    """Wrap ``confluid.<name>`` so each call is recorded by its first argument."""
    import confluid

    real = getattr(confluid, name)
    calls: List[Any] = []

    def counted(first: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(str(first))
        return real(first, *args, **kwargs)

    monkeypatch.setattr(confluid, name, counted)
    return calls


@pytest.fixture
def sharing_flow(tmp_path: Path) -> Path:
    """Two runs naming the same class and including the same file."""
    _write(tmp_path, "shared.yaml", "# Written again.\noverwrite: false\n")
    for name in ("child_a", "child_b"):
        _write(tmp_path, f"{name}.yaml", f"include: shared.yaml\nrunnable: !class:{HERE}.Processor\n")
    return _write(
        tmp_path,
        "flow.yaml",
        f"""\
        runnable: !class:{HERE}.Flow
          steps:
            - !class:{HERE}.Step {{config: child_a.yaml}}
            - !class:{HERE}.Step {{config: child_b.yaml}}
        """,
    )


def test_the_runs_of_one_read_share_the_classes_and_files_they_have_in_common(
    sharing_flow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs name one class and include one file: each is read from source once per call, not once per run."""
    params = _counting(monkeypatch, "source_params")
    docs = _counting(monkeypatch, "source_param_docs")
    notes = _counting(monkeypatch, "document_notes")
    found = read_config_help(sharing_flow)
    assert [key.path for group in found.runs for key in group.keys].count("workers") == 1  # still both runs' options
    processor = f"{HERE}.Processor"
    assert params.count(processor) == 1 and docs.count(processor) == 1
    shared = str(sharing_flow.parent / "shared.yaml")
    assert notes.count(shared) == 1, notes


def test_a_second_call_reads_the_sources_again_so_an_edit_between_two_tabs_is_seen(
    sharing_flow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = _counting(monkeypatch, "source_params")
    read_config_help(sharing_flow)
    first = len(params)
    read_config_help(sharing_flow)
    assert first > 0 and len(params) == 2 * first
