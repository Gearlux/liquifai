# Shell Completion

Every LiquifyApp ships with bash, zsh, and fish tab completion. Candidates
include sub-commands, sub-app names, global flags, each command's own
`--<option>` flags (derived from its function signature — so a plain
`@command` like `run list` completes `--experiment`, `--status`, …, and a
`@script_command` whose argument is a `@configurable` completes its nested
overrides), YAML files for `@script_command` configuration arguments, and
`--<key>` override suggestions derived from the loaded config. A bare
`<cmd> <TAB>` reveals the command's options directly — for a `@script_command`
they appear *alongside* the config-file candidates (so
`train <TAB>` shows both the YAML files to pick and the trainer overrides),
and narrowing with `--<TAB>` drops the files.

**Option flags use the same shortest-unique paths as `--help`.** Completion
runs the command's override paths through the *exact same* confluid functions
(`get_hierarchy` + `shortest_unique_paths`) that build the `--help` options
table, so the two never disagree: a uniquely-named leaf shows as `--class_name`
(not the noisy `--converter.class_name`), and a leaf shared by two sub-objects
keeps just enough prefix to disambiguate (`--model.lr` vs `--optim.lr`). Both
the short flat form and the fully-dotted form work as overrides at runtime;
completion only *suggests* the short one.

**Positionals are advertised as `<placeholders>`, never as flags.** A declared
positional (`create <name> <source_version>`) is hinted with `<name>` /
`<source_version>` (or real cached values — see below) at its slot; its
`--flag` spelling is *excluded* from both the completion candidates and the
`--help` options table, which instead renders a dedicated "Positional
Arguments" block. The flag and `key=value` spellings still **parse** at
runtime (positional, `key=value`, and `--flag` forms interoperate) — they just
aren't advertised. A positional supplied via its flag spelling counts as
filled: the `<placeholder>` hint moves past it.

**Completion is type- and state-aware at flag positions.**

- A **boolean** flag (`append: bool = False` → `--append`, store-true) does
  not open a value slot: `… --append <TAB>` keeps offering the remaining
  flags. Only a *value-taking* flag (`--target_version <TAB>`) stays silent so
  the shell's default filename completion can fill the value. Global boolean
  flags (`--debug`) and the self-contained `--key=value` / `--key+` /
  `--key-` forms behave the same way.
- Flags **already typed** on the line drop out of the suggestions — in any of
  the override grammar's spellings (`--key value`, `--key=value`,
  `--key+`/`--key-`, `+key`, bare `key=value`). A repeated flag still parses
  (last write wins); it just isn't offered again.

> **Note:** the option flags are baked into a per-app cache
> (`~/.cache/liquifai/<app>.json`) that is refreshed automatically every time
> the app runs (including on `--help` and `--install-completion`). After
> upgrading liquifai, run the app once (e.g. `my-app --help`) so newly
> surfaced completions appear.

```bash
my-app --install-completion          # auto-detects $SHELL, appends to your rc file
my-app --install-completion zsh      # explicit shell
my-app --show-completion bash        # print the script to stdout (manual install)
```

After installing, restart your shell (or `source ~/.zshrc` / `~/.bashrc`).
For fish the script is written to `~/.config/fish/completions/<app>.fish`
and auto-loads in the next session.

## Aliases

Shell aliases don't inherit completion automatically (bash and zsh bind
completion to specific command names, not to alias expansions). Use
`liquifai-bind-alias` to wire any alias up:

```bash
alias mt='my-app train'
liquifai-bind-alias mt my-app train
```

The first argument is the alias name; the rest is what the alias expands
to. `mt cfg.yaml<TAB>` then completes with the same `--key` suggestions
you'd get from `my-app train cfg.yaml<TAB>`.

## Workspace-local installation (avoid touching `~/.bashrc`)

`<app> --install-completion` writes into the user's global rc file. For
multi-project workspaces it's often nicer to keep completion confined to
a project-local rc that your `project.bashrc` sources, so a fresh checkout
gets working completion without polluting `~/.bashrc`. Use the bundled
`liquifai-install-completions` console script:

```bash
# Discover every Liquifai app in the active venv and install completion
# blocks for all of them into a single project-local rc fragment.
liquifai-install-completions --target-rc ./.project.bashrc.completion

# Or pin to a specific list of apps.
liquifai-install-completions --target-rc ./.project.bashrc.completion my-app other-app

# Then in your project.bashrc:
#   [ -f ./.project.bashrc.completion ] && source ./.project.bashrc.completion
```

Auto-discovery has two tiers:

1. **Entry-point group (preferred — instant).** An app declares itself in the
   `liquifai.apps` entry-point group; discovery then reads it straight from
   installed dist metadata (milliseconds, deterministic, immune to probe
   timeouts):

   ```toml
   # pyproject.toml — name = the app/binary name, value = the LiquifyApp instance
   [project.entry-points."liquifai.apps"]
   my-app = "my_app.cli:app"
   ```

   Entry-point changes need an (editable) reinstall to become visible — the
   same rule as `confluid.configurables`.

2. **Probe fallback (slow — for apps that haven't opted in).** Remaining
   executables in `sys.prefix/bin` are probed with `--show-completion bash`,
   keeping the ones that emit Liquifai's completion marker. A Liquifai app
   handles that flag early, but a heavy app still imports its full stack
   (torch, Lightning, …) at module load *before* the handler runs, so an
   individual probe is **not** cheap. The probes run concurrently (a small
   bounded thread pool) so a populated ML venv resolves in tens of seconds
   instead of minutes.

## Dynamic positional values (complete `<name>` from a live source)

By default the `<name>` placeholder is just a hint. A command can instead make
a positional complete with **real values** from a provider — e.g. complete
`my-app dataset download <name>` from `dataset list`:

```python
def dataset_names() -> list[str]:           # any Callable[[], list[str]]
    return [d["name"] for d in fetch_datasets()]   # may import the SDK / hit the network

@sub.command("download", positionals=["name"], completions={"name": dataset_names})
def download(name: str = "", path: str = "."): ...

# or, wired separately from the decorator (e.g. to dodge an import cycle):
sub.set_completions("download", {"name": dataset_names})   # before build_commands()
```

How it stays fast and offline-safe:

- The provider runs **only at refresh time** (`my-app --refresh-completions`),
  never on the TAB hot path. Its result is cached under
  `~/.cache/liquifai/<app>.values/`.
- `liquifai-complete` reads that JSON cache and offers the values for the
  `<name>` slot (prefix-filtered like any candidate). No cache yet → it falls
  back to the `<name>` placeholder. A provider that raises (offline / no auth)
  is skipped — completion silently degrades to the placeholder.
- **Self-heals on first use.** A static positional whose value cache is missing
  or stale (`> DEPENDENT_REFRESH_TTL`, 5 min) is filled the same way dependent
  ones are (see below): the first TAB returns the placeholder instantly **and**
  kicks off a *detached, throttled* background refresh, so a freshly-added
  positional (e.g. a new `run list <experiment>`) populates itself on the **next**
  TAB — no manual `--refresh-completions` required.
- **Refresh-everything is explicit by default**: run `my-app --refresh-completions`
  to (re)populate ALL value caches at once. Opt into automatic background refresh by
  setting `LIQUIFAI_BG_REFRESH=1` — then a successful run refreshes stale caches
  (>10 min) in a detached daemon thread, never blocking the command. It is OFF
  by default so a normal run never triggers a surprise provider call (e.g. a
  platform query). A sub-app alias (`ds` for `dataset`) shares the canonical
  command's value cache.
- **Prefix matching is case-insensitive**: `cifar<TAB>` finds `CIFAR_…`.
- **Values with spaces work.** A candidate like `My Dataset V2` is transported
  as one token (the wrappers newline-join `$COMP_WORDS`) and emitted
  backslash-escaped so the shell inserts it as a single argument. bash gets this
  with no re-install; **zsh/fish users should re-run `--install-completion`** once
  to pick up the newline-joining wrapper.

### Dependent positionals (a later `<version>` that depends on an earlier `<name>`)

A provider that takes **one argument** receives the already-typed earlier
positionals, so a second positional can complete from the first — e.g.
`download <name> <version>` where versions depend on the chosen dataset:

```python
def dataset_names() -> list[str]:                      # static (0-arg)
    return [d["name"] for d in fetch_datasets()]

def dataset_versions(inputs: dict[str, str]) -> list[str]:   # dependent (1-arg)
    return versions_of(inputs["name"])                 # inputs = the typed earlier positionals

sub.set_completions("download", {"name": dataset_names, "version": dataset_versions})
```

At refresh, liquifai **pre-enumerates** the dependent values: for each value of
the prior positional(s) it calls the dependent provider and caches the result
per input combination (capped at `max_combos`, default 200). On TAB,
`download "My Dataset V2" <TAB>` reads the cache keyed by that exact name and
offers its versions — still no provider call on the hot path.

**Self-healing (new / changed / beyond-the-cap values).** Pre-enumeration alone
would freeze a name's versions at the last refresh. So on TAB, if a dependent
slot's per-input cache is **missing or stale** (`> DEPENDENT_REFRESH_TTL`, 5 min),
the fast path returns whatever's cached now (instant — stale values or the
placeholder) **and** kicks off a *detached, throttled* background refresh for that
exact input (`<app> --refresh-completion-value …`). So a brand-new dataset, a new
version, or a name beyond the cap becomes current on the **next** TAB without ever
blocking — no manual `--refresh-completions` needed. Throttled per input so rapid
TABbing can't fork a storm; opt out entirely with `LIQUIFAI_NO_LAZY_COMPLETE=1`.

When a self-heal **actually changes** the values, the next TAB shows a transient
`<<positional>-updated>` hint (e.g. `<version-updated>`) alongside them for a short
window, so you know the background refresh took effect — change-only (an unchanged
refresh shows nothing), and it disappears the moment you type a real value.

## Runnable example

[`examples/completion_providers.py`](../examples/completion_providers.py) wires
a static and a dependent provider onto a command's positionals and prints the
candidates each would contribute. Shell interaction itself (TAB, wrappers,
caches) can't run in CI — install the app and try `download <TAB>` live.
