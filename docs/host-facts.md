# Host Facts — the `os` / `device` scopes and the `platform` namespace

Almost every real config has a line or two that depends on the machine it runs
on: a loader knob that misbehaves on one OS, a device string, a path segment.
Liquifai detects two facts once per run and hands them to the document in both
positions a config can use.

| Fact | Values | Detected from |
|---|---|---|
| `os` | `darwin`, `linux`, `windows` | `platform.system()`, lowered — Python's own word, not a friendlier translation |
| `device` | `cuda`, `mps`, `cpu` | torch when it is installed (`cuda` > `mps` > `cpu`); `cpu` when it is not |

Nothing is typed on the command line, and no environment variable is set or read.

## The two positions

**As a scope** — a block that only applies on one machine:

```yaml
workers: 8
mac: !scope:os=darwin
  workers: 0
```

**As a value** — the same fact read inline, from the `platform` namespace
liquifai injects into every document:

```yaml
logdir: /runs/${platform.os}/${platform.device}
```

The namespace is what makes the second form possible, and the dot is load-bearing:
confluid dispatches a placeholder on its *name shape* — a dotted name reads the
config tree, a bare one reads `os.getenv`. So `${platform.os}` resolves and a bare
`${os}` would look for an environment variable that liquifai never sets.

## Your keys always win

The namespace is merged **under** the document, key by key. A framework that
spells the device differently re-spells that one fact and keeps the other:

```yaml
platform:
  device: gpu        # this engine's word for it
logdir: /runs/${platform.os}          # still darwin / linux / windows
accelerator: ${platform.device}       # now gpu
```

The same override can itself be scoped, so the re-spelling only happens where it
applies:

```yaml
keras_naming: !scope:device=cuda
  platform:
    device: gpu
```

## Forcing a value

`--scope os=linux` / `--scope device=cpu` overrides detection, and it moves **both**
surfaces at once — the block that fires and what `${platform.device}` reads — so the
two can never disagree. A forced dimension is not detected at all, so
`--scope device=cpu` also skips torch's import.

Plain `--os linux` / `--device cpu` are **not** scope flags. They stay ordinary
config overrides, because binding them would swallow a `--device cpu` meant for a
constructor kwarg. (A document that declares its *own* `!scope:device=…` blocks
binds `--device` the usual way — that is the existing dimension-flag rule, and it
is why an app should not name a scope dimension after a key it also overrides.)

## A document that does not name your machine

Confluid rejects an undeclared value for a dimension a document *does* declare —
`No scope block matches os='windows'` — which is the typo guard a hand-typed
`--scope` must keep. A detected value is not a typo, so liquifai does not pass one
the document cannot use:

```yaml
# On a Mac, this document runs. `workers` stays 8; the block is simply inert.
workers: 8
lin: !scope:os=linux
  workers: 0
```

A value **you** typed is never filtered and still raises, so `--scope os=windows`
against that document tells you what it declares.

**Known limit.** The filter reads a document's *positive* declarations
(`confluid.discover_dimension_values`). A dimension carrying both a positive block
and a `!notscope:` block — `!scope:os=linux` beside `!notscope:os=darwin` in one
document — is filtered on the positive values alone, so on a Mac the auto value is
dropped and the `!notscope:` block stays active. Name the machine positively if you
need that case to be exact.

## Where the facts show up

The injected key is part of the resolved document, so a config dumped or logged as
a run artifact records which host facts the run used. It also means a top-level key
named `platform` is taken: a document that defines one as something other than a
mapping keeps its own value untouched, and liquifai warns that `${platform.…}` will
read the author's value instead of the detected one.

## Runnable example

[`examples/host_facts_app.py`](../examples/host_facts_app.py) writes a config that
uses both positions, runs it, and shows the same document staying inert on a machine
it does not name.
