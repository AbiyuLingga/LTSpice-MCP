"""Local JSON-RPC sidecar for the Hardware Design Workbench.

The desktop shell owns process lifetime while this module owns the typed
workbench service boundary.  It deliberately exposes project and document
operations and allowlisted simulator jobs through one local service boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Final, TextIO, cast

from .analog_workbench import ANALOG_TOOL_ID, discover_analog_tool, run_analog_simulation
from .design_service import (
    ERR_CHANGESET_CONFLICT,
    ERR_PROJECT_NOT_FOUND,
    DesignService,
    WorkbenchV2Error,
)
from .digital_emulator import RunResult, run_program
from .digital_workbench import (
    IVERILOG_TOOL_ID,
    VERILATOR_TOOL_ID,
    VVP_TOOL_ID,
    YOSYS_TOOL_ID,
    DigitalDesignIR,
    discover_tool,
    run_simulation,
    run_synthesis,
)
from .jobs import JobBroker, JobBrokerError, JobKind, JobNotifier
from .led_matrix import render_tiny8_led_frames
from .live.graph_schema import Analysis, AnalysisKind, CircuitGraph
from .workbench import (
    WorkbenchError,
    create_workbench_project,
)
from .workbench_migration import migrate_workbench_project_to_v2
from .workbench_v2 import HardwareProject

JSON_RPC_VERSION: Final[str] = "2.0"
ENGINE_VERSION: Final[str] = "0.2"
ENGINE_PROTOCOL_VERSION: Final[str] = "2.0"
ERR_METHOD_NOT_FOUND: Final[str] = "ENGINE_METHOD_NOT_FOUND"
ERR_PARAMS_INVALID: Final[str] = "ENGINE_PARAMS_INVALID"
ERR_REQUEST_INVALID: Final[str] = "ENGINE_REQUEST_INVALID"
ERR_INTERNAL: Final[str] = "ENGINE_INTERNAL_ERROR"

METHODS: Final[tuple[str, ...]] = (
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

    def __init__(self, projects_root: Path | str, *, notify: JobNotifier | None = None) -> None:
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
        self.design = DesignService(str(self.projects_root))
        self.jobs = JobBroker(self.projects_root, notify=notify)
        self._handlers: dict[str, Callable[[Mapping[str, object]], dict[str, object]]] = {
            "artifact.readSlice": self._artifact_read_slice,
            "design.applyChanges": self._design_apply_changes,
            "design.get": self._design_get,
            "design.redo": self._design_redo,
            "design.undo": self._design_undo,
            "digital.emulate": self._digital_emulate,
            "engine.handshake": self._engine_handshake,
            "job.cancel": self._job_cancel,
            "job.status": self._job_status,
            "project.create": self._project_create,
            "project.migrate": self._project_migrate,
            "project.open": self._project_open,
            "project.refresh": self._project_refresh,
            "project.validate": self._project_validate,
            "simulation.start": self._simulation_start,
            "synthesis.start": self._synthesis_start,
            "tool.doctor": self._tool_doctor,
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
                return (
                    None
                    if is_notification
                    else _error_response(
                        request_id,
                        -32601,
                        "method not found",
                        {"code": ERR_METHOD_NOT_FOUND, "method": method},
                    )
                )
            result = handler(cast(Mapping[str, object], params))
            return None if is_notification else _result_response(request_id, result)
        except EngineRequestError as exc:
            return (
                None
                if is_notification
                else _error_response(
                    request_id,
                    -32602,
                    exc.message,
                    {"code": exc.code, **exc.data},
                )
            )
        except WorkbenchV2Error as exc:
            code = "REVISION_CONFLICT" if exc.code == ERR_CHANGESET_CONFLICT else exc.code
            return (
                None
                if is_notification
                else _error_response(
                    request_id,
                    -32000,
                    exc.message,
                    {"code": code, **exc.data},
                )
            )
        except WorkbenchError as exc:
            return (
                None
                if is_notification
                else _error_response(
                    request_id,
                    -32000,
                    exc.message,
                    {"code": exc.code, "details": exc.data},
                )
            )
        except JobBrokerError as exc:
            return (
                None
                if is_notification
                else _error_response(
                    request_id,
                    -32000,
                    exc.message,
                    {"code": exc.code},
                )
            )
        except Exception:
            return (
                None
                if is_notification
                else _error_response(
                    request_id,
                    -32603,
                    "internal engine error",
                    {"code": ERR_INTERNAL},
                )
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
        migrate_workbench_project_to_v2(project.project_dir, projects_root=self.projects_root)
        return self._project_payload(self.design.open_project(project_id))

    def _project_open(self, params: Mapping[str, object]) -> dict[str, object]:
        return self._project_payload(self.design.open_project(self._project_id(params)))

    def _project_migrate(self, params: Mapping[str, object]) -> dict[str, object]:
        project_id = self._project_id(params)
        project_dir = self.projects_root / project_id
        result = migrate_workbench_project_to_v2(project_dir, projects_root=self.projects_root)
        return {
            "migration": result.to_dict(),
            "project": self._project_payload(self.design.open_project(project_id)),
        }

    def _project_validate(self, params: Mapping[str, object]) -> dict[str, object]:
        return self.design.validate_project(self._project_id(params))

    def _project_refresh(self, params: Mapping[str, object]) -> dict[str, object]:
        project = self.design.open_project(self._project_id(params))
        known_revision = params.get("knownRevision")
        if known_revision is not None and not isinstance(known_revision, int):
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "knownRevision must be an integer",
                data={"field": "knownRevision"},
            )
        return {
            "changed": known_revision is None or known_revision != project.revision,
            "project": self._project_payload(project),
        }

    def _design_get(self, params: Mapping[str, object]) -> dict[str, object]:
        project_id = self._project_id(params)
        project = self.design.open_project(project_id)
        document = _required_string(params, "document")
        return {
            "document": self.design.read_document(project_id, document),
            "project": self._project_payload(project),
        }

    def _design_apply_changes(self, params: Mapping[str, object]) -> dict[str, object]:
        project_id = self._project_id(params)
        change_set = params.get("changeSet")
        if not isinstance(change_set, Mapping):
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "changeSet must be an object",
                data={"field": "changeSet"},
            )
        return self.design.apply_change_set(project_id, change_set).to_dict()

    def _design_undo(self, params: Mapping[str, object]) -> dict[str, object]:
        result = self.design.undo(self._project_id(params))
        return {"changed": result is not None, **(result.to_dict() if result else {})}

    def _design_redo(self, params: Mapping[str, object]) -> dict[str, object]:
        result = self.design.redo(self._project_id(params))
        return {"changed": result is not None, **(result.to_dict() if result else {})}

    def _simulation_start(self, params: Mapping[str, object]) -> dict[str, object]:
        project_id = self._project_id(params)
        project = self.design.open_project(project_id)
        domain = _required_string(params, "domain")
        timeout = _optional_number(
            params, "timeoutSeconds", default=30.0, minimum=1.0, maximum=3600.0
        )
        if domain == "analog":
            try:
                graph = CircuitGraph.model_validate(self.design.read_document(project_id, "analog"))
            except ValueError as exc:
                raise EngineRequestError(
                    ERR_PARAMS_INVALID,
                    "analog document is not simulation-ready",
                    data={"domain": domain, "reason": str(exc)},
                ) from exc
            if not graph.analyses:
                graph = graph.model_copy(update={"analyses": [Analysis(kind=AnalysisKind.OP)]})

            def work(cancel, run_dir, progress):  # type: ignore[no-untyped-def]
                if cancel.is_set():
                    return {"status": "cancelled"}
                progress(10, "rendering analog netlist")
                result = run_analog_simulation(
                    project_id,
                    self.projects_root,
                    graph,
                    timeout_seconds=timeout,
                    cancel_event=cancel,
                    run_dir=run_dir,
                )
                progress(100, "analog simulation finished")
                return result.bundle.to_dict()

            return self.jobs.start(
                project_id=project_id,
                project_revision=project.revision,
                kind=JobKind.ANALOG_SIMULATE,
                tool_id=ANALOG_TOOL_ID,
                work=work,
                timeout_seconds=timeout,
            )
        if domain == "digital":
            design = self._digital_design(project_id)

            def work(cancel, run_dir, progress):  # type: ignore[no-untyped-def]
                if cancel.is_set():
                    return {"status": "cancelled"}
                progress(10, "linting deterministic Verilog")
                lint = run_simulation(
                    project_id,
                    self.projects_root / project_id,
                    design,
                    tool_id=VERILATOR_TOOL_ID,
                    timeout_seconds=timeout,
                    cancel_event=cancel,
                    run_dir=run_dir,
                )
                if lint.bundle.status != "success":
                    return lint.bundle.to_dict()
                progress(40, "running Icarus simulation")
                result = run_simulation(
                    project_id,
                    self.projects_root / project_id,
                    design,
                    timeout_seconds=timeout,
                    cancel_event=cancel,
                    run_dir=run_dir,
                )
                progress(100, "digital simulation finished")
                return result.bundle.to_dict()

            return self.jobs.start(
                project_id=project_id,
                project_revision=project.revision,
                kind=JobKind.DIGITAL_SIMULATE,
                tool_id=IVERILOG_TOOL_ID,
                work=work,
                timeout_seconds=timeout,
            )
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            "domain must be analog or digital",
            data={"domain": domain},
        )

    def _synthesis_start(self, params: Mapping[str, object]) -> dict[str, object]:
        project_id = self._project_id(params)
        project = self.design.open_project(project_id)
        timeout = _optional_number(
            params, "timeoutSeconds", default=60.0, minimum=1.0, maximum=3600.0
        )
        design = self._digital_design(project_id)

        def work(cancel, run_dir, progress):  # type: ignore[no-untyped-def]
            if cancel.is_set():
                return {"status": "cancelled"}
            progress(10, "generating deterministic Verilog")
            result = run_synthesis(
                project_id,
                self.projects_root / project_id,
                design,
                timeout_seconds=timeout,
                cancel_event=cancel,
                run_dir=run_dir,
            )
            progress(100, "synthesis finished")
            return result.bundle.to_dict()

        return self.jobs.start(
            project_id=project_id,
            project_revision=project.revision,
            kind=JobKind.DIGITAL_SYNTHESIZE,
            tool_id=YOSYS_TOOL_ID,
            work=work,
            timeout_seconds=timeout,
        )

    def _job_status(self, params: Mapping[str, object]) -> dict[str, object]:
        return self.jobs.status(_required_string(params, "jobId"))

    def _job_cancel(self, params: Mapping[str, object]) -> dict[str, object]:
        return self.jobs.cancel(_required_string(params, "jobId"))

    def _artifact_read_slice(self, params: Mapping[str, object]) -> dict[str, object]:
        return self.jobs.read_artifact_slice(
            _required_string(params, "jobId"),
            _required_string(params, "artifact"),
            offset=_optional_unsigned_int(
                params, "offset", default=0, minimum=0, maximum=2**63 - 1
            ),
            limit=_optional_unsigned_int(
                params,
                "limit",
                default=64 * 1024,
                minimum=1,
                maximum=JobBroker.max_artifact_slice,
            ),
        )

    def _tool_doctor(self, _params: Mapping[str, object]) -> dict[str, object]:
        analog = discover_analog_tool()
        tools: list[dict[str, object]] = [
            {
                "available": analog is not None,
                "installHint": "sudo apt install ngspice",
                "path": str(analog.executable) if analog else None,
                "purpose": "analog simulation",
                "required": True,
                "toolId": ANALOG_TOOL_ID,
                "version": analog.version if analog else None,
            }
        ]
        for tool_id, purpose in (
            (IVERILOG_TOOL_ID, "digital compilation"),
            (VVP_TOOL_ID, "digital simulation"),
            (VERILATOR_TOOL_ID, "digital lint"),
            (YOSYS_TOOL_ID, "digital synthesis"),
        ):
            tool = discover_tool(tool_id)
            tools.append(
                {
                    "available": tool is not None,
                    "installHint": (
                        "sudo apt install iverilog"
                        if tool_id in {IVERILOG_TOOL_ID, VVP_TOOL_ID}
                        else f"sudo apt install {tool_id}"
                    ),
                    "path": str(tool.executable) if tool else None,
                    "purpose": purpose,
                    "required": True,
                    "toolId": tool_id,
                    "version": tool.version if tool else None,
                }
            )
        return {
            "status": "pass" if all(bool(item["available"]) for item in tools) else "warn",
            "tools": tools,
        }

    def _digital_design(self, project_id: str) -> DigitalDesignIR:
        document = self.design.read_document(project_id, "digital")
        design = document.get("design")
        if not isinstance(design, Mapping):
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "digital document does not contain a design",
                data={"document": "digital"},
            )
        try:
            return DigitalDesignIR.model_validate(design)
        except ValueError as exc:
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                "digital document is not simulation-ready",
                data={"document": "digital", "reason": str(exc)},
            ) from exc

    def close(self) -> None:
        self.jobs.close()

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

    def _project_id(self, params: Mapping[str, object]) -> str:
        project_id = params.get("projectId")
        if isinstance(project_id, str) and project_id:
            return project_id
        project_dir = (
            Path(_required_string(params, "projectDir")).expanduser().resolve(strict=False)
        )
        try:
            project_dir.relative_to(self.projects_root)
        except ValueError as exc:
            raise WorkbenchV2Error(
                ERR_PROJECT_NOT_FOUND,
                "projectDir must be inside the projects root",
                data={"projectDir": str(project_dir)},
            ) from exc
        return project_dir.name

    def _project_payload(self, project: HardwareProject) -> dict[str, object]:
        return {
            **project.model_dump(mode="json"),
            "projectDir": str(self.projects_root / project.projectId),
        }


def serve(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    projects_root: Path | str,
) -> None:
    """Run the NDJSON stdio loop used by the Tauri sidecar host."""
    output_lock = Lock()

    def write_message(message: Mapping[str, object]) -> None:
        with output_lock:
            output_stream.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")
            output_stream.flush()

    def notify(method: str, payload: dict[str, object]) -> None:
        write_message({"jsonrpc": JSON_RPC_VERSION, "method": method, "params": payload})

    service = EngineService(projects_root, notify=notify)
    try:
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
                write_message(response)
    finally:
        service.close()


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


def _optional_number(
    params: Mapping[str, object],
    field: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = params.get(field, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not minimum <= value <= maximum
    ):
        raise EngineRequestError(
            ERR_PARAMS_INVALID,
            f"{field} must be a number in [{minimum}, {maximum}]",
            data={"field": field},
        )
    return float(value)


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
        if (
            not 0 <= port <= 0xFF
            or isinstance(raw_value, bool)
            or not isinstance(raw_value, int)
            or not 0 <= raw_value <= 0xFF
        ):
            raise EngineRequestError(
                ERR_PARAMS_INVALID,
                f"{field} ports and values must be unsigned bytes",
                data={"field": field, "port": str(raw_port)},
            )
        parsed[port] = raw_value
    return parsed


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
