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

The dotted form is not limited to a single hop — it is confluid's own path
grammar, so the wildcard and multi-segment spellings work from the CLI too:

```bash
myapp run c.yaml --trainer.opt.lr 0.001   # a direct child named `opt`
myapp run c.yaml --trainer.**.lr 0.001    # trainer and every descendant
myapp run c.yaml --**.lr 0.001            # every accepting node in the tree
```

### An override always wins over the file

Precedence is **position**: config values are applied in document order and the
last one wins, with no "CLI beats YAML" tier. A CLI override is therefore
re-seated at the *end* of the document before anything is built — you typed it
after the whole file, so it is applied after the whole file. That holds whether
or not the file already declares the key:

```yaml
run_name: from_yaml       # `--run_name from_cli` wins over this ...
runnable:
  metric: !class:Accuracy
    run_name: at_the_node # ... and over this
```

That includes a value written in a **class-name block** for a dependency-injected
parameter — the block keeps its place in the document, so an override applied
after the file outranks it:

```yaml
lr: 0.1
Trainer:                  # `--lr 0.9` wins over this too
  lr: 0.5
```

Position, not provenance, is what decides — so the reverse still holds: a bare
key written *above* the block loses to it, exactly as it would in a config with
no CLI involved.

### When an override reaches nothing

A dotted key whose head names neither a configured instance, an existing config
key, nor a registered class cannot land anywhere, and says so:

```
Override 'optimizer.lr' matched nothing and was ignored: no configured object is
named 'optimizer' … for a slot declared in code, use the bare form ('--lr 0.001')
or set it inside that object's config block.
```

The bare form has no such failure mode — it is a document-wide cascade, so a key
no node declares is simply not applied, which is normal rather than a mistake.

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
