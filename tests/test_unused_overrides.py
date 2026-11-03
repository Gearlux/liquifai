"""``overrides.unused_overrides``: the keys the configuration report says matched nothing, in the order typed.

It is the question ``warn_unused_overrides`` asks, answered as a list, so a command that does something else with such
a key than warn (``recordstream run`` hands it to the runs of a workflow) asks the same one: the report's spelling of a
key is its head when dotted (``--opt.lr`` registers ``opt``), the key itself when bare or glob-headed.
"""

from typing import List

import pytest
from confluid.report import ConfigurationReport

from liquifai import overrides as overrides_module
from liquifai.overrides import unused_overrides, warn_unused_overrides


def _report(candidates: List[str], used: List[str]) -> ConfigurationReport:
    report = ConfigurationReport()
    report.add_config_keys(candidates)
    for key in used:
        report.mark_used(key)
    return report


def test_the_unused_keys_come_back_in_the_order_typed() -> None:
    report = _report(["zzz", "lr", "aaa"], used=["lr"])
    assert unused_overrides({"zzz": 1, "lr": 0.1, "aaa": 2}, report) == ["zzz", "aaa"]


def test_a_dotted_key_is_judged_by_its_head_and_a_glob_by_its_whole_key() -> None:
    report = _report(["opt", "**.lr", "model"], used=["model"])
    typed = {"opt.lr": 0.1, "**.lr": 0.2, "model.depth": 3}
    assert unused_overrides(typed, report) == ["opt.lr", "**.lr"]


def test_a_key_the_report_never_saw_is_not_called_unused() -> None:
    """A command that materializes nothing registers no candidates: the answer is empty, never a guess."""
    assert unused_overrides({"lr": 0.1}, ConfigurationReport()) == []


def test_the_warning_names_exactly_those_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    said: List[str] = []
    monkeypatch.setattr(overrides_module.logger, "warning", lambda msg, *a, **k: said.append(str(msg)))
    report = _report(["zzz", "lr"], used=["lr"])
    warn_unused_overrides({"zzz": 1, "lr": 0.1}, report)
    assert len(said) == 1 and said[0].startswith("Override 'zzz' matched nothing")
