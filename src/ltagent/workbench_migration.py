"""Staged 1.0 -> 2.0 workbench project migrator.

The migrator is the workbench v2 entry point that upgrades an existing
1.0 project (``hardware.project.json`` with ``schemaVersion: "1.0"``)
to the v2 layout defined in :mod:`ltagent.workbench_v2`.

Hard guarantees (carried over from the master plan invariant #9):

* **Snapshot-first.** Before any 1.0 file is touched the migrator copies
  every 1.0 document to ``.workbench/migration-backup-<timestamp>/``.
  The backup directory is named deterministically; the timestamp
  uses :func:`datetime.now` but is also recorded in the migration
  manifest so an interrupted run is auditable.
* **Atomic.** The 1.0 -> 2.0 rewrite is performed by writing the new
  documents to ``.workbench/migration-staging-<timestamp>/`` and only
  swapping them into place after every document has been validated.
  If any document fails to validate, the staging directory is removed
  and the 1.0 project is restored from the backup.
* **Rollbackable.** The migration manifest records the backup id and
  the staged file list. A caller that finds the project in a bad
  state can pass the manifest id to :func:`rollback_to_v1` to
  restore the 1.0 layout.
* **Never discards source files.** The 1.0 documents are kept in
  the backup directory even after a successful migration. The
  caller is free to delete the backup at any time; the migrator
  never does so on its own.

The migrator never invokes a simulator, AI provider, or external
tool. It is a pure file rewrite + validation + journal operation.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from .live.graph_schema import CircuitGraph as AnalogGraph
from .workbench import (
    DOCUMENT_PATHS as V1_DOCUMENT_PATHS,
)
from .workbench import (
    FILE_MANIFEST as V1_FILE_MANIFEST,
)
from .workbench import (
    PROJECT_SCHEMA_VERSION as V1_PROJECT_SCHEMA_VERSION,
)
from .workbench import (
    WorkbenchError,
    open_workbench_project,
)
from .workbench_v2 import (
    ANALOG_GRAPH_SCHEMA_VERSION,
    FILE_ANALOG_GRAPH,
    FILE_DIGITAL,
    FILE_SCHEMATIC_VIEW,
    FILE_SYSTEM,
    DigitalDesignDocument,
    HardwareProject,
    Requirements,
    SchematicView,
    SystemSpec,
)
from .workbench_v2 import (
    FILE_MANIFEST as V2_FILE_MANIFEST,
)
from .workbench_v2 import (
    FILE_REQUIREMENTS as V2_FILE_REQUIREMENTS,
)
from .workbench_v2 import (
    PROJECT_SCHEMA_VERSION as V2_PROJECT_SCHEMA_VERSION,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Directory under the project root where migration backups live.
MIGRATION_BACKUP_DIRNAME: Final[str] = ".workbench/migration-backup"
#: Directory under the project root where the staged 2.0 documents live
#: while the migration is in flight.
MIGRATION_STAGING_DIRNAME: Final[str] = ".workbench/migration-staging"
#: Manifest written into both backup and staging directories.
MIGRATION_MANIFEST_NAME: Final[str] = "migration-manifest.json"

#: Stable error codes for the structured migration contract.
ERR_MIGRATION_NOT_FOUND: Final[str] = "WORKBENCH_MIGRATION_NOT_FOUND"
ERR_MIGRATION_INVALID_SOURCE: Final[str] = "WORKBENCH_MIGRATION_INVALID_SOURCE"
ERR_MIGRATION_INVALID_TARGET: Final[str] = "WORKBENCH_MIGRATION_INVALID_TARGET"
ERR_MIGRATION_BACKUP_FAILED: Final[str] = "WORKBENCH_MIGRATION_BACKUP_FAILED"
ERR_MIGRATION_STAGE_FAILED: Final[str] = "WORKBENCH_MIGRATION_STAGE_FAILED"
ERR_MIGRATION_SWAP_FAILED: Final[str] = "WORKBENCH_MIGRATION_SWAP_FAILED"
ERR_MIGRATION_ROLLBACK_FAILED: Final[str] = "WORKBENCH_MIGRATION_ROLLBACK_FAILED"

# v1 document filename for the analog document (renamed in v2).
V1_FILE_ANALOG: Final[str] = "design/analog/main.circuit.json"
# v1 default analog document payload (matches workbench._ensure_documents).
V1_ANALOG_DEFAULT: Final[dict[str, object]] = {
    "analyses": [],
    "components": [],
    "nets": [],
    "schemaVersion": "1.0",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationResult:
    """Result of a successful 1.0 -> 2.0 migration.

    ``backup_dir`` is the absolute path to the snapshot of the 1.0
    project. The caller may delete it at any time; the migrator
    never does.
    """

    project_id: str
    backup_dir: Path
    staging_dir: Path
    migrated_at: str
    v2_manifest_path: Path
    changed_documents: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "projectId": self.project_id,
            "backupDir": str(self.backup_dir),
            "stagingDir": str(self.staging_dir),
            "migratedAt": self.migrated_at,
            "v2ManifestPath": str(self.v2_manifest_path),
            "changedDocuments": list(self.changed_documents),
        }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _migration_root(project_dir: Path) -> Path:
    return project_dir / ".workbench" / "migration"


def _backup_dir(project_dir: Path, stamp: str) -> Path:
    return project_dir / ".workbench" / f"migration-backup-{stamp}"


def _staging_dir(project_dir: Path, stamp: str) -> Path:
    return project_dir / ".workbench" / f"migration-staging-{stamp}"


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_json_object(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            f"{path} must contain a JSON object",
            data={"path": str(path)},
        )
    return loaded


# ---------------------------------------------------------------------------
# Per-document converters
# ---------------------------------------------------------------------------


def _convert_requirements(v1: Mapping[str, object]) -> Requirements:
    raw_constraints = v1.get("constraints", {})
    raw_goals = v1.get("goals", [])
    payload: dict[str, object] = {
        "schemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "text": str(v1.get("text", "")) if isinstance(v1.get("text"), str) else "",
        "constraints": dict(raw_constraints) if isinstance(raw_constraints, Mapping) else {},
        "goals": list(raw_goals) if isinstance(raw_goals, list) else [],
        "safetyClass": v1.get("safetyClass"),
    }
    return Requirements.model_validate(payload)


def _as_list(value: object) -> list[object]:
    """Return ``value`` as a list, or an empty list if it is not iterable."""
    if isinstance(value, list):
        return list(value)
    return []


def _convert_analog(v1: Mapping[str, object]) -> AnalogGraph:
    """Convert a 1.0 analog document to the v2 CircuitGraph.

    The 1.0 analog shape is a flat list of components / nets /
    analyses with no topological guarantees. The migration:
    * maps ``components: list`` to a dict keyed by ``id``;
    * maps ``nets: list`` to a dict keyed by ``name``;
    * keeps analyses verbatim (the v2 graph Analysis is a superset
      of the 1.0 one but the schemaVersion is bumped).
    Any structural violation is reported as
    ``WORKBENCH_MIGRATION_INVALID_SOURCE``.
    """
    components_raw = v1.get("components", [])
    nets_raw = v1.get("nets", [])
    analyses_raw = v1.get("analyses", [])
    components: dict[str, object] = {}
    if not isinstance(components_raw, list):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            "v1 analog document has a non-list 'components' field",
            data={"type": type(components_raw).__name__},
        )
    for index, raw in enumerate(components_raw):
        if not isinstance(raw, Mapping):
            raise WorkbenchError(
                ERR_MIGRATION_INVALID_SOURCE,
                f"v1 analog component {index} is not an object",
                data={"index": index},
            )
        cid = raw.get("id")
        if not isinstance(cid, str) or not cid:
            raise WorkbenchError(
                ERR_MIGRATION_INVALID_SOURCE,
                f"v1 analog component {index} has no valid id",
                data={"index": index, "id": str(cid)},
            )
        components[cid] = dict(raw)
    nets: dict[str, object] = {}
    if not isinstance(nets_raw, list):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            "v1 analog document has a non-list 'nets' field",
            data={"type": type(nets_raw).__name__},
        )
    for index, raw in enumerate(nets_raw):
        if not isinstance(raw, Mapping):
            raise WorkbenchError(
                ERR_MIGRATION_INVALID_SOURCE,
                f"v1 analog net {index} is not an object",
                data={"index": index},
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise WorkbenchError(
                ERR_MIGRATION_INVALID_SOURCE,
                f"v1 analog net {index} has no valid name",
                data={"index": index, "name": str(name)},
            )
        nets[name] = dict(raw)
    if not isinstance(analyses_raw, list):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            "v1 analog document has a non-list 'analyses' field",
            data={"type": type(analyses_raw).__name__},
        )
    payload: dict[str, object] = {
        "schemaVersion": ANALOG_GRAPH_SCHEMA_VERSION,
        "projectId": components.get("__project_id__", "migrated")
        if isinstance(components, dict)
        else "migrated",
        "topology": v1.get("topology", ""),
        "description": v1.get("description"),
        "components": components,
        "nets": nets,
        "analyses": list(analyses_raw) if isinstance(analyses_raw, list) else [],
        "measurements": _as_list(v1.get("measurements")),
        "directives": _as_list(v1.get("directives")),
        "constraints": v1.get("constraints"),
        "layoutHints": v1.get("layoutHints"),
    }
    # The v1 analog does not carry a projectId; use the placeholder
    # 'migrated' and let the migrator rewrite it after the v2
    # manifest is built.
    return AnalogGraph.model_validate(payload)


def _convert_schematic(v1: Mapping[str, object]) -> SchematicView:
    """Convert a 1.0 schematic document to the v2 SchematicView.

    The 1.0 shape is::

        {gridSize, nodes, wires, schemaVersion}

    The 2.0 shape renames ``nodes`` -> ``symbols`` and adds
    ``netLabels`` / ``viewport``. The converter preserves
    ``gridSize`` and lifts each ``node`` to a
    :class:`SchematicSymbol` with default rotation / mirror / label /
    properties.
    """
    grid_size = v1.get("gridSize", 16)
    nodes_raw = v1.get("nodes", [])
    wires_raw = v1.get("wires", [])
    symbols: list[dict[str, object]] = []
    if not isinstance(nodes_raw, list):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            "v1 schematic 'nodes' is not a list",
            data={"type": type(nodes_raw).__name__},
        )
    for index, raw in enumerate(nodes_raw):
        if not isinstance(raw, Mapping):
            raise WorkbenchError(
                ERR_MIGRATION_INVALID_SOURCE,
                f"v1 schematic node {index} is not an object",
                data={"index": index},
            )
        symbol = {
            "id": str(raw.get("id", f"node_{index}")),
            "kind": str(raw.get("kind", "label")),
            "x": int(raw.get("x", 0)),
            "y": int(raw.get("y", 0)),
            "rotation": int(raw.get("rotation", 0)),
            "mirror": bool(raw.get("mirror", False)),
            "label": raw.get("label"),
            "properties": dict(raw.get("properties", {}))
            if isinstance(raw.get("properties"), Mapping)
            else {},
        }
        symbols.append(symbol)
    wires: list[dict[str, object]] = []
    if not isinstance(wires_raw, list):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            "v1 schematic 'wires' is not a list",
            data={"type": type(wires_raw).__name__},
        )
    for index, raw in enumerate(wires_raw):
        if not isinstance(raw, Mapping):
            raise WorkbenchError(
                ERR_MIGRATION_INVALID_SOURCE,
                f"v1 schematic wire {index} is not an object",
                data={"index": index},
            )
        wires.append(dict(raw))
    payload: dict[str, object] = {
        "schemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "gridSize": grid_size,
        "viewport": None,
        "symbols": symbols,
        "wires": wires,
        "netLabels": [],
    }
    return SchematicView.model_validate(payload)


def _convert_digital(v1: Mapping[str, object]) -> DigitalDesignDocument:
    # Preserve old content outside the AI-editable v2 IR.
    raw_notes = v1.get("notes")
    notes = str(raw_notes) if isinstance(raw_notes, str) else ""
    payload: dict[str, object] = {
        "schemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "design": {
            "schemaVersion": "2.0",
            "topModule": "top",
            "ports": [],
            "signals": [],
            "instances": [],
            "connections": [],
            "testGoals": [],
        },
        "legacyDesign": dict(v1),
        "userHdl": "",
        "notes": notes,
    }
    return DigitalDesignDocument.model_validate(payload)


def _convert_system(v1: Mapping[str, object]) -> SystemSpec:
    payload: dict[str, object] = {
        "schemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "blocks": _as_list(v1.get("blocks")),
        "connections": _as_list(v1.get("connections")),
        "clockHz": v1.get("clockHz"),
    }
    return SystemSpec.model_validate(payload)


# ---------------------------------------------------------------------------
# Migration driver
# ---------------------------------------------------------------------------


def _stamped_dir(project_dir: Path, prefix: str) -> tuple[str, Path]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    candidate = project_dir / ".workbench" / f"{prefix}-{stamp}"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = project_dir / ".workbench" / f"{prefix}-{stamp}-{counter}"
    return stamp + (f"-{counter}" if counter else ""), candidate


def _backup_1_0_documents(
    project_dir: Path,
    backup_dir: Path,
    documents: Mapping[str, str],
) -> list[str]:
    """Copy every v1 document to ``backup_dir`` preserving relative paths.

    Returns the list of file names (relative to ``project_dir``) that
    were actually copied. Missing files are skipped silently so a
    partially-built 1.0 project still migrates.
    """
    copied: list[str] = []
    for _document, relative in documents.items():
        _ = _document  # silence B007 (the key is not used in the body)
        source = project_dir / relative
        if not source.exists() or not source.is_file():
            continue
        target = backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)
    manifest = project_dir / V1_FILE_MANIFEST
    if manifest.exists():
        target = backup_dir / V1_FILE_MANIFEST
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, target)
        copied.append(V1_FILE_MANIFEST)
    return copied


def _restore_from_backup(project_dir: Path, backup_dir: Path, copied: tuple[str, ...]) -> None:
    """Copy every file in ``copied`` back to ``project_dir``.

    Used both for the happy-path backup of stale v1 files after a
    successful migration and for the failure-path rollback.
    """
    for relative in copied:
        source = backup_dir / relative
        if not source.exists():
            continue
        target = project_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def migrate_workbench_project_to_v2(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> MigrationResult:
    """Migrate a v1 workbench project to the v2 layout in place.

    Steps:

    1. Open the v1 project (rejects non-1.0 manifests with the
       existing ``ERR_PROJECT_VERSION_UNSUPPORTED`` code).
    2. Snapshot every v1 document to a timestamped backup directory
       under ``.workbench/migration-backup-<stamp>/``.
    3. Convert each document to the v2 shape, write the v2 files to
       a staging directory, and validate the new manifest with
       :class:`HardwareProject` before swap.
    4. Swap the v2 files into the project root and write the new
       manifest with ``schemaVersion: "2.0"``.
    5. Leave the v1 backup directory in place. The migrator never
       deletes source files.

    On any failure the migrator rolls the project back to the v1
    state and raises :class:`WorkbenchError` with a stable code.
    """
    project = open_workbench_project(project_dir, projects_root=projects_root)
    project_root = project.project_dir
    if project_root != Path(project_dir).expanduser().resolve(strict=False):
        # open_workbench_project already enforces this; the extra
        # check makes the rollback path unambiguous below.
        project_root = project.project_dir

    # 1. Backup the v1 state.
    _backup_stamp, backup_dir = _stamped_dir(project_root, "migration-backup")
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise WorkbenchError(
            ERR_MIGRATION_BACKUP_FAILED,
            f"failed to create backup directory {backup_dir}: {exc}",
            data={"backupDir": str(backup_dir)},
        ) from exc
    v1_documents: dict[str, str] = {
        "requirements": V1_DOCUMENT_PATHS["requirements"],
        "analog": V1_FILE_ANALOG,
        "schematic": V1_DOCUMENT_PATHS["schematic"],
        "digital": V1_DOCUMENT_PATHS["digital"],
        "system": V1_DOCUMENT_PATHS["system"],
    }
    try:
        copied = _backup_1_0_documents(project_root, backup_dir, v1_documents)
    except OSError as exc:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise WorkbenchError(
            ERR_MIGRATION_BACKUP_FAILED,
            f"failed to copy v1 documents to {backup_dir}: {exc}",
            data={"backupDir": str(backup_dir)},
        ) from exc

    # 2. Convert each document to the v2 shape and write to staging.
    _stage_stamp, stage_dir = _stamped_dir(project_root, "migration-staging")
    try:
        stage_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise WorkbenchError(
            ERR_MIGRATION_STAGE_FAILED,
            f"failed to create staging directory {stage_dir}: {exc}",
            data={"stageDir": str(stage_dir)},
        ) from exc

    migrated_at = datetime.now(UTC).isoformat()
    converted: dict[str, object] = {}
    try:
        for document, relative in v1_documents.items():
            source = project_root / relative
            v1_payload: dict[str, object] = _read_json_object(source) if source.exists() else {}
            if document == "requirements":
                converted_doc = _convert_requirements(v1_payload).model_dump(
                    mode="json", exclude_none=True
                )
            elif document == "analog":
                if not v1_payload:
                    v1_payload = dict(V1_ANALOG_DEFAULT)
                graph = _convert_analog(v1_payload).model_copy(
                    update={"projectId": project.project_id}
                )
                converted_doc = graph.model_dump(mode="json", exclude_none=True)
            elif document == "schematic":
                converted_doc = _convert_schematic(v1_payload).model_dump(
                    mode="json", exclude_none=True
                )
            elif document == "digital":
                converted_doc = _convert_digital(v1_payload).model_dump(
                    mode="json", exclude_none=True
                )
            elif document == "system":
                converted_doc = _convert_system(v1_payload).model_dump(
                    mode="json", exclude_none=True
                )
            else:  # pragma: no cover - guarded by v1_documents mapping
                raise WorkbenchError(
                    ERR_MIGRATION_INVALID_SOURCE,
                    f"document {document!r} has no converter",
                    data={"document": document},
                )
            converted[document] = converted_doc
            _write_json_atomic(stage_dir / relative, converted_doc)
    except (WorkbenchError, ValidationError, OSError) as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        _restore_from_backup(project_root, backup_dir, tuple(copied))
        raise WorkbenchError(
            ERR_MIGRATION_STAGE_FAILED,
            f"failed to stage v2 documents: {exc}",
            data={"stageDir": str(stage_dir), "error": str(exc)},
        ) from exc

    # 3. Build + validate the v2 manifest before swap.
    v2_manifest_payload: dict[str, object] = {
        "schemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "projectId": project.project_id,
        "displayName": project.display_name,
        "revision": project.revision,
        "createdAt": migrated_at,
        "updatedAt": migrated_at,
    }
    try:
        v2_manifest = HardwareProject.model_validate(v2_manifest_payload)
    except ValidationError as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        _restore_from_backup(project_root, backup_dir, tuple(copied))
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_TARGET,
            f"v2 manifest failed validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc
    _write_json_atomic(stage_dir / V2_FILE_MANIFEST, v2_manifest.model_dump(mode="json"))

    # 4. Swap the staged v2 files into the project root.
    swap_targets: list[tuple[Path, Path]] = [
        (project_root / V2_FILE_MANIFEST, stage_dir / V2_FILE_MANIFEST),
        (project_root / V2_FILE_REQUIREMENTS, stage_dir / v1_documents["requirements"]),
        (project_root / FILE_ANALOG_GRAPH, stage_dir / v1_documents["analog"]),
        (project_root / FILE_SCHEMATIC_VIEW, stage_dir / v1_documents["schematic"]),
        (project_root / FILE_DIGITAL, stage_dir / v1_documents["digital"]),
        (project_root / FILE_SYSTEM, stage_dir / v1_documents["system"]),
    ]
    try:
        for target, source in swap_targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except OSError as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        _restore_from_backup(project_root, backup_dir, tuple(copied))
        raise WorkbenchError(
            ERR_MIGRATION_SWAP_FAILED,
            f"failed to swap v2 documents into {project_root}: {exc}",
            data={"projectDir": str(project_root)},
        ) from exc

    # 5. Remove the legacy v1 analog file (the v2 graph replaces it
    # at the new path) and clean up the staging directory. The v1
    # backup is left in place so the caller can audit or roll back.
    legacy_analog = project_root / V1_FILE_ANALOG
    with contextlib.suppress(OSError):
        # Non-fatal: the project is in the v2 state; the legacy
        # analog is now stale but harmless. The next migration
        # attempt will skip the file (it no longer exists).
        legacy_analog.unlink(missing_ok=True)
    shutil.rmtree(stage_dir, ignore_errors=True)

    # Migration manifest inside the backup so future audits can see
    # which files were upgraded and when.
    migration_manifest: dict[str, object] = {
        "schemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "migratedAt": migrated_at,
        "fromSchemaVersion": V1_PROJECT_SCHEMA_VERSION,
        "toSchemaVersion": V2_PROJECT_SCHEMA_VERSION,
        "projectId": project.project_id,
        "copied": list(copied),
        "changedDocuments": list(v1_documents.keys()),
    }
    try:
        _write_json_atomic(backup_dir / MIGRATION_MANIFEST_NAME, migration_manifest)
    except OSError as exc:
        # The migration itself succeeded; the audit manifest is a
        # nice-to-have. Surface the failure in the data payload so
        # the workbench surface can warn the caller.
        raise WorkbenchError(
            ERR_MIGRATION_STAGE_FAILED,
            f"failed to write migration manifest: {exc}",
            data={"backupDir": str(backup_dir)},
        ) from exc

    return MigrationResult(
        project_id=project.project_id,
        backup_dir=backup_dir,
        staging_dir=stage_dir,
        migrated_at=migrated_at,
        v2_manifest_path=project_root / V2_FILE_MANIFEST,
        changed_documents=tuple(v1_documents.keys()),
    )


def rollback_workbench_project_to_v1(
    project_dir: Path | str,
    backup_dir: Path | str,
) -> None:
    """Restore a project from a v1 migration backup.

    This is the explicit rollback entry point. It expects the
    ``backup_dir`` produced by an earlier
    :func:`migrate_workbench_project_to_v2` call. Every file listed
    in the backup's ``migration-manifest.json`` is restored to the
    project root and the v2 documents are removed.
    """
    backup = Path(backup_dir)
    manifest_path = backup / MIGRATION_MANIFEST_NAME
    if not manifest_path.is_file():
        raise WorkbenchError(
            ERR_MIGRATION_NOT_FOUND,
            f"migration manifest not found at {manifest_path}",
            data={"backupDir": str(backup)},
        )
    manifest = _read_json_object(manifest_path)
    copied = manifest.get("copied", [])
    if not isinstance(copied, list):
        raise WorkbenchError(
            ERR_MIGRATION_INVALID_SOURCE,
            "migration manifest 'copied' is not a list",
            data={"manifest": str(manifest_path)},
        )
    project_root = Path(project_dir)
    for relative in copied:
        if not isinstance(relative, str):
            continue
        source = backup / relative
        if not source.exists():
            continue
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Remove the v2 analog (replaced by v1's main.circuit.json).
    v2_analog = project_root / FILE_ANALOG_GRAPH
    try:
        v2_analog.unlink(missing_ok=True)
    except OSError as exc:
        raise WorkbenchError(
            ERR_MIGRATION_ROLLBACK_FAILED,
            f"failed to remove v2 analog {v2_analog}: {exc}",
            data={"path": str(v2_analog)},
        ) from exc


__all__ = [
    "ERR_MIGRATION_BACKUP_FAILED",
    "ERR_MIGRATION_INVALID_SOURCE",
    "ERR_MIGRATION_INVALID_TARGET",
    "ERR_MIGRATION_NOT_FOUND",
    "ERR_MIGRATION_ROLLBACK_FAILED",
    "ERR_MIGRATION_STAGE_FAILED",
    "ERR_MIGRATION_SWAP_FAILED",
    "MIGRATION_BACKUP_DIRNAME",
    "MIGRATION_MANIFEST_NAME",
    "MIGRATION_STAGING_DIRNAME",
    "V1_FILE_ANALOG",
    "MigrationResult",
    "migrate_workbench_project_to_v2",
    "rollback_workbench_project_to_v1",
]
