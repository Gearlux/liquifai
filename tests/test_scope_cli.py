"""CLI-side scope argument handling.

Confluid owns scope resolution; liquifai only translates the CLI surface:

* ``--scope NAME``                — boolean activation forwarded verbatim
* ``--scope KEY=VAL``             — keyed activation forwarded verbatim
* ``--KEY VAL`` / ``--KEY=VAL``   — promoted to ``scopes`` when ``KEY`` was
                                    declared as a scope dimension in the YAML

These tests assert that argv → ``context.scopes`` translation works without
touching confluid's flow pipeline (only ``confluid.load_config`` and
``discover_dimensions`` get exercised, which are pure-dict operations).
"""

from pathlib import Path
from typing import List

from liquifai import LiquifyApp


def _make_app() -> LiquifyApp:
    return LiquifyApp(name="testapp")


def _run(app: LiquifyApp, argv: List[str]) -> List[str]:
    """Drive ``_parse_globals`` + ``_bind_dimension_flags`` and return active scopes."""
    import confluid

    config_path = Path(argv[0]) if argv and not argv[0].startswith("-") else None
    remaining = argv[1:] if config_path is not None else argv

    final_config_path, scopes, _, _, final_argv = app._parse_globals(remaining)
    if final_config_path is not None:
        config_path = final_config_path
    if config_path is not None and config_path.exists():
        raw = confluid.load_config(config_path)
        scopes, final_argv = app._bind_dimension_flags(scopes, raw, final_argv)
    return scopes


def _write_cfg(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# `--scope` direct form
# ---------------------------------------------------------------------------


def test_scope_flag_boolean(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path / "c.yaml", "x: 1\n")
    app = _make_app()
    assert _run(app, [str(cfg), "--scope", "debug"]) == ["debug"]


def test_scope_flag_keyed(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path / "c.yaml", "x: 1\n")
    app = _make_app()
    assert _run(app, [str(cfg), "--scope", "task=classification"]) == ["task=classification"]


def test_scope_short_flag(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path / "c.yaml", "x: 1\n")
    app = _make_app()
    assert _run(app, [str(cfg), "-s", "debug"]) == ["debug"]


def test_scope_comma_separated(tmp_path: Path) -> None:
    """`--scope a,b` is split into two entries (legacy CLI shape)."""
    cfg = _write_cfg(tmp_path / "c.yaml", "x: 1\n")
    app = _make_app()
    assert _run(app, [str(cfg), "--scope", "debug,prod"]) == ["debug", "prod"]


# ---------------------------------------------------------------------------
# Implicit `--KEY` form — promoted only when the YAML declares that dimension
# ---------------------------------------------------------------------------


def test_implicit_dim_space_form(tmp_path: Path) -> None:
    cfg = _write_cfg(
        tmp_path / "c.yaml",
        """
if_cls: !scope:task=classification
  m: classifier
""",
    )
    app = _make_app()
    assert _run(app, [str(cfg), "--task", "classification"]) == ["task=classification"]


def test_implicit_dim_equals_form(tmp_path: Path) -> None:
    cfg = _write_cfg(
        tmp_path / "c.yaml",
        """
if_cls: !scope:task=classification
  m: classifier
""",
    )
    app = _make_app()
    assert _run(app, [str(cfg), "--task=classification"]) == ["task=classification"]


def test_undeclared_dim_passes_through(tmp_path: Path) -> None:
    """`--task` is treated as a config override when no scope declares `task`."""
    cfg = _write_cfg(tmp_path / "c.yaml", "x: 1\n")
    app = _make_app()
    # No scopes activated; --task X stays in remaining argv (visible via
    # _bind_dimension_flags returning the value untouched).
    import confluid

    raw = confluid.load_config(cfg)
    scopes, remaining = app._bind_dimension_flags([], raw, ["--task", "classification"])
    assert scopes == []
    assert remaining == ["--task", "classification"]


def test_mixed_scope_and_implicit(tmp_path: Path) -> None:
    cfg = _write_cfg(
        tmp_path / "c.yaml",
        """
if_dbg: !scope:debug
  x: 1
if_cls: !scope:task=classification
  m: classifier
""",
    )
    app = _make_app()
    assert _run(app, [str(cfg), "--scope", "debug", "--task", "classification"]) == [
        "debug",
        "task=classification",
    ]


def test_implicit_dim_does_not_consume_flag_follower(tmp_path: Path) -> None:
    """`--task --other ...` is not treated as `--task=--other`."""
    cfg = _write_cfg(
        tmp_path / "c.yaml",
        """
if_cls: !scope:task=classification
  m: classifier
""",
    )
    app = _make_app()
    import confluid

    raw = confluid.load_config(cfg)
    scopes, remaining = app._bind_dimension_flags([], raw, ["--task", "--other"])
    assert scopes == []
    assert remaining == ["--task", "--other"]
