"""``liquifai.__version__`` is the installed distribution's version, read from its metadata.

The version is written ONCE, in ``pyproject.toml``. A typed-in ``__version__ = "..."`` is a second
copy that drifts: before this rule three workspace packages reported ``0.2.0`` while their
``pyproject.toml`` said ``0.1.0``.

liquifai reads it LAZILY, on first access. Every shell TAB runs the completion fast path, which
imports the ``liquifai`` package; reading the metadata costs ~15 ms (measured 2026-09-26) against
~11 ms for the whole ``liquifai.completion`` import, so an eager read would more than double it.
"""

import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

import pytest

import liquifai


def test_version_is_the_installed_distribution_version() -> None:
    assert liquifai.__version__ == version("liquifai")


def test_the_from_import_spelling_works_too() -> None:
    from liquifai import __version__

    assert __version__ == version("liquifai")


def test_importing_liquifai_does_not_read_the_metadata() -> None:
    """A fresh interpreter: `import liquifai` must leave `importlib.metadata` unimported."""
    probe = "import sys, liquifai; print('importlib.metadata' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


def test_an_uninstalled_source_tree_reports_a_dev_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr("importlib.metadata.version", not_installed)
    assert liquifai.__version__ == "0.0.0.dev0"
