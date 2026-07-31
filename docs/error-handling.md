# Error Handling

Liquifai raises typed exceptions rooted at `LiquifaiError`; each also inherits the builtin it replaces, so pre-existing `except ValueError:` / `except KeyError:` code keeps working unchanged:

| Exception | Also a | Raised when |
|---|---|---|
| `CommandDefinitionError` | `ValueError` | a `@command` / `@script_command` / `@operation` declaration is invalid (bad `presentation` / `flow_mode`) |
| `UnknownOperationError` | `KeyError` | `set_completions()` names an operation that is not registered |
| `UnknownCommandError` | `ValueError` | the argv tokens resolved to no command, and the group has no default |
| `ConfigNotFoundError` | `FileNotFoundError` | the configuration file named on the CLI does not exist |
| `UnsupportedShellError` | `ValueError` | a completion shell is not one of bash / zsh / fish |

Configuration-loading failures propagate Confluid's own hierarchy
([`confluid.ConfluidError` and subclasses](https://github.com/Gearlux/confluid/blob/main/docs/errors.md))
— `LiquifaiError` covers CLI-definition errors only.

**Library code raises; only `run()` exits.** Every failure above leaves the
framework as an exception — the CLI boundary is the single place that turns one
into a console line and an exit code. That is what makes a `LiquifyApp` safe to
embed: a long-lived host process (a server dispatching commands in-process) can
catch `LiquifaiError` instead of being terminated by a `sys.exit` buried in the
bootstrap.

```python
from liquifai import ConfigNotFoundError

try:
    app._bootstrap()
except ConfigNotFoundError as exc:      # also catchable as FileNotFoundError
    ...
```

## CLI failure contract

When a command runs via `app.run()`:

| Failure | Behavior | Exit code |
|---|---|---|
| `LiquifaiError` or `confluid.ConfluidError` (bad config, unresolvable class, invalid declaration) | One clean `Error: …` line on the console; full traceback written to the log file at DEBUG | 1 |
| Same, with `--debug` on the line | The exception **propagates** — full traceback on the console | (Python default) |
| Missing `--config` file | `ConfigNotFoundError` -> dedicated `Configuration file not found` message | 1 |
| Unknown command/group | `UnknownCommandError` -> `Unknown command or group` (or help when no default command exists) | 1 |
| Any other exception | A bug — always propagates with its traceback, never converted to a clean exit | (Python default) |

## Runnable example

[`examples/failure_contract.py`](../examples/failure_contract.py) triggers a
`CommandDefinitionError` in-process (showing the builtin dual-inheritance) and
then runs an app with a missing config file in a subprocess, asserting the
clean one-line error and exit code 1.
