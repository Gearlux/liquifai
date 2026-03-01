# Liquify

**Liquify** is a modern, type-safe application framework for Python, designed to bind **LogFlow** and **Confluid** into high-performance CLI applications.

## Key Features
- **Zero-Boilerplate Startup:** Automatically handles logging and hierarchical config initialization.
- **Type-Safe CLI:** Built on top of **Typer** for strict validation and beautiful auto-generated help pages.
- **Dependency Injection:** Seamlessly injects configured **Confluid** objects into your commands.
- **Rich Integration:** Beautiful terminal output and progress reporting via **Rich**.
- **Modular Commands:** Register and compose multiple tools into a single entry point.

## Quick Start (Coming Soon)

```python
from liquify import LiquifyApp
from confluid import configurable

@configurable
class MyTrainer:
    def __init__(self, lr: float = 0.01):
        self.lr = lr

app = LiquifyApp(name="my-app")

@app.command()
def train(trainer: MyTrainer):
    # 'trainer' is automatically loaded via Confluid and flowed
    print(f"Training with lr={trainer.lr}")

if __name__ == "__main__":
    app.run()
```

## Installation
```bash
pip install liquify
```

## License
MIT
