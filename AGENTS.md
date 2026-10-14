# Liquifai Mandates

Rules only. The reasons are in [`docs/architecture.md`](docs/architecture.md) (cited as §N),
and usage is in the [README](README.md) and `docs/*.md`. Root `AGENTS.md` rules are not
repeated here.

## Current state

The app framework the workspace CLIs are built on. `LiquifyApp` sets up loggair and confluid
and injects config-built objects into command signatures by type annotation, so an app declares
commands and nothing else. Published on PyPI (0.3.0) from a public repo. It requires
`confluid>=0.3.0`: `overrides.py` imports `accepts_key`/`accepts_broadcast`/`accepts_any_key`
at module level, so an older confluid fails at import.

Three ways to register:
- `@app.command()`: a hand-written CLI handler.
- `@app.script_command()`: a CLI handler whose first argument is a promoted config file.
- `@app.operation()`: a dict-returning function. `build_commands()` turns it into a CLI
  command and `make_mcp_tools()` into an MCP tool (§10).

Modules: `core.py` (`LiquifyApp`, phase orchestration) · `walk.py` (tokenizer, the one argv
walk) · `router.py` (its live-app adapter) · `flags.py` (global-flag parsing) · `grammar.py`
(the flag table, token rules) · `overrides.py` · `di.py` · `host.py` (`os`/`device`) ·
`context.py` · `report.py` (all Rich help) · `exceptions.py` · `introspection.py` (signature
surgery for CLI and MCP wrappers) · `tools.py` (`make_mcp_tools`) · `completion/` (stdlib-only)
· `completion_cli.py` (Rich glue for the completion flags) · `_fast_complete.py`
(`liquifai-complete`) · `bridge/` (provisional SDK bridge, §6).

Run Python from inside `liquifai/` or a neutral directory. From the workspace root,
`from liquifai import LiquifyApp` fails with `ImportError … (unknown location)`, because the
project folder shadows the package.

## Lifecycle and routing

- No app writes bootstrap code: `run()` configures loggair and loads the config.
- `run()` runs in this order, and nothing is skipped or reordered: completion flags
  short-circuit → route (the walk) → help → parse globals → bind dimension flags → host facts →
  context + `_bootstrap` (logging, then config) → bind positionals → apply overrides → execute.
  Logging is live only after `_bootstrap`, so a routing diagnostic is logged from `_prepare`
  (`_log_promotion_source`), never from the router.
- `walk.walk_invocation` is the ONE descent, used by dispatch (`router._AppNav`) and completion
  (`engine._TreeNav`). The two differ only through the injected `resolve_config` callback. Never
  re-implement it. After `--` every token is literal; split tokens only with
  `walk.option_texts`/`walk.literal_texts`. (`tests/test_walk.py`) §3.
- Config promotion: the first non-flag token after a `script_command` is its config when it
  resolves to an existing file through `confluid.resolve_config_path` (a bare name gains
  `.yaml`). A leading token that names no command goes to the default command: its config if it
  is a script command, otherwise its first positional. A token that resolves to no file is left
  for the positional and override parsers. Resolve once; never add a second `.exists()` chain,
  extend confluid's resolver instead. (`tests/test_config_search_paths.py`, `tests/test_walk.py`)
- Positionals bind only as leading tokens, as verbatim strings, into the TOP level of the
  config, before overrides, so `--name` wins its slot. (`tests/test_positionals.py`) §5.
- Host facts: the detected scope activations are PREPENDED, the `platform:` key is merged UNDER
  the document, and only the detected copy is filtered against the document's declared values,
  so a typed `--scope` keeps confluid's typo error. `--os`/`--device` stay ordinary overrides,
  never implicit dimension flags. No environment variable is set or read. (`tests/test_host.py`) §8.
- A log flag the user did not type is forwarded as `None`. Never restate loggair's defaults and
  never read a `LOGGAIR_*` variable here.
  (`tests/test_log_levels.py::test_no_level_literal_reaches_loggair_when_no_flag_is_given`) §9.

## Grammar and flags

- `grammar.GLOBAL_FLAG_SPECS` is the only global-flag list. The parser (`flags.parse_globals`),
  help (`report.show_global_options`), completion, `HELP_FLAGS` and `RESERVED_SHORTS` all derive
  from it. Add a flag, or a help spelling, to the table only; mark plumbing `hidden=True`.
  `grammar.py`, `walk.py` and `exceptions.py` stay stdlib-only.
  (`tests/test_grammar.py`, incl. `::test_grammar_module_is_stdlib_only`)
- Command shorts are DECLARED (`@app.command(short={"b": "background"})`), never derived from
  a parameter's first letter. A reserved, repeated, multi-letter or unknown-parameter letter
  raises `CommandDefinitionError` at decoration. `expand_shorts` rewrites them before override
  parsing; an undeclared `-x` stays unrecognised. Help shows `-b, --background`.
  (`tests/test_short_options.py`, `tests/test_help_flag.py`)
- Flag KEYS are normalised `-` → `_` per dotted segment, after polarity is read
  (`--default-nodes-` means False). Flag VALUES are never normalised. (`tests/test_flag_spelling.py`)
- A value for a bare key the active command declares `str` or `Optional[str]` is kept verbatim;
  everything else keeps YAML typing (`confluid.parse_value`). Do not grow this into a per-type
  coercion table or a new value spelling. (`tests/test_override_str_verbatim.py`)
- `strict_flags` stays OFF by default. When on, it judges only BARE keys, against the command's
  parameters plus the config's top-level keys MINUS the override keys themselves; dotted keys are
  left to `warn_unused_overrides`. (`tests/test_strict_flags.py`)

## Overrides and DI

- Flags behave as if their keys were appended to the document in the order typed:
  `_move_cli_keys_last` re-seats every override head, bare and dotted, at the end in typed order.
  Never reorder flags relative to each other, and never ask confluid for a "CLI beats YAML" tier.
  (the seating group in `tests/test_override_broadcast.py`, e.g.
  `::test_a_specific_flag_typed_after_a_bare_flag_wins_on_its_class`) §4.
- Settability is confluid's question. Dotted `--<name>.<key>` asks `accepts_key`; bare
  `--<key>` asks `accepts_any_key` first and SKIPS such a `**kwargs` target (confluid's broadcast
  delivers it as an attribute), then `accepts_broadcast`. Never reintroduce a local accept list,
  never read a `__confluid_*__` marker, never parse a library's error text and retry.
  (`tests/test_override_broadcast.py::test_flat_override_is_not_written_into_a_kwargs_targets_own_kwargs`
  and its siblings; confluid `tests/test_no_broadcast.py`) §4.
- An override that reached nothing is a WARNING, judged by `confluid.collect_report()` around DI
  and emitted before the command body runs. Never a local heuristic, never debug/trace.
  (`tests/test_override_broadcast.py::test_end_to_end_a_doomed_override_warns_from_the_real_run`)
- A token that matches no override form gets one warning per token (an `UnknownFlagError` under
  `strict_flags`). Making it an error by default needs the user's sign-off (TASKS.md).
  (`tests/test_override_grammar.py`)
- DI picks a block by membership: the class-name key, then the parameter-name key, then the
  whole document only when neither key exists. An empty or null block means "defaults". The
  class-name block is never hoisted into the marker's kwargs (`hoist_block=False`); the other two
  are. (`tests/test_di_block_resolution.py::test_a_cli_override_beats_a_class_name_block`,
  `::test_a_class_name_block_still_wins_over_an_earlier_bare_key`) §4.
- `di.deep_flow` takes the document explicitly (`context=`), and both `run_command` and
  `_resolve_kwargs` pass it. `confluid.active_context` does not broadcast; never rely on it.
  (`tests/test_flat_config_broadcast.py`)
- `LiquifyContext` lives in a ContextVar, never a module global.
  (`tests/test_failure_contract.py::test_context_is_contextvar_isolated`)

## Errors and help

- Raise only classes from `exceptions.py`; each also inherits its builtin, and `LiquifaiError`
  does not subclass `confluid.ConfluidError`. Library code raises; only `run()` exits. There, a
  `LiquifaiError` or `ConfluidError` becomes one `Error: …` line (message
  `rich.markup.escape`d, prefix still styled) and exit 1; under `--debug` it propagates. Any other
  exception always propagates. (`tests/test_exceptions.py`, `tests/test_failure_contract.py`)
- A parameter without a default that nothing supplied (checked after DI) raises
  `MissingArgumentError`, naming the CLI path and both spellings.
  (`tests/test_missing_argument.py`, `examples/pypeek/run.py`)
- A displayed command name comes from `LiquifyApp._command_path`, never from the function's
  `__name__`. (`tests/test_core_extended.py::test_help_names_*`)
- `report.py` renders all help; `core._show_help` only orchestrates. `--docs` is `--help` with
  `layout="lines"`: same data. Help for an app's default command lists the other commands first.
  (`tests/test_core_extended.py::test_bare_help_lists_the_commands_even_when_a_default_command_answers_it`,
  `tests/test_completion.py::test_run_docs_flag_renders_lines`)
- A positional is never advertised as a `--flag` in help or completion, though that spelling
  still parses. (`tests/test_help_with_config.py`, the `_version_create_app` cases in
  `tests/test_completion.py`)

## Code shape

- No stateless wrapper classes: one field plus one method is a function (`router.route`, the
  `completion_cli` functions). `core.py`'s `_maybe_handle_*` methods are thin call-throughs.
- CLI handlers and MCP tools are both built with `introspection.split_context_param` +
  `graft_signature`; never re-inline that. Provider specs come only from
  `completion.tree._provider_spec`; instance walks use `confluid.get_hierarchy_from_instance`. §10.
- `@app.operation` is the only operations path; `command()` takes no `presentation`.
  (`tests/test_operation_command.py`)
- Both operation wrappers pass the context BY NAME as `introspection.CONTEXT_PARAM`, and only to
  an operation that declares it; never `op(conn, …)`. An MCP tool advertises keyword-only
  parameters, the factory's only for an operation that takes the context, refuses an operation
  parameter named like a factory parameter (`CommandDefinitionError`), and builds the context
  inside its `try`. §10. (`tests/test_operation_command.py::test_a_failing_mcp_context_factory_is_reported_in_band`
  and the five tests after it)
- `liquify()` answers from the active context only when the call names no `scopes` and no other
  file; otherwise it loads the named file, and installs that context only if none was active.
  (`tests/test_help_with_config.py::test_liquify_loads_the_config_it_is_given_not_the_one_a_previous_call_loaded`)
- No `from __future__ import annotations` in `tools.py` or `bridge/`: DI, confluid and MCP
  schema generation read live annotation objects.
- `core.py` re-exports nothing from `di`, `overrides` or `grammar`: a helper is imported from
  the module that owns it. Add no alias on `liquifai.core`.

## Bridge (`liquifai/bridge/`, provisional — §6)

- Keep it out of the top-level exports; its API may change in 0.x minors.
  (`tests/test_bridge_group.py::test_bridge_is_not_reachable_from_the_top_level_package`)
- An SDK's own dialect (pagination, wire shapes, result envelopes) never lands here: it goes in
  a consumer-registered policy (`SdkBridge(policies=…)`) or adapter (`adapters=`).
- Registration is per-`SdkBridge` instance, never module-global. An unknown policy or adapter
  key raises `CommandDefinitionError` at decoration.
- A policy names the extra `@expose` settings it reads in `options: FrozenSet[str]`; a policy
  without it reads none. Any other extra setting raises `CommandDefinitionError` at decoration,
  naming the `@expose`'s file:line and the closest setting. Never accept undeclared settings again.
  §11. (`tests/test_bridge_group.py::test_a_misspelled_expose_setting_is_refused_at_decoration`
  and the three tests after it)
- A synthesized op's annotations come from `introspection.annotations_for`, and both built-in
  policies build their op with `policies._single_call_op`; never re-inline either.
  (`tests/test_bridge_group.py::test_a_synthesized_op_annotates_its_parameters_and_return_but_not_conn`)
- A `P` may declare `type=` from the closed `spec.P_TYPES` (`str`, `int`, `float`, `bool`); it is
  the annotation, and `policies.read_typed` reads a text value with `confluid.parse_value` and
  refuses one that is not that type. An unlisted type, `type=` with `adapt=`, or a default of
  another type is a `CommandDefinitionError` at decoration. Never widen the set to a structured
  type here — an adapter decides those.
  (`tests/test_bridge_policies.py::test_a_declared_type_reads_the_value` and its neighbours,
  `tests/test_bridge_group.py::test_a_parameter_type_that_cannot_hold_is_refused_at_decoration`)
- "Missing" is `shaping.is_missing`: `None` or `""`, never `0` or `False` — for a required
  param (`require`) and for `omit_empty`, which leaves out only a missing value or an empty
  declared default. Never test a value with `not value` in a call path.
  (`tests/test_bridge_policies.py::test_a_required_zero_or_false_is_given_not_missing` and the
  five tests after it; `tests/test_bridge_shaping.py::test_require_counts_only_none_and_empty_text_as_missing`)
- Test fakes are hand-written (`tests/bridge_fakes.py`), never `MagicMock`.
  (`tests/test_bridge_group.py`, `tests/test_bridge_policies.py`, `tests/test_bridge_shaping.py`)

## Completion

- The `liquifai-complete` path reads JSON caches and imports only stdlib: every
  `liquifai/completion/` module stays stdlib-only at import (confluid only lazily inside
  functions). It never imports the app or runs a provider in-process; its one side effect is
  detaching a self-heal subprocess. §2. (`tests/test_fast_path_imports.py`)
- `liquifai.__version__` is read lazily in `__init__.__getattr__`, because every TAB imports
  the package. Never make it eager.
  (`tests/test_version.py::test_importing_liquifai_does_not_read_the_metadata`)
- Providers run only at refresh time. Background full refresh stays OFF unless
  `LIQUIFAI_BG_REFRESH` is set. A raising provider is skipped. A provider's arity decides its
  kind: 0 args static, 1 arg dependent. Value caches key on the canonical command path, so an
  alias shares them.
- Self-heal: a missing or stale value cache detaches `<app> --refresh-completion-value`,
  throttled by `LAZY_REFRESH_THROTTLE`. The bash double-TAB bypasses the age check only, never
  the throttle. `changed_at` is stamped only when the values changed.
  (`tests/test_completion.py::test_force_refresh_bypasses_ttl_on_fresh_cache`,
  `::test_force_refresh_spawns_once_per_session`, `::test_wants_forced_refresh`)
- A missing or outdated command-tree cache detaches `<app> --refresh-completions`, which also
  runs every provider (a network query, for an app whose providers make one). A background
  refresh started by a TAB is accepted (user ruling 2026-10-13 — do not re-litigate) as long as
  `LIQUIFAI_NO_LAZY_COMPLETE=1` disables it: every spawn the fast path makes goes through
  `cache._spawn_throttled`, which honours it.
  (`tests/test_completion.py::test_tree_refresh_is_throttled_and_can_be_opted_out`)
- A new field in the serialized tree bumps `completion.tree.CACHE_VERSION`.
- A value with spaces stays one token. bash forwards `$COMP_LINE`/`$COMP_POINT`
  (`words_from_comp_line`); zsh and fish newline-join `$COMP_WORDS` (`split_comp_words`);
  candidates go through `escape_candidate`. Never `COMP_WORDS.split()` and never rely on
  `${COMP_WORDS[*]}`. A wrapper change needs `--install-completion` re-run.
- At a flag position: a bool flag opens no value slot; option keys already typed drop out;
  global flags are never dropped (`--scope` repeats).
- Every app declares the `liquifai.apps` entry point (name = binary = `LiquifyApp.name`). Keep
  the subprocess probe while any workspace app lacks it (TASKS.md). (the
  `declared_liquifai_apps` tests in `tests/test_completion.py`)

## Docs, examples, release

- The README is a landing page. Each topic has a `docs/<topic>.md` and a runnable twin in
  `examples/`; CI runs `examples/*.py` and `examples/*/run.py`. A feature change updates its page
  and its example. A new topic adds a page, an example and a README index row.
  README links are absolute GitHub URLs; links between `docs/` pages stay relative.
  (`tests/test_docs_links.py`)
- Installable example apps (`examples/pypeek/`, `examples/linefit/`) stay subdirectory
  projects. They reach CI through an offline `run.py` self-test. pypeek carries `__init__.py` so
  mypy does not see two modules named `run`.
- An example or test that asserts on captured log text passes
  `env={**os.environ, "NO_COLOR": "1"}` to its subprocess, because loguru colorizes under CI
  variables. Reproduce with `CI=true GITHUB_ACTIONS=true python examples/<name>.py`.
- Public docs name no consumer and no corporate name: root "Published Projects Are Standalone".
- Release: bump `pyproject.toml`, add a dated `CHANGELOG.md` section (pre-1.0 breaks go under
  **Breaking**), then tag `v<version>`; `release.yml` builds, smoke-tests from `/tmp` and
  publishes. Order: loggair → confluid → liquifai. A new confluid or loggair API used here is
  released first, and its floor is raised in the same change. Before tagging, install the wheels
  into a fresh venv from a neutral directory and `import liquifai`.
- Quality gates: the root rules (formatters, flake8, pytest inside `liquifai/`; mypy from the
  root). This repo's CI also runs `mypy .` here, which sees optional imports the root run hides.
