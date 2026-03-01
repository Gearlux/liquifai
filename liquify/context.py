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
    config_data: Dict[str, Any] = field(default_factory=dict)

    # Placeholder for the loaded logger (LogFlow integration in Phase 3)
    logger: Any = None
