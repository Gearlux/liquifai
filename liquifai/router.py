"""CLI argument router for liquifai — the app-side adapter for the shared walk.

This module owns the two pieces that make :func:`liquifai.walk.walk_invocation`
work against a LIVE application: :class:`_AppNav` (how to read a
``LiquifyApp``'s sub-apps/commands/positionals) and
:func:`_resolve_promoted_config` (how dispatch resolves a promoted config
token — through confluid's search tiers, requiring the file to exist).
:func:`route` packages the walk's result as an
:class:`~liquifai.core.Invocation`.

The descent itself is NOT implemented here — :mod:`liquifai.completion.engine`
performs the same descent over the serialized command tree with its own
``Nav``, and both must stay in step (see ``docs/architecture.md`` → "One argv
walk, two data shapes").

There is deliberately no router CLASS: the routing entry point is stateless, so
it is a module function taking the app, matching the shape of
:mod:`liquifai.completion_cli`.
"""

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import confluid

from liquifai.walk import Nav, Token, tokenize, walk_invocation

if TYPE_CHECKING:
    from liquifai.core import Invocation, LiquifyApp


class _AppNav:
    """:class:`~liquifai.walk.Nav` over a live ``LiquifyApp``."""

    def __init__(self, app: "LiquifyApp") -> None:
        self.app = app

    def sub_app(self, token: str) -> Optional[Nav]:
        sub = self.app._sub_apps.get(token)
        return _AppNav(sub) if sub is not None else None

    def has_command(self, token: str) -> bool:
        return token in self.app._commands

    def is_script_command(self, cmd: str) -> bool:
        return cmd in self.app._script_cmds

    def positionals(self, cmd: str) -> List[str]:
        return list(getattr(self.app._commands[cmd], "__liquifai_positionals__", []))

    def default_command(self) -> Optional[str]:
        default = self.app._default_cmd
        return next((name for name, func in self.app._commands.items() if func is default), None)


def _resolve_promoted_config(token: str) -> Optional[Path]:
    """Resolve a promoted config token through confluid's search tiers.

    Returns the resolved path only when it exists, so a token that merely
    *looks* like a config (a positional, a typo) falls through to the
    positional/override parsers instead of being swallowed. A bare name gains
    a ``.yaml`` suffix first — ``app train demo`` finds ``demo.yaml``.
    """
    candidate = Path(token) if Path(token).suffix else Path(token).with_suffix(".yaml")
    resolved = confluid.resolve_config_path(candidate)
    return resolved if resolved.exists() else None


def route(root_app: "LiquifyApp", argv: List[str]) -> "Invocation":
    """Walk ``argv`` to the target sub-app, command, promoted config and positionals."""
    from liquifai.core import Invocation

    tokens: List[Token] = tokenize(argv)
    walk = walk_invocation(tokens, _AppNav(root_app), _resolve_promoted_config)

    target_app = walk.nav.app if isinstance(walk.nav, _AppNav) else root_app
    target_func = target_app._commands.get(walk.cmd_name) if walk.cmd_name else None
    if target_func is None:
        target_func = target_app._default_cmd

    return Invocation(
        target_app=target_app,
        target_func=target_func,
        config_path=walk.config_path,
        config_token=walk.config_token,
        positional_names=walk.positional_names,
        positional_values=walk.positional_values,
        remaining_tokens=walk.remaining,
    )
