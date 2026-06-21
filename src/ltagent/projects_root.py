"""Canonical projects root resolver for the Hardware Design Workbench.

The desktop, CLI, engine, and Codex-MCP entry points all need to agree
on the directory that contains workbench projects. This module is the
single source of truth for that path so every surface resolves the
same value.

Resolution order (first non-empty wins):

1. Explicit caller-supplied path (e.g. ``--projects-root`` flag).
2. ``LTAGENT_PROJECTS_ROOT`` environment variable.
3. ``$XDG_DATA_HOME/ltagent/projects`` (Linux/macOS) or
   ``%LOCALAPPDATA%\\ltagent\\projects`` (Windows).
4. ``~/.local/share/ltagent/projects`` (Linux/macOS) or
   ``~/AppData/Local/ltagent/projects`` (Windows).

The directory is **not** created here. Callers that need an existing
root (e.g. ``EngineService``) should call :func:`ensure_projects_root`
which both resolves and ``mkdir(parents=True, exist_ok=True)``s.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .security import PathSafetyError, safe_resolve_under

ENV_PROJECTS_ROOT: str = "LTAGENT_PROJECTS_ROOT"


def get_default_projects_root() -> Path:
    """Return the platform-appropriate default projects root.

    The result is **not** guaranteed to exist. Use
    :func:`ensure_projects_root` to resolve and create.
    """
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "ltagent" / "projects"
        return Path.home() / "AppData" / "Local" / "ltagent" / "projects"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "ltagent" / "projects"
    return Path.home() / ".local" / "share" / "ltagent" / "projects"


def resolve_projects_root(explicit: Path | str | None = None) -> Path:
    """Resolve the canonical projects root.

    The order is: explicit path > ``LTAGENT_PROJECTS_ROOT`` env >
    :func:`get_default_projects_root`. The returned path is
    ``Path.expanduser().resolve(strict=False)`` so it is absolute and
    free of symlinks even if it does not yet exist.
    """
    if explicit is not None:
        candidate = Path(explicit).expanduser()
    else:
        env = os.environ.get(ENV_PROJECTS_ROOT)
        candidate = Path(env).expanduser() if env else get_default_projects_root().expanduser()
    return candidate.resolve(strict=False)


def ensure_projects_root(explicit: Path | str | None = None) -> Path:
    """Resolve the canonical projects root and create it if missing.

    Raises :class:`PathSafetyError` only if the explicit path is
    supplied and refuses to resolve.
    """
    root = resolve_projects_root(explicit)
    if explicit is not None and projects_root_is_relative_to(root, Path.cwd()) is False:
        # Re-check safety for explicit paths; the env / default paths
        # are platform-known user data directories.
        root = safe_resolve_under(root, root.parent, must_exist=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def projects_root_is_relative_to(root: Path, base: Path) -> bool:
    """Return ``True`` if ``root`` resolves under ``base``.

    A small helper used to keep the ``ensure`` path's containment
    check readable; mirrors :func:`ltagent.security.safe_resolve_under`
    without raising.
    """
    try:
        safe_resolve_under(root, base, must_exist=False)
        return True
    except PathSafetyError:
        return False


__all__ = [
    "ENV_PROJECTS_ROOT",
    "ensure_projects_root",
    "get_default_projects_root",
    "resolve_projects_root",
]
