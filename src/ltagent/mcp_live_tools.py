"""MCP live-editing tools (Phase 13, "file-based live editing + math accuracy").

This module exposes **tool-level** functions for the live-editing
surface documented in
``ltspice_file_based_live_editing_math_plan.md`` (§11 — MCP live editing
tools). :mod:`ltagent.mcp_server` registers these callables on the
stdio FastMCP server.

The module is intentionally MCP-SDK-free: it has no dependency on
``mcp.server.fastmcp`` and can be unit-tested in isolation. It mirrors
the JSON output contract from ``docs/SPEC.md §2``
(``success``, ``command``, ``message``, ``data``, ``warnings``,
``errors``) and reuses the same path-safety primitives as
:mod:`ltagent.security` so the integrator gets a consistent error story.

Design contract
---------------

Eight tool-level callables are provided, one per "live" + "math" tool
listed in the plan:

* :func:`tool_live_open_project`        — resolve + load a live project
* :func:`tool_live_inspect_project`     — return the live state view
* :func:`tool_live_apply_edit`          — apply a single edit op
* :func:`tool_live_snapshot`            — snapshot a project
* :func:`tool_live_restore_snapshot`    — restore from a snapshot
* :func:`tool_live_run_and_verify`      — run + verify the project
* :func:`tool_calculate_circuit`        — pure math calculation
* :func:`tool_explain_calculation`      — explain a calculation

Hard rules (from the live-editing plan §22, restated for the tool
surface):

* **No arbitrary shell execution.** No tool accepts or executes a shell
  command, no tool calls :func:`subprocess.run` with ``shell=True``.
* **No arbitrary file write.** Tools either return structured data or
  delegate writes to the live-editing core (the integrator's contract).
  This module never opens a project file for write itself.
* **No workspace escape.** All path-bearing tools resolve their targets
  with :func:`ltagent.security.safe_resolve_under` and reject traversal
  with the stable code ``PATH_TRAVERSAL``.
* **No ``allow_outside_workspace`` knob.** The MCP integration must
  never be able to opt out of the workspace boundary.
* **No ``.raw`` exposure.** Tools do not read or surface ``*.raw``
  files; :func:`ltagent.security.assert_no_raw_path` is the integrator's
  responsibility for resource URIs that fan out to project files.

Optional backend modules
------------------------

The tools use **safe imports** for ``ltagent.live`` and
``ltagent.math_core``: if an optional backend cannot be imported, the call returns the
structured code ``LIVE_MODULE_UNAVAILABLE`` (or ``MATH_CORE_UNAVAILABLE``)
with no raw Python exception leaking to the MCP client.

Math tools never calculate locally. They delegate to
:mod:`ltagent.math_core`, which is the sole numerical authority.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, TypeAlias

from .config import Config, ConfigError, load_config
from .security import (
    ERR_PATH_TRAVERSAL,
    PathSafetyError,
    SecurityError,
    safe_resolve_under,
    validate_slug,
)
from .serialization import to_jsonable as _to_jsonable

# ---------------------------------------------------------------------------
# Public type alias — same shape the CLI / Phase 10 MCP server use
# ---------------------------------------------------------------------------

HandlerResult: TypeAlias = dict[str, Any]

# ---------------------------------------------------------------------------
# Stable error codes (string literals, never use repr() of an exception)
# ---------------------------------------------------------------------------

# Input validation
ERR_INVALID_INPUT: Final[str] = "INVALID_INPUT"
ERR_MISSING_PARAM: Final[str] = "MISSING_PARAM"
ERR_INVALID_OPERATION: Final[str] = "INVALID_OPERATION"
ERR_INVALID_TOPOLOGY: Final[str] = "INVALID_TOPOLOGY"
ERR_INVALID_SNAPSHOT_ID: Final[str] = "INVALID_SNAPSHOT_ID"

# Path safety — codes aligned with ltagent.security. We import the
# traversal code under a local alias so the ``Final`` re-export below
# doesn't try to rebind the import.
_PATH_TRAVERSAL_CODE: Final[str] = ERR_PATH_TRAVERSAL
ERR_PATH_TRAVERSAL_CODE: Final[str] = _PATH_TRAVERSAL_CODE
ERR_PATH_NOT_FOUND: Final[str] = "PATH_NOT_FOUND"
ERR_PROJECT_NOT_FOUND: Final[str] = "PROJECT_NOT_FOUND"
ERR_SNAPSHOT_NOT_FOUND: Final[str] = "SNAPSHOT_NOT_FOUND"

# Backend availability
ERR_LIVE_MODULE_UNAVAILABLE: Final[str] = "LIVE_MODULE_UNAVAILABLE"
ERR_LIVE_METHOD_MISSING: Final[str] = "LIVE_METHOD_MISSING"
ERR_MATH_CORE_UNAVAILABLE: Final[str] = "MATH_CORE_UNAVAILABLE"
ERR_MATH_CORE_METHOD_MISSING: Final[str] = "MATH_CORE_METHOD_MISSING"

# Live-edit operations
ERR_EDIT_OP_FAILED: Final[str] = "EDIT_OP_FAILED"
ERR_SNAPSHOT_FAILED: Final[str] = "SNAPSHOT_FAILED"
ERR_RESTORE_FAILED: Final[str] = "RESTORE_FAILED"
ERR_RUN_FAILED: Final[str] = "RUN_FAILED"
ERR_VERIFY_FAILED: Final[str] = "VERIFY_FAILED"
ERR_CALCULATION_FAILED: Final[str] = "CALCULATION_FAILED"

# Config
ERR_CONFIG_INVALID: Final[str] = "CONFIG_INVALID"


# ---------------------------------------------------------------------------
# Optional backend imports (live editing + math core)
# ---------------------------------------------------------------------------
#
# These are loaded lazily so a missing module does not break import of
# this file. The integrator (and the tests) can monkey-patch the
# resulting module-level globals to inject a fake backend.

_LIVE_MODULE: Any = None
_LIVE_IMPORT_ERROR: BaseException | None = None
try:
    from . import live as _LIVE_MODULE
except Exception as _exc:  # pragma: no cover - import failure fallback
    _LIVE_IMPORT_ERROR = _exc

_MATH_CORE_MODULE: Any = None
_MATH_CORE_IMPORT_ERROR: BaseException | None = None
try:
    from . import math_core as _MATH_CORE_MODULE
except Exception as _exc:  # pragma: no cover - exercised when the math core lands
    _MATH_CORE_IMPORT_ERROR = _exc


# ---------------------------------------------------------------------------
# JSON contract helpers
# ---------------------------------------------------------------------------


def _ok(command: str, message: str, data: dict[str, Any] | None = None) -> HandlerResult:
    return {
        "success": True,
        "command": command,
        "message": message,
        "data": dict(data) if data else {},
        "warnings": [],
        "errors": [],
    }


def _err(
    command: str,
    message: str,
    code: str,
    detail: str,
    data: dict[str, Any] | None = None,
) -> HandlerResult:
    return {
        "success": False,
        "command": command,
        "message": message,
        "data": dict(data) if data else {},
        "warnings": [],
        "errors": [{"code": code, "detail": detail, "data": dict(data) if data else {}}],
    }


def _from_security_error(command: str, exc: SecurityError) -> HandlerResult:
    return _err(command, exc.message, exc.code, exc.message, exc.data)


def _ensure_jsonable(payload: HandlerResult) -> HandlerResult:
    """Defensive: never let a non-JSONable value escape to MCP."""
    try:
        json.dumps(payload)
        return payload
    except (TypeError, ValueError):
        return {
            "success": bool(payload.get("success")),
            "command": str(payload.get("command", "ltagent.live")),
            "message": str(payload.get("message", "")),
            "data": _to_jsonable(payload.get("data", {})),
            "warnings": _to_jsonable(payload.get("warnings", [])),
            "errors": _to_jsonable(payload.get("errors", [])),
        }


# ---------------------------------------------------------------------------
# Path / config helpers
# ---------------------------------------------------------------------------


def _resolve_config(config_path: str | None) -> tuple[Config | None, HandlerResult | None]:
    try:
        cfg = load_config(Path(config_path).expanduser() if config_path else None)
    except ConfigError as exc:
        return None, _err("config", "Invalid configuration", ERR_CONFIG_INVALID, str(exc))
    return cfg, None


def _resolve_projects_root(cfg: Config) -> Path:
    return (Path.cwd() / cfg.workspace.projects_dir).resolve()


def _resolve_project_dir(
    cfg: Config, project_id: str, *, command: str, must_exist: bool = True
) -> tuple[Path | None, HandlerResult | None]:
    """Validate the slug, then resolve the project directory under workspace."""
    try:
        validate_slug(project_id, kind="project id")
    except SecurityError as exc:
        return None, _from_security_error(command, exc)

    projects_root = _resolve_projects_root(cfg)
    project_dir = projects_root / project_id
    try:
        project_dir = safe_resolve_under(
            project_dir, projects_root, must_exist=must_exist
        )
    except PathSafetyError as exc:
        return None, _from_security_error(command, exc)
    return project_dir, None


# ---------------------------------------------------------------------------
# Live module dispatch helper
# ---------------------------------------------------------------------------


def _live_method(name: str) -> Callable[..., Any] | None:
    if _LIVE_MODULE is None:
        return None
    return getattr(_LIVE_MODULE, name, None)


def _math_core_method(name: str) -> Callable[..., Any] | None:
    if _MATH_CORE_MODULE is None:
        return None
    return getattr(_MATH_CORE_MODULE, name, None)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tool_live_open_project(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Open an existing live-editing project by id.

    Returns a JSON view of the on-disk project: project directory,
    metadata.json contents (if present), the canonical artifact
    filenames, and a flag indicating whether the project already has a
    live-editing graph (``circuit.graph.json``).

    This tool does **not** load the project into process memory; the
    integrator's MCP session is expected to do that on the tool call
    site. The function only resolves the path and reads the small
    project metadata.
    """
    command = "live_open_project"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    metadata_path = project_dir / "metadata.json"
    graph_path = project_dir / "circuit.graph.json"
    ir_path = project_dir / "circuit.ir.json"
    netlist_path = project_dir / "circuit.cir"
    schematic_path = project_dir / "circuit.asc"

    metadata: dict[str, Any] | None = None
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = _to_jsonable(loaded)
        except (OSError, json.JSONDecodeError):
            # Corrupt metadata is not fatal for open; report it as a warning.
            pass

    is_live = graph_path.exists()
    files_present = {
        "metadata": metadata_path.exists(),
        "graph": graph_path.exists(),
        "ir": ir_path.exists(),
        "netlist": netlist_path.exists(),
        "schematic": schematic_path.exists(),
    }
    data: dict[str, Any] = {
        "projectId": project_id,
        "projectDir": str(project_dir),
        "isLiveProject": is_live,
        "files": {
            "metadata": str(metadata_path) if files_present["metadata"] else None,
            "graph": str(graph_path) if files_present["graph"] else None,
            "ir": str(ir_path) if files_present["ir"] else None,
            "netlist": str(netlist_path) if files_present["netlist"] else None,
            "schematic": str(schematic_path) if files_present["schematic"] else None,
        },
        "metadata": metadata,
    }
    warnings: list[dict[str, Any]] = []
    if not is_live:
        warnings.append(
            {
                "code": "LIVE_GRAPH_MISSING",
                "detail": "project has no circuit.graph.json; this is not yet a live project",
                "data": {"expected": str(graph_path)},
            }
        )
    payload = _ok(command, f"opened project {project_id}", data)
    payload["warnings"] = warnings
    return _ensure_jsonable(payload)


def tool_live_inspect_project(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Return a live state view: graph, IR, measurements, snapshots."""
    command = "live_inspect_project"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    graph_path = project_dir / "circuit.graph.json"
    ir_path = project_dir / "circuit.ir.json"
    result_path = project_dir / "result.json"
    snapshots_dir = project_dir / ".snapshots"

    def _safe_read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(loaded, dict):
            result: Any = _to_jsonable(loaded)
            return result if isinstance(result, dict) else None
        wrapped: Any = _to_jsonable({"value": loaded})
        return wrapped if isinstance(wrapped, dict) else None

    graph = _safe_read_json(graph_path)
    ir = _safe_read_json(ir_path)
    result = _safe_read_json(result_path)

    snapshot_ids: list[str] = []
    if snapshots_dir.is_dir():
        snapshot_ids = sorted(p.name for p in snapshots_dir.iterdir() if p.is_dir())

    data: dict[str, Any] = {
        "projectId": project_id,
        "projectDir": str(project_dir),
        "graph": graph,
        "ir": ir,
        "result": result,
        "snapshots": snapshot_ids,
        "hasGraph": graph is not None,
        "hasIR": ir is not None,
        "hasResult": result is not None,
    }
    return _ensure_jsonable(_ok(command, f"inspected live project {project_id}", data))


def tool_live_apply_edit(
    project_id: str,
    operation: Mapping[str, Any] | None,
    *,
    auto_snapshot: bool = True,
    config: str | None = None,
) -> HandlerResult:
    """Apply a single edit operation to a live project.

    The ``operation`` argument is a structured dict following the plan
    §8.2 schema::

        {"op": "set_component_value",
         "args": {"componentId": "R1", "value": "1.6k"},
         "reason": "switch to E24 value"}

    This wrapper delegates persistence to
    :func:`ltagent.live.apply_operation`; it does not perform ad-hoc
    file writes itself.
    """
    command = "live_apply_edit"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if not isinstance(operation, Mapping):
        return _err(
            command, "operation must be a dict",
            ERR_INVALID_OPERATION, "operation is missing or not a dict",
            {"receivedType": type(operation).__name__},
        )

    op_name = operation.get("op")
    if not isinstance(op_name, str) or not op_name:
        return _err(
            command, "operation.op must be a non-empty string",
            ERR_INVALID_OPERATION, "operation.op is missing or empty",
            {"operation": dict(operation)},
        )
    op_args = operation.get("args", {})
    if not isinstance(op_args, Mapping):
        return _err(
            command, "operation.args must be a dict",
            ERR_INVALID_OPERATION, "operation.args is not a dict",
            {"receivedType": type(op_args).__name__},
        )
    op_reason_raw = operation.get("reason", "")
    op_reason = str(op_reason_raw) if op_reason_raw is not None else ""

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {
                    "importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None,
                    "op": op_name,
                    "autoSnapshot": auto_snapshot,
                },
            )
        )

    apply_fn = _live_method("apply_operation")
    if apply_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose apply_operation",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
                {"op": op_name},
            )
        )

    op_payload = {"op": op_name, "args": dict(op_args), "reason": op_reason}
    try:
        result = apply_fn(
            project_dir,
            op_payload,
            auto_snapshot=bool(auto_snapshot),
            projects_root=_resolve_projects_root(cfg),
        )
    except (TypeError, ValueError) as exc:
        return _ensure_jsonable(
            _err(command, "live.apply rejected the operation", ERR_EDIT_OP_FAILED, str(exc), {"op": op_name})
        )
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(command, "live.apply raised an unexpected error", ERR_EDIT_OP_FAILED, repr(exc), {"op": op_name})
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    data.setdefault("op", op_name)
    data.setdefault("autoSnapshot", bool(auto_snapshot))
    return _ensure_jsonable(_ok(command, f"applied {op_name} to {project_id}", data))


def tool_live_snapshot(
    project_id: str,
    reason: str | None = None,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Create a snapshot of a live project before risky edits.

    Snapshot persistence is delegated to :mod:`ltagent.live`. An
    import failure is returned as structured ``LIVE_MODULE_UNAVAILABLE``.
    """
    command = "live_snapshot"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if reason is not None and not isinstance(reason, str):
        return _err(
            command, "reason must be a string",
            ERR_INVALID_INPUT, "reason is not a string",
            {"receivedType": type(reason).__name__},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {"importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None},
            )
        )

    snapshot_fn = _live_method("create_snapshot") or _live_method("snapshot")
    if snapshot_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose snapshot",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
            )
        )

    try:
        result = snapshot_fn(
            project_dir,
            reason=reason or "manual_checkpoint",
            projects_root=_resolve_projects_root(cfg),
        )
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(command, "snapshot failed", ERR_SNAPSHOT_FAILED, repr(exc), {"projectId": project_id})
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    data.setdefault("reason", reason or "")
    return _ensure_jsonable(_ok(command, f"snapshot created for {project_id}", data))


def tool_live_restore_snapshot(
    project_id: str,
    snapshot_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Restore a project from a previously created snapshot."""
    command = "live_restore_snapshot"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return _err(
            command, "snapshot id must be a non-empty string",
            ERR_MISSING_PARAM, "snapshot_id is required",
            {"field": "snapshotId"},
        )
    if "/" in snapshot_id or "\\" in snapshot_id or ".." in Path(snapshot_id).parts:
        return _err(
            command, "snapshot id must not contain path separators",
            ERR_INVALID_SNAPSHOT_ID, "snapshot id is not a plain slug",
            {"snapshotId": snapshot_id},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {"importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None},
            )
        )

    restore_fn = _live_method("restore_snapshot") or _live_method("restore")
    if restore_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose restore",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
            )
        )

    try:
        result = restore_fn(
            project_dir,
            snapshot_id,
            projects_root=_resolve_projects_root(cfg),
        )
    except FileNotFoundError as exc:
        return _ensure_jsonable(
            _err(
                command, "snapshot not found", ERR_SNAPSHOT_NOT_FOUND,
                str(exc), {"projectId": project_id, "snapshotId": snapshot_id},
            )
        )
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(
                command, "restore failed", ERR_RESTORE_FAILED, repr(exc),
                {"projectId": project_id, "snapshotId": snapshot_id},
            )
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    data.setdefault("snapshotId", snapshot_id)
    return _ensure_jsonable(_ok(command, f"restored {project_id} from {snapshot_id}", data))


def tool_live_run_and_verify(
    project_id: str,
    *,
    checks: list[Mapping[str, Any]] | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Run the live project's simulation and verify its targets."""
    command = "live_run_and_verify"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if checks is not None and (
        not isinstance(checks, list)
        or any(not isinstance(check, Mapping) for check in checks)
    ):
        return _err(
            command,
            "checks must be a list of objects",
            ERR_INVALID_INPUT,
            "each verification check must be an object",
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {"importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None},
            )
        )

    run_fn = _live_method("run_project_and_verify")
    if run_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose run_project_and_verify",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
            )
        )

    try:
        result = run_fn(
            project_dir,
            cfg,
            list(checks or []),
            projects_root=_resolve_projects_root(cfg),
        )
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(command, "run and verify failed", ERR_RUN_FAILED, repr(exc), {"projectId": project_id})
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    if data.get("success") is False:
        errors = data.pop("errors", [])
        warnings = data.pop("warnings", [])
        return _ensure_jsonable(
            {
                "success": False,
                "command": command,
                "message": "simulation or verification did not pass",
                "data": data,
                "warnings": warnings if isinstance(warnings, list) else [],
                "errors": errors if isinstance(errors, list) else [],
            }
        )
    return _ensure_jsonable(_ok(command, f"ran and verified {project_id}", data))


def tool_calculate_circuit(
    topology: str,
    parameters: Mapping[str, Any] | None,
    *,
    project_id: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Pure-math circuit calculation. Returns ideal values + formulas.

    The function is intentionally side-effect free: it never writes
    any file, never executes shell, and never depends on the project
    being on disk unless ``project_id`` is provided. When the optional
    ``ltagent.math_core`` module is importable, the tool delegates to
    its ``calculate(topology, parameters)`` entry point.
    """
    command = "calculate_circuit"
    if not isinstance(topology, str) or not topology:
        return _err(
            command, "topology must be a non-empty string",
            ERR_MISSING_PARAM, "topology is required",
            {"field": "topology"},
        )
    if not isinstance(parameters, Mapping):
        return _err(
            command, "parameters must be a dict",
            ERR_INVALID_INPUT, "parameters is missing or not a dict",
            {"receivedType": type(parameters).__name__},
        )

    if project_id is not None and not isinstance(project_id, str):
        return _err(
            command, "project id must be a string",
            ERR_INVALID_INPUT, "project_id is not a string",
            {"receivedType": type(project_id).__name__},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    if project_id is not None:
        # Validate the project id but do not require the project to exist;
        # the calculation is independent of the project's state.
        try:
            validate_slug(project_id, kind="project id")
        except SecurityError as exc:
            return _ensure_jsonable(_from_security_error(command, exc))

    if _MATH_CORE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "math core is unavailable",
                ERR_MATH_CORE_UNAVAILABLE,
                "ltagent.math_core is required for every calculation",
                {"importError": repr(_MATH_CORE_IMPORT_ERROR) if _MATH_CORE_IMPORT_ERROR else None},
            )
        )
    calc_fn = _math_core_method("calculate")
    if calc_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "math core method is unavailable",
                ERR_MATH_CORE_METHOD_MISSING,
                "ltagent.math_core does not expose calculate",
            )
        )
    supported = supported_builtin_topologies()
    if supported and topology not in supported:
        return _ensure_jsonable(
            _err(
                command,
                "topology is not supported",
                ERR_INVALID_TOPOLOGY,
                f"unsupported topology {topology!r}",
                {"topology": topology, "supported": list(supported)},
            )
        )
    try:
        result = calc_fn(topology, dict(parameters))
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return _ensure_jsonable(
            _err(command, "math_core.calculate failed", ERR_CALCULATION_FAILED, str(exc), {"topology": topology})
        )
    except Exception as exc:  # pragma: no cover - backend defensive boundary
        return _ensure_jsonable(
            _err(command, "math_core.calculate raised", ERR_CALCULATION_FAILED, repr(exc), {"topology": topology})
        )
    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("topology", topology)
    data.setdefault("source", "math_core")
    return _ensure_jsonable(_ok(command, f"calculated {topology}", data))


def tool_explain_calculation(
    topology: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    project_id: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Return the formulas, assumptions, and verification contract
    for a topology calculation. Pure read-only, no file writes."""
    command = "explain_calculation"
    if not isinstance(topology, str) or not topology:
        return _err(
            command, "topology must be a non-empty string",
            ERR_MISSING_PARAM, "topology is required",
            {"field": "topology"},
        )
    if parameters is not None and not isinstance(parameters, Mapping):
        return _err(
            command, "parameters must be a dict when provided",
            ERR_INVALID_INPUT, "parameters is not a dict",
            {"receivedType": type(parameters).__name__},
        )
    if project_id is not None and not isinstance(project_id, str):
        return _err(
            command, "project id must be a string",
            ERR_INVALID_INPUT, "project_id is not a string",
            {"receivedType": type(project_id).__name__},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    if project_id is not None:
        try:
            validate_slug(project_id, kind="project id")
        except SecurityError as exc:
            return _ensure_jsonable(_from_security_error(command, exc))

    if _MATH_CORE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "math core is unavailable",
                ERR_MATH_CORE_UNAVAILABLE,
                "ltagent.math_core is required for every explanation",
            )
        )
    explain_fn = _math_core_method("explain")
    if explain_fn is None:
        return _ensure_jsonable(
            _err(command, "math core method is unavailable", ERR_MATH_CORE_METHOD_MISSING, "ltagent.math_core does not expose explain")
        )
    supported = supported_builtin_topologies()
    if supported and topology not in supported:
        return _ensure_jsonable(
            _err(command, "topology is not supported", ERR_INVALID_TOPOLOGY, f"unsupported topology {topology!r}", {"supported": list(supported)})
        )
    try:
        result = explain_fn(topology, dict(parameters) if parameters is not None else None)
    except Exception as exc:  # pragma: no cover - backend defensive boundary
        return _ensure_jsonable(
            _err(command, "math_core.explain raised", ERR_CALCULATION_FAILED, repr(exc), {"topology": topology})
        )
    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("topology", topology)
    data.setdefault("source", "math_core")
    return _ensure_jsonable(_ok(command, f"explained {topology}", data))


# ---------------------------------------------------------------------------
# Public introspection helpers (used by the integrator + tests)
# ---------------------------------------------------------------------------


def live_module_available() -> bool:
    """Return True if :mod:`ltagent.live` is importable."""
    return _LIVE_MODULE is not None


def math_core_available() -> bool:
    """Return True if :mod:`ltagent.math_core` is importable."""
    return _MATH_CORE_MODULE is not None


def supported_builtin_topologies() -> tuple[str, ...]:
    """Return topology names exposed by Math Core (legacy helper name)."""
    method = _math_core_method("supported_topologies")
    if method is None:
        return ()
    result = method()
    return tuple(sorted(str(item) for item in result))


__all__ = [
    "ERR_CALCULATION_FAILED",
    "ERR_CONFIG_INVALID",
    "ERR_EDIT_OP_FAILED",
    "ERR_INVALID_INPUT",
    "ERR_INVALID_OPERATION",
    "ERR_INVALID_SNAPSHOT_ID",
    "ERR_INVALID_TOPOLOGY",
    "ERR_LIVE_METHOD_MISSING",
    "ERR_LIVE_MODULE_UNAVAILABLE",
    "ERR_MATH_CORE_METHOD_MISSING",
    "ERR_MATH_CORE_UNAVAILABLE",
    "ERR_MISSING_PARAM",
    "ERR_PROJECT_NOT_FOUND",
    "ERR_RESTORE_FAILED",
    "ERR_RUN_FAILED",
    "ERR_SNAPSHOT_FAILED",
    "ERR_SNAPSHOT_NOT_FOUND",
    "ERR_VERIFY_FAILED",
    "live_module_available",
    "math_core_available",
    "supported_builtin_topologies",
    "tool_calculate_circuit",
    "tool_explain_calculation",
    "tool_live_apply_edit",
    "tool_live_inspect_project",
    "tool_live_open_project",
    "tool_live_restore_snapshot",
    "tool_live_run_and_verify",
    "tool_live_snapshot",
]
