"""Shared pytest fixtures.

All fixtures are local to the test process and never touch a real
LTspice / Wine install. The doctor module is exercised entirely through
monkeypatched subprocess and filesystem fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ltagent.config import Config


# --- Phase 1 IR test fixtures ---------------------------------------------


PHASE1_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = PHASE1_ROOT / "examples"
INVALID_DIR = PHASE1_ROOT / "tests" / "fixtures" / "invalid"
SCHEMA_PATH = PHASE1_ROOT / "schemas" / "circuit_ir.schema.json"


@pytest.fixture(scope="session")
def phase1_root() -> Path:
    return PHASE1_ROOT


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES_DIR


@pytest.fixture(scope="session")
def invalid_dir() -> Path:
    return INVALID_DIR


@pytest.fixture(scope="session")
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture(scope="session")
def json_schema(schema_path: Path) -> dict:
    """Loaded JSON Schema as dict (cached for the session)."""
    import json

    return json.loads(schema_path.read_text(encoding="utf-8"))


# --- Phase 0 doctor / CLI fixtures ---------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A clean working directory with an isolated ``projects`` and ``templates`` dir."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "projects").mkdir()
    (cwd / "templates").mkdir()
    monkeypatch.chdir(cwd)
    # Make sure we never accidentally pick up the real user's config.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield cwd


@pytest.fixture()
def default_config() -> Config:
    return Config()


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``Path.home()`` and ``HOME`` at a temp dir for the duration of the test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    yield home


@pytest.fixture()
def fake_executable(tmp_path: Path) -> Path:
    """A non-executable file that exists where LTspice would be."""
    p = tmp_path / "XVIIx64.exe"
    p.write_bytes(b"MZ\x90\x00")  # tiny PE-like header
    return p
