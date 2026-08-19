import inspect
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple, get_args

import confluid
import loggair
from loggair import get_logger
from rich.console import Console
from rich.markup import escape

from liquifai import completion_cli, di, flags, grammar, overrides, report, router
from liquifai.context import LiquifyContext, set_context
from liquifai.exceptions import (
    CommandDefinitionError,
    ConfigNotFoundError,
    LiquifaiError,
    UnknownCommandError,
    UnknownOperationError,
)
from liquifai.introspection import graft_signature, split_context_param
from liquifai.overrides import expand_strings, parse_override_args
from liquifai.walk import Token, literal_texts, option_texts, tokenize

FlowMode = Literal["manual", "auto"]

#: Valid values for the ``presentation`` parameter of :meth:`LiquifyApp.operation`.
Presentation = Literal["list", "fields", "status"]

#: How ``--help`` / ``--docs`` render a command's options — the Rich grid or
#: one greppable line per option. Closed so a typo fails at the call site
#: instead of silently falling through to the table branch.
HelpLayout = Literal["table", "lines"]

console = Console()
logger = get_logger("liquifai.core")


@dataclass
class Invocation:
    """The routed result of phase 1 of the bootstrap lifecycle.

    Produced by :meth:`LiquifyApp._route` from raw ``argv`` and consumed by
    the help short-circuit, :meth:`LiquifyApp._prepare`, and
    :meth:`LiquifyApp._execute`. Making the routing outcome an explicit value
    (instead of six loose locals inside ``run()``) keeps the phases
    individually testable.
    """

    #: The (sub-)app the command tokens descended into.
    target_app: "LiquifyApp"
    #: The handler to dispatch (may be the group's default command).
    target_func: Optional[Callable[..., Any]]
    #: Config path consumed by script-command promotion (NOT ``--config``,
    #: which is parsed later in phase 3).
    config_path: Optional[Path]
    #: The raw token promotion consumed, pre-resolution — carried so
    #: :meth:`LiquifyApp._log_promotion_source` can report WHICH search tier
    #: supplied the file once logging is live.
    config_token: Optional[str]
    #: Declared positional names for the matched command, in order.
    positional_names: List[str]
    #: Leading positional tokens actually consumed (may be fewer than names).
    positional_values: List[str]
    #: Every token not consumed by routing — input to the global/override
    #: parsers. Tokens, not strings, so those parsers can tell an option from a
    #: post-``--`` literal (which they must never consume).
    remaining_tokens: List[Token]


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
        # Named operations: consumers (MCP server, agent, configure_app) read this
        # to auto-generate tools/commands without boilerplate. Populated by
        # @app.operation().
        self._operations: Dict[str, Callable[..., Any]] = {}
        # Hooks registered by consumers (e.g. sairen) to wire context injection
        # and result presentation into auto-generated CLI commands and MCP tools.
        self._context_factory: Optional[Callable[[], Any]] = None
        self._mcp_context_factory: Optional[Callable[..., Any]] = None
        self._presenter: Optional[Callable[..., None]] = None

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

    def set_context_factory(self, fn: Callable[[], Any]) -> Callable[[], Any]:
        """Register a no-arg factory that builds the context object (e.g. a connection).

        When an :meth:`operation`-generated CLI command is invoked, the generated
        handler calls this factory to obtain the context it passes as the first argument
        to the underlying operation.  Typically the factory reads from
        :func:`liquifai.get_context` so CLI config (``--server``, ``--dry_run``, …)
        flows in automatically.
        """
        self._context_factory = fn
        return fn

    def set_mcp_context_factory(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a factory whose *signature* defines the extra MCP-tool params.

        :func:`liquifai.tools.make_mcp_tools` inspects this factory's parameters and
        prepends them to every MCP tool it generates.  The factory is called with the
        MCP-supplied values to produce the context passed to the operation.

        Example (sairen)::

            app.set_mcp_context_factory(
                lambda server="PROD", dry_run=False: SairenClient(server=server, dry_run=dry_run)
            )

        This causes every generated tool to advertise ``server`` and ``dry_run`` params
        and build a ``SairenClient`` from them before calling the operation.
        """
        self._mcp_context_factory = fn
        return fn

    def set_presenter(self, fn: Callable[..., None]) -> Callable[..., None]:
        """Register a result presenter for auto-generated CLI commands.

        The presenter is called with ``(result, presentation, *, columns, title, empty,
        **format_kwargs)`` after the operation returns.  It is responsible for rendering
        the result dict to the terminal (or handling dry-run output).

        ``format_kwargs`` contains the CLI call's keyword arguments so the presenter can
        interpolate them into a ``title`` template (e.g. ``"Dataset: {name}"``).
        """
        self._presenter = fn
        return fn

    def build_commands(self) -> None:
        """Register CLI commands for every operation in ``_operations``.

        Reads the registered hooks (:meth:`set_context_factory`,
        :meth:`set_presenter`) to build and register one CLI handler per
        operation.  Call this *after* all hooks are set — typically at the bottom
        of each domain module, replacing the old ``build_cli_commands(app)`` call.

        Positionals are derived automatically from the operation signature: every
        keyword-only parameter with **no default** (excluding the ``conn`` / context
        param) becomes a positional slot.
        """
        for op_name, op_func in self._operations.items():
            meta: Dict[str, Any] = getattr(op_func, "__liquifai_op_metadata__", {})
            if meta.get("mcp_only", False):
                continue  # skip CLI generation — this op is MCP-only
            cli_name: str = meta.get("cmd_name", op_name.replace("_", "-"))
            presentation: str = meta.get("presentation", "status")
            columns: Any = meta.get("columns", ())
            title: str = meta.get("title", cli_name.replace("-", " ").title())
            empty: str = meta.get("empty", "No results")
            completions: Dict[str, Callable[..., Any]] = meta.get("completions", {})

            sig, params_no_ctx = split_context_param(op_func)

            positionals = [p.name for p in params_no_ctx if p.default is inspect.Parameter.empty]

            # Capture all loop variables — Python closures capture by reference.
            def _make_handler(
                op_func_: Callable[..., Any] = op_func,
                presentation_: str = presentation,
                columns_: Any = columns,
                title_: str = title,
                empty_: str = empty,
            ) -> Callable[..., None]:
                def cmd(**kwargs: Any) -> None:
                    ctx_factory = self._context_factory
                    conn = ctx_factory() if ctx_factory is not None else None
                    result = op_func_(conn, **kwargs) if conn is not None else op_func_(**kwargs)
                    presenter = self._presenter
                    if presenter is not None:
                        actual_title = title_.format_map(kwargs) if title_ and "{" in title_ else title_
                        presenter(result, presentation_, columns=columns_, title=actual_title, empty=empty_)

                cmd.__name__ = op_func_.__name__ + "_cmd"
                cmd.__qualname__ = op_func_.__name__ + "_cmd"
                cmd.__doc__ = op_func_.__doc__
                return cmd

            handler = _make_handler()
            graft_signature(handler, sig, params_no_ctx, return_annotation=type(None))
            self.command(cli_name, positionals=positionals, completions=completions)(handler)

    def command(
        self,
        name: Optional[str] = None,
        default: bool = False,
        positionals: Optional[List[str]] = None,
        completions: Optional[Dict[str, Callable[..., Any]]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a command: the decorated function is the full CLI handler.

        For *pure operations* (functions returning data, rendered by an
        app-level presenter and surfaced as MCP tools) use :meth:`operation`
        instead — the former ``@command(presentation=...)`` dual-mode was
        removed in favour of that single registration path.

        Args:
            name: Override the CLI name. Defaults to the function name with
                underscores replaced by hyphens.
            default: Make this the group's default command (runs when no
                command token is given).
            positionals: Explicit ordered positional-argument names. Leading
                non-flag tokens after the command name are bound, in order, to
                these names as string values in the config (so DI resolves the
                matching command-function parameters). ``download`` with
                ``positionals=["name", "version"]`` lets the user write
                ``app download foo 1.0`` instead of
                ``app download --name foo --version 1.0`` — both forms work, and
                consumption stops at the first ``--flag`` / ``+add`` / ``~del``
                / ``key=value`` token. Values are bound verbatim as strings; a
                command that needs another type coerces in its body.
            completions: ``{positional: provider}`` value providers (Q2 dynamic
                completion) attached to the handler.
        """

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or f.__name__.replace("_", "-")
            self._commands[cmd_name] = f
            # Stored on the function (like ``__liquifai_flow_mode__``) so both
            # run() and _show_help() can read it without a per-app registry.
            setattr(f, "__liquifai_positionals__", list(positionals or []))
            # {positional: Callable[[], List[str]]} value providers (Q2 dynamic
            # completion). Read by completion.serialize_app / iter_completion_providers.
            setattr(f, "__liquifai_completions__", dict(completions or {}))
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
                  Attributes annotated with :class:`confluid.Partial` stay deferred
                  so domain code can still flow them at runtime with extra
                  kwargs (the matrainer ``configure_optimizers`` pattern). Any
                  non-``Partial`` Class stub that can't be instantiated raises
                  immediately.
            positionals: Ordered positional-argument names (see
                :meth:`command`). The config-file promotion peek runs first, so
                a ``script_command`` consumes its config path before binding
                positionals.
        """
        if flow_mode not in ("manual", "auto"):
            raise CommandDefinitionError(f"flow_mode must be one of manual/auto; got {flow_mode!r}")

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or f.__name__.replace("_", "-")
            self._script_cmds.add(cmd_name)
            # Store the mode on the function itself; run_command looks it up
            # via getattr, no per-app registry needed.
            setattr(f, "__liquifai_flow_mode__", flow_mode)
            return self.command(name=cmd_name, positionals=positionals)(f)

        return decorator

    def operation(
        self,
        name: Optional[str] = None,
        positionals: Optional[List[str]] = None,
        presentation: Optional[Presentation] = None,
        mcp_only: bool = False,
        completions: Optional[Dict[str, Callable[..., Any]]] = None,
        **metadata: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a function as a named operation (third variant alongside command / script_command).

        Unlike ``command()``, ``operation()`` does **not** auto-generate a CLI command —
        that is the caller's responsibility (call :meth:`build_commands` after all hooks are
        set to auto-generate CLI wrappers, or use :func:`liquifai.tools.make_mcp_tools` for
        MCP tools). Liquifai stores the name, positionals, and any explicit metadata on the
        function and in ``app._operations``.

        **CLI name derivation:** the CLI verb is derived from the function name by stripping
        the app-name prefix (``<app.name>_``) if present, then replacing ``_`` with ``-``.
        Example: ``dataset_version_create`` on app ``"dataset"`` → CLI verb ``version-create``.
        If the function name does not start with the app-name prefix, the whole name is used
        (with ``_``→``-``). The canonical convention is therefore to name every operation
        ``<app_name>_<action>`` so the prefix strips cleanly. Pass ``name=`` explicitly to
        override the derived verb when a different function name is necessary.

        Args:
            name: Override the operation name (defaults to the function's ``__name__``).
                Also overrides the CLI verb derivation described above.
            positionals: Ordered positional-argument names (same semantics as in
                :meth:`command`); passed through to the generated CLI command.
            presentation: How the CLI auto-generated by :meth:`build_commands` should render
                the return value — ``"list"`` (table of rows), ``"fields"`` (key/value pairs),
                or ``"status"`` (plain success message). ``None`` means no special rendering.
            mcp_only: When ``True``, :meth:`build_commands` skips CLI generation for this
                operation. The function remains in ``_operations`` and is therefore available
                to :func:`liquifai.tools.make_mcp_tools`.
            **metadata: Opaque metadata stored on the function as
                ``__liquifai_op_metadata__`` — Liquifai does not interpret these. Consumers
                like sairen read ``columns``, ``title``, ``empty`` etc.
        """

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            if presentation is not None and presentation not in get_args(Presentation):
                raise CommandDefinitionError(
                    f"@operation({f.__name__!r}): presentation must be one of "
                    f"{get_args(Presentation)!r}, got {presentation!r}"
                )
            op_name = name or f.__name__
            # Derive CLI name using the old group-prefix-strip convention so
            # existing @operation() callers continue to work with configure_app().
            prefix = self.name + "_"
            raw = op_name[len(prefix) :] if op_name.startswith(prefix) else op_name
            cmd_name = raw.replace("_", "-")
            self._operations[op_name] = f
            setattr(f, "__liquifai_operation__", op_name)
            setattr(f, "__liquifai_positionals__", list(positionals or []))
            # Include cmd_name and mcp_only always; presentation only when set
            # (omitting it lets build_commands() fall through to its "status" default).
            base_meta: Dict[str, Any] = {"cmd_name": cmd_name, "mcp_only": mcp_only}
            if presentation is not None:
                base_meta["presentation"] = presentation
            # Positional value providers (Q2). Stored in metadata so build_commands()
            # forwards them to the generated CLI handler via command(completions=...).
            if completions:
                base_meta["completions"] = dict(completions)
            setattr(f, "__liquifai_op_metadata__", {**base_meta, **metadata})
            return f

        return decorator

    def set_completions(self, op_name: str, completions: Dict[str, Callable[..., Any]]) -> None:
        """Attach positional value providers to an already-registered operation.

        Companion to ``@operation(..., completions=...)`` for when the provider is
        wired separately from the decorator (e.g. to avoid an import cycle between a
        domain module and its providers). Each entry maps a positional name to a
        ``Callable[[], List[str]]`` whose cached result completes that ``<name>``
        slot on TAB (see :mod:`liquifai.completion`). Must be called **before**
        :meth:`build_commands` so the generated CLI handler carries the providers.

        Raises:
            liquifai.exceptions.UnknownOperationError: (a ``KeyError``) if
                ``op_name`` is not a registered operation.
        """
        fn = self._operations.get(op_name)
        if fn is None:
            raise UnknownOperationError(f"{self.name}: no registered operation {op_name!r}")
        meta = getattr(fn, "__liquifai_op_metadata__", None)
        if not isinstance(meta, dict):
            meta = {}
            setattr(fn, "__liquifai_op_metadata__", meta)
        meta.setdefault("completions", {}).update(completions)

    # --- Shell-completion interception ------------------------------------
    # These are thin delegations to :mod:`liquifai.completion_cli` (the
    # Rich-using CLI glue that can't live in the stdlib-only ``completion/``
    # package). Kept as same-named private methods so ``run()``/``_execute()``
    # call sites and tests (``tests/test_completion.py`` calls
    # ``_maybe_background_refresh_values``) stay unchanged.

    def _maybe_handle_completion_install(self, argv: List[str]) -> bool:
        """Handle ``--show-completion`` / ``--install-completion`` early."""
        return completion_cli.handle_completion_install(self, argv)

    def _refresh_completion_cache(self) -> None:
        """Best-effort refresh of the on-disk command-tree cache."""
        completion_cli.refresh_completion_cache(self)

    def _maybe_handle_refresh_completions(self, argv: List[str]) -> bool:
        """Handle ``--refresh-completions`` early (before bootstrap)."""
        return completion_cli.handle_refresh_completions(self, argv)

    def _maybe_handle_refresh_completion_value(self, argv: List[str]) -> bool:
        """Handle ``--refresh-completion-value '<json>'`` early (self-heal)."""
        return completion_cli.handle_refresh_completion_value(self, argv)

    def _maybe_background_refresh_values(self, ttl: float = 600.0) -> None:
        """Opportunistically refresh stale positional value caches in background."""
        completion_cli.maybe_background_refresh_values(self, ttl)

    def run(self) -> Any:
        """Main entry point for the CLI.

        A thin orchestrator over the strict 5-phase bootstrap lifecycle
        (see ``CLAUDE.md``): completion short-circuits, then
        :meth:`_route` (phase 1: identify command/group/promotion), the help
        short-circuit (phase 2), :meth:`_prepare` (phases 3–5: globals,
        context/logging/config bootstrap, overrides), and finally
        :meth:`_execute` (phase 6).
        """
        # 0. SHELL COMPLETION — must short-circuit before any bootstrap so
        # tab completion stays fast and side-effect-free. (The old in-process
        # ``_<APP>_COMPLETE`` env-var protocol was removed: every installed
        # shell wrapper calls the standalone ``liquifai-complete`` binary, so
        # the in-app path was dead code in production.)
        if self._maybe_handle_completion_install(sys.argv[1:]):
            return None
        if self._maybe_handle_refresh_completion_value(sys.argv[1:]):
            return None
        if self._maybe_handle_refresh_completions(sys.argv[1:]):
            return None

        # App identity for confluid's XDG config search: relative config
        # paths (promoted tokens, --config, include: entries) resolve under
        # ~/.config/<app>/ before ~/.config/confluid/. run() executes only on
        # the root app, so sub-apps never clobber it.
        confluid.set_app_name(self.name)

        argv = sys.argv[1:]
        # Every argv inspection below reads the OPTION texts only — a token
        # after ``--`` is a literal value, so `app run -- --help` runs the
        # command with a literal ``--help`` argument instead of printing help.
        flags = set(option_texts(tokenize(argv)))

        # 1. IDENTIFY COMMAND, GROUP & PROMOTION
        inv = self._route(argv)

        # 2. Check for help (also show help when subgroup reached without a command).
        # ``--docs`` is a help variant that renders the same code-extracted option
        # documentation one option per line (greppable / pipe-friendly) instead of
        # a Rich table.
        wants_docs = "--docs" in flags
        if "--help" in flags or wants_docs or (not inv.target_func and not inv.target_app._default_cmd):
            self._show_help(
                inv.target_app,
                inv.target_func,
                config_path=inv.config_path,
                layout="lines" if wants_docs else "table",
            )
            # Refresh the completion cache so freshly added commands appear under
            # TAB without first requiring a successful real run — a hidden
            # papercut otherwise, since --help is the natural way to discover
            # what's new after editing the CLI.
            self._refresh_completion_cache()
            return

        # 3.–6. PARSE GLOBALS, INITIALIZE STATE, APPLY OVERRIDES, EXECUTE.
        #
        # CLI failure contract: a LiquifaiError (CLI definition/usage) or
        # ConfluidError (configuration) is an *expected* user-facing failure —
        # it renders as one clean error line and exits 1, with the full
        # traceback written to the log file at DEBUG. Under ``--debug`` the
        # exception propagates instead (full traceback on the console). Any
        # OTHER exception is a bug and always propagates with its traceback.
        try:
            self._prepare(inv)
            return self._execute(inv)
        except (LiquifaiError, confluid.ConfluidError) as exc:
            if flags & {"--debug", "-d"} or (self.context is not None and self.context.debug):
                raise
            import traceback

            log = self.context.logger if self.context is not None and self.context.logger is not None else logger
            log.debug(f"CLI failure traceback:\n{traceback.format_exc()}")
            # `escape`: the message is untrusted text interpolated into a Rich MARKUP
            # string, so any bracketed run that parses as a style is silently eaten —
            # a hint like `pip install 'myapp[extra]'` would render as `pip install
            # 'myapp'`, i.e. a WRONG instruction handed to the user.
            console.print(f"[red]Error:[/red] {escape(str(exc))}")
            sys.exit(1)

    def _route(self, argv: List[str]) -> "Invocation":
        """Phase 1: walk ``argv`` to the target sub-app/command (+ promotion).

        Descends through sub-app groups, matches the command token, consumes a
        promoted config path for ``script_command``s, and consumes leading
        positional tokens. Everything unconsumed lands in
        ``Invocation.remaining_tokens`` for the global/override parsers.
        """
        return router.route(self, argv)

    def _prepare(self, inv: "Invocation") -> None:
        """Phases 3–5: parse globals, initialize state, apply overrides."""
        # 3. PARSE GLOBALS. Tokens (not strings) all the way down, so every
        # phase can tell an option from a post-``--`` literal it must not touch.
        config_path = inv.config_path
        globals_ = flags.parse_globals(inv.remaining_tokens)
        scopes, debug, log_overrides = globals_.scopes, globals_.debug, globals_.log_overrides
        final_tokens = globals_.remaining
        if globals_.config_path:
            config_path = globals_.config_path
        if config_path is not None:
            # Resolve once through confluid's search tiers so the context,
            # the not-found error, and the "Loaded configuration from:" line
            # all show the REAL file (possibly an XDG one).
            config_path = confluid.resolve_config_path(config_path)

        # 3b. BIND DIMENSION FLAGS — raw-load the config (if any) to discover
        # which `--KEY` flags should activate scope dimensions, then re-parse
        # `final_tokens` so those flags are routed into `scopes` instead of
        # being treated as config overrides.
        #
        # We use ``load_config_with_paths`` here (instead of plain
        # ``load_config``) so the resolved tree of YAML files — entrypoint
        # plus every transitively ``include:``-d file — is captured for
        # downstream consumers (e.g. matrainer's trainer logs them as
        # artifacts to every wired Lightning logger).
        raw_config: Optional[Any] = None
        included_paths: List[Path] = []
        if config_path is not None and config_path.exists():
            raw_config, included_paths = confluid.load(config_path, until="raw", return_paths=True)
            scopes, final_tokens = flags.bind_dimension_flags(scopes, raw_config, final_tokens)

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
        # Logging only becomes live in _bootstrap, so the promotion provenance
        # captured back in phase 1 is reported HERE, not at the point of the
        # decision (a debug/trace call before configure_logging is dropped).
        self._log_promotion_source(inv, config_path)

        # 4b. BIND POSITIONALS — write each consumed positional into the config
        # under its declared name (verbatim string) so DI resolves the matching
        # command parameter. Done before overrides so an explicit ``--name``
        # flag still wins over a positional, and so the bind survives even when
        # there are no overrides (``_apply_overrides`` early-returns then).
        if inv.positional_values and isinstance(self.context.config_data, dict):
            bound = dict(zip(inv.positional_names, inv.positional_values))
            self.context.config_data.update(bound)
            self.context.logger.debug(f"Bound positionals: {bound}")

        # 5. APPLY OVERRIDES
        self._apply_overrides(final_tokens)
        self._warn_unbound_literals(inv)

    def _log_promotion_source(self, inv: "Invocation", config_path: Optional[Path]) -> None:
        """Record WHERE a promoted config token was resolved from.

        Config promotion is deliberately eager: a bare positional token is
        consumed as a config as soon as a matching YAML exists in ANY of
        confluid's search tiers — the working directory, ``./config/``, or an
        XDG directory. That is convenient in the common case and a genuine
        footgun in the rare one: a stale ``~/.config/<app>/report.yaml`` will
        quietly swallow ``myapp run report`` that meant ``report`` as a
        positional, and nothing in the output says so.

        So every promotion is recorded at TRACE, and a promotion resolved from
        OUTSIDE the working directory — the surprising kind — additionally at
        DEBUG. Called from :meth:`_prepare` rather than at the point of the
        decision because routing happens in phase 1, before ``_bootstrap``
        configures loggair: a ``debug``/``trace`` call there is dropped on the
        floor.
        """
        if self.context is None or self.context.logger is None:
            return
        token, path = inv.config_token, inv.config_path
        if token is None or path is None:
            return
        if config_path != path:
            return  # an explicit --config outranked the promoted token

        cwd_candidate = (Path.cwd() / Path(token)).with_suffix(path.suffix)
        from_cwd = path.resolve() == cwd_candidate.resolve()
        self.context.logger.trace(f"Promoted token {token!r} to config {path} (search tier: {path.parent})")
        if not from_cwd:
            self.context.logger.debug(
                f"Config promotion resolved OUTSIDE the working directory: token {token!r} -> {path}. "
                f"Promotion is eager — a matching YAML in ./config/ or an XDG config dir is consumed "
                f"even when {token!r} was meant as a positional argument."
            )

    def _warn_unbound_literals(self, inv: "Invocation") -> None:
        """Warn about post-``--`` tokens that no positional slot claimed.

        A literal is deliberately never parsed as an override (that is what
        ``--`` buys), so an extra one would otherwise vanish in silence — the
        same failure mode the dropped-token warning exists to prevent.
        """
        assert self.context is not None
        extra = literal_texts(inv.remaining_tokens)
        if extra:
            self.context.logger.warning(
                f"Ignoring {len(extra)} argument(s) after `--` that no positional slot claimed: "
                f"{extra}. Tokens after `--` are literal values, never options."
            )

    def _execute(self, inv: "Invocation") -> Any:
        """Phase 6: dispatch the command and refresh the completion caches."""
        if not inv.target_func:
            raise UnknownCommandError("Unknown command or group")

        result = self.run_command(inv.target_func)

        # Refresh the completion cache so plugin/command changes propagate
        # to the next TAB. Best-effort: never let this break a real run.
        self._refresh_completion_cache()
        # Keep positional value caches warm (Q2) — refreshes stale ones in a
        # detached daemon thread, never blocking the command that just ran.
        self._maybe_background_refresh_values()

        return result

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

        loggair.configure_logging(
            console_level=console_level,
            file_level=file_level,
            log_dir=self.context.log_dir,
            script_name=script_name,
            force=True,
        )
        self.context.logger = get_logger(script_name)

        if self.context.config_path:
            if not self.context.config_path.exists():
                raise ConfigNotFoundError(f"Configuration file not found: {self.context.config_path}")
            data = raw_config if raw_config is not None else confluid.load(self.context.config_path, until="raw")
            # Resolve scopes/includes/markers, then expand `$VAR` / `~` in every
            # string primitive. Overrides get the same expansion later, in
            # `overrides.apply_overrides`.
            loaded = confluid.load(data, until="document", scopes=self.context.scopes or None)
            self.context.config_data = expand_strings(loaded)
            self.context.logger.info(f"Loaded configuration from: {self.context.config_path}")
            self.context.logger.trace(f"BOOTSTRAP CONFIG STATE: {self.context.config_data}")

    def _apply_overrides(self, tokens: List[Token]) -> None:
        """Phase 5: parse the leftover OPTION tokens and merge them into the config.

        Literals (post-``--``) are excluded by construction — they are values
        the user protected, reported separately by :meth:`_warn_unbound_literals`.
        """
        if not self.context or not tokens:
            return

        parsed_overrides, deletions, dropped = parse_override_args(option_texts(tokens))

        # A dropped token is almost always a typo'd override (``lr 0.1``
        # instead of ``--lr 0.1``) — silently ignoring one can cost an entire
        # training run on defaults, so each is surfaced as a warning.
        for token in dropped:
            self.context.logger.warning(
                f"Ignoring unrecognized CLI token {token!r} — expected one of: "
                f"`--key value`, `--key=value`, `key=value`, `+key[=value]`, `~key`."
            )

        if not parsed_overrides and not deletions:
            return

        # Kept for the post-materialization unused-override check in
        # ``run_command`` — confluid's report says which of these matched nothing.
        self.context.cli_overrides = dict(parsed_overrides)

        self.context.logger.debug(f"Applying CLI overrides: {parsed_overrides}; deletions: {deletions}")
        self.context.config_data = overrides.apply_overrides(self.context.config_data, parsed_overrides, deletions)
        self.context.logger.trace(f"POST-OVERRIDE CONFIG STATE: {self.context.config_data}")

    def run_command(self, func: Callable[..., Any]) -> Any:
        """Execute with Dependency Injection.

        When CLI overrides were applied, the DI materialization runs inside
        ``confluid.collect_report()`` and every override the report says
        matched NOTHING is warned about BEFORE the command body runs
        (:func:`liquifai.overrides.warn_unused_overrides`) — confluid is the
        authority on delivery, so this replaces the deleted pre-materialization
        heuristic that had to guess (and guessed wrong on glob heads and
        multi-hop paths). Without overrides the report machinery is not
        engaged at all — the default path stays zero-cost.
        """
        if not self.context:
            return func()
        context = self.context

        def _materialize_kwargs() -> Dict[str, Any]:
            kwargs = self._resolve_kwargs(func)
            flow_mode: FlowMode = getattr(func, "__liquifai_flow_mode__", "manual")
            if flow_mode == "auto":
                # `context=` is what makes the flat-config contract work: a Fluid that came
                # from the document is built AGAINST it, so top-level keys broadcast into
                # same-named constructor params. Without it every one of them is dropped
                # silently — see `di.deep_flow`.
                with di.confluid_active_context(context.config_data):
                    kwargs = {k: di.deep_flow(v, context=context.config_data) for k, v in kwargs.items()}
            return kwargs

        if context.cli_overrides:
            with confluid.collect_report() as report:
                kwargs = _materialize_kwargs()
            overrides.warn_unused_overrides(context.cli_overrides, report)
        else:
            kwargs = _materialize_kwargs()
        return func(**kwargs)

    def _resolve_kwargs(self, func: Callable[..., Any]) -> Dict[str, Any]:
        """DI-resolve ``func``'s parameters against ``self.context.config_data``.

        Shared between :meth:`run_command` and :meth:`liquify` — the latter
        needs the same live instances DI would produce, but without actually
        invoking the command. Delegates to :func:`liquifai.di.resolve_kwargs`.
        """
        assert self.context is not None
        return di.resolve_kwargs(self.context, func)

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
        lazily here (no loggair / no CLI override merge — intended for
        read-only introspection).
        """
        if self.context is None:
            # Same XDG-lookup identity as run() — lazy introspection must
            # resolve relative config paths the way a real run would.
            confluid.set_app_name(self.name)
            ctx = LiquifyContext(
                name=self.name,
                config_path=config_path,
                scopes=scopes or [],
                debug=False,
            )
            ctx.logger = get_logger(self.name)
            if config_path is not None:
                ctx.config_data = confluid.load(config_path, until="document", scopes=scopes or None)
                ctx.config_data = expand_strings(ctx.config_data)
            self.context = ctx
            set_context(self.context)
        kwargs = self._resolve_kwargs(target_func)
        # Deep-flow any unflowed Fluids so callers introspect live instances
        # all the way down the graph. Bare `flow()` leaves nested Class
        # kwargs deferred (they flow lazily in production), but the liquify
        # contract is "fully flowed graph" — introspection tools need every
        # attribute resolved.
        return {k: di.deep_flow(v, context=self.context.config_data) for k, v in kwargs.items()}

    def _show_help(
        self,
        app: "LiquifyApp",
        target_func: Optional[Callable[..., Any]] = None,
        config_path: Optional[Path] = None,
        layout: HelpLayout = "table",
    ) -> None:
        """Beautiful help menu via Rich.

        When a ``config_path`` is known and a ``target_func`` is selected,
        the help path flows the DI graph via :meth:`liquify` and shows every
        configurable kwarg reachable through the flowed instance tree. A
        flow failure downgrades to the static-type view with a brief note.

        ``layout`` is forwarded to :func:`liquifai.report.show_configuration`:
        ``"table"`` (default, Rich grid) or ``"lines"`` (one option per line —
        the ``--docs`` rendering).
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
                    layout=layout,
                    positionals=positionals,
                )
            else:
                show_configuration(
                    target_func, title="Command Configuration Options", layout=layout, positionals=positionals
                )
        else:
            report.show_command_index(app, console)

        # What THIS config's `!scope:KEY=VAL` blocks offer per dimension, and the value
        # its `default_scopes:` names for a bare run — rendered from the RAW document,
        # the same walk `flags.bind_dimension_flags` binds the `--KEY VAL` flags from.
        if config_path is not None:
            try:
                raw = confluid.load(config_path, until="raw")
            except Exception as exc:  # a malformed config: help still renders, the reason is shown
                console.print(f"[dim]Scope dimensions unavailable: {exc}[/dim]")
            else:
                report.show_scope_dimensions(raw, console=console, source=config_path.name)

        # The Global Options block is rendered from the ONE flag declaration
        # (grammar.GLOBAL_FLAG_SPECS) — the same table the parser and completion
        # derive from, so help can never drift from what the CLI accepts.
        report.show_global_options(console)


# ---------------------------------------------------------------------------
# DEPRECATED re-exports — scheduled for removal in v1.0.
#
# The consolidation split moved these helpers to their own modules
# (:mod:`liquifai.grammar`, :mod:`liquifai.overrides`, :mod:`liquifai.di`), but
# external callers imported them from ``liquifai.core`` under their historical
# underscore-prefixed names. They still resolve, and now emit a
# ``DeprecationWarning`` naming the exact replacement import.
#
# They are served through a PEP-562 module ``__getattr__`` rather than plain
# assignments so ACCESS is what warns — a plain alias cannot be detected, which
# is why the previous "keep them forever" note never converged on a cleanup.
# Nothing inside liquifai uses them (pinned by
# ``tests/test_deprecated_aliases.py``); the remaining consumers are external.
# ---------------------------------------------------------------------------

#: ``{alias: (owning module, public name)}`` — the full deprecated surface.
_DEPRECATED_ALIASES: Dict[str, Tuple[str, str]] = {
    "_confluid_active_context": ("liquifai.di", "confluid_active_context"),
    "_deep_flow": ("liquifai.di", "deep_flow"),
    "_parse_override_args": ("liquifai.overrides", "parse_override_args"),
    "_merge_overrides_into_fluids": ("liquifai.overrides", "merge_overrides_into_fluids"),
    "_delete_dotted_key": ("liquifai.overrides", "delete_dotted_key"),
    "_expand_strings": ("liquifai.overrides", "expand_strings"),
    "_stops_positional": ("liquifai.grammar", "stops_positional"),
    "_looks_like_arg": ("liquifai.grammar", "looks_like_arg"),
    "_looks_like_key": ("liquifai.grammar", "looks_like_key"),
}

_ALIAS_MODULES = {"liquifai.di": di, "liquifai.grammar": grammar, "liquifai.overrides": overrides}


def __getattr__(name: str) -> Any:
    """Serve the deprecated ``liquifai.core`` aliases, warning on each access."""
    target = _DEPRECATED_ALIASES.get(name)
    if target is None:
        raise AttributeError(f"module 'liquifai.core' has no attribute {name!r}")
    module_name, public_name = target
    warnings.warn(
        f"liquifai.core.{name} is deprecated and will be removed in v1.0; "
        f"import it from its owning module instead: "
        f"`from {module_name} import {public_name}`.",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_ALIAS_MODULES[module_name], public_name)
