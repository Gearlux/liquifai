# Commands, Config Promotion & Dependency Injection

A `LiquifyApp` binds **Loggair** (logging) and **Confluid** (config/DI) into a CLI
with zero bootstrap code. Command function **signatures define the dependency
contract**: liquifai inspects the type annotations and injects fully-configured
Confluid objects.

```python
from liquifai import LiquifyApp
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

## How DI finds the config block

For each `@configurable`-annotated parameter, liquifai selects the config block
by **membership, not truthiness**:

1. a block keyed by the **class name** (`MyTrainer:`) wins,
2. else a block keyed by the **parameter name** (`trainer:`),
3. else — only when *neither* key exists — the **whole top-level config** is
   used (the flat-config fallback that lets a minimal flat YAML drive a run).

A present-but-empty block (`MyTrainer: {}` or a YAML-null `MyTrainer:`) means
"construct with defaults" — it does **not** fall through. Confluid's top-level
broadcasting still applies accept-listed bare keys regardless of which block
was chosen.

## `@script_command` and config promotion

A **script command** promotes its first positional argument to a configuration
file path: `my-app train experiment.yaml` loads `experiment.yaml` before the
command runs. If the argument has no suffix, `<arg>.yaml` is tried — and if the
**first CLI argument is not a registered command at all**, it is treated as a
config path for the default command (so `my-app experiment.yaml` works).

A relative config path (promoted token or `--config` value) resolves through
confluid's search tiers, local first: `./` → `./config/` → the XDG config
dirs (`~/.config/<app-name>/`, then `~/.config/confluid/`, then
`$XDG_CONFIG_DIRS`). Liquifai sets the confluid app name to the running app's
name at startup, so `my-app train myexp` finds `~/.config/my-app/myexp.yaml`
when no local `myexp.yaml` exists. `include:` entries inside a config resolve
the same way. See confluid's
[Config-File Search Paths](https://github.com/Gearlux/confluid/blob/main/docs/search-paths.md)
guide.

```python
@app.script_command()
def train(trainer: MyTrainer) -> None:
    ...
```

### Flow modes

`@app.script_command(flow_mode=...)` decides how aggressively injected objects
are flowed before the command runs:

- **`"manual"` (default):** injected kwargs are passed unchanged. Nested
  `!class:` stubs stay deferred — domain code is responsible for flowing them.
- **`"auto"`:** every kwarg is deep-flowed before the call. Attributes annotated
  with `confluid.Lazy` stay deferred so domain code can still flow them at
  runtime with extra kwargs (the classic `configure_optimizers` pattern:
  `flow(self.optimizer, params=self.parameters())`). Any non-`Lazy` `Class`
  stub that can't be instantiated raises immediately.

An invalid mode raises `CommandDefinitionError` at decoration time.

## Positional arguments (`positionals=[...]`)

Any command may declare ordered positional-argument names. Leading non-flag
tokens after the command name bind, in order, to those names — **verbatim as
strings** (a command that needs another type coerces in its body) — and
consumption stops at the first `--flag` / `+add` / `~del` / `key=value` token,
so positional, `key=value`, and `--flag` forms interoperate in one invocation:

```python
@sub.command("download", positionals=["name", "version"])
def download(name: str = "", version: str = "latest", path: str = ".") -> None:
    ...
```

```bash
my-app download foo 1.0            # positional form
my-app download --name foo --version 1.0   # flag form — both work
my-app download foo --path /tmp    # mixed: binding stops at the first flag
```

For a `@script_command`, the config-file promotion peek runs **first**, so the
config path is consumed before positionals bind.

## Default command redirection

`@app.command(default=True)` marks the command that runs when no command token
is given — `my-app --lr 0.1` then routes straight to it (and, combined with
promotion, `my-app experiment.yaml` too).

## Runnable examples

- [`examples/basic_app.py`](../examples/basic_app.py) — minimal app with two commands and `get_context`.
- [`examples/simple_app.py`](../examples/simple_app.py) — a `@configurable` component + logger wiring.
- [`examples/positionals_app.py`](../examples/positionals_app.py) — positional binding and its interop with flags.
- [`examples/liquifai_app.py`](../examples/liquifai_app.py) — fuller confluid + loggair wiring.
- [`examples/linefit/`](../examples/linefit/README.md) — an installable training-style CLI built on `@script_command`: config promotion, `flow_mode="auto"` with a `Lazy` optimizer, dotted overrides, and a `print-config` reproducibility verb.
