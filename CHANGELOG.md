# Changelog

All notable changes to liquifai are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semver](https://semver.org/) — pre-1.0, minor bumps may break.

## [Unreleased] — 0.1.0, the first public release

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
