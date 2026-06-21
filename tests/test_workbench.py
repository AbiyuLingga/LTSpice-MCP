"""Tests for the versioned local-first workbench project contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent.workbench import (
    ERR_CHANGESET_CONFLICT,
    ERR_DOCUMENT_INVALID,
    ERR_PROJECT_VERSION_UNSUPPORTED,
    WorkbenchError,
    apply_change_set,
    create_workbench_project,
    migrate_workbench_project,
    open_workbench_project,
    read_document,
)


def _projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def test_create_workbench_project_materialises_versioned_layout(tmp_path: Path) -> None:
    project = create_workbench_project(_projects_root(tmp_path), "analog_lab")

    assert project.project_dir.is_dir()
    assert project.manifest.is_file()
    assert project.requirements.is_file()
    assert project.analog.is_file()
    assert project.schematic.is_file()
    assert project.digital.is_file()
    assert project.system.is_file()
    assert project.firmware.is_dir()
    assert project.verification.is_dir()
    assert project.runs.is_dir()

    manifest = json.loads(project.manifest.read_text(encoding="utf-8"))
    assert manifest == {
        "displayName": "analog_lab",
        "projectId": "analog_lab",
        "revision": 0,
        "schemaVersion": "1.0",
    }
    schematic = read_document(project.project_dir, "schematic")
    assert schematic["gridSize"] == 16
    assert schematic["nodes"] == []
    assert schematic["wires"] == []


def test_apply_change_set_replaces_allowed_document_and_bumps_revision(
    tmp_path: Path,
) -> None:
    project = create_workbench_project(_projects_root(tmp_path), "analog_lab")

    updated = apply_change_set(
        project.project_dir,
        {
            "baseRevision": 0,
            "operations": [
                {
                    "document": "requirements",
                    "type": "replace_document",
                    "value": {
                        "constraints": {"targetGain": 2},
                        "goals": ["Build a non-inverting op-amp"],
                        "schemaVersion": "1.0",
                    },
                }
            ],
            "schemaVersion": "1.0",
        },
    )

    assert updated.revision == 1
    assert updated.changed_documents == ("requirements",)
    assert read_document(project.project_dir, "requirements")["constraints"] == {"targetGain": 2}
    manifest = json.loads(project.manifest.read_text(encoding="utf-8"))
    assert manifest["revision"] == 1


def test_apply_change_set_rejects_stale_revision_without_writing(tmp_path: Path) -> None:
    project = create_workbench_project(_projects_root(tmp_path), "analog_lab")
    original = project.requirements.read_text(encoding="utf-8")

    with pytest.raises(WorkbenchError) as excinfo:
        apply_change_set(
            project.project_dir,
            {
                "baseRevision": 1,
                "operations": [],
                "schemaVersion": "1.0",
            },
        )

    assert excinfo.value.code == ERR_CHANGESET_CONFLICT
    assert project.requirements.read_text(encoding="utf-8") == original


def test_apply_change_set_rejects_unknown_document_before_writing(tmp_path: Path) -> None:
    project = create_workbench_project(_projects_root(tmp_path), "analog_lab")

    with pytest.raises(WorkbenchError) as excinfo:
        apply_change_set(
            project.project_dir,
            {
                "baseRevision": 0,
                "operations": [
                    {
                        "document": "../../outside",
                        "type": "replace_document",
                        "value": {},
                    }
                ],
                "schemaVersion": "1.0",
            },
        )

    assert excinfo.value.code == ERR_DOCUMENT_INVALID


def test_migrate_workbench_project_upgrades_the_first_manifest_version(
    tmp_path: Path,
) -> None:
    root = _projects_root(tmp_path)
    project_dir = root / "legacy_lab"
    project_dir.mkdir()
    (project_dir / "hardware.project.json").write_text(
        json.dumps({"projectId": "legacy_lab", "schemaVersion": "0.1"}),
        encoding="utf-8",
    )

    migrated = migrate_workbench_project(project_dir)

    assert migrated.revision == 0
    manifest = json.loads((project_dir / "hardware.project.json").read_text(encoding="utf-8"))
    assert manifest == {
        "displayName": "legacy_lab",
        "projectId": "legacy_lab",
        "revision": 0,
        "schemaVersion": "1.0",
    }
    assert open_workbench_project(project_dir) == migrated.project


def test_open_workbench_project_rejects_a_future_manifest_version(tmp_path: Path) -> None:
    project = create_workbench_project(_projects_root(tmp_path), "analog_lab")
    project.manifest.write_text(
        json.dumps(
            {
                "displayName": "analog_lab",
                "projectId": "analog_lab",
                "revision": 0,
                "schemaVersion": "99.0",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkbenchError) as excinfo:
        open_workbench_project(project.project_dir)

    assert excinfo.value.code == ERR_PROJECT_VERSION_UNSUPPORTED


def test_open_workbench_project_recovers_an_interrupted_change_set(tmp_path: Path) -> None:
    project = create_workbench_project(_projects_root(tmp_path), "analog_lab")
    original_manifest = project.manifest.read_text(encoding="utf-8")
    original_requirements = project.requirements.read_text(encoding="utf-8")
    project.paths.transaction.write_text(
        json.dumps(
            {
                "before": {"requirements": original_requirements},
                "manifest": original_manifest,
                "schemaVersion": "1.0",
            }
        ),
        encoding="utf-8",
    )
    project.requirements.write_text(
        json.dumps({"goals": ["partial"], "schemaVersion": "1.0"}),
        encoding="utf-8",
    )
    project.manifest.write_text(
        json.dumps(
            {
                "displayName": "analog_lab",
                "projectId": "analog_lab",
                "revision": 1,
                "schemaVersion": "1.0",
            }
        ),
        encoding="utf-8",
    )

    recovered = open_workbench_project(project.project_dir)

    assert recovered.revision == 0
    assert project.requirements.read_text(encoding="utf-8") == original_requirements
    assert not project.paths.transaction.exists()
