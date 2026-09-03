# Changelog

All notable changes to liquifai are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [0.3.0] - 2026-09-03

### Added

- **`short=` on a command declares single-letter options** — `@app.command(short={"b":
  "background"})` makes `-b` mean `--background`, and `--help` renders it as `-b, --background`.
  Declared rather than derived: the first letter of `config` / `scope` / `debug` would shadow the
  globals that already own `-c` / `-s` / `-d`. A reserved, repeated, multi-character or
  unknown-parameter letter raises `CommandDefinitionError` at decoration time.
- **`strict_flags=True`** makes an app refuse a bare CLI flag that names no parameter of the
  command it was given to, instead of letting it fall through to the config. The existing
  report-based warning only speaks when something was materialized, so a CLI of plain-value
  commands had no check at all. It also refuses an unrecognised token (`-x`), which the
  permissive path only warns about. Off by default — pass-through stays legitimate.

### Fixed

- **A value bound to a `str` parameter reaches the command exactly as typed.** Every override
  value is read as YAML, which is right for an untyped config key (`--trainer.lr 0.001` is a
  float) and destructive for declared text: a multi-line value was folded onto one line,
  `#1 priority` read as a comment and became `None`, `3:30` became `210` (YAML 1.1
  sexagesimal), `012` became `10`, `yes` became `True`, and surrounding whitespace was
  stripped. When the command annotates the parameter `str` (or `Optional[str]`), the text is
  now passed through untouched. Everything else keeps YAML typing — a parameter annotated
  `int`/`bool`/`list`, any key the command does not declare, and every dotted key (which
  addresses a nested config object, not the signature). Reported as a multi-line
  `--description` silently losing its newline.
- **`-h` shows help instead of RUNNING the command.** Only `--help` was declared, and a
  single-dash token matches no override form, so `-h` fell through as an unrecognised token and
  execution continued — `<app> restart -h` restarted the server. The help short-circuit now
  derives its spellings from `GLOBAL_FLAG_SPECS` rather than hard-coding one.

- **`--kebab-case` reaches the `snake_case` parameter it means.** `--custom-node URL` parsed
  to a key `custom-node`, matched the parameter `custom_node` never, and did nothing — with
  no error, because a well-formed `--key value` token is not "dropped". Keys now normalise
  hyphens to underscores (per dotted segment); values are untouched, and the `--key-` / `--key+`
  polarity suffix is read before normalising.

- **`app --help` lists the commands even when a default command answers it.** An app with
  a `default=True` command routed bare `--help` to that command and rendered only its
  options, so every sibling command was undiscoverable — the only way to learn one existed
  was to already know its name. The command index now renders first, followed by the
  default command's own block. Apps without a default command are unaffected, and
  `app <cmd> --help` still shows just that command.

## [0.2.0] - 2026-08-24

### Added

- **Host facts on every run — the `os` / `device` scopes and the `platform` namespace.**
  `liquifai.host` detects the machine's OS (`darwin` / `linux` / `windows`) and compute
  device (`cuda` / `mps` / `cpu`, from torch when it is installed) once per invocation, and
  offers each one in both positions a config can use: a scope activation, so a document can
  carry `!scope:os=darwin` blocks, and a `platform: {os, device}` key merged under the
  document, so `logdir: /runs/${platform.os}` resolves. No environment variable is set or
  read — a bare `${os}` is an env-var read in confluid and stays unresolved by design.
  The author's own keys win per key (`platform: {device: gpu}` re-spells one fact for a
  framework that needs another word), `--scope device=cpu` overrides detection and moves
  both surfaces at once, and a detected value a document cannot use is dropped rather than
  raised — while a value typed by hand keeps confluid's typo guard. `--os` / `--device`
  remain ordinary config overrides. See `docs/host-facts.md` and `docs/architecture.md` §8.

- **The default command's arguments bind without its name.** A leading token that
  names no sub-app or command is the default command's first positional
  (`app w.yaml` ≡ `app workspace w.yaml`) or — for the new
  `@script_command(default=True)` — its promoted config (`app experiment` loads
  `./config/experiment.yaml` through the usual search tiers). Only leading tokens
  bind, a config token that resolves to no file is not swallowed, and a default
  command with neither positionals nor promotion behaves as before. `Nav` gains
  `default_command()`, the serialized completion tree a `"default"` key, and
  `Walk.args_index` marks where a command's arguments start; TAB at a bare prompt
  hints the default command's positional / config files / flags beside the command
  names.

- **`--help` with a config renders a Scope Dimensions block** — one implicit `--KEY <v1|v2>`
  flag per dimension the document's `!scope:KEY=VAL` blocks declare, plus `(default: X)` for a
  dimension the document's `default_scopes:` names (`liquifai.report.show_scope_dimensions`,
  read from the RAW document via `confluid.discover_dimension_values` / `confluid.default_scopes`
  — the same walk `flags.bind_dimension_flags` binds the flags from). No dimensions, no block.

### Fixed

- **Log-level environment variables and config files are honoured again.** The bootstrap
  passed a concrete `"INFO"` / `"DEBUG"` to the logging engine even when no flag named a
  level, which short-circuited the engine's own resolution on its first layer and silently
  shadowed `LOGGAIR_CONSOLE_LEVEL` / `LOGGAIR_FILE_LEVEL` and every `loggair.yaml` /
  `pyproject.toml` / XDG `console_level:` key. `--log-dir` was already forwarded unset,
  which is why `LOGGAIR_DIR` worked while the levels did not. A flag the user did not type
  is now left unset, giving `flag > env > config file > default`. A run with no flag and no
  env is unchanged: the literals removed here were the engine's own defaults. See
  `docs/global-flags.md` and `docs/architecture.md` §9.

- **A bare key written after a class-name block now reaches a
  dependency-injected parameter.** Config precedence is document order, last spec
  wins — but DI copied a selected class-name block into the synthesized marker's
  kwargs, which took it out of the document, and a value with no position cannot
  lose that contest. `Trainer: {lr: 0.5}` therefore beat a later `lr: 0.9` — and
  every `--lr` override — unconditionally. The class-name branch no longer
  hoists: the block is confluid's own addressed-block spelling and is read from
  the context document where it was written. Param-name blocks
  (`widget: {size: 7}`) and the flat-config fallback still hoist, neither having
  a spelling confluid can match on its own. A bare key written *above* the block
  still loses to it, and `Trainer: {}` / YAML-null `Trainer:` still mean
  "construct with defaults". Note for existing configs: one that relied on the
  block outranking a *later* top-level key resolves differently now — move that
  key above the block to keep the old value.

- **A CLI override now outranks a key the document already declares.** Confluid
  has one precedence rule — document order, last spec wins — so a bare override
  left to cascade (see the `**kwargs` fix below) beats a value addressed at a
  node only by sitting later. `deep_merge` appends a *new* key, but replaces an
  existing one **in place**, which handed the CLI value that key's original
  position: with a top-level `run_name: from_yaml` on line 1 and a marker
  further down setting its own `run_name`, `--run_name from_cli` lost the
  contest and was discarded. Nothing warned — the key *was* used, just with the
  file's value; only confluid's DEBUG `override:` line showed it. Every bare
  override key is now moved to the end of the document after the merge
  (`_move_cli_keys_last`), which is where the user typed it. Dotted overrides
  are unaffected: they are written straight into the target's kwargs, where
  order does not arbitrate.

- **A dotted `--head.key` override that addresses nothing now warns instead of
  vanishing.** The dotted form targets an instance by its YAML `name:`; aimed at
  anything else — most often a slot declared in code, `--optimizer.lr 0.001` —
  the value expanded into a top-level block that matched no node, so it was
  silently discarded and the run proceeded on the default looking configured.
  That is the failure the dropped-token warning already guards against, one step
  further in. The warning names the offending key and the spelling that does
  work. A head that a marker claims by `name:`, that the document already has as
  a key, or that names a registered class stays silent — so nothing that
  previously applied now warns. `merge_overrides_into_fluids` returns the set of
  dotted keys it claimed (it previously returned `None`); the extra `_matched`
  parameter is internal recursion state.

- **…and that warning no longer fires on two spellings that DO reach a node.**
  Confluid's dotted grammar has more legal heads than an instance `name:`: the
  glob segments (`--**.lr` reaches every accepting descendant, `--*.lr` the
  direct children) route by shape rather than by naming anything, and a
  multi-hop path (`--runner.opt.lr`) floats its first segment and then takes
  strict one-level hops. Both were reported as *"matched nothing and was
  ignored"* while the value landed — for `--**.lr`, confluid's own report says
  `applied=[('lr', "… 'opt'", "glob '**'")]`. Glob heads are now skipped, and a
  head that names a marker counts as addressed even when its tail is not
  something liquifai can write there (routing a multi-hop tail is confluid's
  job; a tail the target refuses is a wrong-*key* failure, which confluid
  reports itself). The motivating `--optimizer.lr` case still warns.

### Changed

- **CLI overrides are appended in the order typed.** `_move_cli_keys_last`
  now re-seats EVERY override key's top-level head (bare keys and
  dotted-expanded heads alike) at the end of the document, iterating in typed
  CLI order — a head mentioned twice seats at its last mention. Previously
  only BARE keys moved, which forced every bare flag after every dotted flag
  regardless of what was typed (`--lr 0.2 --Trainer.lr 0.1` produced 0.2 on
  Trainer in BOTH flag orders) and left a dotted override folded into a
  pre-existing document block at the BLOCK's early position, where a later
  bare document key silently beat it (`--Trainer.lr 0.1` against
  `Trainer: {layers: 8}` + trailing `lr: 0.9` trained at 0.9). Both now
  answer 0.1. Flag order carries meaning: the CLI behaves exactly as if its
  keys were appended to the end of the config, in the order typed — last
  typed wins where flags overlap. Known accepted trade: re-seating a
  pre-existing block moves its unrelated keys' precedence with it (pinned as
  documented behaviour in the seating group of
  `tests/test_override_broadcast.py`). Rationale: `docs/architecture.md` §4
  → *Amendment: the CLI keys are appended in the order typed*.

- **"Did this override reach anything?" is now answered by confluid's report,
  not guessed.** When CLI overrides were applied, `run_command` wraps DI
  materialization in `confluid.collect_report()` and warns — before the
  command body runs — for every override the report says matched nothing.
  The pre-materialization heuristic (`_warn_unmatched_dotted_overrides`) is
  deleted: it re-derived confluid's addressing model and had been wrong twice
  (glob heads and multi-hop paths were reported "ignored" while the value
  landed). Two visible changes: a BARE override no object accepts now warns
  too (`--max_pcaks 3` used to run the job on defaults, silently — the old
  rule structurally could not see bare keys), and the warning now fires at
  execution time (phase 6) rather than during override application (phase 5).
  `merge_overrides_into_fluids` no longer returns the matched-head set
  (returns `None`); `LiquifyContext` gains a `cli_overrides` field. Requires
  confluid > 0.3.0's report fix (a cascade-delivered leaf satisfies its
  glob-registered candidate). A run without CLI overrides does not engage the
  report machinery at all.

- **`liquifai.bridge`: `columns` is typed `Any`** on `ExposeSpec` / `CustomSpec`
  and the `@expose` / `@custom` decorators (was `Tuple[Tuple[str, str], ...]`),
  so a consumer's presenter can accept a richer column shape than a plain
  pairs-tuple — the same "opaque to the engine" treatment `options` already had.
  Inside the provisional `liquifai[bridge]` extra, which sits outside the
  version contract.

- **A bare `--key value` override no longer becomes a constructor argument of a
  `**kwargs` class.** Writing a key into a marker's own kwargs is confluid's
  ADDRESSED channel, so what lands there is passed to the **constructor** while a
  bare cascading key becomes a post-init attribute. `merge_overrides_into_fluids`
  wrote every accepted key there, which erased that distinction — and for a target
  with a `**kwargs` constructor the accept-list is "everything", so *any* bare CLI
  override was delivered as though the user had addressed it at that node. The
  visible failures were a run-identity flag reaching a metric
  (`ValueError: Unexpected keyword arguments: run_name`, raised by the metric
  library from a call site nowhere near the config) and the same flag reaching a
  dataset loader, where nothing raised at all — it silently became part of a cache
  key. Note the asymmetry that made this a CLI-only bug: a top-level *YAML* key
  stays bare and has always landed as an attribute, so a working config broke the
  moment `--run_name` was added. Such a key is now left in the document for
  confluid's own broadcasting to deliver with the right provenance (new predicate
  `confluid.accepts_any_key`, hence the dependency floor). A class that
  **declares** the key is unaffected and still receives it as a constructor
  argument, so `--num_workers 8` and friends are unchanged.

- **A flat config's top-level keys now reach a command parameter annotated `Any`.**
  DI has always built a parameter annotated with a *configurable class* against the
  loaded document, which is what makes broadcasting work (a top-level YAML key
  injecting into the same-named constructor parameter). A parameter annotated `Any`
  took a different route — the raw `Fluid` was deep-flowed in isolation — so every
  top-level key was dropped, and dropped **silently**: a `dataset:` became `None`, a
  `max_epochs: 3` quietly reverted to the parameter default and the run looked
  configured. This is not an edge case: a generic runner (one command that executes
  a trainer, an evaluator or a converter, whichever the YAML names) cannot annotate a
  single class, so `Any` is the only honest annotation. `di.deep_flow` now takes an
  explicit `context=` — the loaded document — and builds document-shaped `Fluid`s with
  `confluid.materialize` against it. An already-built instance's `Fluid` attributes are
  deliberately *not* broadcast into; `!lazy:` still stays deferred.

- **Error messages no longer lose bracketed text to Rich markup.** The failure
  renderer interpolated untrusted exception text into a markup string, so any
  bracketed run Rich read as a style vanished — an install hint reading
  `pip install 'myapp[extra]'` printed as `pip install 'myapp'`, a wrong command
  handed to the user with no sign anything was lost. The message is now escaped;
  the `Error:` prefix keeps its styling.

## [0.1.0] — 2026-07-27

_First public release, published to PyPI as `liquifai` (tag `v0.1.0`)._

### Architecture review follow-ups (2026-07-26)

- **`core.py` no longer parses global flags.** `_parse_globals` and
  `_bind_dimension_flags` moved to a new `liquifai/flags.py` as
  `parse_globals(tokens) -> GlobalFlags` and `bind_dimension_flags(...)`; the
  historical 5-tuple return became the named `GlobalFlags` record, so `_prepare`
  no longer positionally unpacks five values. Both now take and return
  `Token`s and skip post-`--` literals, so a protected value can never be read
  as a flag (`app run -- --debug` keeps `--debug` as a value).
- **Config promotion records its provenance** *(new)*. Promotion is eager — a
  bare token becomes the config the moment a matching YAML exists in any search
  tier, so a stale `~/.config/<app>/report.yaml` silently swallows
  `app run report` that meant `report` as a positional. Every promotion is now
  logged at TRACE, and one resolved from OUTSIDE the working directory
  additionally at DEBUG, naming the token and the file. The log fires from
  `_prepare`, not from the router: routing is phase 1 and loggair is only
  configured in phase 4, so a `debug`/`trace` call at the decision site is
  dropped. `Invocation` gained `config_token` to carry the raw token that far.
  New `examples/promotion_app.py` exercises all four outcomes (CWD,
  `./config/`, an XDG file that swallows a positional, and no match) against
  a sandboxed temp workspace.
- **The nine `liquifai.core` re-export aliases are DEPRECATED** (removal in
  v1.0). They are now served by a PEP-562 `core.__getattr__` that emits a
  `DeprecationWarning` naming the exact replacement import, instead of plain
  assignments — a plain alias is invisible, which is why the previous "keep
  these" comment never converged on a cleanup. liquifai's own source and tests
  were migrated onto the owning modules (`di` / `overrides` / `grammar`) and a
  test now fails if anything internal regresses onto the deprecated surface, so
  the v1.0 deletion is a pure external-consumer migration.
- **Removed**: the `CliRouter` class (routing is the stateless
  `router.route(app, argv)`; `router.py` keeps the real content — the `_AppNav`
  adapter and the promoted-config resolver) and `liquifai/pipeline.py` entirely
  (`ConfigPipeline` was a chainable class with three methods and two call
  sites; the override step is the pure `overrides.apply_overrides`, the load
  step is two lines inline in `_bootstrap`). Generalised into a rule in
  `AGENTS.md`: **no stateless wrapper classes** — a type with one field and one
  method is a function.
- **Breaking (internal)**: `core.LiquifyApp._parse_globals` /
  `._bind_dimension_flags` are gone (use `liquifai.flags`);
  `_apply_overrides` takes `Token`s; `Invocation` gained a required
  `config_token` field.
- **New**: `docs/architecture.md` record 7 — why a deprecation must be served
  by `__getattr__` rather than announced in a comment.

- **Broadcast opt-outs are no longer bypassed by CLI overrides** *(bug fix)*.
  liquifai computed its own accept-list to decide which config nodes an
  override reached, and it diverged from confluid's on four counts: a
  `**kwargs` constructor (confluid accepts everything, liquifai accepted only
  the literal parameter name), `__init__`-body slots (reachable in YAML,
  invisible to the CLI), and — the actual bug — BOTH broadcast opt-outs. A
  class marked `@configurable(broadcast=False)` or a parameter typed
  `NoBroadcast[T]` correctly refused a bare YAML key while `--lr 0.9` went
  straight through. The local accept-list is deleted; `merge_overrides_into_fluids`
  now asks confluid, mapping the two CLI forms onto the two predicates it
  exports: dotted `--<name>.<key>` (addressed) → `accepts_key`, flat
  `--<key>` (bare broadcast) → `accepts_broadcast`. Requires
  `confluid>=0.1.0` with those predicates. The Fluid walker is also cycle-safe
  now, matching its `expand_strings`/`deep_flow` siblings.
- **`--` ends option parsing** *(new)*. Everything after a bare `--` is a
  literal value — never an option, never an override, and it never stops
  positional consumption. A positional whose value starts with a dash
  (`seek -- -5 /tmp/x`) was previously unrepresentable: both tokens fell
  through to the override parser as "unrecognized" and the command ran on
  defaults. An unclaimed literal gets its own warning, so nothing after `--`
  vanishes silently either.
- **Completion resolves a promoted config through confluid's search tiers**
  *(bug fix)*. TAB tested the typed path as-is, so with a `./config/foo.yaml`
  layout — which dispatch resolves and loads for real — the entire
  config-present branch was dead: TAB offered the command's signature flags
  and never the YAML's own override keys. It now resolves the same four tiers
  dispatch does (`./`, `./config/`, XDG), lazily, so the hot path stays
  stdlib-only until a config is actually on the line.
- **One argv walk instead of two** *(internal)*. The dispatcher and the
  completion engine each implemented the descent to
  (sub-app, command, promoted config, positionals); the copies drifted into
  the bug above. Both now run `walk.walk_invocation` over a `Nav` protocol
  (`router._AppNav` for live app objects, `engine._TreeNav` for the serialized
  tree). Side effect: a value-taking global flag now owns the next token in
  dispatch too, so `app --level run` is a flag/value pair rather than the
  `run` command.
- **Library code raises; only `run()` exits** *(behaviour change)*. The
  missing-config and unknown-command paths used to `console.print` +
  `sys.exit(1)` from inside a `LiquifyApp` method, hard-exiting any process
  that embedded an app. They now raise the new `ConfigNotFoundError` (a
  `FileNotFoundError`) and `UnknownCommandError` (a `ValueError`), which
  `run()`'s existing failure handler renders identically — same message, same
  exit code, same `--debug` propagation.
- **`liquifai[bridge]` extra** *(new)*. The provisional `liquifai.bridge`
  subpackage is now explicitly outside the version contract: an opt-in extra
  to declare in consumer metadata, plus `__provisional__` / `__extra__`
  markers and a rationale record. It carries no requirements today.
- **`HelpLayout` is a closed `Literal`**. `layout: str` in `_show_help` /
  `report.show_configuration` became `Literal["table", "lines"]` — a typo
  used to fall through silently to the table branch.
- **Removed**: `liquifai/discovery.py` (`get_configurable_paths` duplicated
  `confluid.get_hierarchy_from_instance`, which `report.py` actually uses),
  `CompletionController` (a five-method pass-through to five module
  functions), `Invocation.cmd_name` (write-only), and
  `overrides.accepted_override_keys` with its `core._accepted_override_keys`
  alias.
- **Breaking (internal)**: `Invocation.remaining_argv` (`List[str]`) is now
  `Invocation.remaining_tokens` (`List[Token]`), so later phases can tell an
  option from a post-`--` literal.
- **New**: `docs/architecture.md` — six decision records covering the
  hand-rolled parser, out-of-process completion, the shared walk, who owns
  settability, how positionals bind, and the bridge's stability boundary.

- **Double-TAB forces a completion value-cache refresh (bash)**: the lazy
  self-heal only fires when a positional's value cache is missing or older than
  5 min, so a cache that is fresh-by-age but wrong (an item deleted upstream)
  would keep showing the stale value. Pressing TAB a second time now forces the
  refresh regardless of age — bash forwards `$COMP_TYPE` (the readline
  completion type) to `liquifai-complete`, which reads a repeated/list TAB as
  "refresh now" and bypasses the age gate. Only the age gate is bypassed — the
  spawn throttle still applies, so a burst of double-TABs triggers at most one
  refresh per completion session. Still detached: the double-TAB shows the
  current cache, the next TAB shows the corrected list.
  bash-only (zsh/fish have no equivalent signal); needs a `--install-completion`
  re-run to pick up the updated wrapper.
- **`core.py` slimmed**: all Rich help rendering now lives in `report.py`
  (`show_command_index`/`show_global_options` join `show_configuration`), and
  the shell-completion flag interception moved to a `completion_cli.py` module,
  leaving `core._show_help` as pure orchestration.
- **Completion/help treat positionals and boolean flags correctly**
  (2026-07-16): a declared positional is advertised only by its
  `<placeholder>`/cached-value hint — its `--flag` spelling no longer appears
  in TAB candidates or the `--help` options table (which now renders a
  dedicated "Positional Arguments" block); the spelling still parses. The
  completion tree (cache v6) records `bool`-typed flags, so a store-true flag
  (`--append`) no longer opens a value slot (which silenced completion and
  made the shell fall back to filenames) — same for global non-value flags
  and the self-contained `--key=value` / `--key+` / `--key-` forms. Options
  already typed on the line drop out of the suggestions, and a positional
  supplied via its flag spelling counts as filled for the hint.
- **`examples/linefit/` training-style showcase app**: an installable CLI in
  the Lightning-CLI shape — `fit`/`validate`/`test`/`predict` script commands
  with config promotion and signature DI, YAML `!class:`/`!lazy:` wiring of
  model/data/optimizer (the optimizer deferred until the run supplies the live
  model), dotted CLI overrides, an app-side `print-config` verb dumping the
  merged config as a reloadable recipe, and the failure contract. Runs fully
  offline; CI drives every verb via its `run.py` self-test (the
  `examples/*/run.py` glob).
- **`examples/pypeek/` showcase app**: a small, installable PyPI query CLI
  demonstrating the whole surface end to end — operations → CLI (+ MCP), a
  `@configurable` client with CLI override broadcast, static (installed
  distributions, offline) + dependent (live PyPI versions) positional
  completion with the lazy self-heal cache, the failure contract, dry-run,
  and the `liquifai.apps` entry-point declaration.
- **XDG config search paths**: relative config paths (promoted script-command
  tokens and `--config` values) resolve through confluid's search tiers —
  `./` → `./config/` → `~/.config/<app-name>/` → `~/.config/confluid/` →
  `$XDG_CONFIG_DIRS` — with the confluid app name set to the running app's
  name at startup. Note this makes config promotion more eager: a positional
  token is consumed as a config path when a matching YAML exists in any tier.
- **Core framework**: `LiquifyApp` with the strict 5-phase bootstrap
  lifecycle, `@command` / `@script_command` (config promotion, `flow_mode`),
  confluid-driven dependency injection, CLI overrides with broadcast,
  sub-apps with aliases, positional arguments, `--docs`, the typed
  exception hierarchy, and shell completion (fast stdlib-only path, dynamic
  + dependent positional value providers).
- **Operations model**: pure operations register once via `@app.operation`
  and surface as auto-generated CLI commands (`build_commands()` + the
  context-factory/presenter hooks) and as MCP tools (`make_mcp_tools`).
  This is the ONE ops-registration path — a transitional
  `@command(presentation=...)` dual-mode existed pre-release and was
  removed before the first tag.
- **`liquifai.bridge` — declarative SDK-to-operations bridge (PROVISIONAL).**
  Turn a Python SDK client's methods into liquifai operations (and therefore
  CLI commands + MCP tools) by decorating a subclass of the real SDK class:

  - `SdkBridge(conn_cls, configure=, adapters=, policies=, target=,
    shape_status=)` + the `@bridge.group(...)` class decorator; group
    registration is **per-instance** state, so two bridged apps in one
    process cannot collide.
  - `@expose` (declarative — the method name is the SDK method; a spec of
    `P` params maps CLI args onto SDK kwargs) and `@custom` (the conn-first
    custom-body escape hatch).
  - Built-in `call` / `items` policies; SDK-dialect surfaces (e.g. a
    platform's paginated `list` vocabulary) are consumer-registered
    `OpPolicy` plugins — consumer-dialect code never lands in liquifai.
  - `liquifai.bridge.shaping`: stdlib-only result/parsing helpers
    (`record_to_dict` reflection ladder, `jsonify`, `dry_descriptor`
    contract, `format_call`, `records_of` / `items_result` /
    `filter_records` / `require`, `parse_csv` / `parse_kv` / `parse_tags`,
    `DEFAULT_ADAPTERS`).
  - Unknown policy / adapter keys raise `CommandDefinitionError` at
    decoration time, not at first call.

  Provisional: single production consumer so far (sairen); the API may
  change in 0.x minor releases without deprecation, and the subpackage is
  deliberately not re-exported from the top-level `liquifai` package.
  Extracted from sairen's decorator bridge; sairen keeps only its MAF
  dialect (list policy + `resources` adapter) as plugins.
