"""Contract tests for the local JSON-RPC workbench engine sidecar."""

from __future__ import annotations

import io
import json
from pathlib import Path

from ltagent.engine_server import EngineService, serve


def _service(tmp_path: Path) -> EngineService:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return EngineService(projects_root)


def _request(request_id: int, method: str, params: dict[str, object]) -> dict[str, object]:
    return {"id": request_id, "jsonrpc": "2.0", "method": method, "params": params}


def test_handshake_advertises_versioned_local_capabilities(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(
        _request(1, "engine.handshake", {"protocolVersion": "1.0"})
    )

    assert response == {
        "id": 1,
        "jsonrpc": "2.0",
        "result": {
            "capabilities": {
                "methods": [
                    "design.applyChanges",
                    "design.get",
                    "engine.handshake",
                    "project.create",
                    "project.migrate",
                    "project.open",
                    "project.validate",
                ],
            },
            "engineVersion": "0.1",
            "protocolVersion": "1.0",
        },
    }


def test_project_and_design_requests_share_one_revisioned_contract(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.handle(
        _request(1, "project.create", {"displayName": "Analog Lab", "projectId": "analog_lab"})
    )
    project_dir = created["result"]["projectDir"]  # type: ignore[index]

    initial = service.handle(
        _request(2, "design.get", {"document": "schematic", "projectDir": project_dir})
    )
    assert initial["result"]["document"]["gridSize"] == 16  # type: ignore[index]

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
                            "type": "replace_document",
                            "value": {
                                "gridSize": 16,
                                "nodes": [{"id": "R1", "kind": "resistor"}],
                                "schemaVersion": "1.0",
                                "wires": [],
                            },
                        }
                    ],
                    "schemaVersion": "1.0",
                },
                "projectDir": project_dir,
            },
        )
    )

    assert applied["result"] == {"changedDocuments": ["schematic"], "revision": 1}  # type: ignore[index]


def test_engine_rejects_project_paths_outside_its_root(tmp_path: Path) -> None:
    response = _service(tmp_path).handle(
        _request(1, "project.open", {"projectDir": str(tmp_path)})
    )

    assert response["error"]["data"]["code"] == "WORKBENCH_PROJECT_NOT_FOUND"  # type: ignore[index]


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
    assert response["result"]["engineVersion"] == "0.1"
