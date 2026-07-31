# liquifai — backlog

Open work for this project. Cross-cutting / multi-project initiatives live in the
workspace root `TASKS.md`. Completed items are not archived here — git history is the record.

- [ ] Declare `liquifai.apps` entry point in sairen's pyproject (work machine — submodule not checked out here) so its discovery stops depending on the probe fallback @todo @chore
- [ ] Escalate dropped-override-token warning to a hard error after a bake period @todo @ux  <!-- the `--` pass-through escape hatch shipped 2026-07-26 (walk.tokenize), so the prerequisite is met. -->
- [ ] Pluggable command registration via entry points @feature  <!-- Liquifai CLI verbs — separate from the confluid.configurables entry-point group; see Navigaitor MCP Surface for the @configurable discovery side. -->

- [ ] **Remove the 9 deprecated `liquifai.core` aliases (v1.0).** They warn on access now and nothing inside liquifai uses them. BLOCKED here: the external consumers (streamstudio / recordstream) are bare submodule shells on this machine (0 `.py` files, mid-rename) — audit + migrate them on the work machine, then delete `core._DEPRECATED_ALIASES` and `core.__getattr__` and drop `tests/test_deprecated_aliases.py` @todo @cleanup
- [ ] Revisit `liquifai.bridge` when a SECOND consumer appears: stabilise the spec vocabulary against both and fold it into the version contract, or split it into its own distribution (docs/architecture.md §6) @todo @architecture
- [ ] DI silently ignores wrapped annotations: `model: Optional[Model] = None` resolves to `None` instead of an injected instance, with no warning (`di.resolve_kwargs` only matches a bare `@configurable` class). Either handle `Optional`/`Union` — confluid's `to_pydantic` already has the configurable-union rule — or warn when an unresolvable annotation CONTAINS a configurable @todo @di
- [ ] `flags.parse_globals` and `flags.bind_dimension_flags` are two sequential passes over the same token list; they are genuinely ordered (the second needs the config the first located) but could share one option-token splitter with `overrides.parse_override_args` @low @cleanup
- [ ] Progress bar integration with Rich @ui
- [ ] **Rich integration:** expand the use of Rich for experiment progress bars. @low @feature
