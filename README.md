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
include sub-commands, sub-app names, global flags, YAML files for
`@script_command` configuration arguments, and `--<key>` override
suggestions derived from the loaded config.

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

## License
MIT
