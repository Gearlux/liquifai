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
The default command's positional is hinted at a *bare* prompt too — `my-app
<TAB>` lists `<workspace>` (or a script default's config files) beside the
command names, because a leading token binds to the default command without
its name (see [Default command redirection](commands-and-di.md#default-command-redirection)).

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

**A `@script_command`'s config contributes its own override keys.** Once a
config file is on the line, TAB offers that YAML's keys alongside the command's
signature flags:

```console
$ my-app train experiment <TAB>
--learning_rate  --hidden_units  --threshold  --config  --level  …
```

The config token resolves through the same search tiers dispatch uses (`./`,
`./config/`, then the XDG dirs), so a `config/experiment.yaml` layout completes
exactly like one in the working directory. (Completion used to test the typed
path as-is, which silently limited this to the CWD — see
[Architecture Decisions §3](architecture.md).)

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

## The second TAB lists the options grouped, with their help

In bash and zsh, listing a command's options shows the global options first, then the command's own, each with
its help, like `--help` does:

```console
$ my-app serve --<TAB><TAB>
global options
  -c, --config PATH             Configuration file.
  -d, --debug                   Enable debug mode.
  --level LEVEL                 Set log level for both sinks (TRACE, DEBUG, INFO).
  …
options of my-app serve
  -b, --background              run detached
  --port PORT                   the port to serve on
$ my-app serve --
```

The line stays as you typed it; keep typing or TAB again. Only the groups the typed word matches are listed
(`--b<TAB><TAB>` shows `options of my-app serve` alone), and an option you already typed drops out. A command
option's help is its `Args:` docstring line; a config's own keys are listed without one. The first TAB is
unchanged: it still fills in the part the matches share.

bash shows its own list instead (sorted alphabetically) whenever liquifai cannot draw its own reliably: the cursor
is inside the line, the line is wider than the terminal, the list is taller than it, or bash is older than 4.4
(macOS's `/bin/bash`). Command names, a positional's values, config files and an option's values are always
bash's list. zsh shows the same groups through its own completion listing, so no such limits apply there. fish
lists the options as before.

To go back to the shell's own sorted list everywhere, set:

```bash
export LIQUIFAI_COMPLETION_LIST=plain   # grouped (the default) or plain; read on every TAB
```

An installed completion script predating this listing keeps the old list: run `my-app --install-completion`
again to get it.

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
- **A missing or outdated command-tree cache self-heals too.** No
  `~/.cache/liquifai/<app>.json`, or one written by an older liquifai, completes
  nothing on that TAB (bash then lists files) and detaches
  `my-app --refresh-completions` in the background, so the **next** TAB works. That
  refresh also runs every value provider, so a provider that queries a server does so
  then. Same throttle and `LIQUIFAI_NO_LAZY_COMPLETE=1` opt-out as the value caches;
  the throttle marker is `~/.cache/liquifai/<app>.pending`.
- **Refresh-everything is explicit by default**: run `my-app --refresh-completions`
  to (re)populate ALL value caches at once. Opt into automatic background refresh by
  setting `LIQUIFAI_BG_REFRESH=1` — then a successful run refreshes stale caches
  (>10 min) in a detached daemon thread, never blocking the command. It is OFF
  by default. A sub-app alias (`ds` for `dataset`) shares the canonical
  command's value cache.
- **Prefix matching is case-insensitive**: `cifar<TAB>` finds `CIFAR_…`.
- **Values with spaces work.** A candidate like `My Dataset V2` is transported
  as one token and emitted backslash-escaped so the shell inserts it as a single
  argument. The bash wrapper forwards `$COMP_LINE` / `$COMP_POINT` so
  `liquifai-complete` re-tokenizes the raw line quote-aware (bash's own
  `$COMP_WORDS` splits on spaces); the zsh and fish wrappers newline-join their
  word arrays. **Re-run `--install-completion`** after upgrading liquifai so the
  installed wrapper matches: an older bash wrapper has no `$COMP_LINE`, and
  shatters a value with spaces.

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

### Force a refresh with a double-TAB (bash)

Self-heal only fires when a cache is missing or **older than 5 min**. A cache that
is fresh-by-age but **wrong** — you deleted an item upstream, but it was cached a
minute ago — would otherwise keep showing the stale value until the TTL lapses.

To force it: press **TAB a second time**. bash reports the repeated TAB via
`$COMP_TYPE`, which the wrapper forwards to `liquifai-complete`; liquifai reads it
as "refresh this now" and kicks off the background refresh **regardless of age**.
As always the refresh is detached — the double-TAB itself still shows the current
cache, and the **next** TAB shows the corrected list (a removed item gone, an
added one present; a changed dependent value also gets the `<…-updated>` hint). So
the flow is: `⇥⇥` to trigger, `⇥` again to see it.

Only the *age* check is bypassed — the normal spawn throttle still applies, so
hammering TAB triggers **at most one refresh per completion session**, not one per
keystroke. The throttle gates only the *refresh*, never the *display*: every TAB
still reads the cache fresh, so once the background refresh lands, the third,
fourth, fifth… TAB all show the corrected list without kicking off more refreshes.
(Wait out the throttle window, or edit the line, for another forced refresh.)

This uses bash's `COMP_TYPE`, so it is **bash-only** — zsh/fish have no equivalent
double-TAB signal and keep the age-gated behaviour. It needs the up-to-date
wrapper: **re-run `my-app --install-completion`** (or `liquifai-install-completions`)
once and re-source, otherwise the double-TAB is treated as an ordinary TAB.

### An option's value (`--library <TAB>`)

A `completions=` key that names an OPTION rather than a positional completes that option's value:

```python
@app.command(positionals=["option"], completions={"option": options, "library": lambda: ["unofficial", "complete"]})
def review(option: str = "", library: str = "") -> None: ...
```

`review a --library <TAB>` → `unofficial complete`. Its provider takes nothing, or the positionals
typed so far (`{name: word}`, `{}` when none is) — cached per prefix of them, like a command's
config scopes. A key that names no parameter of the command is refused when it is decorated
(`CommandDefinitionError`, with the closest parameter): a misspelt key would complete nothing.

### A list's next word (`a,<TAB>`)

After a comma, a positional, an option's value and `--scope` complete the list's next item:
`pictures all a,<TAB>` → `a,b a,c …`, the words already in the list left out. Shells keep a comma
inside the word, so the candidate is the whole list.

### The same values in `--help`

`<command> --help` ends with a "Values" block: each slot with a provider and the values TAB would
offer it, read from the same cache — never by running a provider, which may query a server. A slot
that depends on a positional not typed shows nothing (`download --help` vs `download cifar10
--help`); an empty cache says to run `<app> --refresh-completions`. Long lists show 20 values and a
count.

### `--scope <TAB>` for a command that runs its own config

A command declaring `@command(config_files=provider)` (see [Commands](commands-and-di.md#the-scopes-of-the-config-a-command-runs-config_files))
gets `--scope <TAB>` / `-s <TAB>` offering the scopes of the files it runs for the words typed
before the flag. The refresh reads the files for no words typed and for every combination of the
leading positionals' cached values (`pictures all a`); words it did not enumerate are read in the
background on the first TAB, like a dependent positional's, and appear on the next. A provider
that takes no argument is read once. After any other command's `--scope` nothing is offered: its
scopes belong to the app's config.

## Runnable example

[`examples/completion_providers.py`](../examples/completion_providers.py) wires
a static and a dependent provider onto a command's positionals and prints the
candidates each would contribute. Shell interaction itself (TAB, wrappers,
caches) can't run in CI — install the app and try `download <TAB>` live. It also
prints the grouped listing the second TAB shows for `download rfuav v1 --`.
