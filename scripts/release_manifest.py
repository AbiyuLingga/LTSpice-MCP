#!/usr/bin/env python3
"""Write release checksums and minimal SBOM files using the standard library."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "dist" / "release"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_artifacts(extra: list[Path]) -> list[Path]:
    patterns = [
        "dist/*.whl",
        "dist/*.tar.gz",
        "apps/desktop/sidecar/*-x86_64-unknown-linux-gnu",
        "apps/desktop/src-tauri/target/release/bundle/deb/*.deb",
        "apps/desktop/src-tauri/target/release/bundle/appimage/*.AppImage",
    ]
    paths = [path for pattern in patterns for path in REPO_ROOT.glob(pattern)]
    paths.extend(extra)
    return sorted({path.resolve() for path in paths if path.is_file()})


def _python_sbom() -> list[dict[str, str]]:
    packages = []
    for dist in importlib.metadata.distributions():
        meta: Any = dist.metadata
        packages.append(
            {
                "name": meta.get("Name", ""),
                "version": dist.version,
                "license": meta.get("License", ""),
            }
        )
    return sorted(packages, key=lambda item: item["name"].lower())


def _npm_sbom() -> list[dict[str, str]]:
    lockfile = REPO_ROOT / "apps" / "desktop" / "package-lock.json"
    if not lockfile.exists():
        return []
    data = json.loads(lockfile.read_text(encoding="utf-8"))
    packages = []
    for name, package in data.get("packages", {}).items():
        if not name or "version" not in package:
            continue
        packages.append(
            {
                "name": name.removeprefix("node_modules/"),
                "version": str(package["version"]),
                "license": str(package.get("license", "")),
            }
        )
    return sorted(packages, key=lambda item: item["name"].lower())


def _cargo_sbom() -> list[dict[str, str]]:
    lockfile = REPO_ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.lock"
    if not lockfile.exists():
        return []
    data = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    return sorted(
        [{"name": pkg["name"], "version": pkg["version"], "source": pkg.get("source", "")}
         for pkg in data.get("package", [])],
        key=lambda item: item["name"].lower(),
    )


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--allow-missing-bundles", action="store_true")
    args = parser.parse_args(argv)

    artifacts = _discover_artifacts(args.artifact)
    suffixes = {path.suffix for path in artifacts}
    missing_bundle = ".deb" not in suffixes or ".AppImage" not in suffixes
    if missing_bundle and not args.allow_missing_bundles:
        print("missing .deb or AppImage; build desktop bundles first", file=sys.stderr)
        return 2
    if not artifacts:
        print("no release artifacts found", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    artifact_rows = [
        {
            "path": str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in artifacts
    ]
    (args.out / "SHA256SUMS").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in artifact_rows),
        encoding="utf-8",
    )
    _write_json(args.out / "sbom-python.json", _python_sbom())
    _write_json(args.out / "sbom-npm.json", _npm_sbom())
    _write_json(args.out / "sbom-cargo.json", _cargo_sbom())
    _write_json(
        args.out / "manifest.json",
        {
            "generatedAt": datetime.now(UTC).isoformat(),
            "artifacts": artifact_rows,
            "sboms": ["sbom-python.json", "sbom-npm.json", "sbom-cargo.json"],
        },
    )
    print(json.dumps({"out": str(args.out), "artifacts": len(artifact_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
