"""Tests for the canonical projects-root resolver.

Covers the resolution order (explicit > env > default), the XDG fallback
on Linux, the platform branch on Windows (smoke-tested by monkeypatching
``sys.platform``), and the ``ensure`` helper's creation behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ltagent.projects_root import (
    ENV_PROJECTS_ROOT,
    ensure_projects_root,
    get_default_projects_root,
    resolve_projects_root,
)


def test_default_root_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/srv/xdg")
    monkeypatch.delenv(ENV_PROJECTS_ROOT, raising=False)
    assert get_default_projects_root() == Path("/srv/xdg/ltagent/projects")


def test_default_root_falls_back_to_home_local_share(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv(ENV_PROJECTS_ROOT, raising=False)
    expected = Path.home() / ".local" / "share" / "ltagent" / "projects"
    assert get_default_projects_root() == expected


def test_default_root_uses_localappdata_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    monkeypatch.delenv(ENV_PROJECTS_ROOT, raising=False)
    # The Posix runtime keeps the backslashes from LOCALAPPDATA inside
    # the resulting Path; the assertion is on the trailing segment,
    # not the literal string equality.
    root = get_default_projects_root()
    assert root.name == "projects"
    assert root.parent.name == "ltagent"
    assert "AppData" in str(root)


def test_resolve_explicit_wins_over_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_PROJECTS_ROOT, str(tmp_path / "from_env"))
    resolved = resolve_projects_root(explicit=tmp_path / "explicit")
    assert resolved == (tmp_path / "explicit").resolve()


def test_resolve_env_wins_over_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_PROJECTS_ROOT, str(tmp_path / "from_env"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    resolved = resolve_projects_root()
    assert resolved == (tmp_path / "from_env").resolve()


def test_resolve_returns_absolute_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_PROJECTS_ROOT, str(tmp_path / "from_env"))
    resolved = resolve_projects_root()
    assert resolved.is_absolute()


def test_ensure_creates_missing_root(tmp_path: Path) -> None:
    target = tmp_path / "new_root"
    assert not target.exists()
    created = ensure_projects_root(target)
    assert created == target.resolve()
    assert created.is_dir()


def test_ensure_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "new_root"
    ensure_projects_root(target)
    ensure_projects_root(target)  # must not raise
    assert target.is_dir()


def test_env_constant_is_stable() -> None:
    assert ENV_PROJECTS_ROOT == "LTAGENT_PROJECTS_ROOT"
