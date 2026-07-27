# Global Flags

Every LiquifyApp understands one shared vocabulary of global flags, declared
once in `liquifai/grammar.py` — the parser, `--help`, and shell completion all
derive from that single table, so they can never drift apart.

| Flag | Effect |
|---|---|
| `--config PATH`, `-c` | Configuration file (script commands can also take it as their first positional — see [config promotion](commands-and-di.md)). |
| `--scope NAME` / `--scope KEY=VAL`, `-s` | Activate a confluid [scope](https://github.com/Gearlux/confluid/blob/main/docs/scopes.md); repeatable. Dimension-bound `--KEY VAL` flags (e.g. `--task classification`) are forwarded as `KEY=VAL` scopes automatically. |
| `--debug`, `-d` | Enable debug mode — expected CLI failures propagate with a full traceback instead of a clean one-line error (see [error handling](error-handling.md)). |
| `--level LEVEL` | Set both console and file log level. |
| `--console-level LEVEL` | Console log level only. |
| `--file-level LEVEL` | File log level only. |
| `--log-dir DIR` | Redirect the log file directory. |
| `--help` | Rich options table extracted from the command signature + docstrings. |
| `--docs` | The same code-extracted documentation as `--help`, one option per line. |
| `--install-completion [SHELL]` / `--show-completion [SHELL]` / `--refresh-completions` | Shell completion management (see [shell completion](shell-completion.md)). |

## `--docs` — code-extracted documentation, one option per line

`my-app <cmd> --docs` renders the same option documentation as `--help`
(extracted from the command signature + docstrings via confluid's
`get_hierarchy`) but one option per physical line — `--flag  type  = value  doc`
— so it greps and pipes cleanly instead of wrapping inside a Rich table. Same
data, different layout (`liquifai.report.show_configuration(..., layout="lines")`).

## Runnable example

[`examples/global_flags_app.py`](../examples/global_flags_app.py) runs one app
under different log levels and finishes with the greppable `--docs` layout.
