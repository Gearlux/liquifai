"""Rc-file installation of liquifai completion (per-app blocks + shared helpers).

Owns the marker-delimited splice into ``.bashrc`` / ``.zshrc`` (or a
workspace-local ``target_rc``), the fish per-completion file layout, the
multi-app ``install_for_apps`` bootstrap, and the
``liquifai-install-completions`` console entry.

Pure-stdlib module (fast-path safe — see the completion mandate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from liquifai.exceptions import UnsupportedShellError

from .shells import SHELLS, detect_shell, render_helpers, render_script

_HELPERS_MARKER = "# >>> liquifai shared helpers >>>"
_HELPERS_END_MARKER = "# <<< liquifai shared helpers <<<"


def _splice_block(text: str, start_marker: str, end_marker: str, new_block: str) -> str:
    """Replace an existing ``start_marker``..``end_marker`` block, or append it."""
    if start_marker in text and end_marker in text:
        start = text.index(start_marker)
        end = text.index(end_marker) + len(end_marker)
        if end < len(text) and text[end] == "\n":
            end += 1
        replacement = new_block
        if start > 0 and text[start - 1] != "\n":
            replacement = "\n" + replacement
        return text[:start] + replacement + text[end:]
    prefix = "" if not text or text.endswith("\n") else "\n"
    return text + prefix + "\n" + new_block


def install_script(
    prog: str,
    shell: str,
    home: Optional[Path] = None,
    target_rc: Optional[Path] = None,
) -> Path:
    """Install completion for ``prog`` in ``shell``. Idempotent.

    Embeds the rendered script directly in the rc file (bash/zsh) or the
    fish completions directory — never an ``eval "$(prog --show-completion)"``
    callback, because that would re-invoke the (slow) app on every shell
    startup. For bash/zsh, also installs (or refreshes) a single shared
    ``# >>> liquifai shared helpers >>>`` block providing
    :func:`liquifai-bind-alias` so user aliases can opt in to completion.

    When ``target_rc`` is provided (bash/zsh only), the helpers + per-app
    block are written into that file instead of ``home/.bashrc`` (or
    ``.zshrc``). This lets a project-level bootstrap install completion
    into a workspace-local rc file (e.g. sourced from ``project.bashrc``)
    without polluting the user's global shell rc. ``target_rc`` is
    ignored for fish, which always uses the per-completion file layout
    under ``~/.config/fish/completions``.

    Returns the path that was created or modified.
    """
    if shell not in SHELLS:
        raise UnsupportedShellError(f"Unsupported shell {shell!r}; expected one of {SHELLS}")
    home = home or Path.home()

    if shell == "fish":
        target = home / ".config" / "fish" / "completions" / f"{prog}.fish"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_script(prog, shell))
        return target

    if target_rc is not None:
        rc = Path(target_rc)
        rc.parent.mkdir(parents=True, exist_ok=True)
    else:
        rc = home / (".bashrc" if shell == "bash" else ".zshrc")
    existing = rc.read_text() if rc.exists() else ""

    helpers_body = render_helpers(shell).rstrip("\n")
    helpers_block = f"{_HELPERS_MARKER}\n{helpers_body}\n{_HELPERS_END_MARKER}\n"
    existing = _splice_block(existing, _HELPERS_MARKER, _HELPERS_END_MARKER, helpers_block)

    marker = f"# >>> liquifai completion for {prog} >>>"
    end_marker = f"# <<< liquifai completion for {prog} <<<"
    body = render_script(prog, shell).rstrip("\n")
    app_block = f"{marker}\n{body}\n{end_marker}\n"
    existing = _splice_block(existing, marker, end_marker, app_block)

    rc.write_text(existing)
    return rc


def install_for_apps(
    target_rc: Path,
    apps: Optional[Iterable[str]] = None,
    shell: Optional[str] = None,
    prefix: Optional[Path] = None,
) -> List[str]:
    """Install completion for a set of Liquifai apps into ``target_rc``.

    When ``apps`` is ``None``, auto-discover them via
    :func:`discover_liquifai_apps` against ``prefix`` (default
    ``sys.prefix``). Returns the list of app names that were installed
    (in install order). The same ``target_rc`` accumulates one helpers
    block + one per-app completion block per call; re-running is
    idempotent because :func:`install_script` splices by markers.
    """
    shell = shell or detect_shell()
    if apps is not None:
        names = list(apps)
    else:
        # Late-bound through the PACKAGE namespace (not `.discover` directly)
        # so tests / embedders that monkeypatch
        # ``liquifai.completion.discover_liquifai_apps`` are honored.
        import liquifai.completion as _completion

        names = _completion.discover_liquifai_apps(prefix=prefix)
    rc = Path(target_rc)
    installed: List[str] = []
    for name in names:
        install_script(name, shell, target_rc=rc)
        installed.append(name)
    return installed


def _cli_install_completions(argv: Optional[List[str]] = None) -> int:
    """Console-script entry for ``liquifai-install-completions``.

    Usage::

        liquifai-install-completions --target-rc <path> [--shell <bash|zsh|fish>] [apps...]

    With no positional ``apps``, auto-discover every Liquifai app in the
    current venv and install completion for each into ``--target-rc``.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="liquifai-install-completions",
        description="Install liquifai shell completion for one or more apps into a target rc file.",
    )
    parser.add_argument(
        "--target-rc",
        required=True,
        type=Path,
        help="Path to the rc file to write completion into (e.g. project-local .bashrc fragment).",
    )
    parser.add_argument(
        "--shell",
        choices=SHELLS,
        default=None,
        help="Shell to install completion for (default: detect from $SHELL).",
    )
    parser.add_argument(
        "apps",
        nargs="*",
        help="App names to install. Empty → auto-discover all Liquifai apps in the active venv.",
    )
    args = parser.parse_args(argv)

    shell = args.shell or detect_shell()
    apps = args.apps or None
    installed = install_for_apps(target_rc=args.target_rc, apps=apps, shell=shell)
    if not installed:
        print(f"liquifai-install-completions: no Liquifai apps found to install into {args.target_rc}")
        return 0
    for name in installed:
        print(f"installed {name} ({shell}) → {args.target_rc}")
    return 0
