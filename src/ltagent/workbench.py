"""Versioned local-first project contract for the desktop workbench.

The workbench deliberately keeps electrical meaning, editor layout, and
generated artefacts separate.  This module owns only the durable project
layout and guarded document replacement used by the desktop, CLI, MCP, and
future AI adapters.  It never invokes a simulator or accepts filesystem paths
from a change set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from .security import PathSafetyError, SecurityError, safe_resolve_under, validate_slug

PROJECT_SCHEMA_VERSION: Final[str] = "1.0"
LEGACY_PROJECT_SCHEMA_VERSION: Final[str] = "0.1"

FILE_MANIFEST: Final[str] = "hardware.project.json"
FILE_REQUIREMENTS: Final[str] = "design/requirements.json"
FILE_ANALOG: Final[str] = "design/analog/main.circuit.json"
FILE_SCHEMATIC: Final[str] = "design/schematic/main.view.json"
FILE_DIGITAL: Final[str] = "design/digital/main.digital.json"
FILE_SYSTEM: Final[str] = "design/system.json"
DIR_FIRMWARE: Final[str] = "firmware"
DIR_VERIFICATION: Final[str] = "verification"
DIR_RUNS: Final[str] = "runs"
DIR_INTERNAL: Final[str] = ".workbench"
FILE_TRANSACTION: Final[str] = ".workbench/transaction.json"

DOCUMENT_PATHS: Final[dict[str, str]] = {
    "requirements": FILE_REQUIREMENTS,
    "analog": FILE_ANALOG,
    "schematic": FILE_SCHEMATIC,
    "digital": FILE_DIGITAL,
    "system": FILE_SYSTEM,
}

ERR_PROJECT_INVALID: Final[str] = "WORKBENCH_PROJECT_INVALID"
ERR_PROJECT_EXISTS: Final[str] = "WORKBENCH_PROJECT_EXISTS"
ERR_PROJECT_NOT_FOUND: Final[str] = "WORKBENCH_PROJECT_NOT_FOUND"
ERR_PROJECT_VERSION_UNSUPPORTED: Final[str] = "WORKBENCH_PROJECT_VERSION_UNSUPPORTED"
ERR_PROJECT_IO: Final[str] = "WORKBENCH_PROJECT_IO"
ERR_DOCUMENT_INVALID: Final[str] = "WORKBENCH_DOCUMENT_INVALID"
ERR_DOCUMENT_NOT_FOUND: Final[str] = "WORKBENCH_DOCUMENT_NOT_FOUND"
ERR_CHANGESET_INVALID: Final[str] = "WORKBENCH_CHANGESET_INVALID"
ERR_CHANGESET_CONFLICT: Final[str] = "WORKBENCH_CHANGESET_CONFLICT"
ERR_CHANGESET_OPERATION_INVALID: Final[str] = "WORKBENCH_CHANGESET_OPERATION_INVALID"
ERR_TRANSACTION_INVALID: Final[str] = "WORKBENCH_TRANSACTION_INVALID"


class WorkbenchError(ValueError):
    """Structured error emitted by the workbench project boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, object] = dict(data) if data else {}


@dataclass(frozen=True)
class WorkbenchProjectPaths:
    """Absolute paths in a single workbench project."""

    project_dir: Path
    manifest: Path
    requirements: Path
    analog: Path
    schematic: Path
    digital: Path
    system: Path
    firmware: Path
    verification: Path
    runs: Path
    internal: Path
    transaction: Path


@dataclass(frozen=True)
class WorkbenchProject:
    """Opened project metadata plus its resolved paths."""

    paths: WorkbenchProjectPaths
    project_id: str
    display_name: str
    revision: int

    @property
    def project_dir(self) -> Path:
        return self.paths.project_dir

    @property
    def manifest(self) -> Path:
        return self.paths.manifest

    @property
    def requirements(self) -> Path:
        return self.paths.requirements

    @property
    def analog(self) -> Path:
        return self.paths.analog

    @property
    def schematic(self) -> Path:
        return self.paths.schematic

    @property
    def digital(self) -> Path:
        return self.paths.digital

    @property
    def system(self) -> Path:
        return self.paths.system

    @property
    def firmware(self) -> Path:
        return self.paths.firmware

    @property
    def verification(self) -> Path:
        return self.paths.verification

    @property
    def runs(self) -> Path:
        return self.paths.runs


@dataclass(frozen=True)
class MigrationResult:
    """Result of migrating a project manifest to the current schema."""

    project: WorkbenchProject
    migrated_from: str | None

    @property
    def revision(self) -> int:
        return self.project.revision


@dataclass(frozen=True)
class ChangeSetResult:
    """Acknowledgement returned after an accepted document change set."""

    revision: int
    changed_documents: tuple[str, ...]


def get_workbench_project_paths(project_dir: Path | str) -> WorkbenchProjectPaths:
    """Return paths without reading or creating files."""
    base = Path(project_dir)
    return WorkbenchProjectPaths(
        project_dir=base,
        manifest=base / FILE_MANIFEST,
        requirements=base / FILE_REQUIREMENTS,
        analog=base / FILE_ANALOG,
        schematic=base / FILE_SCHEMATIC,
        digital=base / FILE_DIGITAL,
        system=base / FILE_SYSTEM,
        firmware=base / DIR_FIRMWARE,
        verification=base / DIR_VERIFICATION,
        runs=base / DIR_RUNS,
        internal=base / DIR_INTERNAL,
        transaction=base / FILE_TRANSACTION,
    )


def create_workbench_project(
    projects_root: Path | str,
    project_id: str,
    *,
    display_name: str | None = None,
) -> WorkbenchProject:
    """Create a complete, empty workbench project under ``projects_root``."""
    try:
        safe_id = validate_slug(project_id, kind="workbench project id")
    except SecurityError as exc:
        raise WorkbenchError(ERR_PROJECT_INVALID, exc.message, data=exc.data) from exc

    root = Path(projects_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise WorkbenchError(
            ERR_PROJECT_NOT_FOUND,
            f"projects root {root} does not exist or is not a directory",
            data={"projectsRoot": str(root)},
        )
    try:
        project_dir = safe_resolve_under(root / safe_id, root, must_exist=False)
    except PathSafetyError as exc:
        raise WorkbenchError(ERR_PROJECT_INVALID, exc.message, data=exc.data) from exc
    if project_dir.exists():
        raise WorkbenchError(
            ERR_PROJECT_EXISTS,
            f"workbench project {safe_id!r} already exists",
            data={"projectId": safe_id, "projectDir": str(project_dir)},
        )

    paths = get_workbench_project_paths(project_dir)
    try:
        paths.project_dir.mkdir()
        for directory in (
            paths.requirements.parent,
            paths.analog.parent,
            paths.schematic.parent,
            paths.digital.parent,
            paths.firmware,
            paths.verification,
            paths.runs,
            paths.internal,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            paths.manifest,
            _manifest_payload(safe_id, display_name or safe_id, revision=0),
        )
        _ensure_documents(paths)
    except OSError as exc:
        raise WorkbenchError(
            ERR_PROJECT_IO,
            f"failed to create workbench project: {exc}",
            data={"projectDir": str(paths.project_dir)},
        ) from exc
    return _project_from_manifest(paths, _read_json_object(paths.manifest, "manifest"))


def open_workbench_project(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> WorkbenchProject:
    """Open a current-version project and recover an interrupted change set."""
    paths = get_workbench_project_paths(
        _resolve_project_dir(project_dir, projects_root=projects_root)
    )
    _recover_transaction(paths)
    manifest = _read_json_object(paths.manifest, "manifest")
    version = _read_schema_version(manifest, source="manifest")
    if version != PROJECT_SCHEMA_VERSION:
        raise WorkbenchError(
            ERR_PROJECT_VERSION_UNSUPPORTED,
            f"project schema version {version!r} is not supported; migrate first",
            data={"schemaVersion": version, "supported": PROJECT_SCHEMA_VERSION},
        )
    return _project_from_manifest(paths, manifest)


def migrate_workbench_project(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> MigrationResult:
    """Migrate known historical manifests without touching unknown versions."""
    paths = get_workbench_project_paths(
        _resolve_project_dir(project_dir, projects_root=projects_root)
    )
    _recover_transaction(paths)
    manifest = _read_json_object(paths.manifest, "manifest")
    version = _read_schema_version(manifest, source="manifest")
    if version == PROJECT_SCHEMA_VERSION:
        return MigrationResult(project=_project_from_manifest(paths, manifest), migrated_from=None)
    if version != LEGACY_PROJECT_SCHEMA_VERSION:
        raise WorkbenchError(
            ERR_PROJECT_VERSION_UNSUPPORTED,
            f"cannot migrate project schema version {version!r}",
            data={"schemaVersion": version, "supported": PROJECT_SCHEMA_VERSION},
        )

    project_id = _read_project_id(manifest)
    upgraded = _manifest_payload(
        project_id,
        _read_display_name(manifest, fallback=project_id),
        revision=_read_revision(manifest, default=0),
    )
    _ensure_project_directories(paths)
    _write_json_atomic(paths.manifest, upgraded)
    _ensure_documents(paths)
    return MigrationResult(
        project=_project_from_manifest(paths, upgraded), migrated_from=LEGACY_PROJECT_SCHEMA_VERSION
    )


def read_document(project_dir: Path | str, document: str) -> dict[str, Any]:
    """Read one allowlisted design document from a current project."""
    project = open_workbench_project(project_dir)
    path = _document_path(project.paths, document)
    if not path.is_file():
        raise WorkbenchError(
            ERR_DOCUMENT_NOT_FOUND,
            f"document {document!r} does not exist",
            data={"document": document, "path": str(path)},
        )
    return _read_json_object(path, f"document {document}")


def validate_workbench_project(project_dir: Path | str) -> dict[str, object]:
    """Return a stable validation summary suitable for a job result."""
    project = open_workbench_project(project_dir)
    documents: dict[str, str] = {}
    for document in DOCUMENT_PATHS:
        payload = read_document(project.project_dir, document)
        documents[document] = _read_schema_version(payload, source=document)
    return {
        "projectId": project.project_id,
        "revision": project.revision,
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "status": "pass",
        "documents": documents,
    }


def apply_change_set(
    project_dir: Path | str,
    change_set: Mapping[str, object],
) -> ChangeSetResult:
    """Apply a validated, revision-guarded document replacement transaction.

    The first public operation is intentionally narrow: ``replace_document``
    on one or more allowlisted JSON documents.  It is enough for the initial
    UI and prevents arbitrary filesystem access while semantic component edit
    operations are introduced in a later additive version.
    """
    project = open_workbench_project(project_dir)
    base_revision = _parse_change_set_base_revision(change_set)
    if base_revision != project.revision:
        raise WorkbenchError(
            ERR_CHANGESET_CONFLICT,
            "change set revision does not match the current project revision",
            data={"actualRevision": project.revision, "baseRevision": base_revision},
        )
    parsed = _parse_change_set(change_set)

    operations = cast(tuple[tuple[str, dict[str, Any]], ...], parsed["operations"])
    paths_by_document = {
        document: _document_path(project.paths, document) for document, _ in operations
    }
    before = {
        document: path.read_text(encoding="utf-8") if path.exists() else None
        for document, path in paths_by_document.items()
    }
    before_manifest = project.manifest.read_text(encoding="utf-8")
    journal = {
        "before": before,
        "manifest": before_manifest,
        "schemaVersion": PROJECT_SCHEMA_VERSION,
    }
    _write_json_atomic(project.paths.transaction, journal)

    try:
        for document, value in operations:
            _write_json_atomic(paths_by_document[document], value)
        _write_json_atomic(
            project.manifest,
            _manifest_payload(
                project.project_id,
                project.display_name,
                revision=project.revision + 1,
            ),
        )
    except WorkbenchError:
        _recover_transaction(project.paths)
        raise
    except OSError as exc:
        _recover_transaction(project.paths)
        raise WorkbenchError(
            ERR_PROJECT_IO,
            f"failed to apply workbench change set: {exc}",
            data={"projectDir": str(project.project_dir)},
        ) from exc
    try:
        project.paths.transaction.unlink(missing_ok=True)
    except OSError as exc:
        raise WorkbenchError(
            ERR_PROJECT_IO,
            f"failed to finalise workbench change set: {exc}",
            data={"transaction": str(project.paths.transaction)},
        ) from exc
    return ChangeSetResult(
        revision=project.revision + 1,
        changed_documents=tuple(document for document, _ in operations),
    )


def _resolve_project_dir(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None,
) -> Path:
    try:
        if projects_root is None:
            resolved = Path(project_dir).expanduser().resolve(strict=True)
        else:
            resolved = safe_resolve_under(project_dir, projects_root, must_exist=True)
    except (OSError, PathSafetyError) as exc:
        data = getattr(exc, "data", {"projectDir": str(project_dir)})
        raise WorkbenchError(ERR_PROJECT_NOT_FOUND, str(exc), data=data) from exc
    if not resolved.is_dir():
        raise WorkbenchError(
            ERR_PROJECT_NOT_FOUND,
            f"project directory {resolved} is not a directory",
            data={"projectDir": str(resolved)},
        )
    return resolved


def _ensure_project_directories(paths: WorkbenchProjectPaths) -> None:
    for directory in (
        paths.requirements.parent,
        paths.analog.parent,
        paths.schematic.parent,
        paths.digital.parent,
        paths.firmware,
        paths.verification,
        paths.runs,
        paths.internal,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def _ensure_documents(paths: WorkbenchProjectPaths) -> None:
    defaults: dict[Path, dict[str, object]] = {
        paths.requirements: {"constraints": {}, "goals": [], "schemaVersion": "1.0"},
        paths.analog: {
            "analyses": [],
            "components": [],
            "nets": [],
            "schemaVersion": "1.0",
        },
        paths.schematic: {
            "gridSize": 16,
            "nodes": [],
            "schemaVersion": "1.0",
            "wires": [],
        },
        paths.digital: {"modules": [], "schemaVersion": "1.0"},
        paths.system: {"blocks": [], "connections": [], "schemaVersion": "1.0"},
    }
    for path, payload in defaults.items():
        if not path.exists():
            _write_json_atomic(path, payload)


def _manifest_payload(project_id: str, display_name: str, *, revision: int) -> dict[str, object]:
    return {
        "displayName": display_name,
        "projectId": project_id,
        "revision": revision,
        "schemaVersion": PROJECT_SCHEMA_VERSION,
    }


def _project_from_manifest(
    paths: WorkbenchProjectPaths, manifest: Mapping[str, Any]
) -> WorkbenchProject:
    return WorkbenchProject(
        paths=paths,
        project_id=_read_project_id(manifest),
        display_name=_read_display_name(manifest, fallback=_read_project_id(manifest)),
        revision=_read_revision(manifest, default=0),
    )


def _read_project_id(manifest: Mapping[str, Any]) -> str:
    try:
        return validate_slug(manifest.get("projectId"), kind="workbench project id")
    except SecurityError as exc:
        raise WorkbenchError(ERR_PROJECT_INVALID, exc.message, data=exc.data) from exc


def _read_display_name(manifest: Mapping[str, Any], *, fallback: str) -> str:
    value = manifest.get("displayName", fallback)
    if not isinstance(value, str) or not value.strip():
        raise WorkbenchError(
            ERR_PROJECT_INVALID,
            "manifest displayName must be a non-empty string",
            data={"displayName": str(value)},
        )
    return value


def _read_revision(manifest: Mapping[str, Any], *, default: int) -> int:
    value = manifest.get("revision", default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkbenchError(
            ERR_PROJECT_INVALID,
            "manifest revision must be a non-negative integer",
            data={"revision": str(value)},
        )
    return value


def _read_schema_version(payload: Mapping[str, Any], *, source: str) -> str:
    version = payload.get("schemaVersion")
    if not isinstance(version, str):
        raise WorkbenchError(
            ERR_PROJECT_INVALID,
            f"{source} schemaVersion must be a string",
            data={"source": source, "schemaVersion": str(version)},
        )
    return version


def _document_path(paths: WorkbenchProjectPaths, document: str) -> Path:
    relative = DOCUMENT_PATHS.get(document)
    if relative is None:
        raise WorkbenchError(
            ERR_DOCUMENT_INVALID,
            f"document {document!r} is not supported",
            data={"document": document, "allowed": sorted(DOCUMENT_PATHS)},
        )
    try:
        return safe_resolve_under(paths.project_dir / relative, paths.project_dir, must_exist=False)
    except PathSafetyError as exc:
        raise WorkbenchError(ERR_DOCUMENT_INVALID, exc.message, data=exc.data) from exc


def _parse_change_set(change_set: Mapping[str, object]) -> dict[str, object]:
    version = change_set.get("schemaVersion")
    if version != PROJECT_SCHEMA_VERSION:
        raise WorkbenchError(
            ERR_CHANGESET_INVALID,
            "change set schemaVersion is unsupported",
            data={"schemaVersion": str(version), "supported": PROJECT_SCHEMA_VERSION},
        )
    base_revision = _parse_change_set_base_revision(change_set)
    raw_operations = change_set.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise WorkbenchError(
            ERR_CHANGESET_INVALID,
            "change set operations must be a non-empty array",
            data={},
        )

    parsed: list[tuple[str, dict[str, Any]]] = []
    seen_documents: set[str] = set()
    for index, raw_operation in enumerate(raw_operations):
        if not isinstance(raw_operation, Mapping):
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "change set operation must be an object",
                data={"index": index},
            )
        operation_type = raw_operation.get("type")
        document = raw_operation.get("document")
        value = raw_operation.get("value")
        if operation_type != "replace_document":
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "only replace_document is supported by this workbench version",
                data={"index": index, "type": str(operation_type)},
            )
        if not isinstance(document, str):
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "change set document must be a string",
                data={"index": index},
            )
        if document in seen_documents:
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "a change set may replace each document at most once",
                data={"document": document},
            )
        if document not in DOCUMENT_PATHS:
            _document_path(get_workbench_project_paths(Path(".")), document)
        if not isinstance(value, Mapping):
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "replacement document value must be an object",
                data={"document": document, "index": index},
            )
        copied = dict(cast(Mapping[str, Any], value))
        if _read_schema_version(copied, source=f"change set {document}") != PROJECT_SCHEMA_VERSION:
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "replacement document schemaVersion is unsupported",
                data={"document": document},
            )
        try:
            json.dumps(copied, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise WorkbenchError(
                ERR_CHANGESET_OPERATION_INVALID,
                "replacement document is not JSON serialisable",
                data={"document": document},
            ) from exc
        seen_documents.add(document)
        parsed.append((document, copied))
    return {"baseRevision": base_revision, "operations": tuple(parsed)}


def _parse_change_set_base_revision(change_set: Mapping[str, object]) -> int:
    version = change_set.get("schemaVersion")
    if version != PROJECT_SCHEMA_VERSION:
        raise WorkbenchError(
            ERR_CHANGESET_INVALID,
            "change set schemaVersion is unsupported",
            data={"schemaVersion": str(version), "supported": PROJECT_SCHEMA_VERSION},
        )
    base_revision = change_set.get("baseRevision")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
        raise WorkbenchError(
            ERR_CHANGESET_INVALID,
            "change set baseRevision must be a non-negative integer",
            data={"baseRevision": str(base_revision)},
        )
    return base_revision


def _read_json_object(path: Path, source: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkbenchError(
            ERR_PROJECT_NOT_FOUND,
            f"{source} file {path} does not exist",
            data={"path": str(path)},
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbenchError(
            ERR_PROJECT_INVALID,
            f"cannot read {source} JSON: {exc}",
            data={"path": str(path)},
        ) from exc
    if not isinstance(parsed, dict):
        raise WorkbenchError(
            ERR_PROJECT_INVALID,
            f"{source} JSON must be an object",
            data={"path": str(path)},
        )
    return cast(dict[str, Any], parsed)


def _write_json_atomic(path: Path, payload: Mapping[str, object] | Mapping[str, Any]) -> None:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as exc:
        raise WorkbenchError(
            ERR_DOCUMENT_INVALID,
            f"cannot serialise JSON payload for {path.name}",
            data={"path": str(path)},
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise WorkbenchError(
            ERR_PROJECT_IO,
            f"cannot write {path}: {exc}",
            data={"path": str(path)},
        ) from exc


def _write_text_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise WorkbenchError(
            ERR_PROJECT_IO,
            f"cannot restore {path}: {exc}",
            data={"path": str(path)},
        ) from exc


def _recover_transaction(paths: WorkbenchProjectPaths) -> None:
    if not paths.transaction.exists():
        return
    journal = _read_json_object(paths.transaction, "transaction journal")
    before = journal.get("before")
    manifest_text = journal.get("manifest")
    if not isinstance(before, Mapping) or not isinstance(manifest_text, str):
        raise WorkbenchError(
            ERR_TRANSACTION_INVALID,
            "transaction journal is malformed",
            data={"path": str(paths.transaction)},
        )
    for document, text in before.items():
        if not isinstance(document, str) or (text is not None and not isinstance(text, str)):
            raise WorkbenchError(
                ERR_TRANSACTION_INVALID,
                "transaction journal contains an invalid document backup",
                data={"path": str(paths.transaction)},
            )
        path = _document_path(paths, document)
        if text is None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise WorkbenchError(
                    ERR_PROJECT_IO,
                    f"cannot remove partial document {path}: {exc}",
                    data={"path": str(path)},
                ) from exc
        else:
            _write_text_atomic(path, text)
    _write_text_atomic(paths.manifest, manifest_text)
    try:
        paths.transaction.unlink(missing_ok=True)
    except OSError as exc:
        raise WorkbenchError(
            ERR_PROJECT_IO,
            f"cannot remove transaction journal: {exc}",
            data={"path": str(paths.transaction)},
        ) from exc


__all__ = [
    "DOCUMENT_PATHS",
    "ERR_CHANGESET_CONFLICT",
    "ERR_CHANGESET_INVALID",
    "ERR_CHANGESET_OPERATION_INVALID",
    "ERR_DOCUMENT_INVALID",
    "ERR_DOCUMENT_NOT_FOUND",
    "ERR_PROJECT_EXISTS",
    "ERR_PROJECT_INVALID",
    "ERR_PROJECT_NOT_FOUND",
    "ERR_PROJECT_VERSION_UNSUPPORTED",
    "PROJECT_SCHEMA_VERSION",
    "ChangeSetResult",
    "MigrationResult",
    "WorkbenchError",
    "WorkbenchProject",
    "WorkbenchProjectPaths",
    "apply_change_set",
    "create_workbench_project",
    "get_workbench_project_paths",
    "migrate_workbench_project",
    "open_workbench_project",
    "read_document",
    "validate_workbench_project",
]
