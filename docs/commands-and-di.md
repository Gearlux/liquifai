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

### Flat configs and `Any`-annotated parameters

Under `flow_mode="auto"`, an injected object is built **against the loaded
document**, so the config can be *flat*: a top-level key injects into the
constructor parameter of the same name, with no nesting and no `!ref:`.

```yaml
runnable: !class:mypkg.Trainer
  model: !lazy:mypkg.Backbone { name: resnet18 }

dataset: !class:mypkg.Dataset { path: ./data }
max_epochs: 3          # -> Trainer(max_epochs=3)
```

This works whether the parameter is annotated with a configurable class
(`def train(trainer: MyTrainer)`) or with `Any` (`def run(runnable: Any)`). The
`Any` form matters for a **generic runner** — one command that executes a
trainer, an evaluator or a converter, whichever the YAML names — where no single
class can be named in the signature.

> **Fixed in 0.1.1.** Before that release the `Any` form built the object in
> isolation, so every top-level key was dropped *silently*: `dataset:` became
> `None`, `max_epochs: 3` reverted to the parameter default, and the run looked
> configured. If you call `liquifai.di.deep_flow` yourself, pass the document as
> `context=` — without it you get the old, silent behaviour.

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

Each bound positional is written into the **top level of the config** under its
declared name, before overrides apply — which is why an explicit `--name foo`
still wins its slot, and why a positional broadcasts like a bare YAML key would
(prefer specific names such as `dataset_name` over a generic `name`; see
[Architecture Decisions §5](architecture.md)).

For a `@script_command`, the config-file promotion peek runs **first**, so the
config path is consumed before positionals bind.

### Seeing which file promotion picked

Promotion is **eager**: a bare token becomes the config as soon as a matching
YAML exists in *any* search tier. That is convenient when you meant it and a
trap when you didn't — a forgotten `~/.config/my-app/report.yaml` will quietly
swallow `my-app process report` that meant `report` as a positional argument.

Every promotion is therefore recorded. Run with `--level TRACE` to see all of
them, or `--level DEBUG` to see only the surprising kind — a file resolved from
outside the working directory:

```console
$ my-app --level DEBUG process report
DEBUG | Config promotion resolved OUTSIDE the working directory: token 'report'
      | -> /home/me/.config/my-app/report.yaml. Promotion is eager — a matching
      | YAML in ./config/ or an XDG config dir is consumed even when 'report'
      | was meant as a positional argument.
```

The line is emitted once configuration and logging are both up, so it always
reaches your log file and reflects the file actually loaded.

[`examples/promotion_app.py`](../examples/promotion_app.py) walks all four
outcomes — CWD, `./config/`, an XDG dir that swallows a positional, and no
matching file at all — against a sandboxed temp workspace.

### Values that start with a dash

Because consumption stops at the first flag-like token, a positional whose
value begins with `-` needs the POSIX separator. Everything after a bare `--`
is a literal value — never an option, never an override:

```bash
my-app seek -- -5 /tmp/x    # both bind as positionals
my-app seek -5 /tmp/x       # `-5` reads as an option token; nothing binds
```

A declared positional is **not** advertised as a `--flag` by `--help` or TAB
completion (the flag spelling still parses) — a required argument should read
as required.

## Default command redirection

`@app.command(default=True)` marks the command that runs when no command token
is given — `my-app --lr 0.1` routes straight to it. Its **arguments bind without
the name too**: a leading token that names no command is the default command's
first positional, or its promoted config for a `@script_command(default=True)`:

```python
@app.command(name="workspace", default=True, positionals=["workspace"])
def workspace(workspace: str = "") -> None:
    ...

@app.script_command(name="run", default=True)
def run(threshold: float = 0.5) -> None:
    ...
```

```bash
my-app w.yaml                 # workspace="w.yaml"  — same as `my-app workspace w.yaml`
my-app --workspace w.yaml     # the flag form still works
my-app other                  # a real command name always wins over the positional
my-app experiment             # script default: promotes ./config/experiment.yaml (any search tier)
```

Binding follows the explicit-command rules exactly: only *leading* tokens bind
(`my-app --lr 0.1 w.yaml` leaves `w.yaml` to the override parser, as `cmd --lr
0.1 w.yaml` would), a token that resolves to no config file is not swallowed,
and a default command that declares no positionals and is not a script command
behaves as before. TAB completion hints the positional at a bare prompt
(`my-app <TAB>` → `<workspace>` beside the command names).

## Runnable examples

- [`examples/basic_app.py`](../examples/basic_app.py) — minimal app with two commands and `get_context`.
- [`examples/simple_app.py`](../examples/simple_app.py) — a `@configurable` component + logger wiring.
- [`examples/positionals_app.py`](../examples/positionals_app.py) — positional binding and its interop with flags.
- [`examples/promotion_app.py`](../examples/promotion_app.py) — config promotion across the search tiers, the DEBUG provenance notice, and the case where an XDG file swallows a positional.
- [`examples/liquifai_app.py`](../examples/liquifai_app.py) — fuller confluid + loggair wiring.
- [`examples/linefit/`](../examples/linefit/README.md) — an installable training-style CLI built on `@script_command`: config promotion, `flow_mode="auto"` with a `Lazy` optimizer, dotted overrides, and a `print-config` reproducibility verb.
