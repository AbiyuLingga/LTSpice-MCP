#!/usr/bin/env python3
"""Phase 10 smoke test: workbench v2 round-trip.

Exercises the v2 design service through its MCP tool surface:
inspect an empty project, apply a typed change set, and inspect
the result. Verifies the revision bumps and the on-disk graph
gains the new component.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

# Reuse the workbench v2 tool handlers and document constants.
from ltagent.mcp_workbench_tools import (
    tool_wb_v2_apply_change_set,
    tool_wb_v2_inspect_project,
)
from ltagent.workbench_v2 import (
    FILE_ANALOG_GRAPH,
    FILE_DIGITAL,
    FILE_MANIFEST,
    FILE_REQUIREMENTS,
    FILE_SCHEMATIC_VIEW,
    FILE_SYSTEM,
    PROJECT_SCHEMA_VERSION,
    SchematicView,
    SystemSpec,
)


def _seed(project_dir: Path, project_id: str) -> None:
    (project_dir / "design" / "analog").mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "schematic").mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "digital").mkdir(parents=True, exist_ok=True)
    (project_dir / ".workbench" / "history").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "projectId": project_id,
        "displayName": project_id,
        "revision": 0,
    }
    (project_dir / FILE_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (project_dir / FILE_REQUIREMENTS).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "text": "",
                "constraints": {},
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    graph = {
        "schemaVersion": "0.2",
        "projectId": project_id,
        "topology": "",
        "components": {},
        "nets": {"0": {"name": "0", "type": "ground"}},
        "analyses": [],
        "measurements": [],
        "directives": [],
    }
    (project_dir / FILE_ANALOG_GRAPH).write_text(json.dumps(graph), encoding="utf-8")
    (project_dir / FILE_SCHEMATIC_VIEW).write_text(
        SchematicView().model_dump_json(), encoding="utf-8"
    )
    (project_dir / FILE_DIGITAL).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "design": {}, "notes": ""}),
        encoding="utf-8",
    )
    (project_dir / FILE_SYSTEM).write_text(SystemSpec().model_dump_json(), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "projects"
        project_id = "smoke_v2"
        project_dir = root / project_id
        _seed(project_dir, project_id)

        empty = tool_wb_v2_inspect_project(project_id, projects_root=str(root))
        assert empty["success"] is True
        assert empty["data"]["documents"]["manifest"]["revision"] == 0

        change_set = {
            "schemaVersion": "2.0",
            "baseRevision": 0,
            "actor": "smoke",
            "clientRequestId": "smoke_1",
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "componentId": "R1",
                    "kind": "resistor",
                    "value": "1k",
                    "pins": {"p1": "vin", "p2": "vout"},
                }
            ],
        }
        applied = tool_wb_v2_apply_change_set(
            project_id, change_set, projects_root=str(root)
        )
        assert applied["success"] is True, applied
        assert applied["data"]["revision"] == 1
        assert "R1" in applied["data"]["documents"]["analog"]["components"]

        after = tool_wb_v2_inspect_project(project_id, projects_root=str(root))
        assert after["success"] is True
        assert after["data"]["documents"]["manifest"]["revision"] == 1
        assert "R1" in after["data"]["documents"]["analog"]["components"]

    print("OK: workbench v2 inspect + apply_change_set round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
