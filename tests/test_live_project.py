"""Tests for :mod:`ltagent.live.project` and :mod:`ltagent.live.history`.

Covers the Live Project + Snapshot workstream (plan §6, §9, §10).

* Project creation / open / path safety.
* Atomic graph read + write round-trip.
* Path-traversal rejection at every entry point.
* ``edit_history.jsonl`` append-only semantics: every appended line
  is valid JSON, lines accumulate, the file survives multiple
  appenders.

The tests use only :func:`tmp_path`; no LTspice / Wine is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ltagent.live.history import (
    MAX_EVENT_BYTES,
    HistoryEvent,
    append_history,
    make_history_event,
    next_step,
    read_history,
)
from ltagent.live.project import (
    DIR_SNAPSHOTS,
    ERR_GRAPH_INVALID_JSON,
    ERR_GRAPH_NOT_FOUND,
    ERR_INVALID_PROJECT_ID,
    ERR_NOT_A_DIRECTORY,
    ERR_PROJECT_NOT_FOUND,
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
    PROJECT_FILE_NAMES,
    LiveProjectError,
    ProjectPaths,
    create_live_project,
    get_project_paths,
    open_live_project,
    read_graph,
    write_graph,
)


def _setup_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _slurp(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- create_live_project ----------------------------------------------------


def test_create_live_project_creates_expected_structure(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "rc_lowpass_1khz")
    assert paths.project_dir.is_dir()
    expected_map = {
        FILE_GRAPH: paths.graph,
        FILE_IR: paths.ir,
        FILE_CIR: paths.cir,
        FILE_ASC: paths.asc,
        FILE_METADATA: paths.metadata,
        FILE_RESULT: paths.result,
        FILE_VERIFICATION: paths.verification,
        FILE_CALCULATION_JSON: paths.calculation_json,
        FILE_CALCULATION_MD: paths.calculation_md,
        FILE_HISTORY: paths.history,
    }
    for name in PROJECT_FILE_NAMES:
        assert (paths.project_dir / name) == expected_map[name]
    assert paths.snapshots.is_dir()
    assert paths.snapshots.name == DIR_SNAPSHOTS
    assert sorted(p.name for p in paths.project_dir.iterdir()) == [DIR_SNAPSHOTS]


def test_create_live_project_returns_project_paths_with_absolute_root(
    tmp_path: Path,
) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    assert isinstance(paths, ProjectPaths)
    assert paths.project_dir.parent == root.resolve()


def test_create_live_project_rejects_duplicate(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    create_live_project(root, "dup")
    with pytest.raises(FileExistsError):
        create_live_project(root, "dup")


def test_create_live_project_rejects_invalid_id(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    for bad in ["", "Bad", "1leading_digit", "with space", "with/slash"]:
        with pytest.raises(LiveProjectError) as excinfo:
            create_live_project(root, bad)
        assert excinfo.value.code == ERR_INVALID_PROJECT_ID


# --- open_live_project ------------------------------------------------------


def test_open_live_project_round_trip(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    created = create_live_project(root, "amp_01")
    opened = open_live_project(created.project_dir)
    assert opened.project_dir == created.project_dir
    assert opened.graph == created.graph


def test_open_live_project_with_projects_root_under(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    created = create_live_project(root, "amp_01")
    opened = open_live_project(created.project_dir, projects_root=root)
    assert opened.project_dir == created.project_dir


def test_open_live_project_missing(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        open_live_project(root / "does_not_exist")
    assert excinfo.value.code == ERR_PROJECT_NOT_FOUND


def test_open_live_project_not_a_directory(tmp_path: Path) -> None:
    p = tmp_path / "some_file"
    p.write_text("not a dir", encoding="utf-8")
    with pytest.raises(LiveProjectError) as excinfo:
        open_live_project(p)
    assert excinfo.value.code == ERR_NOT_A_DIRECTORY


# --- get_project_paths ------------------------------------------------------


def test_get_project_paths_is_pure(tmp_path: Path) -> None:
    p = get_project_paths(tmp_path / "demo")
    q = get_project_paths(tmp_path / "demo")
    assert p == q
    assert p.graph == tmp_path / "demo" / FILE_GRAPH


# --- write_graph / read_graph ----------------------------------------------


def test_write_and_read_graph_round_trip(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    graph: dict[str, Any] = {
        "schemaVersion": "0.2",
        "projectId": "amp_01",
        "topology": "noninv_opamp",
        "components": {"R1": {"kind": "resistor", "value": "10k"}},
    }
    out = write_graph(paths.project_dir, graph)
    assert out == paths.graph
    assert out.is_file()
    loaded = read_graph(paths.project_dir)
    assert loaded == graph
    parsed = json.loads(_slurp(paths.graph))
    assert parsed == graph


def test_write_graph_rejects_non_mapping(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    with pytest.raises(LiveProjectError) as excinfo:
        write_graph(paths.project_dir, ["not", "a", "mapping"])  # type: ignore[arg-type]
    assert excinfo.value.code == ERR_GRAPH_INVALID_JSON


def test_read_graph_missing_file(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    with pytest.raises(LiveProjectError) as excinfo:
        read_graph(paths.project_dir)
    assert excinfo.value.code == ERR_GRAPH_NOT_FOUND


def test_read_graph_corrupt_json(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    paths.graph.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LiveProjectError) as excinfo:
        read_graph(paths.project_dir)
    assert excinfo.value.code == ERR_GRAPH_INVALID_JSON


def test_read_graph_rejects_json_array(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    paths.graph.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(LiveProjectError) as excinfo:
        read_graph(paths.project_dir)
    assert excinfo.value.code == ERR_GRAPH_INVALID_JSON


def test_write_graph_atomic_replaces_existing(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    write_graph(paths.project_dir, {"v": 1})
    write_graph(paths.project_dir, {"v": 2})
    assert read_graph(paths.project_dir) == {"v": 2}
    leftovers = list(paths.project_dir.glob("*.tmp"))
    assert leftovers == []


# --- path-traversal ---------------------------------------------------------


def test_create_rejects_path_traversal_with_dotdot(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        create_live_project(root, "..")
    assert excinfo.value.code in {"PATH_TRAVERSAL", ERR_INVALID_PROJECT_ID}


def test_create_rejects_nested_path_traversal(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        create_live_project(root, "nested/../../escape")
    assert excinfo.value.code in {"PATH_TRAVERSAL", ERR_INVALID_PROJECT_ID}


def test_create_rejects_absolute_escape(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    with pytest.raises(LiveProjectError) as excinfo:
        create_live_project(root, "/etc/passwd")
    assert excinfo.value.code == ERR_INVALID_PROJECT_ID


def test_open_rejects_path_traversal_with_projects_root(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    elsewhere = other / "amp_01"
    elsewhere.mkdir()
    with pytest.raises(LiveProjectError) as excinfo:
        open_live_project(elsewhere, projects_root=root)
    assert excinfo.value.code == "PATH_TRAVERSAL"


def test_write_graph_rejects_traversal_with_projects_root(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    elsewhere = other / "amp_01"
    elsewhere.mkdir()
    with pytest.raises(LiveProjectError) as excinfo:
        write_graph(elsewhere, {"v": 1}, projects_root=root)
    assert excinfo.value.code == "PATH_TRAVERSAL"


def test_read_graph_rejects_traversal_with_projects_root(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    elsewhere = other / "amp_01"
    elsewhere.mkdir()
    with pytest.raises(LiveProjectError) as excinfo:
        read_graph(elsewhere, projects_root=root)
    assert excinfo.value.code == "PATH_TRAVERSAL"


# --- history: append + read + next_step ------------------------------------


def test_append_history_creates_file_on_first_write(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    assert not paths.history.exists()
    event = make_history_event(
        step=1, op="create_project", project_id="amp_01", reason="initial"
    )
    out = append_history(paths.project_dir, event)
    assert out == paths.history
    assert paths.history.is_file()


def test_append_history_produces_valid_jsonl(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    events = [
        make_history_event(step=1, op="create_project", reason="initial"),
        make_history_event(
            step=2, op="set_component_value", target="R1", old="10k", new="12k"
        ),
        make_history_event(
            step=3, op="run_simulation", success=True, extra={"fc": "1.05kHz"}
        ),
    ]
    for ev in events:
        append_history(paths.project_dir, ev)

    raw = _slurp(paths.history)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 3
    for line in lines:
        decoded = json.loads(line)
        assert isinstance(decoded, dict)


def test_append_history_preserves_order(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    for i in range(1, 6):
        append_history(
            paths.project_dir,
            make_history_event(step=i, op=f"op_{i}"),
        )
    events = read_history(paths.project_dir)
    assert [e["step"] for e in events] == [1, 2, 3, 4, 5]
    assert [e["op"] for e in events] == ["op_1", "op_2", "op_3", "op_4", "op_5"]


def test_append_history_appends_does_not_overwrite(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    append_history(paths.project_dir, make_history_event(step=1, op="first"))
    append_history(paths.project_dir, make_history_event(step=2, op="second"))
    raw = _slurp(paths.history)
    assert raw.count("\n") == 2


def test_append_history_accepts_plain_mapping(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    payload: dict[str, Any] = {
        "step": 1,
        "op": "create_project",
        "time": "2026-06-19T00:00:00+00:00",
        "extra": {"note": "hi"},
    }
    append_history(paths.project_dir, payload)
    assert read_history(paths.project_dir)[0] == payload


def test_append_history_rejects_invalid_event(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    with pytest.raises(LiveProjectError) as excinfo:
        append_history(paths.project_dir, {"step": "not_an_int", "op": "x", "time": "t"})
    assert excinfo.value.code == "LIVE_HISTORY_INVALID"


def test_append_history_rejects_missing_op(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    with pytest.raises(LiveProjectError) as excinfo:
        append_history(paths.project_dir, {"step": 1, "time": "t"})
    assert excinfo.value.code == "LIVE_HISTORY_INVALID"


def test_read_history_empty_when_missing(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    assert read_history(paths.project_dir) == []


def test_read_history_skips_blank_lines(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    paths.history.parent.mkdir(parents=True, exist_ok=True)
    paths.history.write_text(
        json.dumps({"step": 1, "op": "x", "time": "t"}) + "\n"
        "\n"
        "   \n"
        + json.dumps({"step": 2, "op": "y", "time": "t"}) + "\n",
        encoding="utf-8",
    )
    events = read_history(paths.project_dir)
    assert [e["step"] for e in events] == [1, 2]


def test_read_history_rejects_corrupt_line(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    paths.history.parent.mkdir(parents=True, exist_ok=True)
    paths.history.write_text(
        json.dumps({"step": 1, "op": "x", "time": "t"}) + "\n"
        "this is not json\n",
        encoding="utf-8",
    )
    with pytest.raises(LiveProjectError) as excinfo:
        read_history(paths.project_dir)
    assert excinfo.value.code == "LIVE_HISTORY_INVALID_JSON"


def test_next_step_returns_one_for_empty(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    assert next_step(paths.project_dir) == 1


def test_next_step_returns_max_plus_one(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    for s in (1, 2, 5, 3):
        append_history(paths.project_dir, make_history_event(step=s, op="x"))
    assert next_step(paths.project_dir) == 6


def test_append_history_size_guard(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    huge = "x" * (MAX_EVENT_BYTES + 1)
    with pytest.raises(LiveProjectError) as excinfo:
        append_history(
            paths.project_dir,
            {"step": 1, "op": "x", "time": "t", "extra": {"blob": huge}},
        )
    assert excinfo.value.code == "LIVE_HISTORY_TOO_LARGE"


def test_history_event_to_dict_round_trip() -> None:
    ev = HistoryEvent(
        step=1,
        op="create_project",
        time="2026-06-19T00:00:00+00:00",
        project_id="amp_01",
        reason="initial",
        target=None,
        old=None,
        new=None,
        success=True,
        extra={"x": 1},
    )
    d = ev.to_dict()
    assert d["step"] == 1
    assert d["op"] == "create_project"
    assert d["project_id"] == "amp_01"
    assert d["success"] is True
    assert d["extra"] == {"x": 1}


def test_project_supports_concurrent_graph_and_history(tmp_path: Path) -> None:
    root = _setup_root(tmp_path)
    paths = create_live_project(root, "amp_01")
    write_graph(paths.project_dir, {"v": 1})
    append_history(paths.project_dir, make_history_event(step=1, op="create"))
    write_graph(paths.project_dir, {"v": 2})
    append_history(paths.project_dir, make_history_event(step=2, op="update"))
    assert read_graph(paths.project_dir) == {"v": 2}
    assert [e["step"] for e in read_history(paths.project_dir)] == [1, 2]
