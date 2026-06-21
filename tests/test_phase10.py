"""Tests for the Phase 10 build and smoke scripts.

Covers:
* ``scripts/smoke_codex.py`` exits 0 (run via subprocess so the
  embedded CLI test path is exercised).
* ``scripts/smoke_workbench_v2.py`` exits 0.
* ``scripts/build_sidecar.py`` freezes target-triple sidecars.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_script(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


def test_smoke_codex_exits_zero() -> None:
    proc = _run_script("smoke_codex.py")
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK: codex install/doctor/uninstall round-trip" in proc.stdout


def test_smoke_workbench_v2_exits_zero() -> None:
    proc = _run_script("smoke_workbench_v2.py")
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK: workbench v2 inspect + apply_change_set round-trip" in proc.stdout


def test_build_sidecar_freezes_linux_binaries() -> None:
    proc = _run_script("build_sidecar.py")
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    sidecar = REPO_ROOT / "apps" / "desktop" / "sidecar"
    for name in ("ltagent-engine", "ltagent-mcp"):
        path = sidecar / f"{name}-x86_64-unknown-linux-gnu"
        assert path.is_file(), f"missing frozen sidecar {path}"
        assert path.read_bytes()[:4] == b"\x7fELF"


def test_tauri_bundle_uses_frozen_sidecars() -> None:
    config_path = REPO_ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["bundle"]["active"] is True
    assert config["bundle"]["targets"] == ["deb", "appimage"]
    assert config["bundle"]["externalBin"] == [
        "../sidecar/ltagent-engine",
        "../sidecar/ltagent-mcp",
    ]
