import inspect
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import confluid
import logflow
from confluid import materialize
from logflow import get_logger
from rich.console import Console
from rich.table import Table

from liquifai.context import LiquifyContext, set_context

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
        self._default_cmd: Optional[Callable[..., Any]] = None
        self._script_cmds: Set[str] = set()

    def add_app(self, app: "LiquifyApp", name: Optional[str] = None) -> None:
        """Mount a sub-application to support nested command groups (infinitely sub-appable)."""
        group_name = name or app.name
        self._sub_apps[group_name] = app

    def command(
        self, name: Optional[str] = None, default: bool = False
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a command."""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or f.__name__.replace("_", "-")
            self._commands[cmd_name] = f
            if default:
                self._default_cmd = f
            return f

        return decorator

    def script_command(self, name: Optional[str] = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a command that supports config-promotion."""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or f.__name__.replace("_", "-")
            self._script_cmds.add(cmd_name)
            return self.command(name=cmd_name)(f)

        return decorator

    def run(self) -> Any:
        """Main entry point for the CLI."""
        argv = sys.argv[1:]

        # 1. IDENTIFY COMMAND, GROUP & PROMOTION
        config_path, cmd_name, remaining_argv = None, None, []
        target_app = self
        target_func = None

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
            else:
                remaining_argv.append(arg)
                i += 1

        if not target_func:
            target_func = target_app._default_cmd

        # 2. Check for help (also show help when subgroup reached without a command)
        if "--help" in argv or (not target_func and not target_app._default_cmd):
            self._show_help(target_app, target_func)
            return

        # 3. PARSE GLOBALS
        final_config_path, scopes, debug, log_overrides, final_argv = self._parse_globals(remaining_argv)
        if final_config_path:
            config_path = final_config_path

        # 4. INITIALIZE STATE
        self.context = LiquifyContext(
            name=self.name, config_path=config_path, scopes=scopes, debug=debug, **log_overrides
        )
        set_context(self.context)
        self._bootstrap()

        # 5. APPLY OVERRIDES
        self._apply_overrides(final_argv)

        # 6. EXECUTE
        if not target_func:
            console.print("[red]Error:[/red] Unknown command or group")
            sys.exit(1)

        return self.run_command(target_func)

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

    def _bootstrap(self) -> None:
        """Standard Trio Bootstrap."""
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
            self.context.config_data = confluid.load(self.context.config_path, scopes=self.context.scopes, flow=False)
            self.context.logger.info(f"Loaded configuration from: {self.context.config_path}")
            self.context.logger.trace(f"BOOTSTRAP CONFIG STATE: {self.context.config_data}")

    def _apply_overrides(self, args: List[str]) -> None:
        if not self.context or not args:
            return

        from confluid import deep_merge, parse_value

        overrides = {}
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                key = arg[2:]
                # Check for polarity suffixes
                if key.endswith("+"):
                    overrides[key[:-1]] = True
                    i += 1
                elif key.endswith("-"):
                    overrides[key[:-1]] = False
                    i += 1
                elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                    # Standard key-value pair
                    overrides[key] = parse_value(args[i + 1])
                    i += 2
                else:
                    # Implicit boolean True (standard CLI flag behavior)
                    overrides[key] = True
                    i += 1
            else:
                # Skip non-flag arguments
                i += 1

        if overrides:
            self.context.logger.debug(f"Applying CLI overrides: {overrides}")
            self.context.config_data = deep_merge(self.context.config_data, overrides)
            # Push overrides into Fluid kwargs (Class objects from YAML)
            _merge_overrides_into_fluids(self.context.config_data, overrides)
            self.context.logger.trace(f"POST-OVERRIDE CONFIG STATE: {self.context.config_data}")

    def run_command(self, func: Callable[..., Any]) -> Any:
        """Execute with Dependency Injection."""
        if not self.context:
            return func()

        self.context.logger.debug(f"DI: Resolving arguments for {func.__name__}")
        self.context.logger.trace(f"DI: Global config keys: {list(self.context.config_data.keys())}")

        sig = inspect.signature(func)
        kwargs = {}

        from confluid import get_registry

        reg = get_registry()

        for name, param in sig.parameters.items():
            if reg.is_configurable(param.annotation):
                cls_name = getattr(param.annotation, "__confluid_name__", param.annotation.__name__)
                config_block = (
                    self.context.config_data.get(cls_name)
                    or self.context.config_data.get(name)
                    or self.context.config_data
                )

                self.context.logger.debug(
                    f"DI: Resolving {name} ({cls_name}). Block keys: "
                    f"{list(config_block.keys()) if isinstance(config_block, dict) else 'N/A'}"
                )

                marker_dict = {
                    "_confluid_class_": cls_name,
                    **(config_block if isinstance(config_block, dict) else {}),
                }
                kwargs[name] = materialize(marker_dict, context=self.context.config_data)
            else:
                # Non-configurable: Resolve from context data or use default
                if name in self.context.config_data:
                    kwargs[name] = self.context.config_data[name]
                elif param.default is not inspect.Parameter.empty:
                    kwargs[name] = param.default

        return func(**kwargs)

    def _show_help(self, app: "LiquifyApp", target_func: Optional[Callable[..., Any]] = None) -> None:
        """Beautiful help menu via Rich."""
        console.print(f"\n[bold]{app.name.upper()}[/bold] - Modular Septet Framework")
        if app.description:
            console.print(f"[dim]{app.description}[/dim]")

        if target_func:
            desc = target_func.__doc__ or "No description."
            console.print(f"\n[bold]Command:[/bold] {target_func.__name__.replace('_', '-')}")
            console.print(f"[dim]{desc.strip()}[/dim]")

            from liquifai.report import show_configuration

            show_configuration(target_func, title="Command Configuration Options")
        else:
            table = Table(box=None, padding=(0, 2))
            table.add_column("Command/Group", style="cyan")
            table.add_column("Description")

            for name, sub_app in sorted(app._sub_apps.items()):
                desc = f"[bold]Group:[/bold] {sub_app.description}" if sub_app.description else "Group."
                table.add_row(name, desc)

            for name, func in sorted(app._commands.items()):
                desc = func.__doc__.strip().split("\n")[0] if func.__doc__ else "No description."
                table.add_row(name, desc)

            console.print(table)

        console.print("\n[bold]Global Options:[/bold]")
        console.print("  -c, --config PATH    Configuration file.")
        console.print("  -s, --scope NAME     Active scope(s).")
        console.print("  -d, --debug          Enable debug mode.")
        console.print("  --level LEVEL        Set log level (TRACE, DEBUG, INFO).")
        console.print("")


def _merge_overrides_into_fluids(data: Any, overrides: Dict[str, Any]) -> None:
    """Merge CLI overrides into Fluid kwargs throughout the config tree."""
    from confluid.fluid import Fluid

    if isinstance(data, Fluid):
        for k, v in overrides.items():
            if k in data.kwargs:
                data.kwargs[k] = v
        for v in data.kwargs.values():
            _merge_overrides_into_fluids(v, overrides)
    elif isinstance(data, dict):
        for v in data.values():
            _merge_overrides_into_fluids(v, overrides)
    elif isinstance(data, list):
        for item in data:
            _merge_overrides_into_fluids(item, overrides)
