"""A command-line key reaches the document's ``${…}`` interpolations, as a line appended to the document would.

Flags behave as if their keys were appended to the document in the order typed (§4). The document's own
``${key}`` placeholders are filled when the document is resolved, so a key merged in only AFTER that is seen by a
``!ref:`` (resolved later) but never by a ``${…}`` inside a string (a dotted ``${a.b}`` reads the document; a bare
``${name}`` is an environment variable): ``run.name=zz`` renamed a run while its
``output_dir: …/${run.name}`` kept the scope's folder. These tests drive a real ``app.run()``.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from liquifai import LiquifyApp
from liquifai.context import set_context

CONFIG = """\
run:
  name: base
fit:
  size: 4
output_dir: /runs/${run.name}
label: ${run.name}-${fit.size}
fast: !scope:fast
  run.name: fast
"""


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *argv: str, config: str = CONFIG) -> Dict[str, Any]:
    """The command's view of the resolved document after ``app.run()`` with ``argv`` after the config."""
    seen: List[Dict[str, Any]] = []
    app = LiquifyApp(name="interp-app")

    @app.script_command()
    def go(output_dir: str = "", label: str = "") -> None:
        seen.append({"output_dir": output_dir, "label": label})

    path = tmp_path / "cfg.yaml"
    path.write_text(config)
    monkeypatch.setattr(sys, "argv", ["interp-app", "go", str(path), *argv])
    set_context(None)
    app.run()
    (view,) = seen
    return view


def test_without_command_line_keys_the_document_resolves_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(tmp_path, monkeypatch) == {
        "output_dir": "/runs/base",
        "label": "base-4",
    }


@pytest.mark.parametrize("spelling", [["run.name=zz"], ["--run.name", "zz"], ["--run.name=zz"]])
def test_a_dotted_key_reaches_every_interpolation_that_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: List[str]
) -> None:
    view = _run(tmp_path, monkeypatch, *spelling)
    assert view["output_dir"] == "/runs/zz"
    assert view["label"] == "zz-4"


def test_every_key_of_a_mapping_is_reached_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(tmp_path, monkeypatch, "fit.size=8")["label"] == "base-8"
    assert _run(tmp_path, monkeypatch, "fit.size=8", "run.name=zz")["label"] == "zz-8"


def test_the_command_line_beats_a_scope_as_a_line_after_it_would(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(tmp_path, monkeypatch, "--scope", "fast")["output_dir"] == "/runs/fast"
    assert _run(tmp_path, monkeypatch, "--scope", "fast", "run.name=zz")["output_dir"] == "/runs/zz"


def test_of_two_typings_of_one_key_the_later_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(tmp_path, monkeypatch, "run.name=first", "run.name=second")["output_dir"] == "/runs/second"


def test_a_top_level_mapping_given_whole_replaces_its_keys_for_the_interpolations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(tmp_path, monkeypatch, "run={name: whole}")["output_dir"] == "/runs/whole"


def test_a_deleted_key_is_still_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    view = _run(tmp_path, monkeypatch, "run.name=zz", "~label")
    assert view["output_dir"] == "/runs/zz"
    assert view["label"] == ""  # the parameter's default: the key is gone


def test_a_positional_is_still_bound_and_a_flag_still_wins_over_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: List[str] = []
    app = LiquifyApp(name="interp-app")

    @app.command(positionals=["who"])
    def hello(who: str = "", greeting: str = "") -> None:
        seen.append(f"{greeting} {who}")

    config = tmp_path / "cfg.yaml"
    config.write_text("who: nobody\ngreeting: hi ${who}\n")
    for argv, expected in [
        (["hello", "ada", "-c", str(config), "greeting=hey"], "hey ada"),
        (["hello", "ada", "-c", str(config), "--who", "bob", "greeting=hey"], "hey bob"),
    ]:
        monkeypatch.setattr(sys, "argv", ["interp-app", *argv])
        set_context(None)
        app.run()
    assert seen == ["hey ada", "hey bob"]
