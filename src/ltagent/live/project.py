"""Live project structure and on-disk management.

The :mod:`ltagent.live.project` module is the file-system side of the
File-Based Live Editing pipeline (plan §6). It owns the standard file
layout under a project directory, the path-safety guards around that
layout, and the safe read/write helpers for ``circuit.graph.json``,
which is the live-editing source of truth.

Design contract
---------------

* Every live project is a directory named after a slug-safe project
  id that lives directly under a configured ``projects_root``.
* The directory contains a fixed set of files whose names are defined
  as module-level constants. Adding a new file means adding a constant
  here and updating :class:`ProjectPaths` and the snapshot module.
* Path resolution goes through :func:`ltagent.security.safe_resolve_under`
  so user input can never escape ``projects_root``. The path traversal
  check is enforced at the entry points (``create_live_project``,
  ``open_live_project``) and at every helper that accepts a caller
  supplied path.

This module never spawns a process. It never invokes the runner. It
only creates directories, reads JSON, and writes JSON. Everything else
in the live-editing pipeline (``edit_ops``, ``mcp_live_tools``,
``runner``) composes on top of this layer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from ..security import (
    PathSafetyError,
    SecurityError,
    safe_resolve_under,
    validate_slug,
)

# ---------------------------------------------------------------------------
# File-name constants
# ---------------------------------------------------------------------------

#: Stable file name for the editable circuit graph (plan §4.3, source of
#: truth #1).
FILE_GRAPH: Final[str] = "circuit.graph.json"

#: Stable file name for the validated Circuit IR.
FILE_IR: Final[str] = "circuit.ir.json"

#: Stable file name for the SPICE netlist.
FILE_CIR: Final[str] = "circuit.cir"

#: Stable file name for the LTspice schematic.
FILE_ASC: Final[str] = "circuit.asc"

#: Stable file name for the project metadata.
FILE_METADATA: Final[str] = "metadata.json"

#: Stable file name for the simulation result summary.
FILE_RESULT: Final[str] = "result.json"

#: Stable file name for formula-versus-simulation verification output.
FILE_VERIFICATION: Final[str] = "verification.json"

#: Stable file name for the calculation report (JSON form).
FILE_CALCULATION_JSON: Final[str] = "calculation.json"

#: Stable file name for the calculation report (Markdown form).
FILE_CALCULATION_MD: Final[str] = "calculation.md"

#: Stable file name for the append-only edit history (JSONL).
FILE_HISTORY: Final[str] = "edit_history.jsonl"

#: Stable directory name that holds the project snapshots.
DIR_SNAPSHOTS: Final[str] = ".snapshots"

#: The full ordered list of project file names, used by tests and by
#: the snapshot layer. Order matches plan §6.
PROJECT_FILE_NAMES: Final[tuple[str, ...]] = (
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

#: Stable schema version stamped on every project metadata.json.
METADATA_SCHEMA_VERSION: Final[str] = "0.1"

# ---------------------------------------------------------------------------
# Error codes (stable, machine-readable)
# ---------------------------------------------------------------------------

#: project id is not slug-safe.
ERR_INVALID_PROJECT_ID: Final[str] = "LIVE_INVALID_PROJECT_ID"

#: project directory already exists.
ERR_PROJECT_EXISTS: Final[str] = "LIVE_PROJECT_EXISTS"

#: project directory does not exist.
ERR_PROJECT_NOT_FOUND: Final[str] = "LIVE_PROJECT_NOT_FOUND"

#: path argument is not a directory.
ERR_NOT_A_DIRECTORY: Final[str] = "LIVE_NOT_A_DIRECTORY"

#: graph file is missing.
ERR_GRAPH_NOT_FOUND: Final[str] = "LIVE_GRAPH_NOT_FOUND"

#: graph file is not parseable as JSON.
ERR_GRAPH_INVALID_JSON: Final[str] = "LIVE_GRAPH_INVALID_JSON"

#: an I/O error happened during project creation or write.
ERR_PROJECT_IO: Final[str] = "LIVE_PROJECT_IO_ERROR"


class LiveProjectError(Exception):
    """Base class for all structured live-project errors.

    Carries a stable ``code`` so the MCP / CLI layer can render the
    JSON output contract without re-parsing the message text. The
    optional ``data`` dict carries the structured detail (path
    names, attempted slugs, etc.).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, Any] = dict(data) if data else {}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved absolute paths of every file in a live project.

    The dataclass is the single source of truth for "where does
    ``circuit.graph.json`` live?". Every other module that needs a
    file path takes a :class:`ProjectPaths` instance or rebuilds one
    via :func:`get_project_paths`.
    """

    project_dir: Path
    graph: Path
    ir: Path
    cir: Path
    asc: Path
    metadata: Path
    result: Path
    verification: Path
    calculation_json: Path
    calculation_md: Path
    history: Path
    snapshots: Path

    def file_paths(self) -> tuple[Path, ...]:
        """Return the tuple of all project file paths (not the snapshot dir)."""
        return (
            self.graph,
            self.ir,
            self.cir,
            self.asc,
            self.metadata,
            self.result,
            self.verification,
            self.calculation_json,
            self.calculation_md,
            self.history,
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_project_paths(project_dir: Path | str) -> ProjectPaths:
    """Return the standard paths under ``project_dir``.

    The function is pure and does not touch the filesystem. It only
    composes the standard file names onto the supplied directory. The
    returned :class:`ProjectPaths` uses the **unresolved** ``project_dir``
    so callers can decide whether to resolve or to relative-resolve
    before use.
    """
    base = Path(project_dir)
    return ProjectPaths(
        project_dir=base,
        graph=base / FILE_GRAPH,
        ir=base / FILE_IR,
        cir=base / FILE_CIR,
        asc=base / FILE_ASC,
        metadata=base / FILE_METADATA,
        result=base / FILE_RESULT,
        verification=base / FILE_VERIFICATION,
        calculation_json=base / FILE_CALCULATION_JSON,
        calculation_md=base / FILE_CALCULATION_MD,
        history=base / FILE_HISTORY,
        snapshots=base / DIR_SNAPSHOTS,
    )


def _resolve_project_path(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None,
    must_exist: bool,
) -> Path:
    """Resolve ``project_dir`` and enforce containment under ``projects_root``.

    When ``projects_root`` is ``None``, the path is resolved without a
    containment check (the caller is responsible for trust). When
    ``projects_root`` is provided, the path must resolve under it;
    any attempt to traverse outside raises :class:`LiveProjectError`
    with code ``PATH_TRAVERSAL``.

    A missing path (``must_exist=True`` and the directory is not on
    disk) is surfaced as :class:`LiveProjectError` with code
    ``LIVE_PROJECT_NOT_FOUND`` so the live-project layer has one
    uniform error vocabulary. ``PATH_NOT_FOUND`` from
    :func:`safe_resolve_under` is translated to the same code.
    """
    candidate = Path(project_dir).expanduser()
    if projects_root is None:
        try:
            return candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise LiveProjectError(
                ERR_PROJECT_NOT_FOUND,
                f"project directory {candidate} does not exist",
                data={"projectDir": str(candidate)},
            ) from exc
    try:
        return safe_resolve_under(candidate, projects_root, must_exist=must_exist)
    except PathSafetyError as exc:
        if exc.code == "PATH_NOT_FOUND":
            raise LiveProjectError(
                ERR_PROJECT_NOT_FOUND,
                exc.message,
                data=exc.data,
            ) from exc
        raise LiveProjectError(exc.code, exc.message, data=exc.data) from exc


def _ensure_slug_project_id(project_id: str) -> str:
    """Validate a project id against the slug pattern.

    Re-raises :class:`ltagent.security.IdentifierError` as
    :class:`LiveProjectError` with the ``LIVE_INVALID_PROJECT_ID``
    code so the structured contract is uniform.
    """
    try:
        return validate_slug(project_id, kind="project id")
    except SecurityError as exc:
        raise LiveProjectError(
            ERR_INVALID_PROJECT_ID,
            exc.message,
            data={"projectId": project_id, **exc.data},
        ) from exc


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Write ``payload`` as JSON to ``path`` atomically.

    The write goes to a sibling ``.tmp`` file and is then ``replace``-d
    onto the target. This is the same pattern the other writers in
    the project use (see ``ltagent.result``), kept local here to avoid
    a circular import.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    try:
        tmp.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to write {path}: {exc}",
            data={"path": str(path), "phase": "write_tmp"},
        ) from exc
    try:
        tmp.replace(path)
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to replace {path}: {exc}",
            data={"path": str(path), "phase": "replace"},
        ) from exc


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to write {path}: {exc}",
            data={"path": str(path), "phase": "atomic-write"},
        ) from exc


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def create_live_project(
    projects_root: Path | str,
    project_id: str,
) -> ProjectPaths:
    """Create a new live project under ``projects_root``.

    Steps:

    1. Validate ``project_id`` is slug-safe.
    2. Resolve ``projects_root / project_id`` and enforce containment.
    3. Create the project directory and the ``.snapshots/`` subdirectory.
    4. Return a :class:`ProjectPaths` rooted at the new directory.

    Parameters
    ----------
    projects_root:
        Absolute or ``~``-relative path to the directory that hosts
        all live projects. Must exist; the function does not create it.
    project_id:
        Slug-safe identifier (``^[a-z][a-z0-9_-]{0,63}$``). The id is
        used as the directory name, so the strict pattern is enforced.

    Raises
    ------
    LiveProjectError
        On invalid id, path traversal, or filesystem failure.
    FileExistsError
        When the project directory already exists. The caller may
        catch this and decide whether to overwrite.
    """
    safe_id = _ensure_slug_project_id(project_id)
    root = Path(projects_root).expanduser()
    try:
        project_dir = safe_resolve_under(root / safe_id, root, must_exist=False)
    except PathSafetyError as exc:
        raise LiveProjectError(
            exc.code,
            exc.message,
            data=exc.data,
        ) from exc

    if project_dir.exists():
        raise FileExistsError(
            f"project directory {project_dir} already exists"
        )
    try:
        project_dir.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to create project directory {project_dir}: {exc}",
            data={"projectDir": str(project_dir), "phase": "mkdir"},
        ) from exc

    snapshots_dir = project_dir / DIR_SNAPSHOTS
    try:
        snapshots_dir.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to create snapshots directory {snapshots_dir}: {exc}",
            data={"snapshotsDir": str(snapshots_dir), "phase": "mkdir"},
        ) from exc

    return get_project_paths(project_dir)


def open_live_project(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> ProjectPaths:
    """Open an existing live project and return its standard paths.

    The function does not require ``projects_root``, but supplying it
    enforces a containment check. Without ``projects_root`` the
    directory is only required to exist and be a directory; the
    caller is responsible for trust.

    Raises
    ------
    LiveProjectError
        With code ``LIVE_PROJECT_NOT_FOUND`` if the directory does not
        exist, or ``LIVE_NOT_A_DIRECTORY`` if the path is not a
        directory. Path-traversal rejections from
        :func:`safe_resolve_under` are re-raised as
        :class:`LiveProjectError` with the original code.
    """
    try:
        resolved = _resolve_project_path(
            project_dir, projects_root=projects_root, must_exist=True
        )
    except PathSafetyError as exc:
        raise LiveProjectError(exc.code, exc.message, data=exc.data) from exc

    if not resolved.exists():
        raise LiveProjectError(
            ERR_PROJECT_NOT_FOUND,
            f"project directory {resolved} does not exist",
            data={"projectDir": str(resolved)},
        )
    if not resolved.is_dir():
        raise LiveProjectError(
            ERR_NOT_A_DIRECTORY,
            f"{resolved} is not a directory",
            data={"projectDir": str(resolved)},
        )
    return get_project_paths(resolved)


# ---------------------------------------------------------------------------
# Graph read / write
# ---------------------------------------------------------------------------


def write_graph(
    project_dir: Path | str,
    graph: Mapping[str, Any],
    *,
    projects_root: Path | str | None = None,
) -> Path:
    """Write ``graph`` to ``circuit.graph.json`` atomically.

    The graph must be a JSON-serialisable mapping. Atomic write goes
    to a ``.tmp`` sibling and replaces the target; partial writes
    cannot leave a truncated file at the canonical name.

    When ``projects_root`` is provided the project directory must
    resolve under it. Otherwise the directory is only required to
    exist (the caller is trusted).
    """
    if not isinstance(graph, Mapping):
        raise LiveProjectError(
            ERR_GRAPH_INVALID_JSON,
            f"graph must be a mapping, got {type(graph).__name__}",
            data={"type": type(graph).__name__},
        )
    paths = get_project_paths(
        _resolve_project_path(
            project_dir, projects_root=projects_root, must_exist=True
        )
    )
    _write_json_atomic(paths.graph, dict(graph))
    return paths.graph


def read_graph(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read ``circuit.graph.json`` from the project directory.

    Raises
    ------
    LiveProjectError
        With code ``LIVE_GRAPH_NOT_FOUND`` if the file is missing, or
        ``LIVE_GRAPH_INVALID_JSON`` if it cannot be parsed.
    """
    paths = get_project_paths(
        _resolve_project_path(
            project_dir, projects_root=projects_root, must_exist=True
        )
    )
    if not paths.graph.exists():
        raise LiveProjectError(
            ERR_GRAPH_NOT_FOUND,
            f"graph file {paths.graph} does not exist",
            data={"graphPath": str(paths.graph)},
        )
    try:
        text = paths.graph.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to read {paths.graph}: {exc}",
            data={"graphPath": str(paths.graph), "phase": "read"},
        ) from exc
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiveProjectError(
            ERR_GRAPH_INVALID_JSON,
            f"graph file {paths.graph} is not valid JSON: {exc.msg}",
            data={
                "graphPath": str(paths.graph),
                "line": exc.lineno,
                "column": exc.colno,
            },
        ) from exc
    if not isinstance(loaded, dict):
        raise LiveProjectError(
            ERR_GRAPH_INVALID_JSON,
            f"graph file {paths.graph} must contain a JSON object",
            data={"graphPath": str(paths.graph)},
        )
    return loaded


def apply_operation(
    project_dir: Path | str,
    operation: Mapping[str, Any],
    *,
    projects_root: Path | str,
    auto_snapshot: bool = True,
) -> dict[str, Any]:
    """Apply one validated graph edit and persist it safely.

    This is the disk orchestration boundary used by CLI/MCP adapters.
    Pure graph transforms remain in :mod:`ltagent.live.edit_ops`.
    """
    from .edit_ops import (
        add_component,
        add_directive,
        add_measurement,
        connect_pin,
        disconnect_pin,
        remove_component,
        rename_net,
        set_component_value,
    )
    from .graph import graph_from_dict_safe, graph_to_dict, validate_graph
    from .history import append_history, make_history_event, next_step
    from .snapshot import create_snapshot

    resolved = _resolve_project_path(
        project_dir, projects_root=projects_root, must_exist=True
    )
    if not isinstance(operation, Mapping):
        return {
            "success": False,
            "errors": [{"code": "LIVE_OPERATION_INVALID", "detail": "operation must be an object"}],
            "warnings": [],
            "changes": [],
        }
    op = operation.get("op")
    args = operation.get("args", {})
    reason = operation.get("reason")
    if not isinstance(op, str) or not op or not isinstance(args, Mapping):
        return {
            "success": False,
            "errors": [{"code": "LIVE_OPERATION_INVALID", "detail": "op and object args are required"}],
            "warnings": [],
            "changes": [],
        }

    graph = read_graph(resolved, projects_root=projects_root)
    dispatch = {
        "add_component": lambda: add_component(
            graph,
            cast(str, args.get("componentId")),
            cast(str, args.get("kind")),
            cast(Mapping[str, str | None], args.get("pins")),
            value=args.get("value"),
            model=args.get("model"),
            role=args.get("role"),
        ),
        "remove_component": lambda: remove_component(
            graph, cast(str, args.get("componentId"))
        ),
        "set_component_value": lambda: set_component_value(
            graph, cast(str, args.get("componentId")), cast(str, args.get("value"))
        ),
        "connect": lambda: connect_pin(
            graph,
            cast(str, args.get("componentId")),
            cast(str, args.get("pin")),
            cast(str, args.get("net")),
        ),
        "connect_pin": lambda: connect_pin(
            graph,
            cast(str, args.get("componentId")),
            cast(str, args.get("pin")),
            cast(str, args.get("net")),
        ),
        "disconnect": lambda: disconnect_pin(
            graph, cast(str, args.get("componentId")), cast(str, args.get("pin"))
        ),
        "disconnect_pin": lambda: disconnect_pin(
            graph, cast(str, args.get("componentId")), cast(str, args.get("pin"))
        ),
        "rename_net": lambda: rename_net(
            graph, cast(str, args.get("oldName")), cast(str, args.get("newName"))
        ),
        "add_directive": lambda: add_directive(graph, cast(str, args.get("directive"))),
        "add_measurement": lambda: add_measurement(
            graph,
            cast(str, args.get("name")),
            cast(str, args.get("analysis")),
            cast(str, args.get("expression")),
        ),
    }
    apply_edit = dispatch.get(op)
    if apply_edit is None:
        return {
            "success": False,
            "errors": [{"code": "LIVE_OPERATION_UNKNOWN", "detail": f"unsupported operation {op!r}"}],
            "warnings": [],
            "changes": [],
        }

    edit_result = apply_edit()
    if not edit_result.success:
        payload = edit_result.to_dict()
        payload.pop("graph", None)
        return payload

    candidate, parse_issues = graph_from_dict_safe(edit_result.graph)
    if candidate is None:
        return {
            "success": False,
            "errors": [issue.model_dump(mode="json") for issue in parse_issues],
            "warnings": [],
            "changes": [],
        }
    validation = validate_graph(candidate)
    if not validation.ok:
        return {
            "success": False,
            "errors": [issue.model_dump(mode="json") for issue in validation.errors],
            "warnings": [issue.model_dump(mode="json") for issue in validation.warnings],
            "changes": [],
        }

    generated: tuple[dict[str, Any], str, str] | None = None
    generation_warning: str | None = None
    try:
        from ltagent.asc import ASCError, render_asc
        from ltagent.netlist import NetlistError, render_netlist

        from .graph_to_ir import graph_to_ir

        ir = graph_to_ir(candidate)
        generated = (
            ir.model_dump(mode="json", exclude_none=True),
            render_netlist(ir).text,
            render_asc(ir).text,
        )
    except (ASCError, NetlistError, KeyError, TypeError, ValueError) as exc:
        generation_warning = f"LIVE_GENERATION_NOT_RUN: {exc}"

    snapshot_id: str | None = None
    if auto_snapshot:
        snapshot_id = create_snapshot(
            resolved,
            f"before_{op}",
            projects_root=projects_root,
        ).snapshot_id
    write_graph(resolved, graph_to_dict(candidate), projects_root=projects_root)
    if generated is not None:
        paths = get_project_paths(resolved)
        ir_payload, netlist_text, schematic_text = generated
        _write_json_atomic(paths.ir, ir_payload)
        _write_text_atomic(paths.cir, netlist_text)
        _write_text_atomic(paths.asc, schematic_text)
    append_history(
        resolved,
        make_history_event(
            step=next_step(resolved, projects_root=projects_root),
            op=op,
            project_id=candidate.projectId,
            reason=reason if isinstance(reason, str) else None,
            target=str(args.get("componentId") or args.get("oldName") or "") or None,
            success=True,
            extra={"snapshotId": snapshot_id, "changes": [change.to_dict() for change in edit_result.changes]},
        ),
        projects_root=projects_root,
    )
    return {
        "success": True,
        "projectId": candidate.projectId,
        "operation": op,
        "snapshotId": snapshot_id,
        "changes": [change.to_dict() for change in edit_result.changes],
        "warnings": [
            *[warning.code for warning in edit_result.warnings],
            *[issue.code for issue in validation.warnings],
            *(["LIVE_GENERATION_NOT_RUN"] if generation_warning is not None else []),
        ],
        "errors": [],
    }


__all__ = [
    "DIR_SNAPSHOTS",
    "ERR_GRAPH_INVALID_JSON",
    "ERR_GRAPH_NOT_FOUND",
    "ERR_INVALID_PROJECT_ID",
    "ERR_NOT_A_DIRECTORY",
    "ERR_PROJECT_EXISTS",
    "ERR_PROJECT_IO",
    "ERR_PROJECT_NOT_FOUND",
    "FILE_ASC",
    "FILE_CALCULATION_JSON",
    "FILE_CALCULATION_MD",
    "FILE_CIR",
    "FILE_GRAPH",
    "FILE_HISTORY",
    "FILE_IR",
    "FILE_METADATA",
    "FILE_RESULT",
    "FILE_VERIFICATION",
    "METADATA_SCHEMA_VERSION",
    "PROJECT_FILE_NAMES",
    "LiveProjectError",
    "ProjectPaths",
    "apply_operation",
    "create_live_project",
    "get_project_paths",
    "open_live_project",
    "read_graph",
    "write_graph",
]
