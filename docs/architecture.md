# Architecture Decisions

Why liquifai is shaped the way it is. Each record answers one "why does this
exist?" question in the form **Context → Decision → Consequences → Example →
What you may change**.

This file is design *rationale*. How to USE a mechanism belongs in the topic
guides ([commands & DI](commands-and-di.md), [CLI overrides](cli-overrides.md),
[global flags](global-flags.md), [error handling](error-handling.md),
[shell completion](shell-completion.md)) — each of those has a runnable twin in
`examples/`. The records here carry short inline examples instead, because they
illustrate a decision rather than a workflow.

---

## 1. A hand-rolled parser instead of Click or Typer

*2026-07-26*

**Context.** liquifai is a CLI framework in a landscape that already has good
ones. Click and Typer parse arguments, generate `--help`, and ship shell
completion. Adopting one would delete a large amount of code here.

Three requirements do not fit a conventional parser's model:

1. **Config promotion** — the token after a `script_command` is a YAML path,
   resolved through a config-file search (`./`, `./config/`, XDG dirs), and it
   is *optional*: the same command runs from CLI overrides and defaults alone.
2. **An open-ended override vocabulary** — `--trainer.lr 0.1`, `key=value`,
   `+key`, `~key` are not declared parameters. They address arbitrary nodes in
   a config tree the parser has never seen, so there is no fixed option set to
   validate against. Click's `ignore_unknown_options` allows unknown tokens but
   gives no help/completion for them; liquifai derives both from the loaded
   YAML plus the command's own signature.
3. **Signature-driven DI** — a command's parameters are typed with
   `@configurable` classes that the framework constructs. The signature is a
   dependency contract, not a list of CLI options; the two overlap only partly.

**Decision.** Own the parsing. Depend on nothing but `rich`, `loggair` and
`confluid`.

**Consequences.**

- Everything a conventional parser gives free is ours to build: the global-flag
  table ([`grammar.py`](../liquifai/grammar.py)), the walk
  ([`walk.py`](../liquifai/walk.py)), help rendering
  ([`report.py`](../liquifai/report.py)), and ~2,200 lines of shell completion.
  That is the majority of the library, and it is the price of this decision.
- Conveniences a parser would have supplied are absent until deliberately
  added. `--` (record 3) was missing until 2026-07-26. There is still no arity
  (`nargs`), no `--flag`/`--no-flag` pair (the polarity forms `--flag+` /
  `--flag-` cover it), and no parser-level type coercion — values stay strings
  until confluid's `parse_value` and the constructor's schema validate them.
- Every token rule is ours to keep consistent, which is why `grammar.py` is a
  hard single source of truth and why records 3 and 4 exist.

**Example.** The shape no declarative parser expresses — an optional promoted
config, then arbitrary tree-addressing overrides against it:

```bash
myapp train experiment.yaml --trainer.lr 0.05 ~callbacks.early_stop +tag=v2
myapp train --trainer.lr 0.05          # same command, no config file at all
```

**What you may change.** The decision is reversible in principle: the three
requirements above are ~300 lines that compose as a `click.Command` subclass
with `ignore_unknown_options=True`. Revisit it if the maintenance cost of the
parser plus completion outgrows the cost of adapting to a dependency's
opinions. Do not revisit it piecemeal — a half-migrated parser is worse than
either end state.

---

## 2. Shell completion runs out of process, off a JSON cache

*2026-07-26*

**Context.** A liquifai app can be expensive to import — an ML CLI pulls in
torch, a plugin stack, a whole framework — often seconds. Completion must feel
instant. The conventional approach (the shell re-invokes the app with a magic
env var) pays the app's full import cost on every TAB.

**Decision.** Split completion into two processes with a file between them.

- The **app process**, on every `--help` and every successful run, snapshots
  its static command tree to `~/.cache/liquifai/<app>.json`
  ([`completion/tree.py`](../liquifai/completion/tree.py)).
- The **`liquifai-complete` binary** is what the shell actually calls. It reads
  that JSON and computes candidates ([`completion/engine.py`](../liquifai/completion/engine.py)).
  It never imports the app.

**Consequences.**

- Every module under `liquifai/completion/` must stay **stdlib-only at import
  time**. Anything importing confluid, loggair or rich on that path re-imposes
  the cost the split removed. This is why the Rich-using flag interception
  lives in the top-level `completion_cli.py`, outside the package.
- Completion can be **stale**: a command added since the last run is invisible
  until the next `--help` or run rewrites the cache. Accepted; the cache is
  rewritten on both.
- Completion sees a *snapshot*, not the app — so anything it needs must be
  serialized into the tree at snapshot time (collapsed flags, bool-typed flags,
  declared positionals, provider specs). Adding a completion feature usually
  means adding a tree field and bumping `CACHE_VERSION`.
- Dynamic positional values (a dataset name list) cannot be computed on the hot
  path, because that would mean running user code. They are refreshed
  separately and read from a value cache; a stale one self-heals via a detached
  subprocess, never inline.

**Example.** The two halves and the file between them:

```python
# app process (on --help / after a successful run)
from liquifai.completion import write_cache
write_cache(app)                    # -> ~/.cache/liquifai/myapp.json

# liquifai-complete (what the shell calls; stdlib only)
from liquifai.completion import complete_from_tree, read_cache
complete_from_tree(read_cache("myapp"), words, cword)
```

**What you may change.** The cache format is versioned — bump `CACHE_VERSION`
and stale caches are rewritten rather than misread. What you may NOT change is
the stdlib-only rule for `liquifai/completion/**`; there is a test that fails
if a submodule grows a heavy import.

---

## 3. One argv walk, two data shapes

*2026-07-26*

**Context.** Dispatch and completion must agree on what a command line means:
which sub-app it descended into, which command it matched, whether the next
token was that command's promoted config, and which tokens are positionals.
Dispatch reads live `LiquifyApp` objects; completion reads the serialized JSON
tree (record 2) and cannot touch the app.

Both once implemented that descent independently, and the copies drifted. The
observable bug: dispatch resolved a promoted config through confluid's search
tiers, completion did not — so with a `./config/demo.yaml` layout, `myapp run
demo` loaded the file for real but TAB never offered that YAML's override keys,
because it tested `Path("demo.yaml").exists()` in the CWD.

**Decision.** One walk, [`walk.walk_invocation`](../liquifai/walk.py), over a
`Nav` protocol. Each side supplies a small adapter: `router._AppNav` reads a
live app, `engine._TreeNav` reads a tree dict. Where the two genuinely differ —
resolving the promoted config — the difference is an injected callback, not a
forked loop.

**Consequences.**

- A routing rule is written once. Adding one (sub-app aliases, a new stop
  condition) automatically applies to both.
- The one legitimate difference is explicit and narrow: dispatch resolves the
  config eagerly through confluid; completion defers, because resolving means
  importing confluid on the hot path. Completion resolves later, in the branch
  that was going to parse the YAML anyway.
- `Invocation.remaining_tokens` carries `Token`s rather than strings, so later
  phases can still distinguish an option from a post-`--` literal.
- The default command is one more `Nav` question (`default_command()`), so
  `app w.yaml` binds the positional for dispatch AND hints it for TAB from the
  same branch. Because no token equals the command's name in that case, the
  walk reports where the arguments start (`Walk.args_index`) instead of letting
  completion search the line for the name.

**Example.** The adapter is the whole cost of joining the walk:

```python
class _TreeNav:                       # completion side (stdlib only)
    def __init__(self, node): self.node = node
    def sub_app(self, token):
        sub = (self.node.get("sub_apps") or {}).get(token)
        return _TreeNav(sub) if sub is not None else None
    def has_command(self, token): return token in (self.node.get("commands") or [])
    ...

walk = walk_invocation(tokenize(argv), _TreeNav(tree), _peek_config)
```

**What you may change.** Add `Nav` implementations freely (a dry-run explainer,
a docs generator). Do NOT re-implement the descent inline "just for this one
case" — that is exactly how the previous drift started. `tests/test_walk.py`
pins that both adapters answer identically.

---

## 4. Settability is confluid's question, never re-derived here

*2026-07-26*

**Context.** Applying `--lr 0.1` means answering "may this key set this
attribute on this class?". confluid already answers it for YAML — the accept
list covers constructor parameters, settable properties, `__init__`-body slots,
and `**kwargs` targets, with two opt-outs on top (`@configurable(broadcast=False)`
for a class, `NoBroadcast[T]` for a parameter).

liquifai used to compute its own accept list. It was close, and being close was
the problem — the divergences were silent and each one was a bug:

| Case | confluid | liquifai's copy |
|---|---|---|
| `**kwargs` constructor | accepts everything | accepted only the literal name `kw` |
| `__init__`-body slot (`self.optimizer = …`) | reachable | invisible |
| `@configurable(broadcast=False)` | bare keys blocked | **ignored** |
| `NoBroadcast[T]` parameter | that slot blocked | **ignored** |

So a class that declared "no bare key may land on me" still took CLI overrides:
`lr: 0.9` in YAML was correctly refused while `--lr 0.9` went straight through.

**Decision.** Delete the local accept list. confluid exports the two predicates
it uses internally — `accepts_key` (addressed writes) and `accepts_broadcast`
(bare, cascading keys) — and liquifai calls them.

The two CLI forms map onto the two predicates exactly:

| CLI form | Addressing | Predicate |
|---|---|---|
| `--<name>.<key> v` | addressed at the instance named `<name>` | `accepts_key` |
| `--<key> v` | bare, broadcasts to every matching node | `accepts_broadcast` |

**Consequences.**

- A broadcast opt-out declared in code now holds no matter which front-end
  delivers the key. An addressed override still reaches an opted-out class,
  matching confluid's own rule that the opt-out gates cascades, not settability.
- `**kwargs` targets and `__init__`-body slots became CLI-overridable, because
  they always were YAML-overridable.
- liquifai gained a dependency on two more confluid names, and confluid gained
  two public predicates. That is the right direction: the config engine owns
  the config question.
- A Fluid whose target class cannot be resolved (a `!class:` naming a module not
  importable yet) has no accept list to consult, so it falls back to "the key is
  already in the YAML" — a deferred marker stays overridable.

**Example.**

```python
@configurable(broadcast=False)
class Pinned:
    def __init__(self, lr: float = 0.1) -> None: ...

confluid.accepts_key(Pinned, "lr")        # True  -> `--pinned.lr 0.9` applies
confluid.accepts_broadcast(Pinned, "lr")  # False -> `--lr 0.9` skips it
```

**What you may change.** Add override *forms* (a new spelling, a new addressing
mode) — but each one must be classified as addressed or bare and routed to the
matching predicate. Never reintroduce a local accept list, and never read a
`__confluid_*__` marker directly from here; if a new gate is needed, it belongs
in confluid next to the ones it joins.

### Amendment: asking is not the same as delivering

*2026-08-03*

The consequence above — *"`**kwargs` targets became CLI-overridable, because they
always were YAML-overridable"* — was right about **settability** and wrong about
**delivery**, and the gap took a year's worth of confusing failures to surface.

YAML delivers a bare key to a `**kwargs` class as a post-init **attribute**.
liquifai delivered it by writing into `Fluid.kwargs`, which is confluid's
*addressed* channel — so it arrived as a **constructor argument**. Deferring the
question to confluid while re-implementing the answer's delivery put the two back
out of step, in the one place the predicates cannot warn about: for a target with
no accept list, `accepts_broadcast` says yes to **every key in the document**, so
every bare override was handed to somebody else's constructor.

What that looked like in practice: `--run_name x` reached a metric's constructor
and the metric library raised `Unexpected keyword arguments`, from a call site
nowhere near the config; the same flag reached a dataset loader, where nothing
raised at all and it silently became part of a cache key. The tell that this was
liquifai's bug rather than the engine's: a top-level *YAML* `run_name:` had always
been fine, so a working config broke the moment the flag was added.

**Decision.** confluid gained a third predicate, `accepts_any_key(target)` — not
"may this key land?" but "does this target discriminate between keys at all?" —
and the bare branch checks it **first**. A target that answers `True` is skipped
rather than written; `apply_overrides` has already merged the key into the
document, so confluid's own broadcasting delivers it with the right provenance.
A class that *declares* the key is untouched and still receives it as a
constructor argument.

```python
if accepts_any_key(cls):
    continue                      # no accept-list -> let it cascade as a BARE key
elif accepts_broadcast(cls, k):
    data.kwargs[k] = v            # declared -> an argument is what was meant
```

**Consequences.**

- Delivery now follows the same split as settability: liquifai chooses the
  *channel* from the same predicate family confluid uses internally, instead of
  writing into the addressed channel unconditionally.
- Nothing is dropped. A `**kwargs` target still receives the key, as an attribute
   — verified end to end rather than argued: the metric builds *and*
  `metric.run_name` is set.
- The rejected alternative was to catch the constructor's `ValueError`, parse the
  offending keys out of its message, and retry without them. It fails on all
  three counts that matter: it only catches libraries that **validate** (the
  dataset-loader case raised nothing, and that is the more dangerous half), it
  couples liquifai to every upstream library's error-message spelling, and it
  re-runs a constructor that may already have had side effects.

**What you may change.** The narrowing is deliberately minimal — it moves only
the targets that have no accept list. Removing the bare-key write *entirely* (and
letting confluid broadcast every override) is the tidier end state and is on the
backlog, blocked on proving the coverage first: a marker never materialized
against the document would silently stop seeing overrides.

### Amendment: delivering is not the same as winning

*2026-08-04*

Handing a key to confluid's broadcast, as the amendment above decided, makes its
delivery subject to confluid's precedence rule — and confluid has exactly one:
**document order, last spec wins, no specificity tiers**. So a cascading override
does not beat a value addressed at a node by being an override. It beats it by
sitting *later in the document*.

`deep_merge` gives that away for free only when the key is new — a fresh
top-level key is appended, i.e. last. When the document *already declares* the
key, merge replaces it **in place**, and the CLI value inherits that key's
original position:

```yaml
run_name: from_yaml          # line 1 — and the CLI value lands here
runnable:
  metric: !class:SomeMetric  # a **kwargs target
    run_name: addressed_in_yaml
```

`--run_name from_cli` was silently discarded: the marker's own key sits later, so
it won. Nothing warned, because from the engine's side nothing was wrong — the
key *was* used, with the file's value. Only confluid's DEBUG `override:` line
showed the CLI value losing the contest.

**Decision.** Every bare override key is moved to the END of the top-level
mapping after the merge (`overrides._move_cli_keys_last`). The user typed it
after the whole file; document-last is the honest encoding of that.

```python
data = deep_merge(data, parsed)
data = expand_dotted_keys(data)
for key in parsed:                    # the CLI spoke last -> it sits last
    if "." not in key and key in data:
        data[key] = data.pop(key)
```

**Consequences.**

- Precedence stays confluid's single rule. The rejected alternative was to ask
  for a CLI *tier* that outranks document order — which is the specificity
  ladder confluid deliberately does not have, and adding one for a single
  front-end would make "why did my knob not take?" un-answerable from the
  document alone.
- Only **bare** keys move. A dotted `--<name>.<key>` is written straight into the
  target's kwargs, where order does not arbitrate; and moving its expanded block
  would reorder YAML content the user never overrode.
- Positionals are unaffected: they land in `config_data` before overrides, so an
  explicit `--name` still wins over the positional slot.

**What you may change.** If the bare-key write is ever removed entirely (see
above), this repositioning becomes the *only* thing that makes a CLI override
win, not just the thing that makes a cascading one win — so it must be kept and
its pin (`test_flat_override_beats_a_key_the_document_already_declares`) treated
as load-bearing rather than incidental.

### Amendment: a hoisted block has no position

*2026-08-04*

The same root cause, one layer over and on a different code path. DI resolves a
`@configurable` parameter by selecting a config block and copying it into a
synthesized marker's kwargs:

```python
instance = confluid.Instance(cls_name)
instance.kwargs.update(config_block)          # <- the block leaves the document
kwargs[name] = materialize(instance, context=context.config_data)
```

For a **class-name** block that copy is what breaks precedence. `Trainer: {lr:
0.5}` is confluid's own addressed-block spelling — it reads it out of the context
document, at the position the author wrote it. Hoisted into the marker, it is no
longer *at* any position, and a value with no position cannot lose the one contest
confluid runs. Measured on `{lr: 0.1, Trainer: {lr: 0.5}}` with `--lr 0.9`, after
the repositioning above has correctly produced `['Trainer', 'lr']`:

```
materialize(Instance(Trainer, **block), context=doc)  ->  0.5   # hoisted
materialize(Instance(Trainer),          context=doc)  ->  0.9   # left in place
```

**Decision.** The class-name branch does not hoist. The other two selections keep
copying, because neither has a spelling confluid recognises on its own: a
param-name block (`widget: {size: 7}`) matches by class name or instance `name:`,
and a synthesized marker has neither; the flat-config fallback is the whole
document, which is already the context.

**Consequences.**

- One rule now governs every spelling reaching a DI-injected parameter, so a CLI
  override wins over a class-name block and a bare key written *above* one still
  loses to it. Both directions are pinned — a fix that simply made the bare key
  win would be a specificity tier by another name.
- Behaviour changes for existing configs: a bare key written *after* a class-name
  block now reaches the injected instance where the block previously always won.
  A config that relied on the block winning must move the key above it.
- The empty-block contract is untouched. `Trainer: {}` and YAML-null `Trainer:`
  still mean "construct with defaults" — confluid applies an empty block to
  nothing, which is what the hoist did too.

**What you may change.** The param-name and flat-fallback branches are the
remaining hoists, and they have the same latent shape. Removing them needs a
spelling confluid can match — giving the synthesized marker a `name:` would make
the param-name block addressable — and is only worth doing together with the
`Fluid.kwargs` removal already on the backlog.

### Amendment: the CLI keys are appended in the order typed

*2026-08-13 (supersedes "only bare keys move" above)*

The 2026-08-04 repositioning moved **only bare** override keys to the end. That
forced every bare flag after every dotted flag regardless of what the user
typed, and left a dotted override's expanded block wherever the document put
it. Two measured consequences:

```bash
myapp run c.yaml --lr 0.2 --Trainer.lr 0.1     # doc: two markers
# -> Trainer.lr = 0.2 in BOTH flag orders — the bare key always seated last,
#    so "override all lr, except Trainer's" was unreachable from the CLI.
```

```yaml
Trainer:            # doc line 1
  layers: 8
t: {_target_: Trainer}
lr: 0.9             # doc last line
```
```bash
myapp run c.yaml --Trainer.lr 0.1
# -> Trainer.lr = 0.9. expand_dotted_keys folded the override into the
#    EXISTING block at line 1, so the doc's later bare key silently beat the
#    value the operator typed. With no pre-existing block the same flag WON
#    (the head was appended, i.e. last) — one flag, two outcomes, decided by
#    whether the file happened to declare a block.
```

**Decision.** `_move_cli_keys_last` re-seats **every** override key's top-level
head — bare and dotted-expanded alike — at the end of the mapping, iterating in
typed CLI order; a head mentioned twice seats at its last mention. The CLI's
contract becomes one sentence: *flags behave exactly as if their keys were
appended to the end of the document, in the order typed.* Both cases above now
answer 0.1, and flag order arbitrates where flags overlap (last typed wins) —
which is the shell convention, and the first time the relative order of CLI
flags has meant anything rather than being forged by the mechanism.

**Consequences.**

- Re-seating a PRE-EXISTING block drags its unrelated keys' precedence with it:
  on the doc above plus a trailing `layers: 4`, typing `--Trainer.lr 0.1` moves
  the whole block last, so `layers: 8` beats the bare `layers: 4` the document
  used to win with. Accepted: the alternative (leave pre-existing blocks in
  place) keeps the silent-loss case, which is worse. Both directions are pinned
  in the seating group of `tests/test_override_broadcast.py`.
- Structural dotted overrides (`--run.name pilot` editing a plain `run:`
  mapping) are unaffected — a plain value has no position contest, and the edit
  happens before the re-seat.
- The 2026-08-04 rationale stands unchanged one level up: this is still
  position-encoding, not a "CLI beats YAML" tier, and precedence remains
  answerable from the (effective) document alone.

**What you may change.** Not the no-reorder property: the mechanism may never
change the relative order of the CLI keys again — that is the defect this
amendment removes. The known residual is that order-*independence* (specificity
arbitration: the longest pattern wins regardless of flag order) is not
expressible by seating at all; if that is ever wanted, it is a confluid-side
override channel, not a cleverer seat order.

---

## 5. Positionals bind as top-level config keys

*2026-07-26*

**Context.** `myapp download foo 1.0` must reach a handler declared as
`def download(name: str = "", version: str = "")`. The handler's parameters are
resolved by DI from the config tree, not by a parser binding argv to a
signature — so a positional has to become config before DI runs.

**Decision.** Write each consumed positional into the top level of the config
under its declared name, before overrides are applied.

**Consequences.**

- Binding is verbatim strings. `1.0` stays `"1.0"`, never float `1.0` — a
  version, a run id, or a zero-padded index survives intact. A handler that
  wants another type coerces in its body.
- A positional lands in the same namespace a bare YAML key would, so it
  **broadcasts** by confluid's rules: `positionals=["threshold"]` reaches every
  configurable in the graph that accepts `threshold`, not just the handler
  parameter. Usually what you want for a single-purpose command; surprising if
  the name is generic. Prefer specific names (`dataset_name` over `name`).
- Because the bind happens *before* overrides, an explicit `--name foo` wins
  over the positional slot, and the three spellings interoperate:
  `download foo`, `download name=foo`, `download --name foo`.
- The declared positional is not advertised as a `--flag` by help or completion
  (the spelling still parses) — a required argument should read as required.

**Example.**

```python
@app.command(name="download", positionals=["name", "version"])
def download(name: str = "", version: str = "") -> None: ...
```

```bash
myapp download foo 1.0          # config gains {"name": "foo", "version": "1.0"}
myapp download foo --version 2  # positional + flag; the flag wins its slot
myapp download -- -5            # `--` for a value that starts with a dash
```

**What you may change.** Binding into a *nested* namespace (per-command rather
than top-level) would remove the broadcast side effect — at the cost of the
flat-config ergonomics the workspace's YAMLs rely on. If you attempt it, it is
a breaking change to every app with positionals, not a refinement.

---

## 6. `liquifai.bridge` sits outside the version contract

*2026-07-26*

**Context.** [`liquifai/bridge/`](../liquifai/bridge/) turns a Python SDK
client's methods into liquifai operations declaratively. It was extracted from
one consumer and, so far, still has one. Its spec vocabulary (`P`, `expose`,
policies, adapters) was designed against that consumer's SDK; a second consumer
is likely to reshape it.

Shipping it as ordinary public API would freeze a design validated once.

**Decision.** Keep the code in this repository, exclude it from the version
contract, and make that exclusion explicit in three places: the module warning,
the `__provisional__` / `__extra__` markers, and a `liquifai[bridge]` extra a
consumer declares.

**Consequences.**

- The subpackage is absent from the top-level exports, so `import liquifai`
  never reaches it and no one depends on it by accident.
- The extra carries no requirements today — the subpackage needs nothing beyond
  liquifai's own core. It is a *contract marker*: a consumer writing
  `liquifai[bridge]` records in its own metadata that it depends on an unstable
  surface, and a future real dependency lands there invisibly.
- A pip extra cannot make a dependency-free subpackage physically absent from
  the wheel. This is a declared boundary, not an enforced one; the enforcement
  that does exist is the top-level export exclusion, pinned by a test.
- Breaking changes here need no deprecation cycle in the 0.x line.

**Example.**

```toml
# a consumer's pyproject.toml
dependencies = ["liquifai[bridge]>=0.1.0"]
```

```python
import liquifai.bridge as bridge
assert bridge.__provisional__ is True    # machine-readable form of the warning
```

**What you may change.** When a second consumer arrives, revisit: either
stabilise the vocabulary against both and fold it into the main contract, or
move it to its own distribution. Until then, do not re-export it from the top
level — that is the one step that would quietly make it public.

---

## 7. Deprecations are served by `__getattr__`, not by a comment

*2026-07-26*

**Context.** The 2026-07 consolidation moved helpers out of `core.py` into
`di`, `overrides` and `grammar`. External code imported them from their old
home under underscore-prefixed names, so `core.py` kept nine aliases:

```python
_deep_flow = di.deep_flow
_expand_strings = overrides.expand_strings
...
```

with a comment saying "do not remove these". That note stood for months and the
aliases never got cleaned up, for a structural reason: **a plain assignment is
invisible**. Nothing distinguishes an alias access from any other attribute
access, so there was no way to tell whether a consumer still used one, no
signal reaching anybody who did, and therefore no moment at which removing them
felt safe. A comment cannot deprecate anything.

**Decision.** Serve them from a PEP-562 module `__getattr__` that emits a
`DeprecationWarning` naming the exact replacement import, and remove the plain
assignments. Migrate liquifai's own code and tests onto the owning modules, and
pin that with a test that greps the package for any internal use.

**Consequences.**

- Access is now observable: a consumer's own test suite surfaces the warning,
  and the message says precisely what to write instead — no cross-referencing.
- The internal-usage pin turns the eventual deletion into a pure external
  migration. When `test_deprecated_aliases.py` passes, the only thing standing
  between here and removal is auditing consumers.
- A removal date exists (v1.0) and appears in the warning, so the decision is
  not deferred indefinitely by the absence of a forcing function.
- Cost: attribute access on `liquifai.core` for an unknown name now runs a
  Python-level hook. Irrelevant here (these are import-time lookups), but worth
  knowing before applying the pattern to a hot path.

**Example.**

```python
>>> import liquifai.core as core
>>> core._deep_flow
DeprecationWarning: liquifai.core._deep_flow is deprecated and will be removed
in v1.0; import it from its owning module instead:
`from liquifai.di import deep_flow`.
```

**What you may change.** Nothing here is load-bearing for behaviour — the
aliases resolve to the same objects. What must NOT change is the direction: do
not add a new alias to `_DEPRECATED_ALIASES` (it is a shrinking set), and do
not silence the warning to make a test suite quiet — migrate the import.
