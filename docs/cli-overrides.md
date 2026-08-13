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

### Overrides are appended to the file, in the order typed

Precedence is **position**: config values are applied in document order and the
last one wins, with no "CLI beats YAML" tier. The CLI contract is therefore one
sentence: **flags behave exactly as if their keys were appended to the end of
the document, in the order typed.** Every override key — bare or dotted — is
re-seated at the end before anything is built; you typed it after the whole
file, so it is applied after the whole file, whether or not the file already
declares the key:

```yaml
run_name: from_yaml       # `--run_name from_cli` wins over this ...
runnable:
  metric: !class:Accuracy
    run_name: at_the_node # ... and over this
```

That includes a value written in a **class-name block** for a dependency-injected
parameter — even a block the file already declares is re-seated when a dotted
flag targets it, so the flag cannot be silently outranked by a bare key written
lower in the file:

```yaml
Trainer:
  layers: 8               # `--Trainer.lr 0.1` re-seats this whole block last,
lr: 0.9                   # so it now beats this — and 0.1 is what trains
```

Because flags are appended *in the order typed*, their relative order matters
exactly like lines in a file — the last flag wins where they overlap:

```bash
myapp run c.yaml --lr 0.2 --Trainer.lr 0.1   # all lr 0.2, except Trainer: 0.1
myapp run c.yaml --Trainer.lr 0.1 --lr 0.2   # the bare flag came last: all 0.2
```

Two consequences worth knowing:

* a head mentioned twice seats at its **last** mention — `--Trainer.lr 0.1
  --lr 0.2 --Trainer.layers 4` puts the whole `Trainer:` block after the bare
  key, so `0.1` wins on Trainer;
* re-seating a block the file already declares moves **all** its keys — in the
  example above, the block's `layers: 8` now also sits after any bare
  `layers:` key the file declares later, and wins where it used to lose.

### When an override reaches nothing

Whether an override actually landed is judged by the configuration engine's own
delivery report, collected around dependency-injection materialization — not
guessed from the document's shape. Every override the report says matched
nothing is warned about, before the command body runs. A dotted key whose head
names no configured instance:

```
Override 'optimizer.lr' matched nothing and was ignored: no configured object is
named 'optimizer' … for a slot declared in code, use the bare form ('--lr 0.001')
or set it inside that object's config block.
```

A bare key that *no* object accepts — most often a typo — is caught the same
way:

```
Override 'max_pcaks' matched nothing and was ignored: no configured object
accepts 'max_pcaks'. Check the spelling against the declared parameters
(`--help` lists them).
```

A bare key that lands on *some* nodes and not others stays silent — that is
what a document-wide cascade means, not a mistake.

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
