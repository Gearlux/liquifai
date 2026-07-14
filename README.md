# Liquifai

**Liquifai** is a modern, type-safe application framework for Python, designed to bind **Loggair** and **Confluid** into high-performance CLI applications.

## Key Features
- **Zero-Boilerplate Startup:** Automatically handles logging and hierarchical config initialization.
- **Type-Safe CLI:** Streamlined argument parsing and validation.
- **Dependency Injection:** Seamlessly injects configured **Confluid** objects into your commands.
- **Rich Integration:** Beautiful terminal output and progress reporting via **Rich**.
- **Modular Commands:** Register and compose multiple tools into a single entry point.
- **Shell Completion:** bash/zsh/fish tab completion for commands, options, overrides — and live positional values.

## Documentation

Each topic has its own guide, and every guide has a runnable companion script in [`examples/`](https://github.com/Gearlux/liquifai/tree/main/examples):

| Guide | What it covers | Example |
|---|---|---|
| [Commands & Dependency Injection](https://github.com/Gearlux/liquifai/blob/main/docs/commands-and-di.md) | `@command` / `@script_command`, config promotion, DI block lookup, positional arguments, flow modes | `positionals_app.py` et al. |
| [CLI Overrides](https://github.com/Gearlux/liquifai/blob/main/docs/cli-overrides.md) | The override grammar (`--key value`, dotted keys, polarity, add/delete) and the dropped-token warning | `overrides_app.py` |
| [Global Flags](https://github.com/Gearlux/liquifai/blob/main/docs/global-flags.md) | Log control (`--level`, `--log-dir`, …), `--scope` / dimension flags, `--debug`, `--docs` | `global_flags_app.py` |
| [Error Handling](https://github.com/Gearlux/liquifai/blob/main/docs/error-handling.md) | The typed `LiquifaiError` hierarchy and the CLI failure contract | `failure_contract.py` |
| [Shell Completion](https://github.com/Gearlux/liquifai/blob/main/docs/shell-completion.md) | Install, aliases, workspace-local setup, dynamic & dependent positional values | `completion_providers.py` |

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

```bash
my-app train --lr 0.1          # CLI override, broadcast into the injected trainer
```

See the [Commands & DI guide](https://github.com/Gearlux/liquifai/blob/main/docs/commands-and-di.md) for `@script_command` config promotion (`my-app train experiment.yaml`), how injection finds its config block, positional arguments, and flow modes.

## Installation
```bash
pip install liquifai                     # from PyPI
```

Or straight from GitHub:

```bash
pip install git+https://github.com/Gearlux/liquifai.git@main
```

## License
MIT
