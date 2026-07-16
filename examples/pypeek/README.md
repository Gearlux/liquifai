# pypeek — the liquifai showcase app

A small, real CLI over the [PyPI JSON API](https://docs.pypi.org/api/json/) that
demonstrates every major liquifai capability in ~250 lines
([pypeek.py](pypeek.py)): pure **operations** generating the CLI (and MCP
tools), a **`@configurable` client** injected via the context factory with CLI
**override broadcast**, **static + dependent positional completion** with the
lazy self-heal cache, the **CLI failure contract**, and `liquifai.apps`
**entry-point discovery**.

It lives in a subdirectory (not a flat `examples/*.py` script) on purpose:
CI executes the flat example scripts, and the completion machinery needs a
real console script on `PATH` — the detached self-heal helper literally runs
`pypeek --refresh-completion-value …` by name.

## Install & set up

```bash
pip install -e examples/pypeek        # from the liquifai repo root
pypeek --install-completion           # bash/zsh/fish; restart or re-source your shell
```

Because the pyproject declares the `liquifai.apps` entry point, `pypeek` is
also discovered instantly (no subprocess probe) by the workspace-wide
`liquifai-install-completions --target-rc …` bootstrap.

## The commands

```bash
pypeek list --prefix li               # local inventory (offline)
pypeek list --prefix rich --outdated  # compare installed vs PyPI latest
pypeek info rich                      # summary metadata from PyPI
pypeek info rich --local              # same, from installed metadata (offline)
pypeek versions rich --limit 5        # releases, newest first (--yanked to include yanked)
pypeek files rich 15.0.0 --kind wheel # files of one release
```

All argument forms interoperate: `pypeek info rich`, `pypeek info package=rich`,
and `pypeek info --package rich` are the same call. A mistyped override token
is **warned about**, never silently dropped. `--help` / `--docs` render the
same code-extracted option documentation.

## The completion-caching walkthrough (the star of the demo)

**Static positional** — `<package>` completes from the distributions installed
in your environment (offline, instant):

```text
$ pypeek info li<TAB>
librt  lightning  lightning-utilities  liquifai  ...
```

The first-ever TAB may show nothing: the value cache doesn't exist yet, so the
fast path returns the placeholder AND detaches a background refresh — the next
TAB has real values. No manual step needed.

**Dependent positional** — `<version>` completes from the live PyPI API, keyed
on the package you already typed:

```text
$ pypeek files rich <TAB>
<version>                     # first TAB: placeholder + detached background refresh
$ pypeek files rich <TAB>     # a second later…
<version-updated>  15.0.0  14.3.4  14.3.3  ...
```

The `<version-updated>` hint is the change notice: the background self-heal
actually altered the cached values. It disappears after ~60 s or as soon as
you type a real prefix. Caches live under `~/.cache/liquifai/pypeek.values/`
(command tree: `~/.cache/liquifai/pypeek.json`) — delete them to replay the
walkthrough.

`pypeek --refresh-completions` pre-enumerates everything eagerly instead —
**note**: that runs the version provider for up to 200 cached packages (one
PyPI request each); the lazy per-package self-heal above is the intended path.

**Per-command option flags** are completion candidates too, derived from each
operation's signature:

```text
$ pypeek versions rich --<TAB>
--limit  --local  --yanked  ...
```

## Configuration & overrides

The injected `PyPI` client is a confluid `@configurable`; its constructor
knobs are reachable from YAML or the command line with zero plumbing:

```bash
pypeek info rich --timeout 3          # broadcast into PyPI(timeout=...)
pypeek info rich --dry_run+           # print the would-be request, don't send it
pypeek -c pypeek.yaml info rich       # same knobs from a config file
```

See [pypeek.yaml](pypeek.yaml) for the sample config. Failure contract in
action: `pypeek info no-such-pkg` prints one clean `Error: …` line and exits 1;
add `--debug` for the full traceback.

## MCP tools from the same operations

The CLI commands are generated from pure operations, so an MCP server gets
them for free:

```python
from pypeek import app
from liquifai import make_mcp_tools

for tool in make_mcp_tools(app):      # pypeek_list / pypeek_info / ...
    mcp_server.tool()(tool)
```
