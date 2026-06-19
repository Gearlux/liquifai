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
`@command` like `run list` completes `--experiment`, `--status`, … not just
the globals), YAML files for `@script_command` configuration arguments, and
`--<key>` override suggestions derived from the loaded config. A bare
`<cmd> <TAB>` reveals the command's options directly — for a `@script_command`
they appear *alongside* the config-file candidates (so
`convert-ops-export <TAB>` shows both the YAML files to pick and the
`--converter.*` overrides), and narrowing with `--<TAB>` drops the files.

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

## License
MIT
