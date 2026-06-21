#!/usr/bin/env python3
"""Build frozen Linux sidecars and Python distribution artifacts."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = REPO_ROOT / "build" / "sidecars"
DIST = REPO_ROOT / "dist"
SIDECAR_DIR = REPO_ROOT / "apps" / "desktop" / "sidecar"
TAURI_CONFIG = REPO_ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
ENTRY_POINTS = {
    "ltagent-engine": "ltagent.engine_server",
    "ltagent-mcp": "ltagent.mcp_server",
}


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def _build_python_distribution() -> None:
    DIST.mkdir(exist_ok=True)
    _run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(DIST)])


def _freeze(name: str, module: str) -> Path:
    entry_dir = BUILD_ROOT / "entrypoints"
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry = entry_dir / f"{name.replace('-', '_')}.py"
    entry.write_text(
        f"from {module} import main\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    frozen_dist = BUILD_ROOT / "dist"
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--name",
            name,
            "--distpath",
            str(frozen_dist),
            "--workpath",
            str(BUILD_ROOT / "work" / name),
            "--specpath",
            str(BUILD_ROOT / "spec"),
            "--collect-data",
            "ltagent",
            "--collect-all",
            "keyring",
            *(["--collect-all", "mcp"] if name == "ltagent-mcp" else []),
            str(entry),
        ]
    )
    return frozen_dist / name


def _stage_sidecars() -> list[Path]:
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for name, module in ENTRY_POINTS.items():
        frozen = _freeze(name, module)
        destination = SIDECAR_DIR / f"{name}-{TARGET_TRIPLE}"
        shutil.copy2(frozen, destination)
        destination.chmod(0o755)
        staged.append(destination)
    for obsolete in ("ltagent", "ltagent-engine", "ltagent-mcp"):
        path = SIDECAR_DIR / obsolete
        if path.is_file():
            path.unlink()
    return staged


def _validate_tauri_config(staged: list[Path]) -> None:
    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    bundle = config.get("bundle", {})
    if bundle.get("active") is not True:
        raise RuntimeError("Tauri bundle.active must be true")
    if bundle.get("targets") != ["deb", "appimage"]:
        raise RuntimeError("Tauri bundle targets must be deb and appimage")
    expected = {f"{name}-{TARGET_TRIPLE}" for name in ENTRY_POINTS}
    if {path.name for path in staged} != expected:
        raise RuntimeError("frozen sidecar set does not match Tauri externalBin")


def main() -> int:
    _build_python_distribution()
    staged = _stage_sidecars()
    _validate_tauri_config(staged)
    for path in staged:
        _run([str(path), "--help"])
    print(json.dumps({"sidecars": [str(path) for path in staged]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
