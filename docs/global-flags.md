# Global Flags

Every LiquifyApp understands one shared vocabulary of global flags, declared
once in `liquifai/grammar.py` — the parser, `--help`, and shell completion all
derive from that single table, so they can never drift apart.

| Flag | Effect |
|---|---|
| `--config PATH`, `-c` | Configuration file (script commands can also take it as their first positional — see [config promotion](commands-and-di.md)). |
| `--scope NAME` / `--scope KEY=VAL`, `-s` | Activate a confluid [scope](https://github.com/Gearlux/confluid/blob/main/docs/scopes.md); repeatable. Dimension-bound `--KEY VAL` flags (e.g. `--task classification`) are forwarded as `KEY=VAL` scopes automatically. The `os` / `device` [host facts](host-facts.md) are activated on every run without a flag; `--scope os=linux` overrides one. |
| `--debug`, `-d` | Enable debug mode — expected CLI failures propagate with a full traceback instead of a clean one-line error (see [error handling](error-handling.md)). Also raises the console level to `DEBUG` unless `--console-level` names one. |
| `--level LEVEL` | Set both console and file log level. Omitted, the levels come from the hierarchy below. |
| `--console-level LEVEL` | Console log level only; beats `--level` for that sink. |
| `--file-level LEVEL` | File log level only; beats `--level` for that sink. |
| `--log-dir DIR` | Redirect the log file directory. Omitted, `LOGGAIR_DIR` and the config files apply. |
| `--help` | Rich options table extracted from the command signature + docstrings. With a config named, also the **Scope Dimensions** block — see below. |
| `--docs` | The same code-extracted documentation as `--help`, one option per line. |
| `--install-completion [SHELL]` / `--show-completion [SHELL]` / `--refresh-completions` | Shell completion management (see [shell completion](shell-completion.md)). |

## Where a log level comes from

The four log flags are the TOP layer of a hierarchy, not the whole of it. A flag
you did not type is left unset, and the level is resolved by the logging engine:

```
--console-level DEBUG            the flag you typed          highest
LOGGAIR_CONSOLE_LEVEL=DEBUG      environment variable
loggair.yaml: console_level      config file
INFO                             built-in default            lowest
```

Same four layers for the file sink (`--file-level`, `LOGGAIR_FILE_LEVEL`,
`file_level:`, default `DEBUG`) and for the log directory (`--log-dir`,
`LOGGAIR_DIR`, `log_dir:`, default `./logs`).

```console
$ my-app run                                  # console INFO    — the default
$ LOGGAIR_CONSOLE_LEVEL=DEBUG my-app run      # console DEBUG   — the env var
$ LOGGAIR_CONSOLE_LEVEL=DEBUG my-app --level WARNING run
                                              # console WARNING — the flag wins
```

`--level` sets both sinks at once, so a per-sink flag beside it wins for its own
sink only: `--level TRACE --console-level ERROR` gives an `ERROR` console and a
`TRACE` file.

The config-file layer reads `loggair.yaml` / `loggair.yml` in the working
directory, then `[tool.loggair]` in `pyproject.toml`, then
`$XDG_CONFIG_HOME/loggair/config.yaml` — first hit wins. The logging engine owns
this resolution; liquifai only forwards the flags you typed. Run
`python -m loggair` to see which layers a machine actually has and what they
resolve to.

## `--help` with a config — the Scope Dimensions block

When the command has a config (`my-app train cfg.yaml --help`), help also lists
what that document's `!scope:KEY=VAL` blocks offer, one implicit flag per
dimension, and — when the document carries a
[`default_scopes:`](https://github.com/Gearlux/confluid/blob/main/docs/scopes.md)
line — the value a bare run picks:

```
Scope Dimensions (from cfg.yaml):
  --framework <keras|lightning|torch>  (default: lightning)
  --model <convnet>
```

Read from the RAW document (`confluid.discover_dimension_values` +
`confluid.default_scopes`) — the same walk that binds the `--KEY VAL` flags, so
the block and the flags cannot disagree. A document declaring no dimension
prints no block; a dimension declared only by `!notscope:` blocks offers nothing
to select and shows `<value>`.

## `--docs` — code-extracted documentation, one option per line

`my-app <cmd> --docs` renders the same option documentation as `--help`
(extracted from the command signature + docstrings via confluid's
`get_hierarchy`) but one option per physical line — `--flag  type  = value  doc`
— so it greps and pipes cleanly instead of wrapping inside a Rich table. Same
data, different layout (`liquifai.report.show_configuration(..., layout="lines")`).

## Runnable example

[`examples/global_flags_app.py`](../examples/global_flags_app.py) runs one app
under different log levels and finishes with the greppable `--docs` layout.
