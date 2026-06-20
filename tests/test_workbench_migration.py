"""Tests for the staged 1.0 -> 2.0 workbench project migration.

The migration is the workbench v2 entry point that upgrades an
existing 1.0 project. These tests cover:

* Happy path: a populated 1.0 project migrates in place, the v2
  manifest is written, the legacy analog file is removed, and the
  backup + manifest survive.
* Round-trip: a 1.0 project that migrates to v2 and is then rolled
  back opens cleanly with the v1 surface (``workbench.py``).
* Failure path: a 1.0 document that fails validation triggers a
  rollback; the v1 source files are byte-identical to their
  pre-migration state.
* Open after migration: the v2 manifest can be re-opened with the
  v2 contract (workbench_v2.HardwareProject).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent.workbench import (
    PROJECT_SCHEMA_VERSION as V1_PROJECT_SCHEMA_VERSION,
)
from ltagent.workbench import (
    create_workbench_project,
    open_workbench_project,
)
from ltagent.workbench_migration import (
    MIGRATION_BACKUP_DIRNAME,
    MIGRATION_MANIFEST_NAME,
    V1_FILE_ANALOG,
    migrate_workbench_project_to_v2,
    rollback_workbench_project_to_v1,
)
from ltagent.workbench_v2 import (
    FILE_ANALOG_GRAPH,
    HardwareProject,
    Requirements,
    SchematicView,
)
from ltagent.workbench_v2 import (
    PROJECT_SCHEMA_VERSION as V2_PROJECT_SCHEMA_VERSION,
)


def _projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _seed_v1_project(
    tmp_path: Path,
    *,
    project_id: str = "analog_lab",
    analog: dict | None = None,
    schematic: dict | None = None,
) -> Path:
    """Create a 1.0 workbench project and return its project dir."""
    project = create_workbench_project(_projects_root(tmp_path), project_id)
    if analog is not None:
        project.analog.write_text(json.dumps(analog), encoding="utf-8")
    if schematic is not None:
        project.schematic.write_text(json.dumps(schematic), encoding="utf-8")
    return project.project_dir


def test_migration_happy_path_creates_v2_manifest(tmp_path: Path) -> None:
    project_dir = _seed_v1_project(
        tmp_path,
        schematic={
            "schemaVersion": "1.0",
            "gridSize": 16,
            "nodes": [
                {"id": "r1", "kind": "resistor", "x": 32, "y": 32},
                {"id": "u1", "kind": "opamp", "x": 128, "y": 32},
            ],
            "wires": [],
        },
    )
    result = migrate_workbench_project_to_v2(project_dir)
    assert result.project_id == "analog_lab"
    assert result.v2_manifest_path.is_file()
    v2 = HardwareProject.model_validate(
        json.loads(result.v2_manifest_path.read_text(encoding="utf-8"))
    )
    assert v2.schemaVersion == V2_PROJECT_SCHEMA_VERSION
    assert v2.projectId == "analog_lab"
    assert v2.revision >= 0
    # The legacy analog file is gone, the new analog graph is in place.
    assert not (project_dir / V1_FILE_ANALOG).exists()
    assert (project_dir / FILE_ANALOG_GRAPH).is_file()
    # The v1 backup survives.
    assert result.backup_dir.is_dir()
    assert (result.backup_dir / MIGRATION_MANIFEST_NAME).is_file()
    backup_manifest = json.loads(
        (result.backup_dir / MIGRATION_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert backup_manifest["fromSchemaVersion"] == V1_PROJECT_SCHEMA_VERSION
    assert backup_manifest["toSchemaVersion"] == V2_PROJECT_SCHEMA_VERSION


def test_migration_converts_schematic_nodes_to_symbols(tmp_path: Path) -> None:
    project_dir = _seed_v1_project(
        tmp_path,
        schematic={
            "schemaVersion": "1.0",
            "gridSize": 16,
            "nodes": [
                {"id": "r1", "kind": "resistor", "x": 32, "y": 32},
                {"id": "gnd", "kind": "gnd", "x": 0, "y": 64},
            ],
            "wires": [],
        },
    )
    migrate_workbench_project_to_v2(project_dir)
    v2_schematic = SchematicView.model_validate(
        json.loads(
            (project_dir / "design/schematic/main.view.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert len(v2_schematic.symbols) == 2
    assert v2_schematic.symbols[0].id == "r1"
    assert v2_schematic.symbols[1].id == "gnd"
    # The new field defaults are honoured.
    assert v2_schematic.netLabels == []
    assert v2_schematic.viewport is None


def test_migration_round_trip_back_to_v1(tmp_path: Path) -> None:
    project_dir = _seed_v1_project(tmp_path)
    original_manifest = json.loads(
        (project_dir / "hardware.project.json").read_text(encoding="utf-8")
    )
    original_requirements = (project_dir / "design/requirements.json").read_text(
        encoding="utf-8"
    )
    result = migrate_workbench_project_to_v2(project_dir)
    rollback_workbench_project_to_v1(project_dir, result.backup_dir)
    restored = open_workbench_project(project_dir)
    assert restored.revision == 0
    # The v1 surface still recognises the project.
    assert restored.project_id == "analog_lab"
    manifest = json.loads(
        (project_dir / "hardware.project.json").read_text(encoding="utf-8")
    )
    assert manifest["schemaVersion"] == V1_PROJECT_SCHEMA_VERSION
    assert manifest == original_manifest
    assert (project_dir / "design/requirements.json").read_text(
        encoding="utf-8"
    ) == original_requirements
    # The v2 graph file is gone after rollback.
    assert not (project_dir / FILE_ANALOG_GRAPH).exists()
    # The v1 analog file is back.
    assert (project_dir / V1_FILE_ANALOG).is_file()


def test_migration_failure_leaves_source_unchanged(tmp_path: Path) -> None:
    """A 1.0 document that fails conversion must trigger a rollback."""
    project_dir = _seed_v1_project(
        tmp_path,
        # The schematic has a non-list 'nodes' field, which the v2
        # converter rejects. The migration should roll back and the
        # v1 source files must be byte-identical to their pre-migration
        # state.
        schematic={
            "schemaVersion": "1.0",
            "gridSize": 16,
            "nodes": "not-a-list",
            "wires": [],
        },
    )
    pre_migration = {
        path.relative_to(project_dir).as_posix(): path.read_bytes()
        for path in project_dir.rglob("*")
        if path.is_file()
        and not path.name.endswith(".tmp")
        and MIGRATION_BACKUP_DIRNAME not in path.parts
    }
    from ltagent.workbench import WorkbenchError

    with pytest.raises(WorkbenchError):
        migrate_workbench_project_to_v2(project_dir)
    # The project must still be a valid v1 project after the failure.
    project = open_workbench_project(project_dir)
    assert project.project_id == "analog_lab"
    # The 1.0 source files are byte-identical to their pre-migration
    # state.
    for relative, original in pre_migration.items():
        current = (project_dir / relative).read_bytes()
        assert current == original, f"{relative} changed during failed migration"


def test_migration_writes_v2_requirements(tmp_path: Path) -> None:
    project_dir = _seed_v1_project(tmp_path)
    (project_dir / "design/requirements.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "text": "make RC low-pass 1kHz",
                "constraints": {"cutoffHz": 1000},
                "goals": ["attenuate 40 dB/dec"],
            }
        ),
        encoding="utf-8",
    )
    migrate_workbench_project_to_v2(project_dir)
    v2_requirements = Requirements.model_validate(
        json.loads(
            (project_dir / "design/requirements.json").read_text(encoding="utf-8")
        )
    )
    assert v2_requirements.text == "make RC low-pass 1kHz"
    assert v2_requirements.constraints["cutoffHz"] == 1000


def test_migration_rejects_non_v1_manifest(tmp_path: Path) -> None:
    project_dir = _seed_v1_project(tmp_path)
    # Hand-edit the manifest to a future version; the v1 surface
    # must refuse to open it, which the migrator surfaces as a
    # migration-invalid-source error.
    manifest = project_dir / "hardware.project.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schemaVersion"] = "9.9"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    from ltagent.workbench import WorkbenchError

    with pytest.raises(WorkbenchError):
        migrate_workbench_project_to_v2(project_dir)


def test_migration_persists_digital_and_system_documents(tmp_path: Path) -> None:
    project_dir = _seed_v1_project(tmp_path)
    (project_dir / "design/digital/main.digital.json").write_text(
        json.dumps({"schemaVersion": "1.0", "modules": [{"name": "m1"}]}),
        encoding="utf-8",
    )
    (project_dir / "design/system.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "blocks": [{"id": "a"}],
                "connections": [],
                "clockHz": 1000,
            }
        ),
        encoding="utf-8",
    )
    migrate_workbench_project_to_v2(project_dir)
    # The v1 surface (workbench.read_document) only accepts schema
    # 1.0, so the v2 documents are read directly from disk.
    digital = json.loads(
        (project_dir / "design/digital/main.digital.json").read_text(encoding="utf-8")
    )
    system = json.loads(
        (project_dir / "design/system.json").read_text(encoding="utf-8")
    )
    assert digital["schemaVersion"] == V2_PROJECT_SCHEMA_VERSION
    assert system["schemaVersion"] == V2_PROJECT_SCHEMA_VERSION
    assert digital["design"]["modules"] == [{"name": "m1"}]
    assert system["clockHz"] == 1000
