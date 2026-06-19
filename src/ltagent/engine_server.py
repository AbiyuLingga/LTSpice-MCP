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
    parser = argparse.ArgumentParser(description="Local Hardware Design Workbench engine")
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=Path.cwd() / "projects",
        help="directory used to contain workbench projects",
    )
    args = parser.parse_args(argv)
    serve(sys.stdin, sys.stdout, projects_root=args.projects_root)
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


def _project_payload(project: WorkbenchProject) -> dict[str, object]:
    return {
        "displayName": project.display_name,
        "projectDir": str(project.project_dir),
        "projectId": project.project_id,
        "revision": project.revision,
        "schemaVersion": ENGINE_PROTOCOL_VERSION,
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
