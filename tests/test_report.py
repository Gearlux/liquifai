"""Tests for :mod:`liquifai.report` — the Rich help renderers.

Covers both layouts of ``show_configuration``: the default Rich table and the
``--docs`` one-option-per-line view. (The discovery-walker tests that used to
share this file were removed with ``liquifai/discovery.py`` — its
``get_configurable_paths`` duplicated ``confluid.get_hierarchy_from_instance``,
which is what ``report`` actually calls.)
"""

from typing import Any

import confluid

from liquifai.report import show_configuration


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


# ---------------------------------------------------------------------------
# Q1 — line-by-line ("lines" layout) documentation rendering
# ---------------------------------------------------------------------------


@confluid.configurable
class DocModel:
    def __init__(self, lr: float = 0.01, layers: int = 3) -> None:
        """A documented model.

        Args:
            lr: Learning rate for the optimizer.
            layers: Number of hidden layers.
        """
        self.lr = lr
        self.layers = layers


def test_lines_layout_renders_flags_and_docs(capsys: Any) -> None:
    show_configuration(DocModel(), title="Doc Options", layout="lines")
    out = capsys.readouterr().out
    # Title, both option flags, their docstrings, and default values appear —
    # one option per line (no Rich grid header like "Current/Default Value").
    assert "Doc Options" in out
    assert "--lr" in out and "--layers" in out
    assert "Learning rate for the optimizer." in out
    assert "Number of hidden layers." in out
    assert "0.01" in out and "3" in out
    assert "Current/Default Value" not in out  # the table header — must NOT appear


def test_lines_layout_shows_config_map_value(capsys: Any) -> None:
    show_configuration(DocModel(), config_map={"DocModel": {"lr": 0.5}}, layout="lines")
    out = capsys.readouterr().out
    assert "0.5" in out  # current value from config_map wins over the default


def test_lines_layout_empty_hierarchy(capsys: Any) -> None:
    @confluid.configurable
    class Empty:
        def __init__(self) -> None:
            pass

    show_configuration(Empty(), title="Nothing", layout="lines")
    out = capsys.readouterr().out
    assert "Nothing" in out
    assert "no configurable options" in out


def test_table_layout_unchanged_default(capsys: Any) -> None:
    # Default layout is still the Rich table (regression guard for Q1).
    show_configuration(DocModel(), title="Doc Options")
    out = capsys.readouterr().out
    assert "Current/Default Value" in out
