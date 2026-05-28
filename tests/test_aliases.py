"""Sub-app alias support: aliases resolve on the CLI but fold into one help row."""

import sys
from typing import Any

from liquifai import LiquifyApp
from liquifai.completion import complete_from_tree, serialize_app
from liquifai.context import set_context


def _build() -> tuple[LiquifyApp, list[str]]:
    parent = LiquifyApp(name="t")
    dataset = LiquifyApp(name="dataset", description="Dataset operations.")
    parent.add_app(dataset, "dataset", aliases=["ds"])
    calls: list[str] = []

    @dataset.command(name="list")
    def list_cmd() -> None:
        calls.append("listed")

    return parent, calls


def _run(app: LiquifyApp, argv: list[str], monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["t", *argv])
    set_context(None)  # type: ignore[arg-type]
    app.run()


def test_canonical_name_resolves(monkeypatch: Any) -> None:
    app, calls = _build()
    _run(app, ["dataset", "list"], monkeypatch)
    assert calls == ["listed"]


def test_alias_resolves(monkeypatch: Any) -> None:
    app, calls = _build()
    _run(app, ["ds", "list"], monkeypatch)
    assert calls == ["listed"]


def test_help_folds_alias_into_one_row(monkeypatch: Any, capsys: Any) -> None:
    app, _ = _build()
    _run(app, ["--help"], monkeypatch)
    out = capsys.readouterr().out
    # canonical name carries the alias in parentheses ...
    assert "dataset (ds)" in out
    # ... and the alias does NOT appear as its own group row.
    assert "\nds " not in out
    assert out.count("Dataset operations.") == 1


def test_add_app_without_aliases_unchanged(monkeypatch: Any, capsys: Any) -> None:
    parent = LiquifyApp(name="t")
    parent.add_app(LiquifyApp(name="solo", description="Solo group."), "solo")
    _run(parent, ["--help"], monkeypatch)
    out = capsys.readouterr().out
    assert "solo" in out
    assert "(" not in out.split("solo")[1].split("\n")[0]  # no alias parens on the row


def _completion_app() -> LiquifyApp:
    parent = LiquifyApp(name="t")
    sub = LiquifyApp(name="dataset")
    parent.add_app(sub, "dataset", aliases=["ds"])

    @sub.command("list")
    def list_cmd() -> None:
        pass

    return parent


def test_completion_suggests_canonical_not_alias() -> None:
    tree = serialize_app(_completion_app())
    cands = complete_from_tree(tree, ["t", ""], 1)
    assert "dataset" in cands
    assert "ds" not in cands


def test_completion_alias_still_resolves_for_descent() -> None:
    # `t ds <TAB>` must still descend into the dataset sub-app and list its commands.
    tree = serialize_app(_completion_app())
    assert "list" in complete_from_tree(tree, ["t", "ds", ""], 2)
