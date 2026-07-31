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

## `--` ends option parsing

Everything after a bare `--` is a **literal value**: never an option, never an
override, and it never stops positional consumption. This is how you pass a
value that starts with a dash.

```bash
myapp seek -- -5 /tmp/x     # both bind as positionals
myapp seek -5 /tmp/x        # `-5` reads as an option token; nothing binds
```

A literal that no positional slot claims is reported with its own warning, so
nothing after `--` disappears silently either.

## Which nodes an override reaches

Whether a key may set an attribute is answered by **confluid**, not by
liquifai — the same answer a YAML key gets, so a CLI override and a config key
behave identically:

| Form | Addressing | Reaches |
|---|---|---|
| `--<name>.<key> v` | addressed at the instance whose YAML `name:` is `<name>` | anything that instance can set — constructor params, settable properties, `__init__`-body slots, `**kwargs` targets |
| `--<key> v` | bare: broadcasts across the tree | the same, **minus** nodes that opted out of broadcasting |

The two broadcast opt-outs are declared in code, on the receiving class:

```python
@configurable(broadcast=False)          # no bare key ever lands here
class Pinned:
    def __init__(self, lr: float = 0.1, tag: NoBroadcast[str] = "") -> None: ...
    #                                        ^ this one slot is excluded
```

`--lr 0.9` skips `Pinned` entirely; `--pinned.lr 0.9` still reaches it, because
an addressed write is not a cascade. (Before this was unified, a flat CLI
override ignored both opt-outs — see
[Architecture Decisions §4](architecture.md).)

The single source of truth for the global-flag vocabulary and token
classification is `liquifai/grammar.py` (stdlib-only); the parser, `--help`,
and shell completion all derive from it, so they cannot drift apart.
Tokenization (including `--`) and the argv walk shared with completion live in
`liquifai/walk.py`, override parsing/application in `liquifai/overrides.py`,
and annotation-driven dependency injection in `liquifai/di.py`.

## Runnable examples

- [`examples/overrides_app.py`](../examples/overrides_app.py) — drives one app
  through each override form (flag, equals, bare, polarity, add, delete) and a
  deliberately unrecognized token.
- [`examples/broadcast_demo.py`](../examples/broadcast_demo.py) — a bare
  `--batch_size 64` broadcasting into two sibling loaders, plus dotted
  per-loader overrides.
