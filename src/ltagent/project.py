"""Phase 7: Create Project Workflow orchestrator.

A *project* is the unit of work an agent (or a user) creates in one
``ltagent create`` invocation. It bundles the inputs (Circuit IR), the
generated artifacts (``.cir``, ``.asc``), the optional simulation result
(``.log`` + ``.raw``), and the structured summary (``result.json`` +
``metadata.json``) into a single directory.

This module owns the workflow but delegates every step to the
single-purpose phase modules:

* :mod:`ltagent.ir`        — load + serialise the Circuit IR
* :mod:`ltagent.netlist`   — render the ``.cir`` netlist
* :mod:`ltagent.asc`       — render the ``.asc`` schematic
* :mod:`ltagent.templates` — match an existing template (Phase 6)
* :mod:`ltagent.runner`    — execute LTspice in batch mode (Phase 3)
* :mod:`ltagent.log_parser`— parse the resulting ``.log`` (Phase 4)
* :mod:`ltagent.result`    — assemble and write ``result.json`` (Phase 4)
* :mod:`ltagent.layout_checker` — score the schematic (Phase 5)

The orchestrator is pure with respect to business logic: it never edits
``.cir`` or ``.asc`` content, it never launches LTspice directly (the
runner is the only subprocess surface), and it never invents a path —
all paths it returns are resolved against the caller-supplied target.

Hard rules (per ``AGENTS.md``):

* Subprocess access is mediated by :func:`ltagent.runner.run_simulation`.
  This module never spawns a process directly.
* Every path is resolved with :meth:`Path.resolve` and rejected on
  traversal. The target directory must be inside the configured
  workspace unless the caller opts out.
* The simulation is opt-in. When LTspice is not available the project is
  still created with ``run.attempted=False`` and a clear warning — the
  orchestrator must never lie about success.
* Templates are only *matched* here; promotion is a separate Phase 9
  concern and is intentionally not performed.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config
from .ir import CircuitIR, dump_ir
from .layout_checker import score_layout
from .log_parser import ParseReport, parse_log
from .result import (
    FileMap,
    Result,
    build_result_from_run,
    write_result,
)
from .runner import RunRequest, RunResult, run_simulation
from .templates import MatchResult, match_template

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Stable schema version stamped on every metadata.json. Bump only on
#: backwards-incompatible shape changes.
METADATA_SCHEMA_VERSION = "0.1"

#: Stable schema version of the result.json contract we emit. Mirrors
#: ``result.RESULT_SCHEMA_VERSION`` but is duplicated here so the project
#: module does not import a private constant from a sibling.
PROJECT_RESULT_SCHEMA_VERSION = "0.1"

#: Codes returned in the orchestrator's errors / warnings. Keep them
#: stable: callers (CLI, MCP, agents) may switch on them.
PRJ_ERR_INVALID_IR = "PROJECT_INVALID_IR"
PRJ_ERR_TARGET_EXISTS = "PROJECT_TARGET_EXISTS"
PRJ_ERR_TARGET_NOT_EMPTY = "PROJECT_TARGET_NOT_EMPTY"
PRJ_ERR_TARGET_OUTSIDE_WORKSPACE = "PATH_OUTSIDE_WORKSPACE"
PRJ_ERR_MKDIR_FAILED = "PROJECT_MKDIR_FAILED"
PRJ_ERR_WRITE_FAILED = "PROJECT_WRITE_FAILED"
PRJ_ERR_IR_WRITE_FAILED = "PROJECT_IR_WRITE_FAILED"
PRJ_ERR_CIR_WRITE_FAILED = "PROJECT_CIR_WRITE_FAILED"
PRJ_ERR_ASC_WRITE_FAILED = "PROJECT_ASC_WRITE_FAILED"
PRJ_ERR_RESULT_WRITE_FAILED = "PROJECT_RESULT_WRITE_FAILED"
PRJ_ERR_RUNNER_RAISED = "PROJECT_RUNNER_RAISED"
PRJ_ERR_LOG_PARSE_RAISED = "PROJECT_LOG_PARSE_RAISED"

#: Warning codes. Non-fatal but always visible.
PRJ_WARN_LTSPICE_UNAVAILABLE = "LTSPICE_UNAVAILABLE"
PRJ_WARN_RUN_SKIPPED_BY_CONFIG = "RUN_SKIPPED_NOT_REQUESTED"
PRJ_WARN_RUN_SKIPPED_NO_LTSPICE = "RUN_SKIPPED_NO_LTSPICE"
PRJ_WARN_RUN_FAILED = "RUN_FAILED"
PRJ_WARN_TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"

#: Run-status string constants used in ``data.run.status``.
RUN_STATUS_NOT_REQUESTED = "not_requested"
RUN_STATUS_ATTEMPTED = "attempted"
RUN_STATUS_SKIPPED = "skipped"

#: Names of the files emitted in every project. Centralised so the
#: project dict, the FileMap, and the metadata.json all stay in sync.
FILE_IR = "circuit.ir.json"
FILE_CIR = "circuit.cir"
FILE_ASC = "circuit.asc"
FILE_LOG = "circuit.log"
FILE_RESULT = "result.json"
FILE_METADATA = "metadata.json"

#: Pattern for safe project names. Matches the IR's project name pattern
#: (kept duplicated here to avoid an import cycle at module load time).
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProjectResult:
    """In-memory result of :func:`create_project`.

    All paths are absolute and resolved. The dataclass is intentionally
    minimal — the rich JSON payload is built by the CLI layer from these
    fields plus the on-disk ``result.json``.
    """

    project_id: str
    target: Path
    ir_path: Path
    cir_path: Path
    asc_path: Path
    result_path: Path
    metadata_path: Path
    log_path: Path | None
    raw_path: Path | None
    template_used: str | None
    template_value_variant: bool
    layout_score: int | None
    layout_warnings: list[str]
    run_status: str
    run_result: RunResult | None
    parse_report: ParseReport | None
    result_obj: Result
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """``True`` iff the orchestrator's own workflow had no errors.

        The simulation outcome lives on :attr:`result_obj` (see
        :class:`ltagent.result.Result.success`); a project can be
        "orchestrator-success" while the simulation either was not
        requested, was skipped, or failed.
        """
        return not self.errors


# ---------------------------------------------------------------------------
# Project id
# ---------------------------------------------------------------------------


def build_project_id(name: str, *, when: date | None = None) -> str:
    """Return the project directory name for a given IR name.

    Format: ``YYYY-MM-DD_<safe_name>``. ``name`` is sanitised so it is
    always a single path segment that is safe to ``mkdir``.

    Raises:
        ValueError: if ``name`` cannot be turned into a safe segment.
    """
    safe = _safe_project_name(name)
    stamp = (when or date.today()).isoformat()
    return f"{stamp}_{safe}"


def _safe_project_name(name: str) -> str:
    if not name:
        raise ValueError("project name is empty")
    if _SAFE_NAME_RE.match(name):
        return name
    # Conservative fallback: replace any non-alnum/underscore/hyphen run
    # with a single underscore, then trim. We keep the result under 64
    # characters so it fits the IR pattern.
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_")
    if not cleaned:
        raise ValueError(f"project name {name!r} is not a safe path segment")
    if not _SAFE_NAME_RE.match(cleaned):
        raise ValueError(f"project name {name!r} is not a safe path segment")
    return cleaned


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def create_project(
    ir: CircuitIR | Mapping[str, Any] | str | Path,
    target: Path,
    *,
    templates_dir: Path,
    config: Config,
    run_simulation: bool = False,
    when: date | None = None,
) -> ProjectResult:
    """Create a complete project from ``ir`` inside ``target``.

    The function performs these steps in order:

    1. Resolve and validate the target directory (no traversal, no
       overwrite, must be inside the workspace unless the caller opts
       out).
    2. Normalise ``ir`` into a :class:`CircuitIR` (re-validate through
       the IR layer to catch any drift).
    3. Match the IR against the template library (best-effort; a miss
       is not an error).
    4. Render the netlist and the schematic. The text is also written
       to ``<target>/circuit.cir`` and ``<target>/circuit.asc``.
    5. Persist the normalised IR to ``<target>/circuit.ir.json``.
    6. If ``run_simulation`` is true, attempt the LTspice run via the
       Phase 3 runner and parse the resulting log. A missing LTspice
       executable is *not* an error here — the run status is set to
       ``skipped`` with a structured warning.
    7. Build a :class:`ltagent.result.Result` and write it to
       ``<target>/result.json``.
    8. Compute the layout score and write ``<target>/metadata.json``.

    The function never raises on operational failures (missing
    executable, timeout, file system error). Every failure path is
    captured into the returned :class:`ProjectResult` so the CLI /
    MCP layer can decide whether the orchestrator succeeded.

    Parameters
    ----------
    ir:
        A :class:`CircuitIR`, a dict matching the IR schema, or a path
        to an ``.ir.json`` file. Anything else is rejected with
        ``PRJ_ERR_INVALID_IR``.
    target:
        Absolute path to the project directory. The directory must not
        exist yet (the function creates it).
    templates_dir:
        Absolute path to the templates root (contains ``official``,
        ``candidates``, ``rejected``).
    config:
        Resolved :class:`Config` providing runner / ltspice settings.
    run_simulation:
        When true, attempt to run LTspice after writing the netlist.
        Defaults to false so a project can be created without LTspice.
    when:
        Override the date used in the project id. Primarily for tests.

    Returns
    -------
    ProjectResult
        The in-memory summary. Inspect :attr:`ProjectResult.success`
        for the high-level outcome.
    """
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # ---- 1. resolve + validate target -----------------------------------
    target_resolved = _resolve_target(target, config)

    if target_resolved.exists():
        if any(target_resolved.iterdir()):
            errors.append(
                {
                    "code": PRJ_ERR_TARGET_NOT_EMPTY,
                    "detail": f"{target_resolved} already contains files",
                    "data": {"target": str(target_resolved)},
                }
            )
            return _empty_result(
                target_resolved, errors=errors, warnings=warnings
            )
    else:
        try:
            target_resolved.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            errors.append(
                {
                    "code": PRJ_ERR_MKDIR_FAILED,
                    "detail": str(exc),
                    "data": {"target": str(target_resolved)},
                }
            )
            return _empty_result(
                target_resolved, errors=errors, warnings=warnings
            )

    # ---- 2. normalise IR ------------------------------------------------
    from .ir import load_ir, validate_dict  # local import to avoid cycle

    if isinstance(ir, CircuitIR):
        circuit = ir
    elif isinstance(ir, Mapping):
        rebuilt, ir_errors = validate_dict(dict(ir))
        if rebuilt is None:
            for err in ir_errors:
                errors.append(
                    {
                        "code": PRJ_ERR_INVALID_IR,
                        "detail": err.detail,
                        "data": {"path": err.path, "code": err.code},
                    }
                )
            return _empty_result(
                target_resolved, errors=errors, warnings=warnings
            )
        circuit = rebuilt
    elif isinstance(ir, (str, Path)):
        try:
            circuit = load_ir(Path(ir))
        except FileNotFoundError as exc:
            errors.append(
                {
                    "code": PRJ_ERR_INVALID_IR,
                    "detail": f"IR file not found: {exc}",
                    "data": {"path": str(ir)},
                }
            )
            return _empty_result(
                target_resolved, errors=errors, warnings=warnings
            )
        except Exception as exc:
            errors.append(
                {
                    "code": PRJ_ERR_INVALID_IR,
                    "detail": f"failed to load IR: {exc}",
                    "data": {"path": str(ir)},
                }
            )
            return _empty_result(
                target_resolved, errors=errors, warnings=warnings
            )
    else:
        errors.append(
            {
                "code": PRJ_ERR_INVALID_IR,
                "detail": f"unsupported IR type: {type(ir).__name__}",
                "data": {},
            }
        )
        return _empty_result(
            target_resolved, errors=errors, warnings=warnings
        )

    # Build the project id AFTER we know the IR is valid.
    try:
        project_id = build_project_id(circuit.name, when=when)
    except ValueError as exc:
        errors.append(
            {
                "code": PRJ_ERR_INVALID_IR,
                "detail": str(exc),
                "data": {"name": circuit.name},
            }
        )
        return _empty_result(
            target_resolved, errors=errors, warnings=warnings
        )

    # ---- 3. match template (best-effort) --------------------------------
    match_result: MatchResult | None = None
    template_used: str | None = None
    template_value_variant = False
    try:
        match_result = match_template(templates_dir, circuit)
        if match_result.matched and match_result.template is not None:
            template_used = match_result.template.templateId
            template_value_variant = bool(match_result.isValueVariant)
    except Exception as exc:
        warnings.append(
            {
                "code": "TEMPLATE_MATCH_RAISED",
                "detail": f"template match raised: {exc}",
                "data": {"templatesDir": str(templates_dir)},
            }
        )

    # ---- 4 + 5. render + write IR / .cir / .asc -------------------------
    ir_path = target_resolved / FILE_IR
    cir_path = target_resolved / FILE_CIR
    asc_path = target_resolved / FILE_ASC

    try:
        ir_path.write_text(dump_ir(circuit) + "\n", encoding="utf-8")
    except OSError as exc:
        errors.append(
            {
                "code": PRJ_ERR_IR_WRITE_FAILED,
                "detail": str(exc),
                "data": {"path": str(ir_path)},
            }
        )
        return _empty_result(
            target_resolved, errors=errors, warnings=warnings
        )

    try:
        netlist.write_netlist(circuit, cir_path)
    except Exception as exc:
        from .netlist import NetlistError

        errors.append(
            {
                "code": PRJ_ERR_CIR_WRITE_FAILED,
                "detail": (
                    exc.detail
                    if isinstance(exc, NetlistError) and hasattr(exc, "detail")
                    else str(exc)
                ),
                "data": {"path": str(cir_path)},
            }
        )
        return _empty_result(
            target_resolved, errors=errors, warnings=warnings
        )

    try:
        asc.write_asc(circuit, asc_path)
    except Exception as exc:
        from .asc import ASCError

        errors.append(
            {
                "code": PRJ_ERR_ASC_WRITE_FAILED,
                "detail": (
                    exc.detail
                    if isinstance(exc, ASCError) and hasattr(exc, "detail")
                    else str(exc)
                ),
                "data": {"path": str(asc_path)},
            }
        )
        return _empty_result(
            target_resolved, errors=errors, warnings=warnings
        )

    # ---- 5b. layout score (always) --------------------------------------
    layout_score: int | None = None
    layout_warnings: list[str] = []
    try:
        asc_result = asc.render_asc(circuit)
        layout = score_layout(asc_result)
        layout_score = int(layout.score)
        layout_warnings = [w.detail for w in layout.warnings]
    except Exception as exc:
        warnings.append(
            {
                "code": "LAYOUT_SCORING_FAILED",
                "detail": f"layout score failed: {exc}",
                "data": {},
            }
        )

    # ---- 6. optional run ------------------------------------------------
    run_status = RUN_STATUS_NOT_REQUESTED
    run_result_obj: RunResult | None = None
    parse_report: ParseReport | None = None
    log_path: Path | None = None
    raw_path: Path | None = None

    if not run_simulation:
        warnings.append(
            {
                "code": PRJ_WARN_RUN_SKIPPED_BY_CONFIG,
                "detail": "simulation not requested (no --run flag)",
                "data": {},
            }
        )
    else:
        run_result_obj = _attempt_run(
            cir_path=cir_path,
            workdir=target_resolved,
            config=config,
            warnings=warnings,
        )
        run_status = RUN_STATUS_ATTEMPTED
        if not run_result_obj.success:
            warnings.append(
                {
                    "code": PRJ_WARN_RUN_FAILED,
                    "detail": "LTspice run did not complete cleanly",
                    "data": {
                        "errors": run_result_obj.errors,
                        "warnings": run_result_obj.warnings,
                    },
                }
            )
        # Detect log / raw on disk regardless of run.success — the
        # runner's contract is that the file exists iff the run was
        # observable. The CLI layer reads from disk so we always
        # canonicalise from the filesystem state.
        candidate_log = target_resolved / (cir_path.stem + ".log")
        candidate_raw = target_resolved / (cir_path.stem + ".raw")
        log_path = candidate_log if candidate_log.is_file() else None
        raw_path = candidate_raw if candidate_raw.is_file() else None

        if log_path is not None:
            try:
                parse_report = parse_log(log_path)
            except Exception as exc:
                warnings.append(
                    {
                        "code": PRJ_ERR_LOG_PARSE_RAISED,
                        "detail": f"log parse failed: {exc}",
                        "data": {"logPath": str(log_path)},
                    }
                )

    # ---- 7. build + write result.json -----------------------------------
    files = FileMap(
        ir=FILE_IR,
        cir=FILE_CIR,
        asc=FILE_ASC,
        log=FILE_LOG if log_path is not None else None,
        raw=str(raw_path.name) if raw_path is not None else None,
        result=FILE_RESULT,
    )

    run_payload: Mapping[str, Any] | None = (
        run_result_obj.to_dict() if run_result_obj is not None else None
    )
    result_obj = build_result_from_run(
        project_id=project_id,
        run_payload=run_payload,
        parse_report=parse_report,
        files=files,
        template_used=template_used,
        template_promoted=False,
        layout_score=layout_score,
        layout_warnings=layout_warnings,
    )

    result_path = target_resolved / FILE_RESULT
    try:
        write_result(result_obj, result_path)
    except OSError as exc:
        errors.append(
            {
                "code": PRJ_ERR_RESULT_WRITE_FAILED,
                "detail": str(exc),
                "data": {"path": str(result_path)},
            }
        )
        return _empty_result(
            target_resolved, errors=errors, warnings=warnings
        )

    # ---- 8. metadata.json -----------------------------------------------
    metadata = _build_metadata(
        project_id=project_id,
        circuit=circuit,
        target=target_resolved,
        template_used=template_used,
        template_value_variant=template_value_variant,
        layout_score=layout_score,
        layout_warnings=layout_warnings,
        run_status=run_status,
        run_success=bool(run_result_obj.success) if run_result_obj else None,
        log_path=log_path,
        raw_path=raw_path,
        files=files,
        warnings=warnings,
    )
    metadata_path = target_resolved / FILE_METADATA
    try:
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        errors.append(
            {
                "code": PRJ_ERR_WRITE_FAILED,
                "detail": str(exc),
                "data": {"path": str(metadata_path)},
            }
        )

    return ProjectResult(
        project_id=project_id,
        target=target_resolved,
        ir_path=ir_path,
        cir_path=cir_path,
        asc_path=asc_path,
        result_path=result_path,
        metadata_path=metadata_path,
        log_path=log_path,
        raw_path=raw_path,
        template_used=template_used,
        template_value_variant=template_value_variant,
        layout_score=layout_score,
        layout_warnings=layout_warnings,
        run_status=run_status,
        run_result=run_result_obj,
        parse_report=parse_report,
        result_obj=result_obj,
        warnings=list(warnings),
        errors=list(errors),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_target(target: Path, config: Config) -> Path:
    """Resolve ``target`` against the workspace; never raises.

    The caller is responsible for the "exists / not empty" check; this
    function only normalises the path.
    """
    raw = Path(target).expanduser()
    resolved = (Path.cwd() / raw).resolve() if not raw.is_absolute() else raw.resolve()
    return resolved


def _attempt_run(
    *,
    cir_path: Path,
    workdir: Path,
    config: Config,
    warnings: list[dict[str, Any]],
) -> RunResult:
    """Run the LTspice simulation; return the runner's structured result.

    The function never returns ``None``: when LTspice is not configured
    it synthesises a structured :class:`RunResult` with
    ``success=False`` so the caller can treat both outcomes uniformly.
    Any unexpected exception is captured into the ``RunResult``'s
    error list as a last-resort guard.
    """

    if not config.ltspice.executable:
        warnings.append(
            {
                "code": PRJ_WARN_LTSPICE_UNAVAILABLE,
                "detail": "ltspice.executable is not configured; skipping simulation",
                "data": {},
            }
        )
        # Synthesize a "skipped" RunResult so the rest of the workflow
        # can run uniformly. success=False keeps the result.json honest.
        return RunResult(
            success=False,
            command="run",
            message="LTspice executable not configured",
            data={"mode": config.ltspice.mode, "workdir": str(workdir)},
            errors=[
                {
                    "code": "LTSPICE_EXECUTABLE_NOT_SET",
                    "detail": "ltspice.executable is empty",
                    "data": {},
                }
            ],
            warnings=[],
        )

    request = RunRequest(
        cir_path=cir_path,
        workdir=workdir,
        timeout_seconds=int(config.runner.timeout_seconds),
        mode=config.ltspice.mode,
        executable=config.ltspice.executable,
        wine_command=config.ltspice.wine_command,
    )
    try:
        return run_simulation(request)
    except Exception as exc:
        warnings.append(
            {
                "code": PRJ_ERR_RUNNER_RAISED,
                "detail": f"runner raised: {exc}",
                "data": {"cirPath": str(cir_path)},
            }
        )
        return RunResult(
            success=False,
            command="run",
            message=f"runner raised: {exc}",
            data={"mode": config.ltspice.mode, "workdir": str(workdir)},
            errors=[
                {
                    "code": "RUNNER_RAISED",
                    "detail": str(exc),
                    "data": {"cirPath": str(cir_path)},
                }
            ],
            warnings=[],
        )


def _build_metadata(
    *,
    project_id: str,
    circuit: CircuitIR,
    target: Path,
    template_used: str | None,
    template_value_variant: bool,
    layout_score: int | None,
    layout_warnings: list[str],
    run_status: str,
    run_success: bool | None,
    log_path: Path | None,
    raw_path: Path | None,
    files: FileMap,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the dict that will be serialised to ``metadata.json``."""
    metadata: dict[str, Any] = {
        "schemaVersion": METADATA_SCHEMA_VERSION,
        "resultSchemaVersion": PROJECT_RESULT_SCHEMA_VERSION,
        "projectId": project_id,
        "name": circuit.name,
        "topology": circuit.topology,
        "description": circuit.description,
        "createdBy": "ltagent",
        "createdAt": date.today().isoformat(),
        "target": str(target),
        "files": {
            "ir": files.ir,
            "cir": files.cir,
            "asc": files.asc,
            "log": files.log,
            "raw": files.raw,
            "result": files.result,
            "metadata": FILE_METADATA,
        },
        "template": {
            "used": template_used,
            "valueVariant": template_value_variant,
        },
        "layout": {
            "score": layout_score,
            "warnings": list(layout_warnings),
        },
        "run": {
            "status": run_status,
            "success": run_success,
            "logPath": str(log_path) if log_path else None,
            "rawPath": str(raw_path) if raw_path else None,
        },
        "warnings": list(warnings),
    }
    return metadata


def _empty_result(
    target: Path,
    *,
    errors: Iterable[dict[str, Any]],
    warnings: Iterable[dict[str, Any]],
) -> ProjectResult:
    """Build a :class:`ProjectResult` representing a failed workflow.

    All file paths are computed as if the project had been created
    successfully so the caller can still report them in the JSON
    payload. The ``Result`` object is the standard "not attempted" one.
    """
    err_list = list(errors)
    warn_list = list(warnings)
    files = FileMap()
    empty_result = build_result_from_run(
        project_id="unknown",
        run_payload=None,
        parse_report=None,
        files=files,
        template_used=None,
        template_promoted=False,
        layout_score=None,
        layout_warnings=[],
    )
    for err in err_list:
        empty_result.errors.append(err)
    for warn in warn_list:
        empty_result.warnings.append(warn)
    return ProjectResult(
        project_id="unknown",
        target=target,
        ir_path=target / FILE_IR,
        cir_path=target / FILE_CIR,
        asc_path=target / FILE_ASC,
        result_path=target / FILE_RESULT,
        metadata_path=target / FILE_METADATA,
        log_path=None,
        raw_path=None,
        template_used=None,
        template_value_variant=False,
        layout_score=None,
        layout_warnings=[],
        run_status=RUN_STATUS_NOT_REQUESTED,
        run_result=None,
        parse_report=None,
        result_obj=empty_result,
        warnings=warn_list,
        errors=err_list,
    )


# Late import to keep the top of the module free of cycle concerns.
from . import asc, netlist  # noqa: E402

__all__ = [
    "FILE_ASC",
    "FILE_CIR",
    "FILE_IR",
    "FILE_LOG",
    "FILE_METADATA",
    "FILE_RESULT",
    "METADATA_SCHEMA_VERSION",
    "PRJ_ERR_ASC_WRITE_FAILED",
    "PRJ_ERR_CIR_WRITE_FAILED",
    "PRJ_ERR_INVALID_IR",
    "PRJ_ERR_IR_WRITE_FAILED",
    "PRJ_ERR_LOG_PARSE_RAISED",
    "PRJ_ERR_MKDIR_FAILED",
    "PRJ_ERR_RESULT_WRITE_FAILED",
    "PRJ_ERR_RUNNER_RAISED",
    "PRJ_ERR_TARGET_EXISTS",
    "PRJ_ERR_TARGET_NOT_EMPTY",
    "PRJ_ERR_TARGET_OUTSIDE_WORKSPACE",
    "PRJ_ERR_WRITE_FAILED",
    "PRJ_WARN_LTSPICE_UNAVAILABLE",
    "PRJ_WARN_RUN_FAILED",
    "PRJ_WARN_RUN_SKIPPED_BY_CONFIG",
    "PRJ_WARN_RUN_SKIPPED_NO_LTSPICE",
    "PRJ_WARN_TEMPLATE_NOT_FOUND",
    "PROJECT_RESULT_SCHEMA_VERSION",
    "RUN_STATUS_ATTEMPTED",
    "RUN_STATUS_NOT_REQUESTED",
    "RUN_STATUS_SKIPPED",
    "ProjectResult",
    "build_project_id",
    "create_project",
]


if __name__ == "__main__":  # pragma: no cover
    sys.stderr.write(
        "ltagent.project is a library module; invoke via 'ltagent create'\n"
    )
    sys.exit(2)
