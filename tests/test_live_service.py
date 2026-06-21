from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ltagent.asc import ASCError
from ltagent.live.project import LiveProjectError, apply_operation, create_live_project, write_graph


def _graph() -> dict[str, Any]:
    return {
        "schemaVersion": "0.2",
        "projectId": "demo",
        "domain": "analog",
        "topology": "rc_lowpass",
        "components": {
            "R1": {
                "id": "R1",
                "kind": "resistor",
                "value": "1.59k",
                "pins": {"pins": {"1": "in", "2": "out"}},
            },
            "C1": {
                "id": "C1",
                "kind": "capacitor",
                "value": "100n",
                "pins": {"pins": {"1": "out", "2": "0"}},
            },
        },
        "nets": {
            "0": {"name": "0", "type": "ground", "aliases": []},
            "in": {"name": "in", "type": "signal", "aliases": []},
            "out": {"name": "out", "type": "signal", "aliases": []},
        },
        "analyses": [],
        "measurements": [],
        "directives": [],
        "constraints": {},
        "layoutHints": None,
    }


def test_apply_operation_snapshots_updates_graph_and_appends_history(tmp_path: Path) -> None:
    paths = create_live_project(tmp_path, "demo")
    write_graph(paths.project_dir, _graph(), projects_root=tmp_path)

    result = apply_operation(
        paths.project_dir,
        {
            "op": "set_component_value",
            "args": {"componentId": "R1", "value": "1.6k"},
            "reason": "select E24 value",
        },
        projects_root=tmp_path,
    )

    assert result["success"] is True
    assert result["snapshotId"] == "001_before_set_component_value"
    graph = json.loads(paths.graph.read_text(encoding="utf-8"))
    assert graph["components"]["R1"]["value"] == "1.6k"
    history = [json.loads(line) for line in paths.history.read_text().splitlines()]
    assert history[-1]["op"] == "set_component_value"
    assert history[-1]["reason"] == "select E24 value"
    assert "LIVE_GENERATION_NOT_RUN" in result["warnings"]


def test_invalid_operation_does_not_write_or_snapshot(tmp_path: Path) -> None:
    paths = create_live_project(tmp_path, "demo")
    write_graph(paths.project_dir, _graph(), projects_root=tmp_path)
    before = paths.graph.read_bytes()

    result = apply_operation(
        paths.project_dir,
        {"op": "set_component_value", "args": {"componentId": "missing", "value": "1k"}},
        projects_root=tmp_path,
    )

    assert result["success"] is False
    assert paths.graph.read_bytes() == before
    assert not list(paths.snapshots.iterdir())
    assert not paths.history.exists()


def test_apply_operation_rejects_project_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-live-project"
    outside.mkdir(exist_ok=True)

    with pytest.raises(LiveProjectError, match="not under") as exc_info:
        apply_operation(
            outside,
            {"op": "set_component_value", "args": {"componentId": "R1", "value": "1k"}},
            projects_root=tmp_path,
        )

    assert exc_info.value.code == "PATH_TRAVERSAL"


def test_apply_operation_regenerates_ir_netlist_and_schematic(tmp_path: Path) -> None:
    graph = _graph()
    graph["components"]["Vin"] = {
        "id": "Vin",
        "kind": "voltage_source",
        "value": "AC 1",
        "pins": {"pins": {"+": "in", "-": "0"}},
    }
    graph["analyses"] = [
        {
            "kind": "ac",
            "startFreq": "10",
            "stopFreq": "100k",
            "pointsPerDecade": 100,
        }
    ]
    paths = create_live_project(tmp_path, "demo")
    write_graph(paths.project_dir, graph, projects_root=tmp_path)

    result = apply_operation(
        paths.project_dir,
        {"op": "set_component_value", "args": {"componentId": "R1", "value": "1.6k"}},
        projects_root=tmp_path,
    )

    assert result["success"] is True
    assert "LIVE_GENERATION_NOT_RUN" not in result["warnings"]
    ir_components = json.loads(paths.ir.read_text(encoding="utf-8"))["components"]
    assert {component["id"] for component in ir_components} == {"Vin", "R1", "C1"}
    assert "R1 in out 1.6k" in paths.cir.read_text(encoding="utf-8")
    assert paths.asc.read_text(encoding="utf-8").startswith("Version 4\n")


def test_apply_operation_reports_renderer_failure_as_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph()
    graph["components"]["Vin"] = {
        "id": "Vin",
        "kind": "voltage_source",
        "value": "AC 1",
        "pins": {"pins": {"+": "in", "-": "0"}},
    }
    graph["analyses"] = [
        {
            "kind": "ac",
            "startFreq": "10",
            "stopFreq": "100k",
            "pointsPerDecade": 100,
        }
    ]
    paths = create_live_project(tmp_path, "demo")
    write_graph(paths.project_dir, graph, projects_root=tmp_path)

    called: list[bool] = []

    def reject_layout(*_args: object, **_kwargs: object) -> object:
        called.append(True)
        raise ASCError("layout is not supported")

    monkeypatch.setattr("ltagent.asc.render_asc", reject_layout)
    result = apply_operation(
        paths.project_dir,
        {"op": "set_component_value", "args": {"componentId": "R1", "value": "1.6k"}},
        projects_root=tmp_path,
    )

    assert called == [True]
    assert result["success"] is True
    assert "LIVE_GENERATION_NOT_RUN" in result["warnings"]
    assert (
        json.loads(paths.graph.read_text(encoding="utf-8"))["components"]["R1"]["value"] == "1.6k"
    )
