"""A bare ``--NAME`` that reached nothing, where the config declares ``!scope:NAME``, names ``--scope NAME``.

A keyed dimension binds to ``--KEY VAL`` (``--task seg`` is ``--scope task=seg``); a bare scope gets no flag of its
own, so ``--quick`` beside a ``!scope:quick`` block is the config override ``quick: true``. When nothing takes it, the
unused-override warning says which spelling switches the scope on, instead of the generic spelling advice. The flag
itself still activates nothing, and a class that accepts ``quick`` still receives it without a warning.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

import confluid
import pytest
from confluid import configurable

from liquifai import LiquifyApp
from liquifai import overrides as overrides_module
from liquifai.context import set_context


@configurable
class HintCatalog:
    def __init__(self, catalog: str = "full.csv", max_cases: int = 0) -> None:
        self.catalog = catalog
        self.max_cases = max_cases


@configurable
class HintQuickTaker:
    def __init__(self, quick: bool = False) -> None:
        self.quick = quick


CATALOG = f"{HintCatalog.__module__}.HintCatalog"
TAKER = f"{HintQuickTaker.__module__}.HintQuickTaker"

CONFIG = f"""\
catalog_csv: full.csv
max_cases: 0
quick: !scope:quick
  catalog_csv: quick.csv
  max_cases: 20
runnable: !class:{CATALOG}
  catalog: !ref:catalog_csv
  max_cases: !ref:max_cases
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    set_context(None)  # type: ignore[arg-type]
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg_sys"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    saved = confluid.get_app_name()
    yield tmp_path
    confluid.set_app_name(saved)


@pytest.fixture
def warned(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """The unused-override warnings — loggair does not reach caplog."""
    collected: List[str] = []
    monkeypatch.setattr(overrides_module.logger, "warning", lambda msg, *a, **k: collected.append(str(msg)))
    return collected


def _app() -> "tuple[LiquifyApp, List[Any]]":
    """A config runner like a ``run <config.yaml>`` command: DI builds the runnable it is handed."""
    app = LiquifyApp(name="hint-app")
    built: List[Any] = []

    @app.script_command(flow_mode="auto", default=True)
    def run(runnable: Any = None) -> None:
        """Run a config."""
        built.append(runnable)

    return app, built


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> List[Any]:
    app, built = _app()
    monkeypatch.setattr(sys, "argv", ["hint-app", *args])
    app.run()
    return built


def _write(tmp_path: Path, text: str = CONFIG, name: str = "catalog.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def _about(key: str, messages: List[str]) -> List[str]:
    return [m for m in messages if m.startswith(f"Override {key!r} matched nothing")]


def test_a_bare_flag_naming_a_bare_scope_is_warned_with_the_scope_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str]
) -> None:
    _write(tmp_path)
    (runnable,) = _run(monkeypatch, "catalog.yaml", "--quick")
    (message,) = _about("quick", warned)
    assert message == (
        "Override 'quick' matched nothing and was ignored: no configured object accepts 'quick'. "
        "The configuration declares a scope 'quick': to activate it, use `--scope quick` (`-s quick`)."
    )
    # The flag still switches nothing on: the run is the full catalog.
    assert (runnable.catalog, runnable.max_cases) == ("full.csv", 0)


def test_the_scope_spelling_activates_it_and_warns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str]
) -> None:
    _write(tmp_path)
    (runnable,) = _run(monkeypatch, "catalog.yaml", "-s", "quick")
    assert warned == []
    assert (runnable.catalog, runnable.max_cases) == ("quick.csv", 20)


def test_a_dump_gives_the_same_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path)
    assert _run(monkeypatch, "catalog.yaml", "--quick", "--dump-config") == []  # a dump runs nothing
    (message,) = _about("quick", warned)
    assert "use `--scope quick` (`-s quick`)" in message
    assert "catalog: full.csv" in capsys.readouterr().out


def test_a_flag_naming_no_scope_keeps_the_spelling_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str]
) -> None:
    _write(tmp_path)
    _run(monkeypatch, "catalog.yaml", "--quik")
    (message,) = _about("quik", warned)
    assert "Check the spelling against the declared parameters" in message
    assert "--scope" not in message


def test_a_scope_declared_in_an_included_file_is_named_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str]
) -> None:
    _write(tmp_path, "fast: !scope:fast\n  max_cases: 5\n", name="base.yaml")
    _write(tmp_path, f"include: base.yaml\nmax_cases: 0\nrunnable: !class:{CATALOG}\n  max_cases: !ref:max_cases\n")
    _run(monkeypatch, "catalog.yaml", "--fast")
    (message,) = _about("fast", warned)
    assert "use `--scope fast` (`-s fast`)" in message


def test_a_class_that_accepts_the_key_still_receives_it_unwarned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str]
) -> None:
    _write(tmp_path, f"quick: !scope:quick\n  other: 1\nrunnable: !class:{TAKER}\n")
    (runnable,) = _run(monkeypatch, "catalog.yaml", "--quick")
    assert warned == []
    assert runnable.quick is True


def test_a_dotted_key_whose_head_is_a_scope_keeps_the_dotted_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warned: List[str]
) -> None:
    _write(tmp_path)
    _run(monkeypatch, "catalog.yaml", "--quick.max_cases", "3")
    (message,) = _about("quick.max_cases", warned)
    assert "addresses an instance by its YAML 'name:'" in message
    assert "--scope" not in message


@pytest.mark.parametrize("where", ["no path", "missing file"])
def test_without_a_readable_config_the_warning_keeps_the_spelling_advice(
    tmp_path: Path, warned: List[str], where: str
) -> None:
    """Judged straight from a report: no file to read scopes from means no hint, never an error."""
    from confluid.fluid import Target

    from liquifai import di

    path = None if where == "no path" else tmp_path / "gone.yaml"
    argv_overrides: Dict[str, Any] = {"quick": True}
    cfg = overrides_module.apply_overrides({"runnable": Target(HintCatalog)}, argv_overrides, [])
    with confluid.collect_report() as report:
        with di.confluid_active_context(cfg):
            di.deep_flow(cfg["runnable"], context=cfg)
    overrides_module.warn_unused_overrides(argv_overrides, report, path)
    (message,) = _about("quick", warned)
    assert "Check the spelling" in message
