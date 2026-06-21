from __future__ import annotations

import json
from pathlib import Path

from ltagent.config import Config
from ltagent.live.project import create_live_project
from ltagent.live.sim_loop import run_project_and_verify
from ltagent.runner import RunResult


def test_run_project_and_verify_writes_structured_report(tmp_path: Path) -> None:
    paths = create_live_project(tmp_path, "demo")
    paths.cir.write_text("R1 in out 1k\n.end\n", encoding="utf-8")
    log_path = paths.project_dir / "circuit.log"
    log_path.write_text(
        "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\nElapsed time: 0.1 seconds\n",
        encoding="utf-8",
    )

    def fake_run(_request) -> RunResult:
        return RunResult(
            success=True,
            command="run",
            message="ok",
            data={
                "logPath": str(log_path),
                "durationMs": 5,
                "exitCode": 0,
            },
        )

    result = run_project_and_verify(
        paths.project_dir,
        Config(),
        [
            {
                "name": "vout_max",
                "kind": "near_target",
                "target": 0.7071,
                "tolerancePercent": 1.0,
            }
        ],
        projects_root=tmp_path,
        run_func=fake_run,
    )

    assert result["verification"]["overallPassed"] is True
    persisted = json.loads(paths.verification.read_text(encoding="utf-8"))
    assert persisted == result


def test_run_project_and_verify_requires_checks(tmp_path: Path) -> None:
    paths = create_live_project(tmp_path, "demo")
    paths.cir.write_text(".end\n", encoding="utf-8")

    result = run_project_and_verify(
        paths.project_dir,
        Config(),
        [],
        projects_root=tmp_path,
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "VERIFY_TARGETS_MISSING"
