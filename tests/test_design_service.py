"""Tests for the Workbench v2 design service.

Covers the typed ChangeSet operations, the revision guard, the
undo / redo stack, the validation-before-write contract, and the
structured error surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent.design_service import (
    ChangeSet,
    DesignService,
    WorkbenchV2Error,
)
from ltagent.workbench_v2 import (
    FILE_MANIFEST,
    PROJECT_SCHEMA_VERSION,
    HardwareProject,
    SchematicView,
)


def _projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    return root


def _seed_v2_project(tmp_path: Path, project_id: str = "rc_lab") -> Path:
    """Create a v2 project on disk directly (no migration needed)."""
    from ltagent.workbench_v2 import (
        FILE_ANALOG_GRAPH,
        FILE_DIGITAL,
        FILE_REQUIREMENTS,
        FILE_SCHEMATIC_VIEW,
        FILE_SYSTEM,
    )

    root = _projects_root(tmp_path)
    project_dir = root / project_id
    if project_dir.exists():
        import shutil

        shutil.rmtree(project_dir)
    (project_dir / "design" / "analog").mkdir(parents=True)
    (project_dir / "design" / "schematic").mkdir(parents=True)
    (project_dir / "design" / "digital").mkdir(parents=True)
    (project_dir / ".workbench" / "history").mkdir(parents=True)
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
    (project_dir / FILE_ANALOG_GRAPH).write_text(
        json.dumps(
            {
                "schemaVersion": "0.2",
                "projectId": project_id,
                "topology": "",
                "components": {},
                "nets": {},
                "analyses": [],
                "measurements": [],
                "directives": [],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_SCHEMATIC_VIEW).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "gridSize": 16,
                "viewport": None,
                "symbols": [],
                "wires": [],
                "netLabels": [],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_DIGITAL).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "design": {}, "notes": ""}),
        encoding="utf-8",
    )
    (project_dir / FILE_SYSTEM).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "blocks": [], "connections": []}),
        encoding="utf-8",
    )
    return project_dir


def test_change_set_parses_minimal_payload() -> None:
    cs = ChangeSet.model_validate(
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "componentId": "R1",
                    "kind": "resistor",
                    "pins": {"p1": "vin", "p2": "vout"},
                    "value": "1k",
                }
            ],
        }
    )
    assert cs.baseRevision == 0
    assert cs.operations[0]["type"] == "add_component"


def test_add_component_persists_and_bumps_revision(tmp_path: Path) -> None:
    project_dir = _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    cs = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "baseRevision": 0,
        "actor": "user",
        "operations": [
            {
                "document": "analog",
                "type": "add_component",
                "componentId": "R1",
                "kind": "resistor",
                "pins": {"p1": "vin", "p2": "vout"},
                "value": "1k",
            }
        ],
    }
    result = service.apply_change_set("rc_lab", cs)
    assert result.revision == 1
    assert result.affected_component_ids == ("R1",)
    analog = json.loads((project_dir / "design/analog/main.graph.json").read_text(encoding="utf-8"))
    assert "R1" in analog["components"]
    manifest = HardwareProject.model_validate(
        json.loads((project_dir / FILE_MANIFEST).read_text(encoding="utf-8"))
    )
    assert manifest.revision == 1


def test_revision_conflict_raises(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    cs = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "baseRevision": 99,
        "operations": [
            {
                "document": "analog",
                "type": "add_component",
                "componentId": "R1",
                "kind": "resistor",
                "pins": {"p1": "vin", "p2": "vout"},
            }
        ],
    }
    with pytest.raises(WorkbenchV2Error) as captured:
        service.apply_change_set("rc_lab", cs)
    assert "conflict" in captured.value.code.lower()


def test_validation_before_write_rejects_invalid_kind(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    cs = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "baseRevision": 0,
        "operations": [
            {
                "document": "analog",
                "type": "add_component",
                "componentId": "X1",
                "kind": "rocket",
                "pins": {"p1": "a", "p2": "b"},
            }
        ],
    }
    with pytest.raises(WorkbenchV2Error) as captured:
        service.apply_change_set("rc_lab", cs)
    assert "operation" in captured.value.code.lower() or "valid" in captured.value.message.lower()


def test_layout_ops_cover_place_move_rotate_grid(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "schematic",
                    "type": "place_node",
                    "symbolId": "r1",
                    "kind": "resistor",
                    "x": 16,
                    "y": 32,
                }
            ],
        },
    )
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 1,
            "operations": [
                {"document": "schematic", "type": "move_node", "symbolId": "r1", "x": 64, "y": 64},
                {"document": "schematic", "type": "rotate_node", "symbolId": "r1", "rotation": 90},
                {"document": "schematic", "type": "set_grid_size", "gridSize": 20},
            ],
        },
    )
    view = SchematicView.model_validate(
        json.loads(
            (
                tmp_path / "projects" / "rc_lab" / "design" / "schematic" / "main.view.json"
            ).read_text(encoding="utf-8")
        )
    )
    assert view.symbols[0].x == 64
    assert view.symbols[0].rotation == 90
    assert view.gridSize == 20


def test_undo_and_redo_round_trip(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "componentId": "R1",
                    "kind": "resistor",
                    "pins": {"p1": "vin", "p2": "vout"},
                }
            ],
        },
    )
    undo_result = service.undo("rc_lab")
    assert undo_result is not None
    assert undo_result.revision == 0
    analog = json.loads(
        (tmp_path / "projects" / "rc_lab" / "design" / "analog" / "main.graph.json").read_text(
            encoding="utf-8"
        )
    )
    assert "R1" not in analog["components"]
    redo_result = service.redo("rc_lab")
    assert redo_result is not None
    assert redo_result.revision == 1


def test_set_wire_route_persists(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "schematic",
                    "type": "set_wire_route",
                    "wireId": "w1",
                    "points": [[0, 0], [10, 0]],
                    "net": "vcc",
                }
            ],
        },
    )
    view = SchematicView.model_validate(
        json.loads(
            (
                tmp_path / "projects" / "rc_lab" / "design" / "schematic" / "main.view.json"
            ).read_text(encoding="utf-8")
        )
    )
    assert len(view.wires) == 1
    assert view.wires[0].net == "vcc"


def test_update_and_delete_schematic_items(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "schematic",
                    "type": "place_node",
                    "symbolId": "r1",
                    "kind": "resistor",
                    "x": 16,
                    "y": 32,
                },
                {
                    "document": "schematic",
                    "type": "set_wire_route",
                    "wireId": "w1",
                    "points": [[0, 0], [16, 0]],
                },
            ],
        },
    )
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 1,
            "operations": [
                {
                    "document": "schematic",
                    "type": "set_node_properties",
                    "symbolId": "r1",
                    "label": "Rload",
                    "properties": {"value": "1k"},
                },
                {"document": "schematic", "type": "remove_wire", "wireId": "w1"},
            ],
        },
    )
    updated = service.read_document("rc_lab", "schematic")
    assert updated["symbols"][0]["label"] == "Rload"  # type: ignore[index]
    assert updated["symbols"][0]["properties"] == {"value": "1k"}  # type: ignore[index]
    assert updated["wires"] == []

    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 2,
            "operations": [
                {"document": "schematic", "type": "delete_node", "symbolId": "r1"},
            ],
        },
    )
    assert service.read_document("rc_lab", "schematic")["symbols"] == []


def test_replace_document_compatibility_path(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    new_requirements = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "text": "make RC low-pass 1kHz",
        "constraints": {"cutoffHz": 1000},
        "goals": [],
    }
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "requirements",
                    "type": "replace_document",
                    "value": new_requirements,
                }
            ],
        },
    )
    on_disk = json.loads(
        (tmp_path / "projects" / "rc_lab" / "design" / "requirements.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["text"] == "make RC low-pass 1kHz"


def test_connect_pin_adds_net_to_ground_when_needed(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "componentId": "R1",
                    "kind": "resistor",
                    "pins": {"p1": "vin", "p2": "vout"},
                }
            ],
        },
    )
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 1,
            "operations": [
                {
                    "document": "analog",
                    "type": "connect_pin",
                    "componentId": "R1",
                    "pin": "p1",
                    "net": "0",
                },
            ],
        },
    )
    analog = json.loads(
        (tmp_path / "projects" / "rc_lab" / "design" / "analog" / "main.graph.json").read_text(
            encoding="utf-8"
        )
    )
    assert "0" in analog["nets"]
    assert analog["nets"]["0"]["type"] == "ground"


def test_history_log_appended(tmp_path: Path) -> None:
    _seed_v2_project(tmp_path)
    service = DesignService(projects_root=str(_projects_root(tmp_path)))
    service.apply_change_set(
        "rc_lab",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "baseRevision": 0,
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "componentId": "R1",
                    "kind": "resistor",
                    "pins": {"p1": "a", "p2": "b"},
                }
            ],
        },
    )
    history = tmp_path / "projects" / "rc_lab" / ".workbench" / "history" / "changes.jsonl"
    assert history.is_file()
    line = history.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["revision"] == 1
    assert record["changedDocuments"] == ["analog"]
