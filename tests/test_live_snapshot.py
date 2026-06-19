"""Tests for :mod:`ltagent.live.snapshot`.

Covers the snapshot / restore / diff system (plan §9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent.live.history import make_history_event
from ltagent.live.project import (
    DIR_SNAPSHOTS,
    FILE_GRAPH,
    FILE_HISTORY,
    LiveProjectError,
    create_live_project,
    write_graph,
)
from ltagent.live.snapshot import (
    DEFAULT_SNAPSHOT_FILES,
    ERR_SNAPSHOT_INVALID_FILES,
    ERR_SNAPSHOT_INVALID_ID,
    ERR_SNAPSHOT_NOT_FOUND,
    SNAPSHOT_ID_PATTERN,
    SNAPSHOT_MANIFEST_NAME,
    SNAPSHOT_MANIFEST_SCHEMA_VERSION,
    SnapshotDiff,
    SnapshotInfo,
    create_snapshot,
    diff_snapshot,
    list_snapshots,
    restore_snapshot,
)


def _setup_project(tmp_path: Path, name: str = "amp_01") -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    paths = create_live_project(root, name)
    return paths.project_dir


def _write_file(project_dir: Path, name: str, content: str) -> Path:
    p = project_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# --- create_snapshot --------------------------------------------------------


def test_create_snapshot_copies_default_files(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    _write_file(project, FILE_HISTORY, "first line\n")

    info = create_snapshot(project, "initial")

    assert isinstance(info, SnapshotInfo)
    assert info.snapshot_dir.is_dir()
    assert info.snapshot_dir.parent == project / DIR_SNAPSHOTS
    assert FILE_GRAPH in info.files
    assert FILE_HISTORY in info.files
    assert "circuit.cir" not in info.files


def test_create_snapshot_writes_manifest(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))

    info = create_snapshot(project, "before edit")
    manifest_path = info.snapshot_dir / SNAPSHOT_MANIFEST_NAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == SNAPSHOT_MANIFEST_SCHEMA_VERSION
    assert manifest["snapshotId"] == info.snapshot_id
    assert manifest["reason"] == "before edit"
    assert FILE_GRAPH in manifest["files"]
    assert SNAPSHOT_MANIFEST_NAME in {p.name for p in info.snapshot_dir.iterdir()}


def test_create_snapshot_id_is_monotonic_and_slugged(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    a = create_snapshot(project, "first")
    b = create_snapshot(project, "Second Try")
    c = create_snapshot(project, "Third/With Slashes")
    assert a.snapshot_id == "001_first"
    assert b.snapshot_id == "002_second_try"
    assert c.snapshot_id == "003_third_with_slashes"
    for sid in (a.snapshot_id, b.snapshot_id, c.snapshot_id):
        assert SNAPSHOT_ID_PATTERN.match(sid) is not None


def test_create_snapshot_id_counter_survives_existing_dirs(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    a = create_snapshot(project, "alpha")
    (project / DIR_SNAPSHOTS / "garbage").mkdir()
    b = create_snapshot(project, "beta")
    assert a.snapshot_id == "001_alpha"
    assert b.snapshot_id == "002_beta"


def test_create_snapshot_rejects_empty_reason(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    with pytest.raises(LiveProjectError):
        create_snapshot(project, "")


def test_create_snapshot_rejects_absolute_path_in_files(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        create_snapshot(project, "bad", files=["/etc/passwd"])
    assert excinfo.value.code == ERR_SNAPSHOT_INVALID_FILES


def test_create_snapshot_rejects_dotdot_in_files(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        create_snapshot(project, "bad", files=["../escape"])
    assert excinfo.value.code == ERR_SNAPSHOT_INVALID_FILES


def test_create_snapshot_id_collides_with_manually_planted_dir(
    tmp_path: Path,
) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    create_snapshot(project, "alpha")
    manual = project / DIR_SNAPSHOTS / "002_alpha"
    manual.mkdir()
    b = create_snapshot(project, "alpha")
    assert manual.is_dir()
    assert b.snapshot_id != manual.name
    assert int(b.snapshot_id[:3]) >= 3


def test_create_snapshot_keeps_projects_isolated(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    p1 = create_live_project(root, "p1").project_dir
    p2 = create_live_project(root, "p2").project_dir
    _write_file(p1, FILE_GRAPH, json.dumps({"v": "p1"}))
    info = create_snapshot(p1, "alpha")
    assert info.snapshot_dir.is_relative_to(p1)
    assert not (p2 / DIR_SNAPSHOTS).joinpath(info.snapshot_id).exists()
    snap_graph = json.loads((info.snapshot_dir / FILE_GRAPH).read_text(encoding="utf-8"))
    assert snap_graph == {"v": "p1"}


# --- list_snapshots ---------------------------------------------------------


def test_list_snapshots_returns_in_creation_order(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    a = create_snapshot(project, "alpha")
    b = create_snapshot(project, "beta")
    c = create_snapshot(project, "gamma")
    listing = list_snapshots(project)
    assert [s.snapshot_id for s in listing] == [a.snapshot_id, b.snapshot_id, c.snapshot_id]


def test_list_snapshots_skips_corrupt_manifests(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    good = create_snapshot(project, "alpha")
    (project / DIR_SNAPSHOTS / "999_corrupt").mkdir()
    (project / DIR_SNAPSHOTS / "999_corrupt" / SNAPSHOT_MANIFEST_NAME).write_text(
        "{not valid json", encoding="utf-8"
    )
    listing = list_snapshots(project)
    assert [s.snapshot_id for s in listing] == [good.snapshot_id]


def test_list_snapshots_empty_when_no_snapshots(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    assert list_snapshots(project) == []


# --- restore_snapshot -------------------------------------------------------


def test_restore_snapshot_brings_files_back(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    _write_file(project, FILE_HISTORY, "alpha\n")
    snap = create_snapshot(project, "before edit")

    _write_file(project, FILE_GRAPH, json.dumps({"v": 2}))
    _write_file(project, FILE_HISTORY, "beta\n")

    restored = restore_snapshot(project, snap.snapshot_id)
    assert FILE_GRAPH in restored
    assert FILE_HISTORY in restored
    assert json.loads((project / FILE_GRAPH).read_text(encoding="utf-8")) == {"v": 1}
    assert (project / FILE_HISTORY).read_text(encoding="utf-8") == "alpha\n"


def test_restore_snapshot_can_restore_a_subset(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    _write_file(project, FILE_HISTORY, "alpha\n")
    snap = create_snapshot(project, "before edit")
    _write_file(project, FILE_GRAPH, json.dumps({"v": 2}))
    _write_file(project, FILE_HISTORY, "beta\n")

    restored = restore_snapshot(project, snap.snapshot_id, files=[FILE_GRAPH])
    assert restored == [FILE_GRAPH]
    assert json.loads((project / FILE_GRAPH).read_text(encoding="utf-8")) == {"v": 1}
    assert (project / FILE_HISTORY).read_text(encoding="utf-8") == "beta\n"


def test_restore_snapshot_rejects_unknown_id(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        restore_snapshot(project, "nope")
    assert excinfo.value.code == ERR_SNAPSHOT_INVALID_ID


def test_restore_snapshot_rejects_malformed_id(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    for bad in ("", "alpha", "001", "1_alpha", "001_"):
        with pytest.raises(LiveProjectError) as excinfo:
            restore_snapshot(project, bad)
        assert excinfo.value.code == ERR_SNAPSHOT_INVALID_ID


def test_restore_snapshot_rejects_missing_snapshot(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        restore_snapshot(project, "001_ghost")
    assert excinfo.value.code == ERR_SNAPSHOT_NOT_FOUND


def test_restore_snapshot_rejects_file_not_in_manifest(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    snap = create_snapshot(project, "alpha")
    with pytest.raises(LiveProjectError) as excinfo:
        restore_snapshot(project, snap.snapshot_id, files=["circuit.cir"])
    assert excinfo.value.code == ERR_SNAPSHOT_INVALID_FILES


# --- diff_snapshot ----------------------------------------------------------


def test_diff_snapshot_reports_changed_and_unchanged(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    _write_file(project, FILE_HISTORY, "alpha\n")
    a = create_snapshot(project, "alpha")
    _write_file(project, FILE_GRAPH, json.dumps({"v": 2}))
    b = create_snapshot(project, "beta")
    diff = diff_snapshot(project, a.snapshot_id, b.snapshot_id)
    assert isinstance(diff, SnapshotDiff)
    d = diff.to_dict()
    assert d["snapshotA"] == a.snapshot_id
    assert d["snapshotB"] == b.snapshot_id
    assert FILE_GRAPH in d["changed"]
    assert FILE_HISTORY in d["unchanged"]
    assert d["added"] == []
    assert d["removed"] == []


def test_diff_snapshot_reports_added_and_removed(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    a = create_snapshot(project, "alpha", files=[FILE_GRAPH])
    _write_file(project, FILE_HISTORY, "beta\n")
    b = create_snapshot(project, "beta", files=[FILE_GRAPH, FILE_HISTORY])
    diff = diff_snapshot(project, a.snapshot_id, b.snapshot_id)
    d = diff.to_dict()
    assert FILE_HISTORY in d["added"]
    assert d["removed"] == []
    assert FILE_GRAPH in d["unchanged"]


def test_diff_snapshot_rejects_unknown_id(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    with pytest.raises(LiveProjectError):
        diff_snapshot(project, "nope", "001_also_nope")


def test_diff_snapshot_rejects_missing_snapshot(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _write_file(project, FILE_GRAPH, json.dumps({"v": 1}))
    a = create_snapshot(project, "alpha")
    with pytest.raises(LiveProjectError) as excinfo:
        diff_snapshot(project, a.snapshot_id, "999_ghost")
    assert excinfo.value.code == ERR_SNAPSHOT_NOT_FOUND


def test_diff_snapshot_rejects_malformed_id(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    for bad in ("", "alpha", "001", "1_alpha"):
        with pytest.raises(LiveProjectError) as excinfo:
            diff_snapshot(project, bad, "001_alpha")
        assert excinfo.value.code == ERR_SNAPSHOT_INVALID_ID


# --- default file list is the canonical project set -------------------------


def test_default_snapshot_files_match_project_file_names() -> None:
    from ltagent.live.project import PROJECT_FILE_NAMES

    assert set(DEFAULT_SNAPSHOT_FILES) == set(PROJECT_FILE_NAMES)


# --- end-to-end -------------------------------------------------------------


def test_edit_then_snapshot_then_restore_round_trip(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    write_graph(project, {"v": 1, "component": "R1"})
    snap1 = create_snapshot(project, "before add opamp")

    write_graph(project, {"v": 2, "component": "R1", "opamp": "U1"})
    snap2 = create_snapshot(project, "after add opamp")

    diff = diff_snapshot(project, snap1.snapshot_id, snap2.snapshot_id)
    assert FILE_GRAPH in diff.changed

    restore_snapshot(project, snap1.snapshot_id, files=[FILE_GRAPH])
    loaded = json.loads((project / FILE_GRAPH).read_text(encoding="utf-8"))
    assert loaded == {"v": 1, "component": "R1"}


def test_snapshot_preserves_history_events(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    from ltagent.live.history import append_history

    append_history(project, make_history_event(step=1, op="create"))
    append_history(project, make_history_event(step=2, op="set_value"))
    snap = create_snapshot(project, "after edits")
    raw = (snap.snapshot_dir / FILE_HISTORY).read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert "step" in obj
        assert "op" in obj
