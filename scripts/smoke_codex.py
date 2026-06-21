#!/usr/bin/env python3
"""Phase 10 smoke test: codex install/uninstall/doctor.

Exercises the ``ltagent codex`` CLI against a fresh config path
and asserts the structured output matches expectations. Returns
exit code 0 on success, 1 on failure.

This is the smoke hook CI calls after ``pip install -e .``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CLI = ["ltagent"] if shutil.which("ltagent") else [sys.executable, "-m", "ltagent.cli"]


def _run(args: list[str], *, expect_zero: bool = True) -> dict[str, Any]:
    proc = subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if expect_zero and proc.returncode != 0:
        sys.stderr.write(
            f"FAIL: {' '.join([*CLI, *args])}\nstdout: {proc.stdout}\nstderr: {proc.stderr}\n"
        )
        raise SystemExit(1)
    return json.loads(proc.stdout)  # type: ignore[no-any-return]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        config = Path(td) / "codex.toml"

        dry = _run(["codex", "install", "--dry-run", "--config", str(config)])
        assert dry["success"] is True
        assert dry["data"]["dryRun"] is True
        assert not config.exists(), "dry-run should not write the file"

        installed = _run(["codex", "install", "--config", str(config)])
        assert installed["success"] is True
        assert installed["data"]["created"] is True
        assert config.is_file(), "install should write the file"
        text = config.read_text(encoding="utf-8")
        assert "[mcp_servers.ltagent]" in text
        assert "ltagent-mcp" in text

        doctor = _run(["codex", "doctor", "--config", str(config)])
        # doctor reports issues + the SDK status. The MCP SDK is
        # optional; the smoke test only cares that the server
        # entry is in place.
        assert Path(doctor["data"]["server"]["command"]).name == "ltagent-mcp"
        assert doctor["data"]["issues"] == []

        uninstalled = _run(["codex", "uninstall", "--config", str(config)])
        assert uninstalled["success"] is True
        assert uninstalled["data"]["removed"] is True
        assert not config.exists(), "uninstall should remove the file"

        doctor2 = _run(["codex", "doctor", "--config", str(config)], expect_zero=False)
        assert doctor2["data"]["server"] is None
        codes = {issue["code"] for issue in doctor2["data"]["issues"]}
        assert "CODEX_SERVER_NOT_INSTALLED" in codes

    print("OK: codex install/doctor/uninstall round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
