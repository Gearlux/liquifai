"""Deterministic configuration transformation pipeline for liquifai."""

from typing import Any, Dict, List, Optional

import confluid
from confluid import deep_merge, expand_dotted_keys

from liquifai.overrides import delete_dotted_key, expand_strings, merge_overrides_into_fluids


class ConfigPipeline:
    """Encapsulates configuration parsing, positional binding, and override application into a pipeline."""

    def __init__(self, data: Any = None) -> None:
        self.data: Any = data

    @classmethod
    def load(cls, raw_config: Any, scopes: Optional[List[str]] = None) -> "ConfigPipeline":
        """Initialize pipeline with raw config or Fluid, expanding string primitives."""
        data = confluid.load(raw_config, flow=False, scopes=scopes or None) if raw_config is not None else None
        pipeline = cls(data)
        return pipeline.expand_primitives()

    def expand_primitives(self) -> "ConfigPipeline":
        """Recursively expand environment variables and ~ in string primitives."""
        if self.data is not None:
            self.data = expand_strings(self.data)
        return self

    def bind_positionals(self, names: List[str], values: List[str]) -> "ConfigPipeline":
        """Bind consumed positional values into top-level config dict."""
        if values and isinstance(self.data, dict):
            bound = dict(zip(names, values))
            self.data.update(bound)
        return self

    def apply_overrides(self, overrides: Dict[str, Any], deletions: List[str]) -> "ConfigPipeline":
        """Apply CLI overrides and key deletions to the config tree."""
        if not overrides and not deletions:
            return self

        parsed = expand_strings(overrides)
        if self.data is None:
            self.data = {}

        self.data = deep_merge(self.data, parsed)
        if isinstance(self.data, dict):
            self.data = expand_dotted_keys(self.data)

        for path in deletions:
            delete_dotted_key(self.data, path)

        merge_overrides_into_fluids(self.data, parsed)
        return self
