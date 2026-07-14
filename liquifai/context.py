from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LiquifyContext:
    """Stores the runtime state of a Liquify application."""

    name: str
    config_path: Optional[Path] = None
    scopes: List[str] = field(default_factory=list)
    debug: bool = False
    log_level: Optional[str] = None
    console_level: Optional[str] = None
    file_level: Optional[str] = None
    log_dir: Optional[Path] = None
    config_data: Dict[str, Any] = field(default_factory=dict)
    # Resolved YAML tree: ``config_path`` plus every transitively
    # ``include:``-d file in load order. Populated by the CLI bootstrap when
    # confluid resolves the include graph; consumers (e.g. marainer's
    # trainer) read this to log the full configuration as a run artifact.
    included_paths: List[Path] = field(default_factory=list)

    # The loaded logger instance
    logger: Any = None


# The active context, held in a ContextVar (not a bare module global) so
# concurrent in-process embeddings — threads, asyncio tasks (e.g. an MCP
# server running commands) — each see their own context. Mirrors confluid's
# own ContextVar migration. For the ordinary single-threaded CLI process the
# behavior is identical to the old global.
_active_context: ContextVar[Optional[LiquifyContext]] = ContextVar("liquifai_active_context", default=None)


def get_context() -> Optional[LiquifyContext]:
    """Get the currently active Liquify context."""
    return _active_context.get()


def set_context(ctx: Optional[LiquifyContext]) -> None:
    """Set the active Liquify context (``None`` clears it, e.g. between tests)."""
    _active_context.set(ctx)
