"""CLI argument router for liquifai."""

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import confluid

from liquifai import grammar

if TYPE_CHECKING:
    from liquifai.core import Invocation, LiquifyApp


class CliRouter:
    """Walks raw argv to identify target sub-app, command handler, promoted config path, and positionals."""

    def __init__(self, root_app: "LiquifyApp") -> None:
        self.root_app = root_app

    def route(self, argv: List[str]) -> "Invocation":
        from liquifai.core import Invocation

        config_path: Optional[Path] = None
        cmd_name: Optional[str] = None
        remaining_argv: List[str] = []
        target_app = self.root_app
        target_func = None
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
                    cp = confluid.resolve_config_path(cp)
                    if cp.exists():
                        config_path, i = cp, i + 1
                positional_names = list(getattr(target_func, "__liquifai_positionals__", []))
                for _ in positional_names:
                    if i < len(argv) and not grammar.stops_positional(argv[i]):
                        positional_values.append(argv[i])
                        i += 1
                    else:
                        break
            else:
                remaining_argv.append(arg)
                i += 1

        if not target_func:
            target_func = target_app._default_cmd

        return Invocation(
            target_app=target_app,
            cmd_name=cmd_name,
            target_func=target_func,
            config_path=config_path,
            positional_names=positional_names,
            positional_values=positional_values,
            remaining_argv=remaining_argv,
        )
