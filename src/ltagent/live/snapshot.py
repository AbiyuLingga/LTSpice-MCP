"""Snapshot, restore, and diff system for live projects.

A *snapshot* is a copy of the project's important files captured at
a point in time, stored under ``<project>/.snapshots/<id>/``.
Snapshots back the undo / restore workflow described in plan §9 and
are also the diff substrate used by the explain and compare tools.

Design contract
---------------

* Snapshot ids have the form ``NNN_<slug>``. ``NNN`` is a zero-padded
  monotonically increasing counter. ``<slug>`` is the human-supplied
  reason lower-cased and slug-ified. The counter is recomputed on
  every create by listing the existing directories, so the snapshot
  manager survives copy / restore of ``.snapshots/``.
* Every snapshot ships with a ``manifest.json`` that names the
  files that were captured plus the reason, the timestamp, and the
  schema version. The manifest is the only piece of metadata the
  restore / list / diff functions read; if it is missing or
  unparseable the snapshot is treated as corrupt and skipped (list)
  or rejected (restore / diff).
* Snapshot operations only ever read or write inside the project
  directory. The path-safety guard from
  :mod:`ltagent.live.project` is reused so the same
  ``PATH_TRAVERSAL`` code is used everywhere.
* The default set of snapshotted files is the union of the project
  files defined in :mod:`ltagent.live.project`. Callers may override
  the list, but the union is what the standard edit pipeline uses.

This module never spawns a process. Snapshots are pure file copies
performed with :func:`shutil.copy2` (preserves mtime, no metadata
exfiltration). Diffing is byte-equal comparison; line / JSON-level
diffing is the responsibility of upstream agents.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from .project import (
    DIR_SNAPSHOTS,
    ERR_PROJECT_IO,
    FILE_ASC,
    FILE_CALCULATION_JSON,
    FILE_CALCULATION_MD,
    FILE_CIR,
    FILE_GRAPH,
    FILE_HISTORY,
    FILE_IR,
    FILE_METADATA,
    FILE_RESULT,
    FILE_VERIFICATION,
    LiveProjectError,
    _resolve_project_path,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Stable schema version stamped on every snapshot manifest. Bumped
#: only on backwards-incompatible manifest changes.
SNAPSHOT_MANIFEST_SCHEMA_VERSION: Final[str] = "0.1"

#: Stable name of the manifest file inside every snapshot.
SNAPSHOT_MANIFEST_NAME: Final[str] = "manifest.json"

#: Default list of project files copied into every snapshot. Mirrors
#: :data:`ltagent.live.project.PROJECT_FILE_NAMES` so a fresh project
#: can be round-tripped from a single snapshot.
DEFAULT_SNAPSHOT_FILES: Final[tuple[str, ...]] = (
    FILE_GRAPH,
    FILE_IR,
    FILE_CIR,
    FILE_ASC,
    FILE_METADATA,
    FILE_RESULT,
    FILE_VERIFICATION,
    FILE_CALCULATION_JSON,
    FILE_CALCULATION_MD,
    FILE_HISTORY,
)

#: Pattern that matches a snapshot id produced by this module.
SNAPSHOT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d{3,})_(.+)$")

#: Cap on the slug suffix of a snapshot id.
_SNAPSHOT_SLUG_MAX: Final[int] = 48

# ---------------------------------------------------------------------------
# Error codes (stable, machine-readable)
# ---------------------------------------------------------------------------

ERR_SNAPSHOT_NOT_FOUND: Final[str] = "LIVE_SNAPSHOT_NOT_FOUND"
ERR_SNAPSHOT_INVALID_ID: Final[str] = "LIVE_SNAPSHOT_INVALID_ID"
ERR_SNAPSHOT_EXISTS: Final[str] = "LIVE_SNAPSHOT_EXISTS"
ERR_SNAPSHOT_INVALID_MANIFEST: Final[str] = "LIVE_SNAPSHOT_INVALID_MANIFEST"
ERR_SNAPSHOT_INVALID_FILES: Final[str] = "LIVE_SNAPSHOT_INVALID_FILES"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotInfo:
    """The metadata of a single snapshot.

    ``files`` is the list of project file names that were copied
    into the snapshot at creation time. ``snapshot_dir`` is the
    absolute path to the snapshot directory itself; it is a derived
    field and the manifest is the source of truth.
    """

    snapshot_id: str
    snapshot_dir: Path
    created_at: str
    reason: str
    files: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (no Path objects)."""
        return {
            "snapshotId": self.snapshot_id,
            "createdAt": self.created_at,
            "reason": self.reason,
            "files": list(self.files),
        }


@dataclass(frozen=True)
class SnapshotDiff:
    """The result of :func:`diff_snapshot`.

    ``added`` and ``removed`` are the project file names that exist
    only in one of the two snapshots. ``changed`` is the list of
    files whose byte content differs; ``unchanged`` is the list of
    files whose content matches.
    """

    snapshot_a: str
    snapshot_b: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "snapshotA": self.snapshot_a,
            "snapshotB": self.snapshot_b,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": list(self.changed),
            "unchanged": list(self.unchanged),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify_reason(reason: str) -> str:
    """Return a slug-safe suffix derived from ``reason``.

    The result is lower-cased, contains only ``[a-z0-9_-]``, never
    starts or ends with a separator, and is at most
    :data:`_SNAPSHOT_SLUG_MAX` characters long. An empty input
    yields ``"snapshot"`` so the produced id is never just a number.
    """
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", reason.lower()).strip("_")
    if not cleaned:
        return "snapshot"
    return cleaned[:_SNAPSHOT_SLUG_MAX]


def _list_existing_snapshot_ids(snapshots_dir: Path) -> list[tuple[int, str]]:
    """List snapshot ids under ``snapshots_dir`` paired with their counter.

    Only directories that match the ``NNN_...`` pattern are returned,
    sorted by counter ascending. Garbage directories (e.g. left over
    from a crash) are silently skipped.
    """
    if not snapshots_dir.exists() or not snapshots_dir.is_dir():
        return []
    pairs: list[tuple[int, str]] = []
    for entry in snapshots_dir.iterdir():
        if not entry.is_dir():
            continue
        m = SNAPSHOT_ID_PATTERN.match(entry.name)
        if m is None:
            continue
        pairs.append((int(m.group(1)), entry.name))
    pairs.sort()
    return pairs


def _next_snapshot_id(snapshots_dir: Path, reason: str) -> str:
    """Compute the next snapshot id, given the existing ones."""
    existing = _list_existing_snapshot_ids(snapshots_dir)
    counter = len(existing) + 1
    return f"{counter:03d}_{_slugify_reason(reason)}"


def _validate_snapshot_id(snapshot_id: str) -> str:
    """Reject a snapshot id that does not match :data:`SNAPSHOT_ID_PATTERN`."""
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise LiveProjectError(
            ERR_SNAPSHOT_INVALID_ID,
            "snapshot id must be a non-empty string",
            data={"snapshotId": str(snapshot_id)},
        )
    if SNAPSHOT_ID_PATTERN.match(snapshot_id) is None:
        raise LiveProjectError(
            ERR_SNAPSHOT_INVALID_ID,
            f"snapshot id {snapshot_id!r} must match {SNAPSHOT_ID_PATTERN.pattern}",
            data={"snapshotId": snapshot_id},
        )
    return snapshot_id


def _read_manifest(snapshot_dir: Path) -> dict[str, Any]:
    """Read ``manifest.json`` and validate the schema."""
    manifest_path = snapshot_dir / SNAPSHOT_MANIFEST_NAME
    if not manifest_path.exists():
        raise LiveProjectError(
            ERR_SNAPSHOT_INVALID_MANIFEST,
            f"snapshot at {snapshot_dir} has no manifest.json",
            data={"snapshotDir": str(snapshot_dir)},
        )
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to read {manifest_path}: {exc}",
            data={"manifestPath": str(manifest_path), "phase": "read"},
        ) from exc
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiveProjectError(
            ERR_SNAPSHOT_INVALID_MANIFEST,
            f"manifest {manifest_path} is not valid JSON: {exc.msg}",
            data={
                "manifestPath": str(manifest_path),
                "line": exc.lineno,
                "column": exc.colno,
            },
        ) from exc
    if not isinstance(loaded, dict):
        raise LiveProjectError(
            ERR_SNAPSHOT_INVALID_MANIFEST,
            f"manifest {manifest_path} must be a JSON object",
            data={"manifestPath": str(manifest_path)},
        )
    files = loaded.get("files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise LiveProjectError(
            ERR_SNAPSHOT_INVALID_MANIFEST,
            f"manifest {manifest_path} has a non-string 'files' entry",
            data={"manifestPath": str(manifest_path)},
        )
    return loaded


def _copy_files(
    src_root: Path,
    dst_root: Path,
    files: Iterable[str],
) -> list[str]:
    """Copy a list of files from ``src_root`` to ``dst_root``.

    Returns the list of files that were actually copied. Missing
    files in ``src_root`` are skipped silently — a snapshot taken
    mid-edit is allowed to be partial.
    """
    copied: list[str] = []
    for name in files:
        src = src_root / name
        if not src.exists() or not src.is_file():
            continue
        dst = dst_root / name
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise LiveProjectError(
                ERR_PROJECT_IO,
                f"failed to copy {src} -> {dst}: {exc}",
                data={"src": str(src), "dst": str(dst), "phase": "copy"},
            ) from exc
        copied.append(name)
    return copied


def _resolve_snapshot_dir(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None,
) -> tuple[Path, Path]:
    """Resolve the project root and its ``.snapshots/`` directory.

    Returns the absolute ``project_dir`` and the absolute snapshots
    directory. The ``.snapshots/`` directory is **not** required to
    exist — it is created lazily by :func:`create_snapshot`.
    """
    project_resolved = _resolve_project_path(
        project_dir, projects_root=projects_root, must_exist=True
    )
    snapshots = project_resolved / DIR_SNAPSHOTS
    return project_resolved, snapshots


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def create_snapshot(
    project_dir: Path | str,
    reason: str,
    *,
    files: Iterable[str] | None = None,
    projects_root: Path | str | None = None,
    when: datetime | None = None,
) -> SnapshotInfo:
    """Create a new snapshot of the project.

    Parameters
    ----------
    project_dir:
        Path to the live project directory. The directory must exist.
    reason:
        Human-readable reason for the snapshot. It is slug-ified and
        used as the suffix of the snapshot id. The full reason is
        preserved verbatim in the manifest.
    files:
        Optional override of the file list. Defaults to
        :data:`DEFAULT_SNAPSHOT_FILES`. Each entry is a project-relative
        path; absolute paths or ``..`` segments are rejected.
    projects_root:
        Optional containment root. When provided the project must
        resolve under it; otherwise the path is only required to
        exist.
    when:
        Optional wall-clock override. Tests pin the timestamp here
        so manifest output is deterministic.

    Returns
    -------
    SnapshotInfo
        The metadata of the newly created snapshot. ``snapshot_dir``
        is the absolute path of the new directory.

    Raises
    ------
    LiveProjectError
        On validation failure, path traversal, manifest write
        failure, or filesystem error.
    """
    if not isinstance(reason, str) or not reason:
        raise LiveProjectError(
            "LIVE_SNAPSHOT_REASON_REQUIRED",
            "snapshot reason must be a non-empty string",
            data={"reason": reason},
        )
    file_list = tuple(files) if files is not None else DEFAULT_SNAPSHOT_FILES
    for fname in file_list:
        if not isinstance(fname, str) or not fname:
            raise LiveProjectError(
                ERR_SNAPSHOT_INVALID_FILES,
                "snapshot file entries must be non-empty strings",
                data={"file": str(fname)},
            )
        if Path(fname).is_absolute():
            raise LiveProjectError(
                ERR_SNAPSHOT_INVALID_FILES,
                f"snapshot file {fname!r} must be a project-relative path",
                data={"file": fname},
            )
        if ".." in Path(fname).parts:
            raise LiveProjectError(
                ERR_SNAPSHOT_INVALID_FILES,
                f"snapshot file {fname!r} must not contain '..' segments",
                data={"file": fname},
            )

    project_resolved, snapshots_dir = _resolve_snapshot_dir(
        project_dir, projects_root=projects_root
    )

    try:
        snapshots_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to create {snapshots_dir}: {exc}",
            data={"snapshotsDir": str(snapshots_dir), "phase": "mkdir"},
        ) from exc

    snapshot_id = _next_snapshot_id(snapshots_dir, reason)
    snap_dir = snapshots_dir / snapshot_id
    if snap_dir.exists():
        raise LiveProjectError(
            ERR_SNAPSHOT_EXISTS,
            f"snapshot directory {snap_dir} already exists",
            data={"snapshotId": snapshot_id, "snapshotDir": str(snap_dir)},
        )
    try:
        snap_dir.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to create {snap_dir}: {exc}",
            data={"snapshotDir": str(snap_dir), "phase": "mkdir"},
        ) from exc

    copied = _copy_files(project_resolved, snap_dir, file_list)
    created_at = (when or datetime.now(UTC)).isoformat()
    manifest = {
        "schemaVersion": SNAPSHOT_MANIFEST_SCHEMA_VERSION,
        "snapshotId": snapshot_id,
        "createdAt": created_at,
        "reason": reason,
        "files": copied,
    }
    manifest_path = snap_dir / SNAPSHOT_MANIFEST_NAME
    try:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to write {manifest_path}: {exc}",
            data={"manifestPath": str(manifest_path), "phase": "write"},
        ) from exc

    return SnapshotInfo(
        snapshot_id=snapshot_id,
        snapshot_dir=snap_dir,
        created_at=created_at,
        reason=reason,
        files=tuple(copied),
    )


def list_snapshots(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> list[SnapshotInfo]:
    """List snapshots in creation order (oldest first).

    Snapshots whose ``manifest.json`` is missing or unparseable are
    silently skipped. The list is always sorted by the counter
    embedded in the snapshot id.
    """
    _, snapshots_dir = _resolve_snapshot_dir(
        project_dir, projects_root=projects_root
    )
    out: list[SnapshotInfo] = []
    for _, sid in _list_existing_snapshot_ids(snapshots_dir):
        snap_dir = snapshots_dir / sid
        try:
            manifest = _read_manifest(snap_dir)
        except LiveProjectError:
            continue
        out.append(
            SnapshotInfo(
                snapshot_id=sid,
                snapshot_dir=snap_dir,
                created_at=str(manifest.get("createdAt", "")),
                reason=str(manifest.get("reason", "")),
                files=tuple(manifest.get("files", [])),
            )
        )
    return out


def restore_snapshot(
    project_dir: Path | str,
    snapshot_id: str,
    *,
    files: Iterable[str] | None = None,
    projects_root: Path | str | None = None,
) -> list[str]:
    """Restore files from a snapshot back into the project directory.

    Parameters
    ----------
    project_dir:
        Path to the live project directory. Must exist.
    snapshot_id:
        The id of the snapshot to restore.
    files:
        Optional subset of the manifest ``files`` list. Defaults to
        restoring every file the manifest names.
    projects_root:
        Optional containment root.

    Returns
    -------
    list[str]
        The list of project file names that were actually restored
        (i.e. existed in the snapshot). Missing files in the
        snapshot are skipped silently.

    Raises
    ------
    LiveProjectError
        On invalid id, missing snapshot, invalid manifest, or
        filesystem error.
    """
    _validate_snapshot_id(snapshot_id)
    project_resolved, snapshots_dir = _resolve_snapshot_dir(
        project_dir, projects_root=projects_root
    )
    snap_dir = snapshots_dir / snapshot_id
    if not snap_dir.exists() or not snap_dir.is_dir():
        raise LiveProjectError(
            ERR_SNAPSHOT_NOT_FOUND,
            f"snapshot {snapshot_id!r} not found under {snapshots_dir}",
            data={
                "snapshotId": snapshot_id,
                "snapshotsDir": str(snapshots_dir),
            },
        )
    manifest = _read_manifest(snap_dir)
    manifest_files = tuple(manifest.get("files", []))
    selected = tuple(files) if files is not None else manifest_files
    for fname in selected:
        if fname not in manifest_files:
            raise LiveProjectError(
                ERR_SNAPSHOT_INVALID_FILES,
                f"file {fname!r} is not in snapshot {snapshot_id}",
                data={"snapshotId": snapshot_id, "file": fname},
            )
    return _copy_files(snap_dir, project_resolved, selected)


def diff_snapshot(
    project_dir: Path | str,
    snapshot_a: str,
    snapshot_b: str,
    *,
    projects_root: Path | str | None = None,
) -> SnapshotDiff:
    """Compute a snapshot-to-snapshot diff.

    The diff is byte-level. Files in ``snapshot_a`` that are not in
    ``snapshot_b`` are returned as ``removed``; the inverse is
    ``added``. Files in both whose content differs are returned as
    ``changed``; files in both with identical content are returned
    as ``unchanged``.

    Both snapshot ids are validated and both snapshot directories
    are required to exist.
    """
    _validate_snapshot_id(snapshot_a)
    _validate_snapshot_id(snapshot_b)
    _, snapshots_dir = _resolve_snapshot_dir(
        project_dir, projects_root=projects_root
    )
    dir_a = snapshots_dir / snapshot_a
    dir_b = snapshots_dir / snapshot_b
    if not dir_a.exists() or not dir_a.is_dir():
        raise LiveProjectError(
            ERR_SNAPSHOT_NOT_FOUND,
            f"snapshot {snapshot_a!r} not found under {snapshots_dir}",
            data={"snapshotId": snapshot_a, "snapshotsDir": str(snapshots_dir)},
        )
    if not dir_b.exists() or not dir_b.is_dir():
        raise LiveProjectError(
            ERR_SNAPSHOT_NOT_FOUND,
            f"snapshot {snapshot_b!r} not found under {snapshots_dir}",
            data={"snapshotId": snapshot_b, "snapshotsDir": str(snapshots_dir)},
        )
    manifest_a = _read_manifest(dir_a)
    manifest_b = _read_manifest(dir_b)
    files_a = set(manifest_a.get("files", []))
    files_b = set(manifest_b.get("files", []))
    added = tuple(sorted(files_b - files_a))
    removed = tuple(sorted(files_a - files_b))
    common = sorted(files_a & files_b)
    changed: list[str] = []
    unchanged: list[str] = []
    for fname in common:
        fa = dir_a / fname
        fb = dir_b / fname
        if not fa.exists() or not fb.exists():
            # If the file is listed in the manifest but missing on
            # disk we surface it as a removal / addition to keep
            # the diff total.
            if fa.exists() and not fb.exists():
                removed_set = set(removed)
                removed_set.add(fname)
                removed = tuple(sorted(removed_set))
            elif fb.exists() and not fa.exists():
                added_set = set(added)
                added_set.add(fname)
                added = tuple(sorted(added_set))
            continue
        try:
            same = fa.read_bytes() == fb.read_bytes()
        except OSError as exc:
            raise LiveProjectError(
                ERR_PROJECT_IO,
                f"failed to read {fa} or {fb}: {exc}",
                data={"pathA": str(fa), "pathB": str(fb), "phase": "diff"},
            ) from exc
        if same:
            unchanged.append(fname)
        else:
            changed.append(fname)
    return SnapshotDiff(
        snapshot_a=snapshot_a,
        snapshot_b=snapshot_b,
        added=added,
        removed=removed,
        changed=tuple(changed),
        unchanged=tuple(unchanged),
    )


__all__ = [
    "DEFAULT_SNAPSHOT_FILES",
    "SNAPSHOT_ID_PATTERN",
    "SNAPSHOT_MANIFEST_NAME",
    "SNAPSHOT_MANIFEST_SCHEMA_VERSION",
    "SnapshotDiff",
    "SnapshotInfo",
    "create_snapshot",
    "diff_snapshot",
    "list_snapshots",
    "restore_snapshot",
]
