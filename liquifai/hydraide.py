"""``hydraide`` — the command line of confluid's preprocessor, as a :class:`LiquifyApp`.

Confluid ships the FUNCTIONS (``confluid.hydraide.emit`` / ``check``: resolve a config to ONE
plain-YAML document — includes spliced, scopes applied, dotted keys expanded, interpolation
burned in, broadcasting settled, shared markers anchored); the COMMAND lives here, because
this is where every workspace CLI is built and it needs nothing a ``LiquifyApp`` does not
already give it: config promotion (the first token after the verb is the file), ``--scope``
and the config's own dimension flags (``--framework torch``), the search tiers, and the
failure contract (a located ``ConfluidError`` renders as one line, exit 1).

::

    hydraide emit experiment.yaml --scope framework=torch            # resolved document on stdout
    hydraide emit experiment.yaml --output resolved.yaml             # …or written to a file
    hydraide check resolved.yaml                                     # exit 1 with a diff if the
                                                                     # file is not its own resolution
"""

import sys
from pathlib import Path
from typing import Optional

from loggair import get_logger

from liquifai.context import get_context
from liquifai.core import LiquifyApp
from liquifai.exceptions import LiquifaiError

logger = get_logger(__name__)

app = LiquifyApp(
    name="hydraide",
    description="Resolve a confluid config to ONE plain-YAML document (includes, scopes, dotted keys, "
    "interpolation and broadcasting settled; shared markers anchored).",
)

__all__ = ["app", "check", "emit", "main"]


def _config_and_scopes() -> "tuple[Path, list[str]]":
    """The promoted config path and the active scopes, off the liquifai context."""
    ctx = get_context()
    if ctx is None or ctx.config_path is None:
        raise LiquifaiError("hydraide needs a config: `hydraide emit <config.yaml>` (or `--config <path>`).")
    return ctx.config_path, list(ctx.scopes)


@app.script_command(flow_mode="manual")
def emit(output: Optional[str] = None) -> None:
    """Resolve the config and print the plain-YAML document (or write it with ``--output``).

    Args:
        output: Write the resolved document to this file instead of stdout.
    """
    from confluid.hydraide import emit as _emit

    config_path, scopes = _config_and_scopes()
    text = _emit(config_path, scopes=scopes)
    if output:
        Path(output).write_text(text)
        logger.info(f"hydraide: {config_path} -> {output}")
    else:
        sys.stdout.write(text)


@app.script_command(flow_mode="manual")
def check() -> None:
    """Exit 1 (diff on stdout) unless the config is its OWN resolution — a committed artefact's CI gate."""
    from confluid.hydraide import check as _check

    config_path, scopes = _config_and_scopes()
    diff = _check(config_path, scopes=scopes)
    if diff is not None:
        sys.stdout.write(diff)
        sys.exit(1)
    logger.info(f"hydraide: {config_path} is its own resolution")


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
