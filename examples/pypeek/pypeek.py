"""pypeek — a small PyPI query CLI that demonstrates liquifai end to end.

One file shows the whole framework surface:

* **Operations** (:meth:`LiquifyApp.operation`) — each command is a pure
  dict-returning function; ``build_commands()`` generates the CLI handlers and
  ``liquifai.make_mcp_tools(app)`` would expose the same functions as MCP tools.
* **Dependency injection** — the ``conn: PyPI`` first parameter is supplied by
  the context factory, never by the caller; the ``@configurable PyPI`` client
  is built through confluid, so ``--timeout 3`` / ``--base_url …`` /
  ``--dry_run+`` CLI overrides broadcast straight into it.
* **Completion caching** — ``<package>`` completes from the distributions
  installed in the current environment (STATIC provider: offline, instant) and
  ``<version>`` completes from the live PyPI JSON API keyed on the typed
  package (DEPENDENT provider: exercises the value cache, the detached lazy
  self-heal, and the ``<version-updated>`` notice).
* **Failure contract** — network/lookup failures raise :class:`PyPeekError`
  (a ``LiquifaiError``), so the CLI prints ONE clean error line and exits 1;
  rerun with ``--debug`` for the full traceback.

Try it (from the liquifai repo root)::

    pip install -e examples/pypeek
    pypeek --install-completion
    pypeek list --prefix li
    pypeek info rich
    pypeek files rich <TAB>        # dependent completion self-heals in the background

See README.md next to this file for the full walkthrough.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, distribution, distributions
from typing import Any, Dict, List, Literal, Optional

import confluid
from rich.console import Console
from rich.table import Table

from liquifai import LiquifyApp, get_context
from liquifai.exceptions import LiquifaiError

console = Console()
app = LiquifyApp(name="pypeek", description="Query the PyPI JSON API (liquifai demo app).")


class PyPeekError(LiquifaiError):
    """A user-facing pypeek failure (package not found, network trouble).

    Subclassing ``LiquifaiError`` opts these into liquifai's CLI failure
    contract: one clean ``Error: …`` line + exit 1, full traceback in the log
    and on the console under ``--debug``.
    """


# ---------------------------------------------------------------------------
# The injected client — a zero-arg-constructible @configurable
# ---------------------------------------------------------------------------


@confluid.configurable
class PyPI:
    """Minimal PyPI JSON API client (stdlib urllib, no extra dependencies).

    Args:
        base_url: Root of the JSON API (override to point at a mirror/devpi).
        timeout: Per-request timeout in seconds.
        user_agent: HTTP User-Agent header sent with every request.
        dry_run: Print the would-be request instead of performing it.
    """

    def __init__(
        self,
        base_url: str = "https://pypi.org/pypi",
        timeout: float = 10.0,
        user_agent: str = "pypeek/0.1 (liquifai demo)",
        dry_run: bool = False,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.user_agent = user_agent
        self.dry_run = dry_run

    def url_for(self, package: str, version: Optional[str] = None) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/{package}/{version}/json" if version else f"{base}/{package}/json"

    def get(self, package: str, version: Optional[str] = None) -> Dict[str, Any]:
        """GET the JSON document for a package (optionally one release)."""
        url = self.url_for(package, version)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return dict(json.load(response))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                what = f"{package} {version}" if version else package
                raise PyPeekError(f"{what!r} not found on {self.base_url}") from exc
            raise PyPeekError(f"PyPI returned HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PyPeekError(f"cannot reach {self.base_url}: {exc}") from exc


# ---------------------------------------------------------------------------
# Completion value providers (see docs/shell-completion.md)
# ---------------------------------------------------------------------------


def installed_packages() -> List[str]:
    """STATIC provider (zero-arg): distributions installed in this environment.

    Runs only at refresh time (never on the TAB hot path) and needs no
    network, so the ``<package>`` slot completes instantly and offline.
    """
    names = {dist.metadata["Name"] for dist in distributions()}
    return sorted((n for n in names if n), key=str.lower)


def package_versions(inputs: Dict[str, str]) -> List[str]:
    """DEPENDENT provider (one-arg): versions of the ALREADY-TYPED package.

    Receives the earlier positionals (``{"package": …}``) and hits the live
    PyPI API — which is exactly why it runs only at refresh time / in the
    detached lazy self-heal helper, never while you're TABbing.
    """
    package = inputs.get("package", "")
    return _versions_newest_first(PyPI().get(package).get("releases", {}))


def _versions_newest_first(releases: Dict[str, Any]) -> List[str]:
    """Order release keys newest-first by upload time.

    The JSON API's ``releases`` dict is keyed in LEXICOGRAPHIC order (``9.9``
    sorts above ``14.3``), so the upload timestamp of each release's first
    file is the reliable recency signal; file-less releases sort last.
    """

    def uploaded(version: str) -> str:
        files = releases.get(version) or []
        return str(files[0].get("upload_time_iso_8601", "")) if files else ""

    return sorted(releases, key=uploaded, reverse=True)


# ---------------------------------------------------------------------------
# Operations — pure dict-returning functions; CLI + MCP surfaces are generated
# ---------------------------------------------------------------------------


def _human_size(n_bytes: int) -> str:
    size = float(n_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n_bytes} B"


@app.operation(
    presentation="list",
    columns=(("name", "Package"), ("version", "Installed"), ("latest", "Latest")),
    title="Installed packages",
    empty="No installed packages matched",
)
def pypeek_list(conn: PyPI, *, prefix: str = "", limit: int = 0, outdated: bool = False) -> Dict[str, Any]:
    """List distributions installed in the current environment.

    Args:
        prefix: Keep only names starting with this prefix (case-insensitive).
        limit: Show at most this many entries (0 = no limit).
        outdated: Also fetch each package's latest version from PyPI and keep
            only outdated entries — one request per package, so combine with
            --prefix / --limit.
    """
    names = [n for n in installed_packages() if n.lower().startswith(prefix.lower())]
    if limit > 0:
        names = names[:limit]
    if outdated and conn.dry_run:
        return {"dry_run": f"{conn.base_url}/<name>/json — one request for each of {len(names)} package(s)"}
    items: List[Dict[str, Any]] = []
    for name in names:
        entry: Dict[str, Any] = {"name": name, "version": _installed_version(name), "latest": ""}
        if outdated:
            try:
                entry["latest"] = conn.get(name)["info"]["version"]
            except PyPeekError:
                continue  # unpublished/renamed dists can't be outdated-checked
            if entry["latest"] == entry["version"]:
                continue
        items.append(entry)
    return {"items": items, "count": len(items)}


@app.operation(
    presentation="fields",
    title="Package: {package}",
    completions={"package": installed_packages},
)
def pypeek_info(conn: PyPI, *, package: str, local: bool = False) -> Dict[str, Any]:
    """Show summary metadata for one package.

    Args:
        package: The distribution name (e.g. ``rich``).
        local: Read the locally installed metadata instead of querying PyPI
            (fully offline).
    """
    if local:
        meta = _installed_metadata(package)
        return {
            "name": meta.get("Name", package),
            "version": meta.get("Version", ""),
            "summary": meta.get("Summary", ""),
            "license": meta.get("License-Expression") or meta.get("License", ""),
            "requires_python": meta.get("Requires-Python", ""),
            "source": "installed metadata (--local)",
        }
    if conn.dry_run:
        return {"dry_run": conn.url_for(package)}
    info = conn.get(package)["info"]
    return {
        "name": info.get("name", package),
        "version": info.get("version", ""),
        "summary": info.get("summary", ""),
        "license": info.get("license_expression") or info.get("license", ""),
        "homepage": info.get("home_page") or (info.get("project_urls") or {}).get("Homepage", ""),
        "requires_python": info.get("requires_python", ""),
        "source": conn.base_url,
    }


@app.operation(
    presentation="list",
    columns=(("version", "Version"), ("uploaded", "Uploaded"), ("yanked", "Yanked")),
    title="Versions of {package}",
    empty="No versions found",
    completions={"package": installed_packages},
)
def pypeek_versions(
    conn: PyPI, *, package: str, limit: int = 10, yanked: bool = False, local: bool = False
) -> Dict[str, Any]:
    """List a package's release versions, newest first.

    Args:
        package: The distribution name.
        limit: Show at most this many versions (0 = all).
        yanked: Include yanked releases (hidden by default).
        local: Show only the locally installed version (fully offline).
    """
    if local:
        return {"items": [{"version": _installed_version(package), "uploaded": "", "yanked": ""}], "count": 1}
    if conn.dry_run:
        return {"dry_run": conn.url_for(package)}
    releases = conn.get(package).get("releases", {})
    items: List[Dict[str, Any]] = []
    for version in _versions_newest_first(releases):
        files = releases[version]
        is_yanked = bool(files) and all(f.get("yanked") for f in files)
        if is_yanked and not yanked:
            continue
        uploaded = files[0].get("upload_time_iso_8601", "")[:10] if files else ""
        items.append({"version": version, "uploaded": uploaded, "yanked": "yes" if is_yanked else ""})
        if 0 < limit <= len(items):
            break
    return {"items": items, "count": len(items)}


@app.operation(
    presentation="list",
    columns=(("filename", "File"), ("kind", "Type"), ("size", "Size")),
    title="Files of {package} {version}",
    empty="No files in this release",
    completions={"package": installed_packages, "version": package_versions},
)
def pypeek_files(
    conn: PyPI, *, package: str, version: str, kind: Literal["all", "wheel", "sdist"] = "all"
) -> Dict[str, Any]:
    """List the files of one release (the ``<version>`` TAB-completes from PyPI).

    Args:
        package: The distribution name.
        version: The release version (dependent completion: candidates come
            from the package you already typed).
        kind: Keep only wheels or sdists (``all`` shows both).
    """
    if conn.dry_run:
        return {"dry_run": conn.url_for(package, version)}
    urls = conn.get(package, version).get("urls", [])
    items = [
        {
            "filename": f.get("filename", ""),
            "kind": f.get("packagetype", "").replace("bdist_wheel", "wheel"),
            "size": _human_size(int(f.get("size", 0))),
        }
        for f in urls
    ]
    if kind != "all":
        items = [i for i in items if i["kind"] == kind]
    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# Local-metadata helpers
# ---------------------------------------------------------------------------


def _installed_metadata(package: str) -> Dict[str, str]:
    try:
        meta = distribution(package).metadata
    except PackageNotFoundError:
        raise PyPeekError(f"{package!r} is not installed in this environment (drop --local to query PyPI)") from None
    # PackageMetadata is iterable over field names (repeated fields keep the last).
    return {key: str(meta[key]) for key in meta}


def _installed_version(package: str) -> str:
    return _installed_metadata(package).get("Version", "")


# ---------------------------------------------------------------------------
# CLI wiring: context factory + presenter + generated commands
# ---------------------------------------------------------------------------


def _build_client() -> PyPI:
    """Build the injected ``conn`` from the active liquifai context.

    ``materialize`` with the loaded config as context means confluid
    BROADCASTS matching top-level keys into the constructor — so a YAML
    ``timeout: 3`` or a CLI override ``--timeout 3`` / ``--dry_run+``
    configures the client with no plumbing here.
    """
    ctx = get_context()
    cfg = ctx.config_data if ctx is not None and isinstance(ctx.config_data, dict) else {}
    client = confluid.materialize(confluid.Target("PyPI"), context=cfg)
    assert isinstance(client, PyPI)
    return client


def _present(result: Any, presentation: str, *, columns: Any = (), title: str = "", empty: str = "", **_: Any) -> None:
    """Render an operation's result dict for the terminal (liquifai presenter hook)."""
    if not isinstance(result, dict):
        console.print(result)
        return
    if "dry_run" in result:
        console.print(f"[yellow]DRY RUN[/yellow] GET {result['dry_run']}")
        return
    if presentation == "list":
        items = result.get("items", [])
        if not items:
            console.print(f"[dim]{empty or 'No results'}[/dim]")
            return
        table = Table(title=title or None, box=None, header_style="bold cyan")
        for _key, header in columns:
            table.add_column(header)
        for item in items:
            table.add_row(*(str(item.get(key, "")) for key, _header in columns))
        console.print(table)
    elif presentation == "fields":
        if title:
            console.print(f"[bold]{title}[/bold]")
        width = max((len(k) for k in result), default=0)
        for key, value in result.items():
            console.print(f"  [bold cyan]{key.ljust(width)}[/bold cyan]  {value}")
    else:  # "status"
        console.print(f"[green]OK[/green] {result}")


app.set_context_factory(_build_client)
app.set_presenter(_present)
app.build_commands()


def main() -> Any:
    """Console-script entry point."""
    return app.run()


if __name__ == "__main__":
    main()
