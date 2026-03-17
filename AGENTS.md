# Liquify Mandates

- **Zero Boilerplate:** Application startup MUST automatically initialize **LogFlow** logging and **Confluid** configuration. Users should never write bootstrap code.
- **Type-Safe DI:** Command function signatures define the dependency contract. Liquify MUST resolve dependencies from Confluid config by inspecting type annotations.
- **Bootstrap Lifecycle:** The 5-phase lifecycle (parse globals, init context, configure logging, load config, execute) MUST remain strict and sequential. Never skip or reorder phases.
- **Config Promotion:** If the first CLI argument is not a registered command, it MUST be treated as a config file path. This convention is non-negotiable.
- **Rich Integration:** All CLI output (help, errors, tables) MUST use Rich for consistent, beautiful terminal formatting.
- **Singleton Context:** `LiquifyContext` is a global singleton. Never instantiate multiple contexts in a single process.

## Testing & Validation
- **CLI Runner Tests:** All command tests MUST use pytest's `CliRunner` for deterministic, isolated execution.
- **DI Resolution Tests:** Verify both simple and nested dependency injection with scoped overrides.
- **Line Length:** 120 characters (Black, isort, flake8).
