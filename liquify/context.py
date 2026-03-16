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

    # The loaded logger instance
    logger: Any = None


# Global Singleton for the active context
_active_context: Optional[LiquifyContext] = None


def get_context() -> Optional[LiquifyContext]:
    """Get the currently active Liquify context."""
    return _active_context


def set_context(ctx: LiquifyContext) -> None:
    """Set the active Liquify context."""
    global _active_context
    _active_context = ctx
