# Liquify Mandates

- **Zero Boilerplate:** Application startup MUST automatically initialize **LogFlow** logging and **Confluid** configuration. Users should never write bootstrap code.
- **Type-Safe DI:** Command function signatures define the dependency contract. Liquify MUST resolve dependencies from Confluid config by inspecting type annotations.
- **Bootstrap Lifecycle:** The 5-phase lifecycle (parse globals, init context, configure logging, load config, execute) MUST remain strict and sequential. Never skip or reorder phases.
- **Config Promotion:** If the first CLI argument is not a registered command, it MUST be treated as a config file path. This convention is non-negotiable.
## Project Quality Gates
To run the full quality suite for Liquify, execute the following commands from the project root:

```bash
# From ~/source
isort --settings-file liquifai/pyproject.toml liquifai
black --config liquifai/pyproject.toml liquifai
flake8 --config liquifai/.flake8 liquifai
mypy --config-file mypy.ini liquifai
pytest --cov=liquifai --cov-report=term-missing liquifai/tests
```

**Note:** Always use the root `mypy.ini` for cross-project type checking, as it contains the correct `mypy_path` for all internal packages.
