"""Local JSON-RPC sidecar for the Hardware Design Workbench.

The desktop shell owns process lifetime while this module owns the typed
workbench service boundary.  It deliberately exposes project and document
operations only; simulator execution remains behind the existing allowlisted
Python runners until its job broker is added in a later increment.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, TextIO, cast

from .digital_emulator import RunResult, run_program
from .led_matrix import render_tiny8_led_frames
from .workbench import (
    ChangeSetResult,
    WorkbenchError,
    WorkbenchProject,
    apply_change_set,
    create_workbench_project,
    migrate_workbench_project,
    open_workbench_project,
    read_document,
    validate_workbench_project,
)

JSON_RPC_VERSION: Final[str] = "2.0"
ENGINE_VERSION: Final[str] = "0.1"
ENGINE_PROTOCOL_VERSION: Final[str] = "1.0"
ERR_METHOD_NOT_FOUND: Final[str] = "ENGINE_METHOD_NOT_FOUND"
ERR_PARAMS_INVALID: Final[str] = "ENGINE_PARAMS_INVALID"
ERR_REQUEST_INVALID: Final[str] = "ENGINE_REQUEST_INVALID"
ERR_INTERNAL: Final[str] = "ENGINE_INTERNAL_ERROR"

METHODS: Final[tuple[str, ...]] = (
    "design.applyChanges",
    "design.get",
    "digital.emulate",
    "engine.handshake",
    "project.create",
    "project.migrate",
    "project.open",
    "project.validate",
)


class EngineRequestError(ValueError):
    """Validation failure at the JSON-RPC boundary."""

    def __init__(self, code: str, message: str, *, data: Mapping[str, object]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = dict(data)


class EngineService:
    """Dispatch only allowlisted local workbench operations."""

    def __init__(self, projects_root: Path | str) -> None:
        self.projects_root = Path(projects_root).expanduser().resolve(strict=False)
        try:
            self.projects_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                f"cannot initialise projects root: {exc}",
                data={"projectsRoot": str(self.projects_root)},
            ) from exc
        if not self.projects_root.is_dir():
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "projects root must be a directory",
                data={"projectsRoot": str(self.projects_root)},
            )
        self._handlers: dict[str, Callable[[Mapping[str, object]], dict[str, object]]] = {
            "design.applyChanges": self._design_apply_changes,
            "design.get": self._design_get,
            "digital.emulate": self._digital_emulate,
            "engine.handshake": self._engine_handshake,
            "project.create": self._project_create,
            "project.migrate": self._project_migrate,
            "project.open": self._project_open,
            "project.validate": self._project_validate,
        }

    def handle(self, request: object) -> dict[str, object] | None:
        """Handle one JSON-RPC request and return its response when requested."""
        request_id: object | None = None
        is_notification = False
        try:
            if not isinstance(request, Mapping):
                raise EngineRequestError(
                    ERR_REQUEST_INVALID,
                    "request must be an object",
                    data={},
                )
            request_id = request.get("id")
            is_notification = "id" not in request
            if request.get("jsonrpc") != JSON_RPC_VERSION:
                raise EngineRequestError(
                    ERR_REQUEST_INVALID,
                    "jsonrpc must equal 2.0",
                    data={"jsonrpc": str(request.get("jsonrpc"))},
                )
            method = request.get("method")
            if not isinstance(method, str):
                raise EngineRequestError(
                    ERR_REQUEST_INVALID,
                    "method must be a string",
                    data={},
                )
            params = request.get("params", {})
            if not isinstance(params, Mapping):
                raise EngineRequestError(
                    ERR_PARAMS_INVALID,
                    "params must be an object",
                    data={"method": method},
                )
            handler = self._handlers.get(method)
            if handler is None:
                return None if is_notification else _error_response(
                    request_id,
                    -32601,
                    "method not found",
                    {"code": ERR_METHOD_NOT_FOUND, "method": method},
                )
            result = handler(cast(Mapping[str, object], params))
            return None if is_notification else _result_response(request_id, result)
        except EngineRequestError as exc:
            return None if is_notification else _error_response(
                request_id,
                -32602,
                exc.message,
                {"code": exc.code, **exc.data},
            )
        except WorkbenchError as exc:
            return None if is_notification else _error_response(
                request_id,
                -32000,
                exc.message,
                {"code": exc.code, "details": exc.data},
            )
        except Exception:
            return None if is_notification else _error_response(
                request_id,
                -32603,
                "internal engine error",
                {"code": ERR_INTERNAL},
            )

    def _engine_handshake(self, _params: Mapping[str, object]) -> dict[str, object]:
        return {
            "capabilities": {"methods": list(METHODS)},
            "engineVersion": ENGINE_VERSION,
            "protocolVersion": ENGINE_PROTOCOL_VERSION,
        }

    def _project_create(self, params: Mapping[str, object]) -> dict[str, object]:
        project_id = _required_string(params, "projectId")
        display_name = _optional_string(params, "displayName")
        project = create_workbench_project(
            self.projects_root, project_id, display_name=display_name
        )
        return _project_payload(project)

    def _project_open(self, params: Mapping[str, object]) -> dict[str, object]:
        project = self._open_scoped_project(params)
        return _project_payload(project)

    def _project_migrate(self, params: Mapping[str, object]) -> dict[str, object]:
        project_dir = _required_string(params, "projectDir")
        result = migrate_workbench_project(project_dir, projects_root=self.projects_root)
        return {
            "migratedFrom": result.migrated_from,
            "project": _project_payload(result.project),
        }

    def _project_validate(self, params: Mapping[str, object]) -> dict[str, object]:
        project = self._open_scoped_project(params)
        return validate_workbench_project(project.project_dir)

    def _design_get(self, params: Mapping[str, object]) -> dict[str, object]:
        project = self._open_scoped_project(params)
        document = _required_string(params, "document")
        return {
            "document": read_document(project.project_dir, document),
            "project": _project_payload(project),
        }

    def _design_apply_changes(self, params: Mapping[str, object]) -> dict[str, object]:
        project = self._open_scoped_project(params)
        change_set = params.get("changeSet")
        if not isinstance(change_set, Mapping):
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "changeSet must be an object",
                data={"field": "changeSet"},
            )
        result: ChangeSetResult = apply_change_set(project.project_dir, change_set)
        return {
            "changedDocuments": list(result.changed_documents),
            "revision": result.revision,
        }

    def _digital_emulate(self, params: Mapping[str, object]) -> dict[str, object]:
        rom = _required_unsigned_array(params, "rom", maximum=0xFFFF, limit=256)
        max_cycles = _optional_unsigned_int(
            params, "maxCycles", default=10_000, minimum=1, maximum=1_000_000
        )
        render_led = params.get("renderLed", False)
        if not isinstance(render_led, bool):
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "renderLed must be a boolean",
                data={"field": "renderLed"},
            )
        result = run_program(
            rom,
            max_cycles=max_cycles,
            inputs=_optional_byte_mapping(params, "inputs"),
        )
        payload = _run_result_payload(result)
        if render_led:
            rendered = render_tiny8_led_frames(result.output_events)
            payload["led"] = {
                "diagnostics": [
                    {"code": item.code, "cycle": item.cycle, "x": item.x, "y": item.y}
                    for item in rendered.diagnostics
                ],
                "frames": [
                    {
                        "cycle": frame.cycle,
                        "height": frame.height,
                        "pixels": list(frame.pixels),
                        "width": frame.width,
                    }
                    for frame in rendered.frames
                ],
                "height": rendered.height,
                "width": rendered.width,
            }
        return payload

    def _open_scoped_project(self, params: Mapping[str, object]) -> WorkbenchProject:
        project_dir = _required_string(params, "projectDir")
        return open_workbench_project(project_dir, projects_root=self.projects_root)


def serve(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    projects_root: Path | str,
) -> None:
    """Run the NDJSON stdio loop used by the Tauri sidecar host."""
    service = EngineService(projects_root)
    for raw_line in input_stream:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            response: dict[str, object] | None = _error_response(
                None,
                -32700,
                "parse error",
                {"code": ERR_REQUEST_INVALID},
            )
        else:
            response = service.handle(request)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
            output_stream.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the engine as a local stdio sidecar."""
    from .projects_root import get_default_projects_root

    parser = argparse.ArgumentParser(description="Local Hardware Design Workbench engine")
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=None,
        help=(
            "directory used to contain workbench projects "
            f"(falls back to LTAGENT_PROJECTS_ROOT or {get_default_projects_root()})"
        ),
    )
    args = parser.parse_args(argv)
    root = args.projects_root
    if root is None:
        root = get_default_projects_root()
    serve(sys.stdin, sys.stdout, projects_root=root)
    return 0


def _required_string(params: Mapping[str, object], field: str) -> str:
    value = params.get(field)
    if not isinstance(value, str) or not value:
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            f"{field} must be a non-empty string",
            data={"field": field},
        )
    return value


def _optional_string(params: Mapping[str, object], field: str) -> str | None:
    value = params.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            f"{field} must be a non-empty string when supplied",
            data={"field": field},
        )
    return value


def _required_unsigned_array(
    params: Mapping[str, object], field: str, *, maximum: int, limit: int
) -> list[int]:
    value = params.get(field)
    if not isinstance(value, list) or not value or len(value) > limit:
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            f"{field} must be a non-empty array containing at most {limit} items",
            data={"field": field},
        )
    parsed: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= maximum:
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                f"{field} values must be unsigned integers no larger than {maximum}",
                data={"field": field, "index": index},
            )
        parsed.append(item)
    return parsed


def _optional_unsigned_int(
    params: Mapping[str, object],
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = params.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            f"{field} must be an integer in [{minimum}, {maximum}]",
            data={"field": field},
        )
    return value


def _optional_byte_mapping(params: Mapping[str, object], field: str) -> dict[int, int]:
    value = params.get(field, {})
    if not isinstance(value, Mapping):
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            f"{field} must be an object",
            data={"field": field},
        )
    parsed: dict[int, int] = {}
    for raw_port, raw_value in value.items():
        try:
            port = int(str(raw_port), 0)
        except ValueError as exc:
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                f"{field} keys must be byte values",
                data={"field": field, "port": str(raw_port)},
            ) from exc
        if not 0 <= port <= 0xFF or isinstance(raw_value, bool) or not isinstance(raw_value, int) or not 0 <= raw_value <= 0xFF:
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                f"{field} ports and values must be unsigned bytes",
                data={"field": field, "port": str(raw_port)},
            )
        parsed[port] = raw_value
    return parsed


def _project_payload(project: WorkbenchProject) -> dict[str, object]:
    return {
        "displayName": project.display_name,
        "projectDir": str(project.project_dir),
        "projectId": project.project_id,
        "revision": project.revision,
        "schemaVersion": ENGINE_PROTOCOL_VERSION,
    }


def _run_result_payload(result: RunResult) -> dict[str, object]:
    if result.fault is not None:
        status = "fault"
    elif result.timed_out:
        status = "timeout"
    elif result.halted:
        status = "halted"
    else:
        status = "completed"
    return {
        "fault": (
            {"code": result.fault.code, "pc": result.fault.pc, "word": result.fault.word}
            if result.fault is not None
            else None
        ),
        "outputEvents": [
            {"cycle": event.cycle, "data": event.data, "port": event.port}
            for event in result.output_events
        ],
        "state": {
            "acc": result.state.acc,
            "cycles": result.state.cycles,
            "halted": result.state.halted,
            "pc": result.state.pc,
            "zeroFlag": result.state.zero_flag,
        },
        "status": status,
        "timedOut": result.timed_out,
    }


def _result_response(request_id: object | None, result: Mapping[str, object]) -> dict[str, object]:
    return {"id": request_id, "jsonrpc": JSON_RPC_VERSION, "result": dict(result)}


def _error_response(
    request_id: object | None,
    code: int,
    message: str,
    data: Mapping[str, object],
) -> dict[str, object]:
    return {
        "error": {"code": code, "data": dict(data), "message": message},
        "id": request_id,
        "jsonrpc": JSON_RPC_VERSION,
    }


if __name__ == "__main__":  # pragma: no cover - exercised through the entry point.
    raise SystemExit(main())


__all__ = [
    "ENGINE_PROTOCOL_VERSION",
    "ENGINE_VERSION",
    "JSON_RPC_VERSION",
    "METHODS",
    "EngineRequestError",
    "EngineService",
    "main",
    "serve",
]
