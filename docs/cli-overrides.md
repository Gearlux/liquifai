# CLI Overrides

Any token left after the command, positionals, and global flags is parsed as a
config override. All forms interoperate in one invocation:

| Form | Example | Effect |
|---|---|---|
| `--key value` | `--max_epochs 10` | set `max_epochs` (broadcast into matching nested configs) |
| `--key=value` | `--trainer.lr=0.001` | equals form; dotted keys target nested blocks |
| `key=value` | `model.dropout=0.2` | bare form, no dashes |
| `--key+` / `--key-` | `--debug+` | polarity: explicit `True` / `False` |
| `--key` | `--verbose` | implicit `True` |
| `+key=value` | `+new_feature=true` | add a new key |
| `~key` | `~trainer.stale` | delete the dotted key from the config |

A token that matches **none** of these forms is not applied and liquifai logs a
**warning** naming it (`Ignoring unrecognized CLI token 'lr' — expected one of:
…`). Previously such tokens were dropped silently — a typo'd `lr 0.1` instead
of `--lr 0.1` would run the whole job on defaults without a trace.

The single source of truth for the global-flag vocabulary and token
classification is `liquifai/grammar.py` (stdlib-only); the parser, `--help`,
and shell completion all derive from it, so they cannot drift apart. Override
parsing/application lives in `liquifai/overrides.py`, and annotation-driven
dependency injection in `liquifai/di.py`.

## Runnable examples

- [`examples/overrides_app.py`](../examples/overrides_app.py) — drives one app
  through each override form (flag, equals, bare, polarity, add, delete) and a
  deliberately unrecognized token.
- [`examples/broadcast_demo.py`](../examples/broadcast_demo.py) — a bare
  `--batch_size 64` broadcasting into two sibling loaders, plus dotted
  per-loader overrides.
