#!/usr/bin/env python3
"""Smoke-test local Linux desktop bundles without installing system-wide."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _newest(pattern: str) -> Path | None:
    paths = list(REPO_ROOT.glob(pattern))
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def _run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 10) -> dict[str, object]:
    proc = subprocess.run(args, env=env, timeout=timeout, text=True, capture_output=True, check=False)
    return {"cmd": args, "returncode": proc.returncode, "stderr": proc.stderr[-1200:]}


def _smoke_deb(path: Path) -> list[dict[str, object]]:
    if shutil.which("dpkg-deb") is None:
        return [{"name": "deb", "skipped": "dpkg-deb not found"}]
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ltagent-deb-smoke-") as tmp:
        root = Path(tmp) / "root"
        subprocess.run(["dpkg-deb", "-x", str(path), str(root)], check=True)
        for binary, args in {
            "ltagent-engine": ["--help"],
            "ltagent-mcp": ["--check"],
        }.items():
            result = _run([str(root / "usr" / "bin" / binary), *args])
            result["name"] = binary
            results.append(result)
        desktop = root / "usr" / "bin" / "ltagent-workbench-desktop"
        results.append(_smoke_gui([str(desktop)]))
    return results


def _smoke_gui(command: list[str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    xvfb = shutil.which("xvfb-run")
    if xvfb is None:
        return {"name": "desktop-gui", "skipped": "xvfb-run not found"}
    proc = subprocess.run(
        [xvfb, "-a", "timeout", "8s", *command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    # 124 means timeout kept the app alive for the smoke window.
    ok = proc.returncode in {0, 124}
    return {"name": "desktop-gui", "returncode": proc.returncode, "ok": ok, "stderr": proc.stderr[-1200:]}


def _smoke_appimage(path: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    result = _smoke_gui([str(path)], env=env)
    result["name"] = "appimage-gui"
    return result if "skipped" in result else {**result, "env": {"APPIMAGE_EXTRACT_AND_RUN": "1"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deb", type=Path, default=None)
    parser.add_argument("--appimage", type=Path, default=None)
    args = parser.parse_args(argv)

    deb = args.deb or _newest("apps/desktop/src-tauri/target/release/bundle/deb/*.deb")
    appimage = args.appimage or _newest("apps/desktop/src-tauri/target/release/bundle/appimage/*.AppImage")
    if deb is None or appimage is None:
        print("missing .deb or AppImage; run tauri build first", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = [
        {"artifact": str(deb), "checks": _smoke_deb(deb)},
        {"artifact": str(appimage), "checks": [_smoke_appimage(appimage)]},
    ]
    print(json.dumps(results, indent=2))
    failed = [
        check
        for artifact in results
        for check in artifact["checks"]
        if "skipped" not in check and check.get("ok", check.get("returncode") == 0) is not True
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
