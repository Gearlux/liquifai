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

## Documentation

Each topic has its own guide, and every guide has a runnable companion script in [`examples/`](https://github.com/Gearlux/liquifai/tree/main/examples):

| Guide | What it covers | Example |
|---|---|---|
| [Commands & Dependency Injection](https://github.com/Gearlux/liquifai/blob/main/docs/commands-and-di.md) | `@command` / `@script_command`, config promotion (with `./config/` + XDG search paths and its DEBUG provenance notice), DI block lookup, positional arguments, flow modes | `positionals_app.py`, `promotion_app.py` et al. |
| [CLI Overrides](https://github.com/Gearlux/liquifai/blob/main/docs/cli-overrides.md) | The override grammar (`--key value`, dotted keys, polarity, add/delete) and the dropped-token warning | `overrides_app.py` |
| [Global Flags](https://github.com/Gearlux/liquifai/blob/main/docs/global-flags.md) | Log control (`--level`, `--log-dir`, …), `--scope` / dimension flags, `--debug`, `--docs` | `global_flags_app.py` |
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
- **Default Command Redirection:** Support running a default command if no subcommand is provided.

### User Experience
- **Abbreviation Support:** Allow brief aliases for the main executable (e.g. `ma` for `my-app`).
- **Dynamic Overrides:** Support `--KEY VAL` CLI overrides with broadcast injection into nested configurations.
- **Observability Overrides:** Provide CLI flags for log control (`--level`, `--console-level`, `--file-level`, `--log-dir`).

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

Any token left after the command, positionals, and global flags is parsed as a
config override. All forms interoperate in one invocation:

| Form | Example | Effect |
|---|---|---|
| `--key value` | `--max_epochs 10` | set `max_epochs` (broadcast into matching nested configs) |
| `--key=value` | `--trainer.lr=0.001` | equals form; dotted keys target nested blocks |
| `key=value` | `model.dropout=0.2` | bare form, no dashes |
| `--key+` / `--key-` | `--debug+` | polarity: explicit `True` / `False` |
| `--key` | `--verbose` | implicit `True` |
| `+key=value` | `+new_feature=true` | add a new key |
| `~key` | `~trainer.stale` | delete the dotted key from the config |

A token that matches **none** of these forms is not applied and liquifai logs a
**warning** naming it (`Ignoring unrecognized CLI token 'lr' — expected one of:
…`). Previously such tokens were dropped silently — a typo'd `lr 0.1` instead
of `--lr 0.1` would run the whole job on defaults without a trace.

The single source of truth for the global-flag vocabulary and token
classification is `liquifai/grammar.py` (stdlib-only); the parser, `--help`,
and shell completion all derive from it, so they cannot drift apart. Override
parsing/application lives in `liquifai/overrides.py`, and annotation-driven
dependency injection in `liquifai/di.py`.

## Error Handling

Liquifai raises typed exceptions rooted at `LiquifaiError`; each also inherits the builtin it replaces, so pre-existing `except ValueError:` / `except KeyError:` code keeps working unchanged:

| Exception | Also a | Raised when |
|---|---|---|
| `CommandDefinitionError` | `ValueError` | a `@script_command` / `@operation` / bridge declaration is invalid (bad `flow_mode` / `presentation`, or an `SdkBridge` group naming an unregistered policy / adapter) |
| `UnknownOperationError` | `KeyError` | `set_completions()` names an operation that is not registered |
| `UnsupportedShellError` | `ValueError` | a completion shell is not one of bash / zsh / fish |

Configuration-loading failures propagate Confluid's own hierarchy (`confluid.ConfluidError` and subclasses) — `LiquifaiError` covers CLI-definition errors only.

### CLI failure contract

When a command runs via `app.run()`:

| Failure | Behavior | Exit code |
|---|---|---|
| `LiquifaiError` or `confluid.ConfluidError` (bad config, unresolvable class, invalid declaration) | One clean `Error: …` line on the console; full traceback written to the log file at DEBUG | 1 |
| Same, with `--debug` on the line | The exception **propagates** — full traceback on the console | (Python default) |
| Missing `--config` file | Dedicated `Configuration file not found` message | 1 |
| Unknown command/group | `Unknown command or group` (or help when no default command exists) | 1 |
| Any other exception | A bug — always propagates with its traceback, never converted to a clean exit | (Python default) |

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

### Optional extras

| Extra | What it opts into |
|---|---|
| `liquifai[bridge]` | The **provisional** `liquifai.bridge` subpackage (see above). It adds no requirements today — it is a contract marker that records, in your metadata, a dependency on a surface excluded from the version contract. |

## License
MIT
