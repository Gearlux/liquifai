import inspect
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Literal, Optional, Set, Tuple

import confluid
import logflow
from confluid import materialize
from logflow import get_logger
from rich.console import Console
from rich.table import Table

from liquifai.context import LiquifyContext, set_context

FlowMode = Literal["manual", "auto"]

console = Console()
logger = get_logger("liquifai.core")


class LiquifyApp:
    """Pure Python CLI Framework without Typer/Click baggage."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.context: Optional[LiquifyContext] = None
        self._commands: Dict[str, Callable[..., Any]] = {}
        self._sub_apps: Dict[str, "LiquifyApp"] = {}
        # alias name -> canonical group name (so help folds `ds` into `dataset`).
        self._sub_app_aliases: Dict[str, str] = {}
        self._default_cmd: Optional[Callable[..., Any]] = None
        self._script_cmds: Set[str] = set()

    def add_app(self, app: "LiquifyApp", name: Optional[str] = None, aliases: Optional[List[str]] = None) -> None:
        """Mount a sub-application to support nested command groups (infinitely sub-appable).

        ``aliases`` register extra names that resolve to the same sub-app on the
        command line (e.g. ``ds`` for ``dataset``). They dispatch identically,
        but are folded into the canonical group's help row as ``dataset (ds)``
        instead of being listed as separate groups.
        """
        group_name = name or app.name
        self._sub_apps[group_name] = app
        for alias in aliases or []:
            self._sub_apps[alias] = app
            self._sub_app_aliases[alias] = group_name

    def command(
        self,
        name: Optional[str] = None,
        default: bool = False,
        positionals: Optional[List[str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a command.

        Args:
            name: Override the CLI name. Defaults to the function name with
                underscores replaced by hyphens.
            default: Make this the group's default command (runs when no
                command token is given).
            positionals: Ordered names of positional arguments the command
                accepts. Leading non-flag tokens after the command name are
                bound, in order, to these names as string values in the config
                (so DI resolves the matching command-function parameters).
                ``download`` with ``positionals=["name", "version"]`` lets the
                user write ``app download foo 1.0`` instead of
                ``app download --name foo --version 1.0`` — both forms work, and
                consumption stops at the first ``--flag`` / ``+add`` / ``~del``
                / ``key=value`` token. Values are bound verbatim as strings; a
                command that needs another type coerces in its body.
        """

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or f.__name__.replace("_", "-")
            self._commands[cmd_name] = f
            # Stored on the function (like ``__liquifai_flow_mode__``) so both
            # run() and _show_help() can read it without a per-app registry.
            setattr(f, "__liquifai_positionals__", list(positionals or []))
            if default:
                self._default_cmd = f
            return f

        return decorator

    def script_command(
        self,
        name: Optional[str] = None,
        flow_mode: FlowMode = "manual",
        positionals: Optional[List[str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a command that supports config-promotion.

        Args:
            name: Override the CLI name. Defaults to the function name with
                underscores replaced by hyphens.
            flow_mode: How aggressively to flow injected objects before the
                command runs.

                * ``"manual"`` (default): pass injected kwargs unchanged. Nested
                  ``!class:`` stubs stay deferred — domain code is responsible
                  for flowing them.
                * ``"auto"``: deep-flow every kwarg before calling the command.
                  Attributes annotated with :class:`confluid.Lazy` stay deferred
                  so domain code can still flow them at runtime with extra
                  kwargs (the marainer ``configure_optimizers`` pattern). Any
                  non-``Lazy`` Class stub that can't be instantiated raises
                  immediately.
            positionals: Ordered positional-argument names (see
                :meth:`command`). The config-file promotion peek runs first, so
                a ``script_command`` consumes its config path before binding
                positionals.
        """
        if flow_mode not in ("manual", "auto"):
            raise ValueError(f"flow_mode must be one of manual/auto; got {flow_mode!r}")

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or f.__name__.replace("_", "-")
            self._script_cmds.add(cmd_name)
            # Store the mode on the function itself; run_command looks it up
            # via getattr, no per-app registry needed.
            setattr(f, "__liquifai_flow_mode__", flow_mode)
            return self.command(name=cmd_name, positionals=positionals)(f)

        return decorator

    def _completion_env_var(self) -> str:
        return f"_{self.name.upper().replace('-', '_')}_COMPLETE"

    def _maybe_emit_completion(self) -> bool:
        """If the shell is asking for completions, print them and return True."""
        if self._completion_env_var() not in os.environ:
            return False
        from liquifai.completion import complete

        words = os.environ.get("COMP_WORDS", "").split()
        try:
            cword = int(os.environ.get("COMP_CWORD", "0"))
        except ValueError:
            cword = 0
        for cand in complete(self, words, cword):
            print(cand)
        sys.exit(0)

    def _maybe_handle_completion_install(self, argv: List[str]) -> bool:
        """Handle ``--show-completion`` / ``--install-completion`` early.

        Both must run before Confluid bootstrap (no config required) and
        before help rendering. ``--install-completion`` also primes the
        on-disk command-tree cache so the very first TAB after installing
        is fast (the user does not have to invoke the slow app once first).
        Returns True if one was handled.
        """
        for special in ("--show-completion", "--install-completion"):
            if special not in argv:
                continue
            from liquifai.completion import SHELLS, detect_shell, install_script, render_script, write_cache

            idx = argv.index(special)
            shell = argv[idx + 1] if idx + 1 < len(argv) and argv[idx + 1] in SHELLS else detect_shell()
            if special == "--show-completion":
                print(render_script(self.name, shell))
                # Side effect: prime the cache while the app is loaded.
                # liquifai-install-completions auto-discovers apps by
                # probing them with `<app> --show-completion bash`; the
                # cache is what makes the resulting `complete` calls
                # actually return suggestions, so we MUST seed it here
                # — otherwise tab-completion is registered but silent.
                # Best-effort: never fail the script output on a cache
                # write error.
                try:
                    write_cache(self)
                except Exception:
                    pass
            else:
                target = install_script(self.name, shell)
                cache_target = write_cache(self)
                console.print(f"[green]Installed[/green] {self.name} {shell} completion in [cyan]{target}[/cyan]")
                console.print(f"[dim]Cached command tree: {cache_target}[/dim]")
                console.print(f"[dim]Restart your shell or `source {target}` to activate.[/dim]")
            return True
        return False

    def _refresh_completion_cache(self) -> None:
        """Best-effort refresh of the on-disk command-tree cache."""
        try:
            from liquifai.completion import write_cache

            write_cache(self)
        except Exception:
            pass

    def run(self) -> Any:
        """Main entry point for the CLI."""
        # 0. SHELL COMPLETION — must short-circuit before any bootstrap so
        # tab completion stays fast and side-effect-free.
        if self._maybe_emit_completion():
            return None
        if self._maybe_handle_completion_install(sys.argv[1:]):
            return None

        argv = sys.argv[1:]

        # 1. IDENTIFY COMMAND, GROUP & PROMOTION
        config_path, cmd_name, remaining_argv = None, None, []
        target_app = self
        target_func = None
        # Positional args consumed for the matched command (bound after
        # bootstrap, before overrides — see step 4b).
        positional_names: List[str] = []
        positional_values: List[str] = []

        i = 0
        while i < len(argv):
            arg = argv[i]
            if not target_func and arg in target_app._sub_apps:
                target_app = target_app._sub_apps[arg]
                i += 1
            elif not target_func and arg in target_app._commands:
                cmd_name = arg
                target_func = target_app._commands[cmd_name]
                i += 1
                if cmd_name in target_app._script_cmds and i < len(argv) and not argv[i].startswith("-"):
                    cp = Path(argv[i]) if Path(argv[i]).suffix else Path(argv[i]).with_suffix(".yaml")
                    if cp.exists():
                        config_path, i = cp, i + 1
                # Consume leading positional tokens declared via
                # ``@command(positionals=[...])``. Stops at the first flag-like
                # (``-``/``+``/``~``) or ``key=value`` token so the equals form
                # (``info name=foo``) and trailing flags still route through the
                # normal override path. Fewer tokens than declared is fine —
                # the rest fall back to the command's parameter defaults.
                positional_names = list(getattr(target_func, "__liquifai_positionals__", []))
                for _ in positional_names:
                    if i < len(argv) and not _stops_positional(argv[i]):
                        positional_values.append(argv[i])
                        i += 1
                    else:
                        break
            else:
                remaining_argv.append(arg)
                i += 1

        if not target_func:
            target_func = target_app._default_cmd

        # 2. Check for help (also show help when subgroup reached without a command)
        if "--help" in argv or (not target_func and not target_app._default_cmd):
            self._show_help(target_app, target_func, config_path=config_path)
            # Refresh the completion cache so freshly added commands appear under
            # TAB without first requiring a successful real run — a hidden
            # papercut otherwise, since --help is the natural way to discover
            # what's new after editing the CLI.
            self._refresh_completion_cache()
            return

        # 3. PARSE GLOBALS
        final_config_path, scopes, debug, log_overrides, final_argv = self._parse_globals(remaining_argv)
        if final_config_path:
            config_path = final_config_path

        # 3b. BIND DIMENSION FLAGS — raw-load the config (if any) to discover
        # which `--KEY` flags should activate scope dimensions, then re-parse
        # `final_argv` so those flags are routed into `scopes` instead of
        # being treated as config overrides.
        #
        # We use ``load_config_with_paths`` here (instead of plain
        # ``load_config``) so the resolved tree of YAML files — entrypoint
        # plus every transitively ``include:``-d file — is captured for
        # downstream consumers (e.g. marainer's trainer logs them as
        # artifacts to every wired Lightning logger).
        raw_config: Optional[Any] = None
        included_paths: List[Path] = []
        if config_path is not None and config_path.exists():
            raw_config, included_paths = confluid.load_config_with_paths(config_path)
            scopes, final_argv = self._bind_dimension_flags(scopes, raw_config, final_argv)

        # 4. INITIALIZE STATE
        self.context = LiquifyContext(
            name=self.name,
            config_path=config_path,
            scopes=scopes,
            debug=debug,
            included_paths=included_paths,
            **log_overrides,
        )
        set_context(self.context)
        self._bootstrap(raw_config=raw_config)

        # 4b. BIND POSITIONALS — write each consumed positional into the config
        # under its declared name (verbatim string) so DI resolves the matching
        # command parameter. Done before overrides so an explicit ``--name``
        # flag still wins over a positional, and so the bind survives even when
        # there are no overrides (``_apply_overrides`` early-returns then).
        if positional_values and isinstance(self.context.config_data, dict):
            for nm, val in zip(positional_names, positional_values):
                self.context.config_data[nm] = val
            self.context.logger.debug(f"Bound positionals: {dict(zip(positional_names, positional_values))}")

        # 5. APPLY OVERRIDES
        self._apply_overrides(final_argv)

        # 6. EXECUTE
        if not target_func:
            console.print("[red]Error:[/red] Unknown command or group")
            sys.exit(1)

        result = self.run_command(target_func)

        # Refresh the completion cache so plugin/command changes propagate
        # to the next TAB. Best-effort: never let this break a real run.
        self._refresh_completion_cache()

        return result

    def _parse_globals(self, argv: List[str]) -> Tuple[Optional[Path], List[str], bool, Dict[str, Any], List[str]]:
        config_path, scopes, debug = None, [], False
        log_overrides, remaining = {}, []

        handlers = {
            ("--config", "-c"): lambda v: ("config_path", Path(v)),
            ("--scope", "-s"): lambda v: ("scopes", v.split(",")),
            ("--level",): lambda v: ("log_level", v),
            ("--console-level",): lambda v: ("console_level", v),
            ("--file-level",): lambda v: ("file_level", v),
            ("--log-dir",): lambda v: ("log_dir", Path(v)),
        }

        i = 0
        while i < len(argv):
            arg = argv[i]
            found = False
            for flags, handler in handlers.items():
                if arg in flags and i + 1 < len(argv):
                    key, val = handler(argv[i + 1])
                    if key == "config_path":
                        config_path = val
                    elif key == "scopes":
                        scopes.extend(val)
                    else:
                        log_overrides[key] = val
                    i, found = i + 2, True
                    break
            if not found:
                if arg in ("--debug", "-d"):
                    debug, i = True, i + 1
                else:
                    remaining.append(arg)
                    i += 1
        return config_path, scopes, debug, log_overrides, remaining

    def _bind_dimension_flags(self, scopes: List[str], raw_config: Any, argv: List[str]) -> Tuple[List[str], List[str]]:
        """Promote ``--KEY VAL`` / ``--KEY=VAL`` flags into ``scopes`` when ``KEY``
        is a declared scope dimension in the raw config.

        After globals are parsed, the raw YAML is walked once by
        :func:`confluid.discover_dimensions` to learn which keys appear in any
        ``!scope:KEY=VAL`` / ``!scope:KEY(VAL)`` block. Those keys then bind to
        implicit CLI flags so users can write ``--task classification`` in
        addition to ``--scope task=classification``. Non-dimension flags pass
        through unchanged and continue down the normal CLI-override path.
        """
        dimensions = confluid.discover_dimensions(raw_config)
        if not dimensions:
            return scopes, argv

        remaining: List[str] = []
        i = 0
        while i < len(argv):
            arg = argv[i]
            if arg.startswith("--"):
                # ``--KEY=VAL`` form.
                if "=" in arg:
                    key, value = arg[2:].split("=", 1)
                    if key in dimensions:
                        scopes.append(f"{key}={value}")
                        i += 1
                        continue
                # ``--KEY VAL`` form — requires a non-flag follower.
                else:
                    key = arg[2:]
                    if key in dimensions and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                        scopes.append(f"{key}={argv[i + 1]}")
                        i += 2
                        continue
            remaining.append(arg)
            i += 1
        return scopes, remaining

    def _bootstrap(self, raw_config: Optional[Any] = None) -> None:
        """Standard Trio Bootstrap.

        ``raw_config`` is the pre-loaded raw dict (or Fluid) — passed in from
        the CLI path so we don't re-read the file. Internal callers (the
        public ``liquify`` shortcut) may also pass it; everyone else gets a
        fresh ``load_config`` here.
        """
        if not self.context:
            return

        script_name = self.context.name
        if self.context.config_path:
            script_name = self.context.config_path.stem

        console_level = (
            self.context.console_level or self.context.log_level or ("DEBUG" if self.context.debug else "INFO")
        )
        file_level = self.context.file_level or self.context.log_level or "DEBUG"

        logflow.configure_logging(
            console_level=console_level,
            file_level=file_level,
            log_dir=self.context.log_dir,
            script_name=script_name,
            force=True,
        )
        self.context.logger = get_logger(script_name)

        if self.context.config_path:
            if not self.context.config_path.exists():
                console.print(f"[red]Error:[/red] Configuration file not found: {self.context.config_path}")
                sys.exit(1)
            data = raw_config if raw_config is not None else confluid.load_config(self.context.config_path)
            self.context.config_data = confluid.load(data, flow=False, scopes=self.context.scopes or None)
            self.context.config_data = _expand_strings(self.context.config_data)
            self.context.logger.info(f"Loaded configuration from: {self.context.config_path}")
            self.context.logger.trace(f"BOOTSTRAP CONFIG STATE: {self.context.config_data}")

    def _apply_overrides(self, args: List[str]) -> None:
        if not self.context or not args:
            return

        overrides, deletions = _parse_override_args(args)

        if not overrides and not deletions:
            return

        from confluid import deep_merge, expand_dotted_keys

        overrides = _expand_strings(overrides)
        self.context.logger.debug(f"Applying CLI overrides: {overrides}; deletions: {deletions}")
        self.context.config_data = deep_merge(self.context.config_data, overrides)
        # ``deep_merge`` leaves dotted-key overrides as literal-string keys
        # at the top level (``{"processor.lookback_days": 5}``). We need to
        # collapse them INTO the existing config tree so a CLI
        # ``--processor.lookback_days 5`` actually reaches the Fluid at
        # ``config["processor"]``. ``expand_dotted_keys`` walks dicts AND
        # Fluid.kwargs, so the override lands in the Fluid's kwargs dict
        # before any later ``flow()`` reads from it. This step is critical
        # for the ``flow_mode="auto"`` + ``Any``-typed param path, where
        # the Fluid is consumed directly without going through
        # ``materialize()`` (which internally does the same expansion on
        # its context).
        if isinstance(self.context.config_data, dict):
            self.context.config_data = expand_dotted_keys(self.context.config_data)
        for path in deletions:
            _delete_dotted_key(self.context.config_data, path)
        # Second-pass: flat overrides still need to broadcast to nested
        # Fluids by name (``--max_epochs 10`` reaching every Fluid whose
        # accept-list includes ``max_epochs``) and dotted overrides need to
        # match Fluids by their ``name:`` kwarg (``--overlay.visualize
        # true`` reaching the Fluid with ``name: overlay`` even when it
        # isn't at ``config["overlay"]``). New ``+key=val`` adds also
        # need this pass because the new key isn't yet in any Fluid's
        # kwargs.
        _merge_overrides_into_fluids(self.context.config_data, overrides)
        self.context.logger.trace(f"POST-OVERRIDE CONFIG STATE: {self.context.config_data}")

    def run_command(self, func: Callable[..., Any]) -> Any:
        """Execute with Dependency Injection."""
        if not self.context:
            return func()
        kwargs = self._resolve_kwargs(func)
        flow_mode: FlowMode = getattr(func, "__liquifai_flow_mode__", "manual")
        if flow_mode == "auto":
            with _confluid_active_context(self.context.config_data):
                kwargs = {k: _deep_flow(v) for k, v in kwargs.items()}
        return func(**kwargs)

    def _resolve_kwargs(self, func: Callable[..., Any]) -> Dict[str, Any]:
        """DI-resolve ``func``'s parameters against ``self.context.config_data``.

        Shared between :meth:`run_command` and :meth:`liquify` — the latter
        needs the same live instances DI would produce, but without actually
        invoking the command.
        """
        assert self.context is not None

        self.context.logger.debug(f"DI: Resolving arguments for {func.__name__}")
        # config_data may be a Fluid when the YAML's root is a single
        # `!class:` document — guard the introspection so DI stays usable
        # for commands that don't depend on top-level keys.
        cfg = self.context.config_data
        cfg_keys = list(cfg.keys()) if isinstance(cfg, dict) else "<root-Fluid>"
        self.context.logger.trace(f"DI: Global config keys: {cfg_keys}")

        from confluid import get_registry
        from confluid.fluid import Fluid

        reg = get_registry()
        sig = inspect.signature(func)
        kwargs: Dict[str, Any] = {}

        for name, param in sig.parameters.items():
            if reg.is_configurable(param.annotation):
                cls_name = getattr(param.annotation, "__confluid_name__", param.annotation.__name__)
                if isinstance(cfg, dict):
                    config_block = cfg.get(cls_name) or cfg.get(name) or cfg
                else:
                    # Root-level Fluid: there is no surrounding dict to look
                    # up by class- or param-name, so the Fluid itself is the
                    # candidate block.
                    config_block = cfg

                self.context.logger.debug(
                    f"DI: Resolving {name} ({cls_name}). Block keys: "
                    f"{list(config_block.keys()) if isinstance(config_block, dict) else 'N/A'}"
                )

                if isinstance(config_block, Fluid):
                    # User wrote `name: !class:...` — the Fluid already carries
                    # the full kwargs; materialize it directly so its payload
                    # isn't discarded by the marker-dict path below.
                    kwargs[name] = materialize(config_block, context=self.context.config_data)
                else:
                    marker_dict = {
                        "_confluid_class_": cls_name,
                        **(config_block if isinstance(config_block, dict) else {}),
                    }
                    kwargs[name] = materialize(marker_dict, context=self.context.config_data)
            else:
                # Non-configurable: Resolve from context data or use default
                if isinstance(cfg, dict) and name in cfg:
                    kwargs[name] = cfg[name]
                elif param.default is not inspect.Parameter.empty:
                    kwargs[name] = param.default

        return kwargs

    def liquify(
        self,
        target_func: Callable[..., Any],
        *,
        config_path: Optional[Path] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Bootstrap + DI-resolve ``target_func`` into live instances, without calling it.

        Returns the kwargs dict that ``run_command`` would pass to ``target_func`` —
        the same flowed object graph, but produced without invoking the command.
        Public hook for tooling that needs the flowed graph (help rendering,
        graph export, test harnesses).

        If ``config_path`` is None and a context already exists, the current
        context's config is used verbatim. Otherwise the config is loaded
        lazily here (no logflow / no CLI override merge — intended for
        read-only introspection).
        """
        if self.context is None:
            ctx = LiquifyContext(
                name=self.name,
                config_path=config_path,
                scopes=scopes or [],
                debug=False,
            )
            ctx.logger = get_logger(self.name)
            if config_path is not None:
                ctx.config_data = confluid.load(config_path, flow=False, scopes=scopes or None)
                ctx.config_data = _expand_strings(ctx.config_data)
            self.context = ctx
            set_context(self.context)
        kwargs = self._resolve_kwargs(target_func)
        # Deep-flow any unflowed Fluids so callers introspect live instances
        # all the way down the graph. Bare `flow()` leaves nested Class
        # kwargs deferred (they flow lazily in production), but the liquify
        # contract is "fully flowed graph" — introspection tools need every
        # attribute resolved.
        return {k: _deep_flow(v) for k, v in kwargs.items()}

    def _show_help(
        self,
        app: "LiquifyApp",
        target_func: Optional[Callable[..., Any]] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        """Beautiful help menu via Rich.

        When a ``config_path`` is known and a ``target_func`` is selected,
        the help path flows the DI graph via :meth:`liquify` and shows every
        configurable kwarg reachable through the flowed instance tree. A
        flow failure downgrades to the static-type view with a brief note.
        """
        console.print(f"\n[bold]{app.name.upper()}[/bold] - Modular Framework")
        if app.description:
            console.print(f"[dim]{app.description}[/dim]")

        if target_func:
            desc = target_func.__doc__ or "No description."
            positionals = getattr(target_func, "__liquifai_positionals__", [])
            usage = target_func.__name__.replace("_", "-") + "".join(f" <{p}>" for p in positionals)
            console.print(f"\n[bold]Command:[/bold] {usage}")
            console.print(f"[dim]{desc.strip()}[/dim]")

            from liquifai.report import show_configuration

            flowed_kwargs: Optional[Dict[str, Any]] = None
            if config_path is not None:
                try:
                    flowed_kwargs = self.liquify(target_func, config_path=config_path)
                except Exception as exc:
                    console.print("[dim]Config failed to flow; showing command signature only. " f"Reason: {exc}[/dim]")

            if flowed_kwargs is not None and config_path is not None:
                console.print(
                    "[dim]Plain --<name> overrides broadcast to every matching ctor kwarg "
                    "across the flowed graph.[/dim]"
                )
                show_configuration(
                    target_func,
                    config_map=flowed_kwargs,
                    title=f"Command Configuration (flowed from {config_path.name})",
                )
            else:
                show_configuration(target_func, title="Command Configuration Options")
        else:
            table = Table(box=None, padding=(0, 2))
            table.add_column("Command/Group", style="cyan")
            table.add_column("Description")

            for name, sub_app in sorted(app._sub_apps.items()):
                if name in app._sub_app_aliases:
                    continue  # alias rows fold into their canonical group below
                aliases = sorted(a for a, canon in app._sub_app_aliases.items() if canon == name)
                label = f"{name} ({', '.join(aliases)})" if aliases else name
                desc = f"[bold]Group:[/bold] {sub_app.description}" if sub_app.description else "Group."
                table.add_row(label, desc)

            for name, func in sorted(app._commands.items()):
                desc = func.__doc__.strip().split("\n")[0] if func.__doc__ else "No description."
                table.add_row(name, desc)

            console.print(table)

        console.print("\n[bold]Global Options:[/bold]")
        console.print("  -c, --config PATH      Configuration file.")
        console.print("  -s, --scope NAME       Active boolean scope(s); accepts `NAME` or `KEY=VAL`.")
        console.print("  --KEY VAL              Implicit per-dimension flag for any `!scope:KEY=…` block")
        console.print("                         declared in the YAML (e.g. `--task classification`).")
        console.print("  -d, --debug            Enable debug mode.")
        console.print("  --level LEVEL          Set log level for both sinks (TRACE, DEBUG, INFO).")
        console.print("  --console-level LEVEL  Set console log level (overrides --level).")
        console.print("  --file-level LEVEL     Set file log level (overrides --level).")
        console.print("  --install-completion [SHELL]  Install tab completion (bash/zsh/fish).")
        console.print("  --show-completion [SHELL]     Print the completion script to stdout.")
        console.print("")


@contextmanager
def _confluid_active_context(context_data: Dict[str, Any]) -> Iterator[None]:
    """Activate confluid's thread-local context so bare ``flow()`` resolves ``!ref:``.

    ``materialize()`` already does this internally, but liquifai's deep-flow
    runs *after* ``_resolve_kwargs`` has returned (with confluid's context
    restored). For non-configurable parameters whose YAML values contain
    nested ``!ref:`` markers, we need the context active again during the
    deep-flow walk — otherwise references silently fail to resolve.
    """
    from confluid.loader import _state

    old_ctx = getattr(_state, "context", None)
    old_flow_memo = getattr(_state, "flow_memo", None)
    old_instance_memo = getattr(_state, "instance_memo", None)
    _state.context = context_data
    _state.flow_memo = {}
    _state.instance_memo = {}
    try:
        yield
    finally:
        _state.context = old_ctx
        _state.flow_memo = old_flow_memo
        _state.instance_memo = old_instance_memo


def _deep_flow(value: Any, _visited: Optional[Set[int]] = None) -> Any:
    """Recursively flow any ``Fluid`` stubs embedded in ``value``.

    Walks lists, tuples, dicts, and live instances' ``vars()``; any attribute
    that is still a ``Fluid`` is replaced in-place with the flowed instance.
    Cycle-safe via ``id(obj)`` tracking. Primitives pass through unchanged.

    Skips dunder attrs (``__*__``) on instances — those are framework
    bookkeeping (e.g. confluid's ``__confluid_kwargs__`` round-trip mirror,
    Python internals) that shouldn't be re-flowed by an external walker.
    Honors :func:`confluid.lazy.lazy_param_names` to leave attrs marked
    ``Lazy[T]`` deferred.

    ``confluid.fluid.Lazy`` (YAML ``!lazy:``) Fluids are likewise left
    deferred at every level — they are runtime-injection points (e.g. an
    optimizer needing ``params=model.parameters()``) and must be flowed
    later by domain code with the missing runtime kwargs.
    """
    from confluid import flow
    from confluid.fluid import Fluid
    from confluid.fluid import Lazy as LazyFluid

    if _visited is None:
        _visited = set()

    if isinstance(value, LazyFluid):
        return value

    if isinstance(value, Fluid):
        return _deep_flow(flow(value), _visited)

    if isinstance(value, (list, tuple)):
        out = [_deep_flow(v, _visited) for v in value]
        if isinstance(value, tuple):
            # NamedTuple subclasses take their fields as POSITIONAL args, not
            # as a single iterable. Without the splat, e.g.
            # ``Sample([input, target, metadata])`` wraps the entire triplet
            # into the ``input`` field with target/metadata at their defaults
            # — silently breaking any dataset whose elements are NamedTuples
            # (most notably ``dataflux.sample.Sample``).
            if hasattr(type(value), "_fields"):
                return type(value)(*out)
            return type(value)(out)
        return out

    if type(value) is dict:
        return {k: _deep_flow(v, _visited) for k, v in value.items()}

    # Live instance: walk its __dict__ and replace any Fluid attrs in place.
    if hasattr(value, "__dict__") and not isinstance(value, type):
        vid = id(value)
        if vid in _visited:
            return value
        _visited.add(vid)
        from confluid.lazy import lazy_param_names

        lazy = lazy_param_names(type(value))
        for attr_name, attr_value in list(vars(value).items()):
            if attr_name.startswith("__") and attr_name.endswith("__"):
                continue  # framework bookkeeping (e.g. __confluid_kwargs__)
            if attr_name in lazy:
                continue  # honor Lazy[T]: leave runtime-injection attrs deferred
            if isinstance(attr_value, LazyFluid):
                continue  # YAML !lazy: stays deferred even without the Lazy[T] mirror
            resolved = _deep_flow(attr_value, _visited)
            if resolved is not attr_value:
                try:
                    setattr(value, attr_name, resolved)
                except (AttributeError, TypeError):
                    pass
    return value


def _merge_overrides_into_fluids(data: Any, overrides: Dict[str, Any]) -> None:
    """Merge CLI overrides into Fluid kwargs throughout the config tree."""
    from confluid.fluid import Fluid

    if isinstance(data, Fluid):
        accepted = _accepted_override_keys(data.target)
        # If this Fluid has a YAML-set `name: "<id>"`, dotted keys like
        # `"overlay.visualize"` land here by suffix — targeting this
        # instance only. Flat keys still broadcast as before.
        fluid_name = data.kwargs.get("name") if isinstance(data.kwargs, dict) else None
        for k, v in overrides.items():
            if fluid_name and "." in k:
                head, _, tail = k.partition(".")
                if head == str(fluid_name) and (tail in data.kwargs or tail in accepted):
                    data.kwargs[tail] = v
                    continue  # dotted form handled — don't also broadcast-match.
            # Flat form: apply when the kwarg is already in YAML (catches the
            # post-construction setattr pattern like `Enable.visualize`) OR
            # when the target class accepts it (ctor params always; for
            # ``@configurable`` classes, also public class-level attributes
            # that Confluid would setattr at flow time — e.g. @property
            # setters, plain class attrs).
            if k in data.kwargs or k in accepted:
                data.kwargs[k] = v
        for v in data.kwargs.values():
            _merge_overrides_into_fluids(v, overrides)
    elif isinstance(data, dict):
        for v in data.values():
            _merge_overrides_into_fluids(v, overrides)
    elif isinstance(data, list):
        for item in data:
            _merge_overrides_into_fluids(item, overrides)


def _accepted_override_keys(target: Any) -> Set[str]:
    """Return every attribute name ``target`` accepts as an override.

    For any class: the set of ``__init__`` parameter names.

    For ``@configurable`` classes additionally: every public class-level
    attribute — that is, any non-dunder, non-underscore name on the class
    that is not a method, is not a read-only ``@property``, and is not
    ``__confluid_ignore__``'d. This mirrors Confluid's post-construction
    setattr pattern — ``flow()`` accepts any extra kwarg that targets a
    public attribute, so overrides must too.

    ``target`` can be a class, an instance, or the dotted string Confluid
    uses for deferred class resolution (``!class:module.Cls``). Returns an
    empty set if the target can't be resolved or introspected.
    """
    from confluid.registry import resolve_class

    cls: Any = target
    if isinstance(cls, str):
        cls = resolve_class(cls)
    if cls is None:
        return set()
    if not isinstance(cls, type):
        cls = cls.__class__
    init = getattr(cls, "__init__", None)
    if init is None:
        return set()
    try:
        sig = inspect.signature(init)
    except (ValueError, TypeError):
        return set()
    accepted: Set[str] = {p for p in sig.parameters if p not in ("self", "cls", "args", "kwargs")}

    if not getattr(cls, "__confluid_configurable__", False):
        return accepted

    # @configurable: Confluid setattr-applies any extra kwarg whose target is
    # a public class attribute. Include those in the accepted set.
    for name in dir(cls):
        if name.startswith("_"):
            continue
        member = getattr(cls, name, None)
        if getattr(member, "__confluid_ignore__", False):
            continue
        if callable(member) and not isinstance(member, property):
            continue  # skip bound methods / functions
        if isinstance(member, property) and member.fset is None:
            continue  # read-only properties can't accept overrides
        accepted.add(name)
    return accepted


def _expand_strings(data: Any, _visited: Optional[Set[int]] = None) -> Any:
    """Recursively expand environment variables and ~ in strings."""
    from confluid.fluid import Fluid

    if isinstance(data, str):
        if "$" in data or "~" in data:
            return os.path.expanduser(os.path.expandvars(data))
        return data

    if isinstance(data, (int, float, bool, type(None))):
        return data

    if _visited is None:
        _visited = set()

    vid = id(data)
    if vid in _visited:
        return data
    _visited.add(vid)

    if isinstance(data, dict):
        return {k: _expand_strings(v, _visited) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_strings(v, _visited) for v in data]
    if isinstance(data, tuple):
        out = [_expand_strings(v, _visited) for v in data]
        if hasattr(type(data), "_fields"):
            return type(data)(*out)
        return type(data)(out)
    if isinstance(data, Fluid):
        if isinstance(data.kwargs, dict):
            data.kwargs = {k: _expand_strings(v, _visited) for k, v in data.kwargs.items()}

    return data


def _parse_override_args(args: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Tokenize ``args`` into a (overrides, deletions) pair.

    Supported forms (order-independent; longest match wins per token):

    * ``--key value``           — legacy space-separated form (still primary).
    * ``--key=value``           — equals form.
    * ``key=value``             — bare equals form, no ``--`` prefix.
    * ``--key+`` / ``--key-``   — polarity (True / False).
    * ``--key``                 — implicit ``True`` flag.
    * ``+key=value`` / ``+--key=value`` — add a new key (today merged with
      same semantics as a normal override; future: fail if key exists).
    * ``~key`` / ``~--key``     — delete the dotted key from the config.

    Any token that doesn't match a recognised form is silently dropped
    (matches the prior behaviour where loose non-flag args were skipped).
    """
    from confluid import parse_value

    overrides: Dict[str, Any] = {}
    deletions: List[str] = []
    i = 0
    while i < len(args):
        arg = args[i]

        if arg.startswith("~"):
            key = arg[1:]
            if key.startswith("--"):
                key = key[2:]
            if key:
                deletions.append(key)
            i += 1
            continue

        if arg.startswith("+"):
            body = arg[1:]
            if body.startswith("--"):
                body = body[2:]
            if "=" in body:
                k, v = body.split("=", 1)
                if k:
                    overrides[k] = parse_value(v)
            elif body and i + 1 < len(args) and not _looks_like_arg(args[i + 1]):
                overrides[body] = parse_value(args[i + 1])
                i += 1
            elif body:
                overrides[body] = True
            i += 1
            continue

        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                if k:
                    overrides[k] = parse_value(v)
                i += 1
                continue
            if key.endswith("+"):
                overrides[key[:-1]] = True
                i += 1
                continue
            if key.endswith("-"):
                overrides[key[:-1]] = False
                i += 1
                continue
            if i + 1 < len(args) and not _looks_like_arg(args[i + 1]):
                overrides[key] = parse_value(args[i + 1])
                i += 2
                continue
            overrides[key] = True
            i += 1
            continue

        # Bare ``key=value`` (no ``--``). Lets users drop the dashes when
        # they want — common ergonomics ask from the user.
        if "=" in arg and not arg.startswith("="):
            k, v = arg.split("=", 1)
            # Filter out random tokens that contain ``=`` but aren't shaped
            # like a config key (e.g. JSON-ish blobs, file paths).
            if k and _looks_like_key(k):
                overrides[k] = parse_value(v)
                i += 1
                continue

        # Unrecognised token — skip (matches legacy behaviour).
        i += 1

    return overrides, deletions


def _looks_like_arg(token: str) -> bool:
    """True if the token looks like the *start* of another CLI option, so
    it should NOT be consumed as the value for a preceding ``--key``.

    Catches ``--foo``, ``+foo=bar``, ``~foo`` — anything that ``_parse_override_args``
    would itself parse as a new option in the next iteration.
    """
    if not token:
        return False
    return token.startswith("--") or token.startswith("+") or token.startswith("~")


def _stops_positional(token: str) -> bool:
    """True if ``token`` must NOT be consumed as a positional value.

    Positional consumption halts at the first flag-like token (a ``-`` / ``+`` /
    ``~`` prefix — covers short ``-c`` and long ``--config`` options as well as
    the ``+add`` / ``~delete`` override forms) or any ``key=value`` token. This
    lets a user supply positionals (``info foo``), the equals form
    (``info name=foo``), or trailing flags (``download foo 1.0 --path /tmp``)
    interchangeably without the parser mistaking one for another.
    """
    if not token:
        return True
    if token[0] in ("-", "+", "~"):
        return True
    return "=" in token


def _looks_like_key(token: str) -> bool:
    """Conservative shape check for the bare ``key=value`` form.

    Keys are word characters + dots (``trainer.max_epochs``). Anything
    else (slashes, colons inside the head) probably isn't an override.
    """
    import re

    return bool(re.fullmatch(r"[A-Za-z_][\w.\-]*", token))


def _delete_dotted_key(config: Any, path: str) -> None:
    """Best-effort deletion of ``config[path[0]][path[1]]...``.

    Walks the dotted path through nested dicts and Fluid ``kwargs``. Silent
    no-op if any segment is missing or the leaf can't be removed.
    """
    from confluid.fluid import Fluid

    parts = path.split(".")
    current: Any = config
    for part in parts[:-1]:
        if isinstance(current, Fluid):
            current = current.kwargs
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    leaf = parts[-1]
    if isinstance(current, Fluid):
        current = current.kwargs
    if isinstance(current, dict):
        current.pop(leaf, None)
