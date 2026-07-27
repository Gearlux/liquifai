# Changelog

All notable changes to liquifai are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [Unreleased] — 0.1.0, the first public release

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
