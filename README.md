# Liquifai

**Liquifai** is a modern, type-safe application framework for Python, designed to bind **Loggair** and **Confluid** into high-performance CLI applications.

## Key Features
- **Zero-Boilerplate Startup:** Automatically handles logging and hierarchical config initialization.
- **Type-Safe CLI:** Streamlined argument parsing and validation.
- **Dependency Injection:** Seamlessly injects configured **Confluid** objects into your commands.
- **Rich Integration:** Beautiful terminal output and progress reporting via **Rich**.
- **Modular Commands:** Register and compose multiple tools into a single entry point.
- **Operations → CLI + MCP:** register a pure operation once (`@app.operation`) and surface it as an auto-generated CLI command *and* an MCP tool (`make_mcp_tools`).
- **SDK Bridge (provisional):** mirror an existing Python SDK as a full CLI/MCP app by decorating its client classes (`liquifai.bridge`).
- **Shell Completion:** bash/zsh/fish tab completion for commands, options, overrides — and live positional values.
- **Host Facts:** every run carries the machine's `os` and `device` — as scopes (`!scope:os=darwin`) and as values (`${platform.os}`), with no env vars and nothing typed.

## Documentation

Each topic has its own guide, and every guide has a runnable companion script in [`examples/`](https://github.com/Gearlux/liquifai/tree/main/examples):

| Guide | What it covers | Example |
|---|---|---|
| [Commands & Dependency Injection](https://github.com/Gearlux/liquifai/blob/main/docs/commands-and-di.md) | `@command` / `@script_command`, config promotion (with `./config/` + XDG search paths and its DEBUG provenance notice), DI block lookup, positional arguments, flow modes | `positionals_app.py`, `promotion_app.py` et al. |
| [CLI Overrides](https://github.com/Gearlux/liquifai/blob/main/docs/cli-overrides.md) | The override grammar (`--key value`, dotted keys, polarity, add/delete), the dropped-token warning, short options and strict flags | `overrides_app.py` |
| [Global Flags](https://github.com/Gearlux/liquifai/blob/main/docs/global-flags.md) | Log control (`--level`, `--log-dir`, …), `--scope` / dimension flags, `--debug`, `--docs` | `global_flags_app.py` |
| [Host Facts](https://github.com/Gearlux/liquifai/blob/main/docs/host-facts.md) | The `os` / `device` scopes and the injected `platform` namespace — `!scope:os=darwin` blocks and `${platform.os}` values | `host_facts_app.py` |
| [Error Handling](https://github.com/Gearlux/liquifai/blob/main/docs/error-handling.md) | The typed `LiquifaiError` hierarchy and the CLI failure contract | `failure_contract.py` |
| [Shell Completion](https://github.com/Gearlux/liquifai/blob/main/docs/shell-completion.md) | Install, aliases, workspace-local setup, dynamic & dependent positional values | `completion_providers.py` |
| [Architecture Decisions](https://github.com/Gearlux/liquifai/blob/main/docs/architecture.md) | Why liquifai is shaped this way: the hand-rolled parser, out-of-process completion, the shared argv walk, who owns settability | — (records carry inline examples) |

For everything at once, [`examples/pypeek/`](https://github.com/Gearlux/liquifai/tree/main/examples/pypeek)
is a complete, installable showcase app — a small PyPI query CLI whose
`<package>` completes from your installed distributions (offline) and whose
`<version>` completes from the live PyPI API (dependent completion with the
background self-heal cache), plus the failure contract, override broadcast,
and dry-run in action.

Its counterpart [`examples/linefit/`](https://github.com/Gearlux/liquifai/tree/main/examples/linefit)
is a **training-style** installable CLI (think Lightning CLI): `fit` /
`validate` / `test` / `predict` script commands with config promotion, a YAML
that instantiates the model/data/optimizer via confluid tags (the optimizer
`!lazy:` until the run supplies the live model), dotted CLI overrides reaching
any knob, and a `print-config` verb that dumps the fully-merged configuration
as a reloadable recipe.

## Design Goals & Requirements

### CLI Framework
- **Zero-Boilerplate Startup:** Automate the bootstrapping of Loggair and Confluid.
- **Contextual Scripting:** Support `@app.script_command()` which promotes the first positional argument to a configuration file path.
- **Type-Safe DI:** Inject fully-configured objects directly into command signatures based on type hints.
- **Default Command Redirection:** Run a default command when no subcommand is given — its positionals (and a script default's config) bind without the name: `my-app w.yaml`.

### User Experience
- **Abbreviation Support:** Allow brief aliases for the main executable (e.g. `ma` for `my-app`).
- **Dynamic Overrides:** Support `--KEY VAL` CLI overrides with broadcast injection into nested configurations.
- **Observability Overrides:** Provide CLI flags for log control (`--level`, `--console-level`,
  `--file-level`, `--log-dir`) as the top layer of a four-step hierarchy — flag, then
  `LOGGAIR_*` environment variable, then config file, then default. A flag you do not type
  is left unset rather than defaulted, so the lower layers still apply
  ([global flags](https://github.com/Gearlux/liquifai/blob/main/docs/global-flags.md)).

### Architecture
- **Config Promotion:** Automatically look for `<arg>.yaml` if the first argument is not a registered command.
- **Smart DI Lookup:** Search configuration blocks by both argument name and class name to ensure hydration.

## Quick Start

```python
from liquifai import LiquifyApp, LiquifyContext
from confluid import configurable

@configurable
class MyTrainer:
    def __init__(self, lr: float = 0.01):
        self.lr = lr

app = LiquifyApp(name="my-app")

@app.command()
def train(trainer: MyTrainer) -> None:
    # 'trainer' is automatically loaded via Confluid and injected
    print(f"Training with lr={trainer.lr}")

if __name__ == "__main__":
    app.run()
```

## CLI Overrides

Any token left after the command, positionals and global flags is a config override:
`--max_epochs 10`, `--trainer.lr=0.001`, `model.dropout=0.2`, `+new_key=1`, `~stale.key`. Flags behave
exactly as if their keys were appended to the end of the config file, in the order typed. A token that
matches no override form is not applied and is reported with a warning. Single-letter command aliases
(`@app.command(short={"p": "port"})`) and `strict_flags=True` apps, which refuse a flag that names no
parameter, are covered in the
[CLI Overrides guide](https://github.com/Gearlux/liquifai/blob/main/docs/cli-overrides.md).

## Error Handling

Liquifai raises typed exceptions rooted at `LiquifaiError`; each also inherits the builtin it replaces
(`ValueError`, `KeyError`, `TypeError`, `FileNotFoundError`), so pre-existing `except` code keeps working.
Under `app.run()` a `LiquifaiError` or `confluid.ConfluidError` becomes one clean `Error: …` line and
exit code 1; with `--debug` on the line the exception propagates with its full traceback. The exception
table and the CLI failure contract are in the
[Error Handling guide](https://github.com/Gearlux/liquifai/blob/main/docs/error-handling.md).

## Operations, MCP Tools, and the SDK Bridge

Beyond plain `@app.command` handlers, liquifai has a *pure operations* model: a
function returning a JSON-serializable dict is registered once and surfaced on
every front-end.

```python
app = LiquifyApp(name="dataset")

@app.operation(presentation="list", columns=(("name", "Name"),))
def dataset_list(conn: MyConn) -> dict:      # `conn` injected, never CLI-visible
    return {"items": [...], "count": 3}

app.set_context_factory(build_conn)          # how CLI calls get their `conn`
app.set_presenter(render_result)             # how CLI renders the returned dict
app.build_commands()                         # -> CLI command `dataset list`

from liquifai import make_mcp_tools          # -> MCP tools from the same ops
for tool in make_mcp_tools(app):
    mcp_server.tool()(tool)
```

`@app.operation` is the ONE ops-registration path (the pre-release
`@app.command(presentation=...)` dual-mode was removed). CLI
positionals are derived from the operation signature: every keyword-only
parameter without a default becomes a positional slot.

The context reaches an operation **by name**, as its `conn` parameter; an
operation without one is called without a context, and its MCP tool does not
advertise the MCP context factory's parameters. A tool returns every failure as
`{"error": "…"}`, a context the factory could not build included. An operation
parameter named like a factory parameter (both with `dry_run`, say) is refused
when the tools are built, with a `CommandDefinitionError` naming the operation.

### SDK bridge (`liquifai.bridge`, provisional)

For CLIs that mirror an existing Python SDK, `liquifai.bridge` generates the
operations themselves: decorate a subclass of the real SDK client and declare
how each method maps to a CLI/MCP operation.

```python
from liquifai.bridge import P, SdkBridge, custom, expose

bridge = SdkBridge(conn_cls=MyConn)              # your @configurable connection

@bridge.group(name="widget", sub="widget", aliases=["w"])
class WidgetClient(sdk.WidgetClient):            # inherits the real SDK class

    @expose(verb="info", presentation="fields", params=[P("name")])
    def get_widget(self) -> None: ...            # method name = the SDK method

    @expose(verb="delete", presentation="status", params=[P("name")],
            status_word="deleted", status_echo=("name",))
    def delete_widget(self) -> None: ...

    @custom(verb="export", presentation="status")
    def widget_export(conn, *, name: str) -> dict:   # escape hatch: conn-first body
        """Export one widget somewhere the declarative engine can't express."""
        ...

bridge.mount(root_app)                           # CLI sub-app `widget` / `w`
```

The knobs you can tune, and where they live:

| Knob | Where | Purpose |
|---|---|---|
| `conn_cls=` | `SdkBridge(...)` | Concrete connection class baked into op signatures (contract: `dry_run` attr + `get_client()` — see `BridgeConnection`) |
| `configure=` | `SdkBridge(...)` | Hook run per group after all ops register (wire context factory / presenter, then `build_commands()`); default just builds commands |
| `adapters=` | `SdkBridge(...)` | Extra `P(adapt=...)` string→value parsers merged over `DEFAULT_ADAPTERS` (`list`/`csv`/`kv`/`props`/`str`) |
| `policies=` | `SdkBridge(...)` | Spec→op builders merged over the built-ins (`call`, `items`); register SDK dialects (e.g. a paginated `list`) here |
| `target=` | `SdkBridge(...)` | `(conn, sub) -> call target` override for non-attribute sub-client access |
| `shape_status=` | `SdkBridge(...)` | Override the default `status`-presentation result shaper |
| `P(cli, sdk=, default=, adapt=, ...)` | `@expose(params=[...])` | One CLI param mapped onto an SDK kwarg; no default = required = CLI positional |
| extra `**options` | `@expose(...)` | Policy-specific knobs (e.g. a list policy's `client_filter=` / `extras=`) — opaque to the generic bridge |

Every generated op honors `conn.dry_run` **before** touching the SDK client,
returning a `{"dry_run": True, "command", "action", "call"}` descriptor — so
`--help`, MCP schemas, and tests run without credentials.

> **Provisional — outside the version contract.** `liquifai.bridge` has a
> single production consumer so far; its API may change in 0.x minor releases
> without a deprecation cycle, and it may move to its own distribution. It is
> not re-exported from the top-level `liquifai` package, so `import liquifai`
> never reaches it. Depend on it as **`liquifai[bridge]`** so the dependency on
> an unstable surface is recorded in your own metadata. Rationale:
> [Architecture Decisions §6](https://github.com/Gearlux/liquifai/blob/main/docs/architecture.md).

## Installation

Released on [PyPI](https://pypi.org/project/liquifai/):

```bash
pip install liquifai
```

The installed version (read from the package metadata):

```python
import liquifai
print(liquifai.__version__)  # e.g. 0.3.0
```

### Optional extras

| Extra | What it opts into |
|---|---|
| `liquifai[bridge]` | The **provisional** `liquifai.bridge` subpackage (see above). It adds no requirements today — it is a contract marker that records, in your metadata, a dependency on a surface excluded from the version contract. |

## Environment variables

Both tune shell completion; neither is needed to run an app.

| Variable | Default | Set it to `1` to |
|---|---|---|
| `LIQUIFAI_NO_LAZY_COMPLETE` | unset (refresh on) | stop a TAB from starting a background refresh. Without it, a TAB that finds a completion cache missing, outdated or stale detaches `<app> --refresh-completions` or `<app> --refresh-completion-value …`, and those run your value providers — a provider that queries a server queries it then. |
| `LIQUIFAI_BG_REFRESH` | unset (off) | refresh stale value caches in the background after every successful command. |

```bash
export LIQUIFAI_NO_LAZY_COMPLETE=1   # TAB reads the caches and never starts anything
```

Details: [Shell Completion](https://github.com/Gearlux/liquifai/blob/main/docs/shell-completion.md).

## License
MIT
