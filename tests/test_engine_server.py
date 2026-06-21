"""Contract tests for the local JSON-RPC workbench engine sidecar."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from ltagent.engine_server import EngineService, serve


def _write_rc_graph(project_dir: Path) -> None:
    (project_dir / "design" / "analog" / "main.graph.json").write_text(
        json.dumps(
            {
                "analyses": [{"kind": "op"}],
                "components": {
                    "C1": {
                        "id": "C1",
                        "kind": "capacitor",
                        "pins": {"pins": {"p1": "vout", "p2": "0"}},
                        "value": "100n",
                    },
                    "R1": {
                        "id": "R1",
                        "kind": "resistor",
                        "pins": {"pins": {"p1": "vin", "p2": "vout"}},
                        "value": "1k",
                    },
                    "V1": {
                        "id": "V1",
                        "kind": "voltage_source",
                        "pins": {"pins": {"p1": "vin", "p2": "0"}},
                        "value": "1",
                    },
                },
                "directives": [],
                "domain": "analog",
                "measurements": [],
                "nets": {
                    "0": {"name": "0", "type": "ground"},
                    "vin": {"name": "vin", "type": "signal"},
                    "vout": {"name": "vout", "type": "signal"},
                },
                "projectId": "rc_lab",
                "schemaVersion": "0.2",
                "topology": "rc_lowpass",
            }
        ),
        encoding="utf-8",
    )


def _service(tmp_path: Path) -> EngineService:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return EngineService(projects_root)


def _request(request_id: int, method: str, params: dict[str, object]) -> dict[str, object]:
    return {"id": request_id, "jsonrpc": "2.0", "method": method, "params": params}


def test_handshake_advertises_versioned_local_capabilities(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(
        _request(1, "engine.handshake", {"protocolVersion": "2.0"})
    )

    assert response == {
        "id": 1,
        "jsonrpc": "2.0",
        "result": {
            "capabilities": {
                "methods": [
                    "artifact.readSlice",
                    "design.applyChanges",
                    "design.get",
                    "design.redo",
                    "design.undo",
                    "digital.emulate",
                    "engine.handshake",
                    "job.cancel",
                    "job.status",
                    "project.create",
                    "project.migrate",
                    "project.open",
                    "project.refresh",
                    "project.validate",
                    "simulation.start",
                    "synthesis.start",
                    "tool.doctor",
                ],
            },
            "engineVersion": "0.2",
            "protocolVersion": "2.0",
        },
    }


def test_project_and_design_requests_share_one_revisioned_contract(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.handle(
        _request(1, "project.create", {"displayName": "Analog Lab", "projectId": "analog_lab"})
    )
    project_dir = created["result"]["projectDir"]  # type: ignore[index]
    assert created["result"]["schemaVersion"] == "2.0"  # type: ignore[index]

    initial = service.handle(
        _request(2, "design.get", {"document": "schematic", "projectDir": project_dir})
    )
    assert initial["result"]["document"]["gridSize"] == 16  # type: ignore[index]
    assert initial["result"]["document"]["symbols"] == []  # type: ignore[index]

    applied = service.handle(
        _request(
            3,
            "design.applyChanges",
            {
                "changeSet": {
                    "baseRevision": 0,
                    "operations": [
                        {
                            "document": "schematic",
                            "type": "place_node",
                            "symbolId": "R1",
                            "kind": "resistor",
                            "x": 96,
                            "y": 144,
                        }
                    ],
                    "schemaVersion": "2.0",
                },
                "projectDir": project_dir,
            },
        )
    )

    assert applied["result"]["changedDocuments"] == ["schematic"]  # type: ignore[index]
    assert applied["result"]["revision"] == 1  # type: ignore[index]

    refreshed = service.handle(
        _request(4, "project.refresh", {"knownRevision": 0, "projectDir": project_dir})
    )
    assert refreshed["result"]["changed"] is True  # type: ignore[index]
    assert refreshed["result"]["project"]["revision"] == 1  # type: ignore[index]

    undone = service.handle(_request(5, "design.undo", {"projectDir": project_dir}))
    assert undone["result"]["revision"] == 0  # type: ignore[index]
    redone = service.handle(_request(6, "design.redo", {"projectDir": project_dir}))
    assert redone["result"]["revision"] == 1  # type: ignore[index]


def test_engine_reports_revision_conflict_without_overwriting(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.handle(_request(1, "project.create", {"projectId": "conflict"}))
    project_dir = created["result"]["projectDir"]  # type: ignore[index]
    change_set = {
        "baseRevision": 0,
        "operations": [
            {
                "document": "schematic",
                "type": "place_node",
                "symbolId": "R1",
                "kind": "resistor",
                "x": 0,
                "y": 0,
            }
        ],
        "schemaVersion": "2.0",
    }
    service.handle(
        _request(2, "design.applyChanges", {"changeSet": change_set, "projectDir": project_dir})
    )

    response = service.handle(
        _request(3, "design.applyChanges", {"changeSet": change_set, "projectDir": project_dir})
    )

    assert response["error"]["data"]["code"] == "REVISION_CONFLICT"  # type: ignore[index]
    assert response["error"]["data"]["actualRevision"] == 1  # type: ignore[index]


def test_engine_rejects_project_paths_outside_its_root(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(_request(1, "project.open", {"projectDir": str(tmp_path)}))

    assert response["error"]["data"]["code"] == "WORKBENCH_V2_PROJECT_NOT_FOUND"  # type: ignore[index]


def test_engine_returns_json_rpc_errors_for_unknown_methods(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(_request(7, "run_shell", {}))

    assert response["error"] == {  # type: ignore[index]
        "code": -32601,
        "data": {"code": "ENGINE_METHOD_NOT_FOUND", "method": "run_shell"},
        "message": "method not found",
    }


def test_serve_processes_ndjson_without_writing_protocol_noise(tmp_path: Path) -> None:
    incoming = io.StringIO(json.dumps(_request(1, "engine.handshake", {})) + "\n")
    outgoing = io.StringIO()

    serve(incoming, outgoing, projects_root=tmp_path / "projects")

    response = json.loads(outgoing.getvalue())
    assert response["result"]["engineVersion"] == "0.2"


def test_engine_emulates_a_tiny8_led_program_without_external_toolchains(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(
        _request(
            8,
            "digital.emulate",
            {
                "maxCycles": 16,
                "renderLed": True,
                "rom": [0x1002, 0xC0F0, 0x1003, 0xC0F1, 0x1001, 0xC0F2, 0xC0F4, 0xF000],
            },
        )
    )

    assert response["result"]["status"] == "halted"  # type: ignore[index]
    assert response["result"]["led"]["frames"][0]["pixels"][26] is True  # type: ignore[index]


def test_engine_runs_analog_job_and_reports_missing_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    created = service.handle(_request(1, "project.create", {"projectId": "rc_lab"}))
    project_dir = Path(created["result"]["projectDir"])  # type: ignore[index]
    _write_rc_graph(project_dir)
    monkeypatch.setattr("ltagent.analog_workbench.discover_analog_tool", lambda: None)

    started = service.handle(
        _request(
            2,
            "simulation.start",
            {"domain": "analog", "projectDir": str(project_dir)},
        )
    )
    job_id = started["result"]["jobId"]  # type: ignore[index]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = service.handle(_request(3, "job.status", {"jobId": job_id}))
        if status["result"]["state"] in {"completed", "failed", "skipped"}:  # type: ignore[index]
            break
        time.sleep(0.01)
    else:
        raise AssertionError("analog job did not finish")

    assert status["result"]["state"] == "skipped"  # type: ignore[index]
    assert status["result"]["result"]["status"] == "skipped"  # type: ignore[index]
    service.close()


def test_engine_reads_artifact_from_its_job_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ltagent.analog_workbench import ToolInfo

    service = _service(tmp_path)
    created = service.handle(_request(1, "project.create", {"projectId": "rc_lab"}))
    project_dir = Path(created["result"]["projectDir"])  # type: ignore[index]
    _write_rc_graph(project_dir)
    fake_tool = tmp_path / "ngspice"
    fake_tool.write_text("#!/bin/sh\necho 'gain = 1' > \"$3\"\n", encoding="utf-8")
    fake_tool.chmod(0o755)
    monkeypatch.setattr(
        "ltagent.analog_workbench.discover_analog_tool",
        lambda: ToolInfo(toolId="ngspice", executable=fake_tool, version="test"),
    )

    started = service.handle(
        _request(2, "simulation.start", {"domain": "analog", "projectId": "rc_lab"})
    )
    job_id = started["result"]["jobId"]  # type: ignore[index]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = service.handle(_request(3, "job.status", {"jobId": job_id}))
        if status["result"]["state"] == "completed":  # type: ignore[index]
            break
        time.sleep(0.01)
    else:
        raise AssertionError("analog job did not finish")

    artifact = service.handle(
        _request(
            4,
            "artifact.readSlice",
            {"artifact": "circuit.cir", "jobId": job_id, "limit": 4096, "offset": 0},
        )
    )
    assert "R1" in artifact["result"]["text"]  # type: ignore[index]
    service.close()


def test_engine_rejects_unknown_simulation_domain(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.handle(_request(1, "project.create", {"projectId": "unsafe"}))

    response = service.handle(
        _request(2, "simulation.start", {"domain": "shell", "projectId": "unsafe"})
    )

    assert response["error"]["data"]["code"] == "ENGINE_PARAMS_INVALID"  # type: ignore[index]
    service.close()


def test_tool_doctor_reports_allowlisted_backends(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(_request(1, "tool.doctor", {}))

    tools = response["result"]["tools"]  # type: ignore[index]
    assert {item["toolId"] for item in tools} == {
        "iverilog",
        "ngspice",
        "verilator",
        "vvp",
        "yosys",
    }
    assert all(item["installHint"].startswith("sudo apt install") for item in tools)
