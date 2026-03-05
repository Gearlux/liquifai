import functools
import inspect
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import confluid
import logflow
import typer
from rich.console import Console

from liquify.context import LiquifyContext

console = Console()


class LiquifyApp:
    """Simplified Liquify Framework."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.context: Optional[LiquifyContext] = None
        self._default_cmd: Optional[Callable[..., Any]] = None
        self._configurable_types: List[Any] = []

        # Initialize Typer
        self.typer_app = typer.Typer(name=name)
        self.typer_app.callback(
            context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
            invoke_without_command=True,
        )(self._global_callback)

    def _global_callback(
        self,
        ctx: typer.Context,
        config: Optional[Path] = typer.Option(None, "--config", "-c", help="Config file."),
        scope: List[str] = typer.Option([], "--scope", "-s", help="Active scopes."),
        debug: bool = typer.Option(False, "--debug", "-d", help="Debug mode."),
        options: bool = typer.Option(False, "--options", help="Show overview."),
    ) -> None:
        # 1. Initialize Context & Trio
        self.context = LiquifyContext(name=self.name, config_path=config, scopes=scope, debug=debug)
        self._bootstrap()

        # 2. Process dynamic overrides from extra args
        self._apply_overrides(ctx.args)

        # 3. Handle --options
        if options:
            from liquify.report import show_configuration

            for cls in self._configurable_types:
                show_configuration(cls, config_map=self.context.config_data)
            raise typer.Exit()

        # 4. Handle Default Command (if no subcommand)
        if ctx.invoked_subcommand is None and self._default_cmd:
            # Execute the default command function directly
            # We must use the wrapper logic to ensure DI works
            self.run_command(self._default_cmd)

    def _bootstrap(self) -> None:
        """Standard trio bootstrapping."""
        if not self.context:
            return
        log_level = "DEBUG" if self.context.debug else "INFO"
        logflow.configure_logging(console_level=log_level)
        self.context.logger = logflow.get_logger(self.name)

        if self.context.config_path:
            if not self.context.config_path.exists():
                if self.context.logger:
                    self.context.logger.error(f"Configuration file not found: {self.context.config_path}")
                raise typer.Exit(code=1)
            self.context.config_data = confluid.load(self.context.config_path, scopes=self.context.scopes)

    def _apply_overrides(self, args: List[str]) -> None:
        """Simple --KEY VAL parsing with broadcast support."""
        if not self.context:
            return
        from confluid import deep_merge, expand_dotted_keys, parse_value

        overrides = {}
        for i in range(0, len(args), 2):
            if i + 1 < len(args) and args[i].startswith("--"):
                key = args[i][2:]
                # Use Confluid to parse the value (handles ints, lists, bools, etc)
                val = parse_value(args[i + 1])
                overrides[key] = val

        if overrides:
            # 1. Expand dotted keys
            expanded = expand_dotted_keys(overrides)

            # 2. Apply Broadcast logic: if a key exists in overrides but not at top level
            # of config_data, try to find it inside nested dicts and update them.
            final_overrides = {}
            for k, v in expanded.items():
                if k not in self.context.config_data:
                    # Search and inject into any dict that has this key
                    self._broadcast_inject(self.context.config_data, k, v)
                else:
                    final_overrides[k] = v

            # 3. Deep merge the remaining explicit/top-level overrides
            if final_overrides:
                self.context.config_data = deep_merge(self.context.config_data, final_overrides)

    def _broadcast_inject(self, data: Dict[str, Any], target_key: str, value: Any) -> None:
        """Recursively inject a value into any dictionary containing target_key."""
        for k, v in data.items():
            if k == target_key:
                data[k] = value
            elif isinstance(v, dict):
                self._broadcast_inject(v, target_key, value)

    def command(
        self, *args: Any, default: bool = False, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register command with DI and hidden signature."""
        # Ensure command allows extra args for overrides
        context_settings: Dict[str, Any] = kwargs.pop("context_settings", {})
        context_settings.setdefault("allow_extra_args", True)
        context_settings.setdefault("ignore_unknown_options", True)
        kwargs["context_settings"] = context_settings

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            if default:
                self._default_cmd = f

            @functools.wraps(f)
            def wrapper(**f_kwargs: Any) -> Any:
                # 1. Capture and remove ctx if present in kwargs
                ctx = f_kwargs.pop("ctx", None)

                # 2. Process overrides from extra arguments
                if ctx and ctx.args:
                    self._apply_overrides(ctx.args)

                # 3. Execute with DI
                return self.run_command(f, ctx=ctx, **f_kwargs)

            # 2. Update the wrapper's signature for Typer
            sig = inspect.signature(f)
            new_params = []
            for name, param in sig.parameters.items():
                if name == "ctx" or param.annotation is typer.Context:
                    continue
                if hasattr(param.annotation, "__confluid_configurable__"):
                    if param.annotation not in self._configurable_types:
                        self._configurable_types.append(param.annotation)
                else:
                    new_params.append(param)

            # Append Context at the end to satisfy Typer and Python ordering
            ctx_param = inspect.Parameter(
                "ctx",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=typer.Context,
                default=None,  # Make it optional so it doesn't break positional calls
            )
            new_params.append(ctx_param)

            wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore

            return self.typer_app.command(*args, **kwargs)(wrapper)

        return decorator

    def run_command(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Perform DI and execute a function."""
        if not self.context:
            return func(*args, **kwargs)

        sig = inspect.signature(func)
        # 1. Resolve and inject configurable objects only if NOT already provided
        for name, param in sig.parameters.items():
            if hasattr(param.annotation, "__confluid_configurable__"):
                if name not in kwargs:
                    # Resolve from Confluid
                    kwargs[name] = confluid.load(
                        {param.annotation.__name__: self.context.config_data.get(param.annotation.__name__, {})},
                        scopes=self.context.scopes,
                    )

        # 2. FILTER: Only pass kwargs that the function actually accepts
        # This prevents "unexpected keyword argument 'ctx'" for user functions
        valid_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        }

        # 3. Execute with the merged and filtered argument set
        return func(*args, **valid_kwargs)

    def run(self) -> Any:
        import sys

        if self._default_cmd:
            cmds = [c.name for c in self.typer_app.registered_commands if c.name]
            # If no subcommand present in argv, we perform redirection
            if not any(arg in cmds for arg in sys.argv[1:]):
                if "--help" not in sys.argv and "--options" not in sys.argv:
                    # 1. Identify known global flags
                    globals = ["--config", "-c", "--scope", "-s", "--debug", "-d"]

                    # 2. Re-assemble argv: [Script] [Globals] [DefaultCmd] [Remaining]
                    new_argv = [sys.argv[0]]
                    remaining = []

                    i = 1
                    while i < len(sys.argv):
                        if sys.argv[i] in globals:
                            new_argv.append(sys.argv[i])
                            # Globals usually have a following value (except --debug)
                            if sys.argv[i] not in ["--debug", "-d"]:
                                if i + 1 < len(sys.argv):
                                    new_argv.append(sys.argv[i + 1])
                                    i += 2
                                else:
                                    i += 1
                            else:
                                i += 1
                        else:
                            remaining.append(sys.argv[i])
                            i += 1

                    cmd_name = self._default_cmd.__name__.replace("_", "-")
                    sys.argv = new_argv + [cmd_name] + remaining

        return self.typer_app()
