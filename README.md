# Liquify

**Liquify** is a modern, type-safe application framework for Python, designed to bind **LogFlow** and **Confluid** into high-performance CLI applications.

## Key Features
- **Zero-Boilerplate Startup:** Automatically handles logging and hierarchical config initialization.
- **Type-Safe CLI:** Streamlined argument parsing and validation.
- **Dependency Injection:** Seamlessly injects configured **Confluid** objects into your commands.
- **Rich Integration:** Beautiful terminal output and progress reporting via **Rich**.
- **Modular Commands:** Register and compose multiple tools into a single entry point.

## Design Goals & Requirements

### CLI Framework
- **Zero-Boilerplate Startup:** Automate the bootstrapping of LogFlow and Confluid.
- **Contextual Scripting:** Support `@app.script_command()` which promotes the first positional argument to a configuration file path.
- **Type-Safe DI:** Inject fully-configured objects directly into command signatures based on type hints.
- **Default Command Redirection:** Support running a default command if no subcommand is provided.

### User Experience
- **Abbreviation Support:** Allow brief aliases for the main executable (e.g. `wf` for `waivefront`).
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

## Installation
```bash
pip install git+https://github.com/Gearlux/liquifai.git@main
```

## Shell Completion

Every LiquifyApp ships with bash, zsh, and fish tab completion. Candidates
include sub-commands, sub-app names, global flags, each command's own
`--<option>` flags (derived from its function signature — so a plain
`@command` like `run list` completes `--experiment`, `--status`, …, and a
`@script_command` whose argument is a `@configurable` completes its nested
overrides), YAML files for `@script_command` configuration arguments, and
`--<key>` override suggestions derived from the loaded config. A bare
`<cmd> <TAB>` reveals the command's options directly — for a `@script_command`
they appear *alongside* the config-file candidates (so
`convert-ops-export <TAB>` shows both the YAML files to pick and the converter
overrides), and narrowing with `--<TAB>` drops the files.

**Option flags use the same shortest-unique paths as `--help`.** Completion
runs the command's override paths through the *exact same* confluid functions
(`get_hierarchy` + `shortest_unique_paths`) that build the `--help` options
table, so the two never disagree: a uniquely-named leaf shows as `--class_name`
(not the noisy `--converter.class_name`), and a leaf shared by two sub-objects
keeps just enough prefix to disambiguate (`--model.lr` vs `--optim.lr`). Both
the short flat form and the fully-dotted form work as overrides at runtime;
completion only *suggests* the short one.

> **Note:** the option flags are baked into a per-app cache
> (`~/.cache/liquifai/<app>.json`) that is refreshed automatically every time
> the app runs (including on `--help` and `--install-completion`). After
> upgrading liquifai, run the app once (e.g. `my-app --help`) so newly
> surfaced completions appear.

```bash
my-app --install-completion          # auto-detects $SHELL, appends to your rc file
my-app --install-completion zsh      # explicit shell
my-app --show-completion bash        # print the script to stdout (manual install)
```

After installing, restart your shell (or `source ~/.zshrc` / `~/.bashrc`).
For fish the script is written to `~/.config/fish/completions/<app>.fish`
and auto-loads in the next session.

### Aliases

Shell aliases don't inherit completion automatically (bash and zsh bind
completion to specific command names, not to alias expansions). Use
`liquifai-bind-alias` to wire any alias up:

```bash
alias mt='marainer train'
liquifai-bind-alias mt marainer train
```

The first argument is the alias name; the rest is what the alias expands
to. `mt cfg.yaml<TAB>` then completes with the same `--key` suggestions
you'd get from `marainer train cfg.yaml<TAB>`.

### Workspace-local installation (avoid touching `~/.bashrc`)

`<app> --install-completion` writes into the user's global rc file. For
multi-project workspaces it's often nicer to keep completion confined to
a project-local rc that your `project.bashrc` sources, so a fresh checkout
gets working completion without polluting `~/.bashrc`. Use the bundled
`liquifai-install-completions` console script:

```bash
# Discover every Liquifai app in the active venv and install completion
# blocks for all of them into a single project-local rc fragment.
liquifai-install-completions --target-rc ./.project.bashrc.completion

# Or pin to a specific list of apps.
liquifai-install-completions --target-rc ./.project.bashrc.completion marainer annotaide

# Then in your project.bashrc:
#   [ -f ./.project.bashrc.completion ] && source ./.project.bashrc.completion
```

Auto-discovery probes each executable in `sys.prefix/bin` with
`--show-completion bash` and keeps the ones that emit Liquifai's completion
marker. A Liquifai app handles that flag early, but a heavy app still imports
its full stack (torch, Lightning, …) at module load *before* the handler
runs, so an individual probe is **not** cheap. Discovery therefore runs the
probes concurrently (a small bounded thread pool) so a populated ML venv
resolves in tens of seconds instead of minutes. The aisland workspace runs
this step as part of `bash aisland/setup.sh`.

### Dynamic positional values (complete `<name>` from a live source)

By default the `<name>` placeholder is just a hint. A command can instead make
a positional complete with **real values** from a provider — e.g. complete
`my-app dataset download <name>` from `dataset list`:

```python
def dataset_names() -> list[str]:           # any Callable[[], list[str]]
    return [d["name"] for d in fetch_datasets()]   # may import the SDK / hit the network

@sub.command("download", positionals=["name"], completions={"name": dataset_names})
def download(name: str = "", path: str = "."): ...

# or, wired separately from the decorator (e.g. to dodge an import cycle):
sub.set_completions("download", {"name": dataset_names})   # before build_commands()
```

How it stays fast and offline-safe:

- The provider runs **only at refresh time** (`my-app --refresh-completions`),
  never on the TAB hot path. Its result is cached under
  `~/.cache/liquifai/<app>.values/`.
- `liquifai-complete` reads that JSON cache and offers the values for the
  `<name>` slot (prefix-filtered like any candidate). No cache yet → it falls
  back to the `<name>` placeholder. A provider that raises (offline / no auth)
  is skipped — completion silently degrades to the placeholder.
- **Self-heals on first use.** A static positional whose value cache is missing
  or stale (`> DEPENDENT_REFRESH_TTL`, 5 min) is filled the same way dependent
  ones are (see below): the first TAB returns the placeholder instantly **and**
  kicks off a *detached, throttled* background refresh, so a freshly-added
  positional (e.g. a new `run list <experiment>`) populates itself on the **next**
  TAB — no manual `--refresh-completions` required.
- **Refresh-everything is explicit by default**: run `my-app --refresh-completions`
  to (re)populate ALL value caches at once. Opt into automatic background refresh by
  setting `LIQUIFAI_BG_REFRESH=1` — then a successful run refreshes stale caches
  (>10 min) in a detached daemon thread, never blocking the command. It is OFF
  by default so a normal run never triggers a surprise provider call (e.g. a
  platform query). A sub-app alias (`ds` for `dataset`) shares the canonical
  command's value cache.
- **Prefix matching is case-insensitive**: `helios<TAB>` finds `Helios_…`.
- **Values with spaces work.** A candidate like `Test Script VB` is transported
  as one token (the wrappers newline-join `$COMP_WORDS`) and emitted
  backslash-escaped so the shell inserts it as a single argument. bash gets this
  with no re-install; **zsh/fish users should re-run `--install-completion`** once
  to pick up the newline-joining wrapper.

#### Dependent positionals (a later `<version>` that depends on an earlier `<name>`)

A provider that takes **one argument** receives the already-typed earlier
positionals, so a second positional can complete from the first — e.g.
`download <name> <version>` where versions depend on the chosen dataset:

```python
def dataset_names() -> list[str]:                      # static (0-arg)
    return [d["name"] for d in fetch_datasets()]

def dataset_versions(inputs: dict[str, str]) -> list[str]:   # dependent (1-arg)
    return versions_of(inputs["name"])                 # inputs = the typed earlier positionals

sub.set_completions("download", {"name": dataset_names, "version": dataset_versions})
```

At refresh, liquifai **pre-enumerates** the dependent values: for each value of
the prior positional(s) it calls the dependent provider and caches the result
per input combination (capped at `max_combos`, default 200). On TAB,
`download "Test Script VB" <TAB>` reads the cache keyed by that exact name and
offers its versions — still no provider call on the hot path.

**Self-healing (new / changed / beyond-the-cap values).** Pre-enumeration alone
would freeze a name's versions at the last refresh. So on TAB, if a dependent
slot's per-input cache is **missing or stale** (`> DEPENDENT_REFRESH_TTL`, 5 min),
the fast path returns whatever's cached now (instant — stale values or the
placeholder) **and** kicks off a *detached, throttled* background refresh for that
exact input (`<app> --refresh-completion-value …`). So a brand-new dataset, a new
version, or a name beyond the cap becomes current on the **next** TAB without ever
blocking — no manual `--refresh-completions` needed. Throttled per input so rapid
TABbing can't fork a storm; opt out entirely with `LIQUIFAI_NO_LAZY_COMPLETE=1`.

When a self-heal **actually changes** the values, the next TAB shows a transient
`<<positional>-updated>` hint (e.g. `<version-updated>`) alongside them for a short
window, so you know the background refresh took effect — change-only (an unchanged
refresh shows nothing), and it disappears the moment you type a real value.

### `--docs` — code-extracted documentation, one option per line

`my-app <cmd> --docs` renders the same option documentation as `--help`
(extracted from the command signature + docstrings via confluid's
`get_hierarchy`) but one option per physical line — `--flag  type  = value  doc`
— so it greps and pipes cleanly instead of wrapping inside a Rich table. Same
data, different layout (`liquifai.report.show_configuration(..., layout="lines")`).

## License
MIT
