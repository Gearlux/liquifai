"""Pins the stdlib-only rule for the TAB fast path (``liquifai-complete``).

Every shell TAB starts a fresh interpreter that runs :func:`liquifai._fast_complete.main`.
It must import nothing outside the standard library and liquifai itself: confluid, loggair,
rich or yaml would cost tens of milliseconds per TAB (architecture record 2). A source grep
cannot see a heavy import reached through another module, so each case runs the real entry
point in a CLEAN subprocess and lists every top-level module it loaded that was not already
loaded at interpreter start-up (``.pth`` hooks such as an editable install's finder load
before any code runs and are not the fast path's doing).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from liquifai import LiquifyApp
from liquifai.completion import refresh_value_caches, write_cache

#: A name no installed binary has: the missing-cache case detaches `<app> --refresh-completions`,
#: which must fail (and be swallowed) rather than start a real program.
APP = "liquifai-fast-path-probe"

_PROBE = """
import json, sys
before = set(sys.modules)
import liquifai._fast_complete as fast_complete
if {run_main}:
    sys.argv = ["liquifai-complete", {app!r}]
    fast_complete.main()
loaded = {{name.split(".")[0] for name in set(sys.modules) - before}}
foreign = sorted(n for n in loaded if n != "liquifai" and n not in sys.stdlib_module_names)
print("FOREIGN=" + json.dumps(foreign))
"""


def _foreign_imports(tmp_path: Path, run_main: bool, env: Optional[Dict[str, str]] = None) -> List[str]:
    """Run the fast path in a clean interpreter; return the non-stdlib top-level modules it loaded."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(run_main=run_main, app=APP)],
        cwd=tmp_path,  # never the project folder, which would shadow the installed package
        env={**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache"), **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(line for line in result.stdout.splitlines() if line.startswith("FOREIGN="))
    return list(json.loads(line[len("FOREIGN=") :]))


@pytest.fixture
def cached_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LiquifyApp:
    """An app with a plain command and a provider-backed positional, its caches written."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    app = LiquifyApp(name=APP)

    @app.command()
    def greet(name: str = "world") -> None: ...

    @app.command(positionals=["dataset"], completions={"dataset": lambda: ["mnist", "cifar10"]})
    def pull(dataset: str) -> None: ...

    write_cache(app)
    refresh_value_caches(app)
    return app


def test_importing_the_fast_path_loads_only_the_standard_library(tmp_path: Path) -> None:
    assert _foreign_imports(tmp_path, run_main=False) == []


def test_completing_a_command_loads_only_the_standard_library(tmp_path: Path, cached_app: LiquifyApp) -> None:
    assert _foreign_imports(tmp_path, True, {"COMP_WORDS": f"{APP} gr", "COMP_CWORD": "1"}) == []


def test_completing_a_flag_loads_only_the_standard_library(tmp_path: Path, cached_app: LiquifyApp) -> None:
    assert _foreign_imports(tmp_path, True, {"COMP_WORDS": f"{APP} greet --", "COMP_CWORD": "2"}) == []


def test_completing_a_cached_positional_value_loads_only_the_standard_library(
    tmp_path: Path, cached_app: LiquifyApp
) -> None:
    assert _foreign_imports(tmp_path, True, {"COMP_WORDS": f"{APP} pull ", "COMP_CWORD": "2"}) == []


def test_a_missing_cache_and_its_detached_refresh_load_only_the_standard_library(tmp_path: Path) -> None:
    """No cache: the fast path detaches a refresh (here of a program that does not exist)."""
    assert _foreign_imports(tmp_path, True, {"COMP_WORDS": f"{APP} ", "COMP_CWORD": "1"}) == []
    assert (tmp_path / "cache" / "liquifai" / f"{APP}.pending").exists()  # the refresh was attempted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
