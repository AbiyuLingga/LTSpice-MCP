"""MCP server (Phase 10) for ``ltspice-ai-agent``.

This module exposes the ltagent Python core to AI agents through the
Model Context Protocol. The server is intentionally thin:

* It runs on **stdio only** (no HTTP, no SSE). See plan §17.2.
* It exposes **curated** tools and resources only (no ``run_shell``,
  no ``execute_python``, no generic ``read_file``/``write_file``).
  See plan §17.3, §17.5, and security.md §2.
* It wraps the same Python core the CLI uses. No business logic
  lives only in the MCP layer. See plan §17.1.
* Every tool returns the JSON output contract from ``SPEC.md §2``
  (``success``, ``command``, ``message``, ``data``, ``warnings``,
  ``errors``). Failures never raise raw Python exceptions to the
  client - they are reported structurally with a stable ``code``.

The optional SDK dependency ``mcp[cli]>=1.0`` is imported lazily. When
it is missing, :func:`main` prints a structured JSON error with code
``MCP_SDK_MISSING`` to stderr and exits non-zero so the calling MCP
client surfaces a clear, actionable message rather than a stack trace.

Tools (10 - plan §17.3 + Phase 9 evaluators):

* ``create_project``
* ``inspect_project``
* ``generate_netlist``
* ``generate_schematic``
* ``run_simulation``
* ``read_measurements``
* ``check_layout``
* ``find_template``
* ``evaluate_template_candidate``
* ``promote_template``

Resources (8 - plan §17.4):

* ``ltagent://projects``                              (collection)
* ``ltagent://projects/{project_id}/metadata``
* ``ltagent://projects/{project_id}/result``
* ``ltagent://projects/{project_id}/circuit-ir``
* ``ltagent://projects/{project_id}/netlist``
* ``ltagent://projects/{project_id}/log``
* ``ltagent://templates``                             (collection)
* ``ltagent://templates/{template_id}/metadata``

``.raw`` files are **never** exposed (plan §17.4 + section 25).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeAlias, TypeVar

from . import __version__
from .config import Config, ConfigError, load_config
from .security import (
    ALLOWED_PROJECT_RESOURCE_NAMES,
    ERR_PATH_TRAVERSAL,
    PathSafetyError,
    SecurityError,
    assert_no_raw_path,
    safe_resolve_under,
    validate_slug,
)

# ---------------------------------------------------------------------------
# Optional SDK import
# ---------------------------------------------------------------------------

MCP_SDK_ERROR_CODE = "MCP_SDK_MISSING"

try:
    from mcp.server.fastmcp import (
        FastMCP as _FastMCP,  # type: ignore[unused-ignore,import-not-found]
    )
except Exception as exc:  # pragma: no cover - exercised by the fallback
    _FastMCP = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None

HandlerResult: TypeAlias = dict[str, Any]

_T = TypeVar("_T", bound=Callable[..., HandlerResult])
_P = ParamSpec("_P")


# ---------------------------------------------------------------------------
# Fallback when SDK is missing
# ---------------------------------------------------------------------------


def _emit_sdk_missing() -> int:
    """Print a structured JSON error and return a non-zero exit code."""
    payload = {
        "success": False,
        "command": "ltagent-mcp",
        "message": "MCP SDK not installed",
        "data": {
            "installHint": (
                'pip install "ltspice-ai-agent[mcp]" '
                "(or run `uv add ltspice-ai-agent[mcp]`)"
            ),
            "importError": repr(_IMPORT_ERROR) if _IMPORT_ERROR else None,
        },
        "warnings": [],
        "errors": [
            {
                "code": MCP_SDK_ERROR_CODE,
                "detail": (
                    "ltagent-mcp requires the optional [mcp] extra "
                    "which provides the modelcontextprotocol SDK."
                ),
                "data": {},
            }
        ],
    }
    sys.stderr.write(json.dumps(payload, indent=2) + "\n")
    return 1


# ---------------------------------------------------------------------------
# Shared helpers
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


def _resolve_config(config_path: str | None) -> tuple[Config | None, HandlerResult | None]:
    try:
        cfg = load_config(Path(config_path).expanduser() if config_path else None)
    except ConfigError as exc:
        return None, _err(
            "config",
            "Invalid configuration",
            "CONFIG_INVALID",
            str(exc),
        )
    return cfg, None


def _resolve_projects_root(cfg: Config) -> Path:
    return (Path.cwd() / cfg.workspace.projects_dir).resolve()


def _resolve_templates_root(cfg: Config) -> Path:
    return (Path.cwd() / cfg.workspace.templates_dir).resolve()


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / Path to JSON-friendly types."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, type):
        return obj.__name__
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _security_boundary(command: str) -> Callable[[_T], _T]:
    """Decorator: convert any :class:`SecurityError` raised inside the tool
    body into the standard JSON error payload.

    This is what guarantees that ``validate_slug`` / ``safe_resolve_under``
    / ``assert_no_raw_path`` rejections never propagate as raw Python
    exceptions to MCP clients.
    """

    def wrap(fn: _T) -> _T:
        def inner(*args: _P.args, **kwargs: _P.kwargs) -> HandlerResult:  # type: ignore[valid-type]
            try:
                return fn(*args, **kwargs)
            except SecurityError as exc:
                return _from_security_error(command, exc)

        inner.__name__ = fn.__name__
        inner.__doc__ = fn.__doc__
        return inner  # type: ignore[return-value]

    return wrap


# ---------------------------------------------------------------------------
# Tool implementations (pure functions - no MCP coupling)
# ---------------------------------------------------------------------------


@_security_boundary('create_project')
def tool_create_project(
    ir_source: str,
    *,
    out: str | None = None,
    templates_dir: str | None = None,
    run: bool = False,
    config: str | None = None,
    allow_outside_workspace: bool = False,
) -> HandlerResult:
    """Phase 7 wrapper exposed as MCP ``create_project``."""
    from .cli import CreatePlannerRefused  # defined in cli for dispatch glue
    from .project import create_project

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("create_project", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    templates_root = _resolve_templates_root(cfg)

    target_arg = Path(out).expanduser() if out else projects_root
    try:
        target = safe_resolve_under(target_arg, projects_root)
    except PathSafetyError as exc:
        if exc.code == ERR_PATH_TRAVERSAL and allow_outside_workspace:
            target = Path(target_arg).expanduser().resolve(strict=False)
        else:
            return _from_security_error("create_project", exc)

    templates_root_arg = Path(templates_dir).expanduser() if templates_dir else templates_root
    try:
        templates_resolved = safe_resolve_under(templates_root_arg, templates_root)
    except PathSafetyError as exc:
        return _from_security_error("create_project", exc)

    ir_path: Path | None = None
    prompt: str | None = None
    candidate = Path(ir_source).expanduser()
    looks_like_path = candidate.suffix == ".json" or "/" in ir_source or "\\" in ir_source
    if looks_like_path and candidate.exists():
        ir_path = candidate
    if ir_path is None:
        prompt = ir_source

    try:
        if ir_path is not None:
            pr = create_project(
                ir_path,
                target,
                templates_dir=templates_resolved,
                config=cfg,
                run_simulation=run,
            )
        else:
            assert prompt is not None
            pr = create_project(
                prompt,
                target,
                templates_dir=templates_resolved,
                config=cfg,
                run_simulation=run,
            )
    except CreatePlannerRefused as exc:
        return _err(
            "create_project",
            exc.message,
            exc.code,
            exc.message,
            {
                "supportedTopologies": list(exc.supported_topologies),
                "nextStep": exc.next_step,
            },
        )

    payload_raw = _to_jsonable(pr)
    payload_dict: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {"result": payload_raw}
    # Separate the standard contract keys from the project's domain payload.
    contract_keys = {"success", "command", "message", "warnings", "errors"}
    contract = {k: payload_dict[k] for k in contract_keys if k in payload_dict}
    data = {k: v for k, v in payload_dict.items() if k not in contract_keys}
    return _ok(
        "create_project",
        contract.get("message") or f"project created at {data.get('projectId', target)}",
        data,
    )


@_security_boundary('inspect_project')
def tool_inspect_project(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Return the project's metadata + result.json combined view."""
    validate_slug(project_id, kind="project id")
    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("inspect_project", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    project_dir = projects_root / project_id
    try:
        project_dir = safe_resolve_under(project_dir, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error("inspect_project", exc)

    metadata_path = project_dir / "metadata.json"
    result_path = project_dir / "result.json"

    if not metadata_path.exists():
        return _err(
            "inspect_project",
            f"project {project_id} has no metadata.json",
            "PROJECT_METADATA_MISSING",
            f"{metadata_path} does not exist",
            {"projectId": project_id, "projectDir": str(project_dir)},
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _err(
            "inspect_project",
            "failed to read metadata.json",
            "PROJECT_METADATA_INVALID",
            str(exc),
            {"path": str(metadata_path)},
        )

    result: dict[str, Any] | None = None
    if result_path.exists():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, dict) else {"value": loaded}
        except (OSError, json.JSONDecodeError) as exc:
            return _err(
                "inspect_project",
                "failed to read result.json",
                "PROJECT_RESULT_INVALID",
                str(exc),
                {"path": str(result_path)},
            )

    return _ok(
        "inspect_project",
        f"inspected project {project_id}",
        {
            "projectId": project_id,
            "projectDir": str(project_dir),
            "metadata": metadata,
            "result": result,
        },
    )


@_security_boundary('generate_netlist')
def tool_generate_netlist(
    ir_path: str,
    *,
    out: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Generate a ``.cir`` from an IR file."""
    from pydantic import ValidationError

    from .ir import load_ir
    from .netlist import render_netlist

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("generate_netlist", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    ir_arg = Path(ir_path).expanduser()
    try:
        ir_resolved = safe_resolve_under(ir_arg, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error("generate_netlist", exc)

    try:
        ir_obj = load_ir(ir_resolved)
    except (OSError, ValidationError, ValueError, TypeError) as exc:
        return _err(
            "generate_netlist",
            "failed to load IR",
            "IR_LOAD_FAILED",
            str(exc),
            {"path": str(ir_resolved)},
        )

    netlist = render_netlist(ir_obj)

    written_to: str | None = None
    if out:
        out_arg = Path(out).expanduser()
        try:
            out_resolved = safe_resolve_under(out_arg, projects_root)
        except PathSafetyError as exc:
            return _from_security_error("generate_netlist", exc)
        try:
            out_resolved.parent.mkdir(parents=True, exist_ok=True)
            out_resolved.write_text(netlist.text, encoding="utf-8")
            written_to = str(out_resolved)
        except OSError as exc:
            return _err(
                "generate_netlist",
                "failed to write netlist",
                "WRITE_FAILED",
                str(exc),
                {"path": str(out_resolved)},
            )

    return _ok(
        "generate_netlist",
        f"netlist generated for {ir_resolved.name}",
        {
            "netlist": netlist.text,
            "lineCount": netlist.line_count,
            "writtenTo": written_to,
            "ir": str(ir_resolved),
        },
    )


@_security_boundary('generate_schematic')
def tool_generate_schematic(
    ir_path: str,
    *,
    out: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Generate a ``.asc`` from an IR file."""
    from pydantic import ValidationError

    from .asc import render_asc, write_asc
    from .ir import load_ir
    from .layout_checker import score_layout

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("generate_schematic", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    ir_arg = Path(ir_path).expanduser()
    try:
        ir_resolved = safe_resolve_under(ir_arg, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error("generate_schematic", exc)

    try:
        ir_obj = load_ir(ir_resolved)
    except (OSError, ValidationError, ValueError, TypeError) as exc:
        return _err(
            "generate_schematic",
            "failed to load IR",
            "IR_LOAD_FAILED",
            str(exc),
            {"path": str(ir_resolved)},
        )

    asc_result = render_asc(ir_obj)

    written_to: str | None = None
    if out:
        out_arg = Path(out).expanduser()
        try:
            out_resolved = safe_resolve_under(out_arg, projects_root)
        except PathSafetyError as exc:
            return _from_security_error("generate_schematic", exc)
        try:
            out_resolved.parent.mkdir(parents=True, exist_ok=True)
            write_asc(ir_obj, out_resolved)
            written_to = str(out_resolved)
        except OSError as exc:
            return _err(
                "generate_schematic",
                "failed to write schematic",
                "WRITE_FAILED",
                str(exc),
                {"path": str(out_resolved)},
            )

    layout = score_layout(asc_result)
    return _ok(
        "generate_schematic",
        f"schematic generated for {ir_resolved.name}",
        {
            "asc": asc_result.text,
            "lineCount": asc_result.line_count,
            "componentCount": asc_result.component_count,
            "wireCount": asc_result.wire_count,
            "writtenTo": written_to,
            "ir": str(ir_resolved),
            "layout": _to_jsonable(layout),
        },
    )


@_security_boundary('run_simulation')
def tool_run_simulation(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Run LTspice on a project's netlist."""
    from .runner import RunnerBuildError, RunRequest
    from .runner import run_simulation as runner_run

    validate_slug(project_id, kind="project id")
    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("run_simulation", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    project_dir = projects_root / project_id
    try:
        project_dir = safe_resolve_under(project_dir, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error("run_simulation", exc)

    cir_path = project_dir / "circuit.cir"
    if not cir_path.exists():
        return _err(
            "run_simulation",
            "project has no circuit.cir",
            "PROJECT_NO_CIRCUIT",
            f"{cir_path} does not exist",
            {"projectId": project_id, "expected": str(cir_path)},
        )

    try:
        request = RunRequest(
            cir_path=cir_path,
            workdir=project_dir,
            timeout_seconds=cfg.runner.timeout_seconds,
            mode=cfg.ltspice.mode,
            executable=cfg.ltspice.executable,
            wine_command=cfg.ltspice.wine_command,
        )
    except (TypeError, ValueError) as exc:
        return _err(
            "run_simulation",
            "runner request build failed",
            "RUNNER_BUILD_FAILED",
            str(exc),
            {"projectId": project_id},
        )

    try:
        result = runner_run(request)
    except RunnerBuildError as exc:
        return _err(
            "run_simulation",
            "runner build failed",
            getattr(exc, "code", "RUNNER_BUILD_FAILED"),
            str(exc),
            {"projectId": project_id},
        )

    payload = _to_jsonable(result)
    return _ok(
        "run_simulation",
        f"simulation finished for {project_id}",
        {"projectId": project_id, **payload},
    )


@_security_boundary('read_measurements')
def tool_read_measurements(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Read a project's ``.log`` and return parsed measurements."""
    from .log_parser import parse_log
    from .result import read_result

    validate_slug(project_id, kind="project id")
    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("read_measurements", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    project_dir = projects_root / project_id
    try:
        project_dir = safe_resolve_under(project_dir, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error("read_measurements", exc)

    log_path = project_dir / "circuit.log"
    result_path = project_dir / "result.json"

    parsed_meas: dict[str, float] = {}
    log_summary: dict[str, Any] | None = None
    if log_path.exists():
        try:
            report = parse_log(log_path)
        except (OSError, ValueError) as exc:
            return _err(
                "read_measurements",
                "failed to parse circuit.log",
                "LOG_PARSE_FAILED",
                str(exc),
                {"path": str(log_path)},
            )
        parsed_meas = {name: m.value for name, m in report.measurements.items()}
        log_summary = _to_jsonable(report)

    saved_result: dict[str, Any] | None = None
    if result_path.exists():
        try:
            saved_result = read_result(result_path)
        except (OSError, ValueError) as exc:
            return _err(
                "read_measurements",
                "failed to read result.json",
                "RESULT_READ_FAILED",
                str(exc),
                {"path": str(result_path)},
            )

    return _ok(
        "read_measurements",
        f"measurements for {project_id}",
        {
            "projectId": project_id,
            "measurements": parsed_meas,
            "log": log_summary,
            "savedResult": saved_result,
        },
    )


@_security_boundary('check_layout')
def tool_check_layout(
    ir_path: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Score an .asc rendered from a Circuit IR file."""
    from pydantic import ValidationError

    from .asc import render_asc
    from .ir import load_ir
    from .layout_checker import score_layout

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("check_layout", "config", "CONFIG_INVALID", "no config")

    projects_root = _resolve_projects_root(cfg)
    ir_arg = Path(ir_path).expanduser()
    try:
        ir_resolved = safe_resolve_under(ir_arg, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error("check_layout", exc)

    try:
        ir_obj = load_ir(ir_resolved)
    except (OSError, ValidationError, ValueError, TypeError) as exc:
        return _err(
            "check_layout",
            "failed to load IR",
            "IR_LOAD_FAILED",
            str(exc),
            {"path": str(ir_resolved)},
        )

    asc_result = render_asc(ir_obj)
    layout = score_layout(asc_result)
    return _ok(
        "check_layout",
        "layout scored",
        {"layout": _to_jsonable(layout)},
    )


@_security_boundary('find_template')
def tool_find_template(
    ir_path: str | None = None,
    *,
    topology: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Find an official or candidate template that matches an IR or topology."""
    from pydantic import ValidationError

    from .ir import load_ir
    from .templates import list_templates, match_template

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("find_template", "config", "CONFIG_INVALID", "no config")

    templates_root = _resolve_templates_root(cfg)

    if ir_path:
        ir_arg = Path(ir_path).expanduser()
        try:
            ir_resolved = safe_resolve_under(ir_arg, templates_root, must_exist=True)
        except PathSafetyError as exc:
            return _from_security_error("find_template", exc)
        try:
            ir_obj = load_ir(ir_resolved)
        except (OSError, ValidationError, ValueError, TypeError) as exc:
            return _err(
                "find_template",
                "failed to load IR",
                "IR_LOAD_FAILED",
                str(exc),
                {"path": str(ir_resolved)},
            )
        match = match_template(templates_root, ir_obj)
        return _ok(
            "find_template",
            "template match attempted",
            {"match": _to_jsonable(match)},
        )

    if topology:
        templates = list_templates(templates_root, status="official")
        filtered = [t for t in templates if getattr(t, "topology", None) == topology]
        return _ok(
            "find_template",
            f"{len(filtered)} templates match topology {topology!r}",
            {
                "topology": topology,
                "templates": [_to_jsonable(t) for t in filtered],
            },
        )

    templates = list_templates(templates_root, status="official")
    return _ok(
        "find_template",
        f"{len(templates)} official templates",
        {"templates": [_to_jsonable(t) for t in templates]},
    )


@_security_boundary('evaluate_template_candidate')
def tool_evaluate_template_candidate(
    template_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Run the Phase 9 evaluator on a candidate template."""
    from .evaluator import evaluate_candidate

    validate_slug(template_id, kind="template id")
    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err(
            "evaluate_template_candidate", "config", "CONFIG_INVALID", "no config"
        )

    templates_root = _resolve_templates_root(cfg)
    try:
        evaluation = evaluate_candidate(templates_root, template_id)
    except Exception as exc:
        return _err(
            "evaluate_template_candidate",
            "evaluator failed",
            "EVALUATE_FAILED",
            str(exc),
            {"templateId": template_id},
        )

    return _ok(
        "evaluate_template_candidate",
        f"evaluated {template_id}",
        {"evaluation": _to_jsonable(evaluation)},
    )


@_security_boundary('promote_template')
def tool_promote_template(
    template_id: str,
    *,
    force: bool = False,
    config: str | None = None,
) -> HandlerResult:
    """Promote a candidate template to ``official`` (Phase 9)."""
    from .evaluator import promote_candidate

    validate_slug(template_id, kind="template id")
    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return err or _err("promote_template", "config", "CONFIG_INVALID", "no config")

    templates_root = _resolve_templates_root(cfg)
    try:
        manifest, evaluation = promote_candidate(templates_root, template_id, force=force)
    except Exception as exc:
        return _err(
            "promote_template",
            "promotion failed",
            "PROMOTE_FAILED",
            str(exc),
            {"templateId": template_id, "force": force},
        )

    return _ok(
        "promote_template",
        f"promoted {template_id}",
        {
            "manifest": _to_jsonable(manifest),
            "evaluation": _to_jsonable(evaluation),
            "force": force,
        },
    )


# ---------------------------------------------------------------------------
# Resource implementations
# ---------------------------------------------------------------------------


def _list_projects(cfg: Config) -> HandlerResult:
    projects_root = _resolve_projects_root(cfg)
    if not projects_root.exists():
        return _ok("projects.list", "no projects yet", {"projects": []})

    projects: list[dict[str, Any]] = []
    for entry in sorted(projects_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        projects.append(
            {
                "id": entry.name,
                "path": str(entry),
                "hasMetadata": (entry / "metadata.json").exists(),
            }
        )
    return _ok("projects.list", f"{len(projects)} projects", {"projects": projects})


def _read_project_file(
    cfg: Config, project_id: str, filename: str, *, command: str
) -> HandlerResult:
    validate_slug(project_id, kind="project id")
    if filename not in ALLOWED_PROJECT_RESOURCE_NAMES:
        return _err(
            command,
            f"project resource {filename!r} is not allowed",
            "RESOURCE_SUBPATH_INVALID",
            f"allowed: {sorted(ALLOWED_PROJECT_RESOURCE_NAMES)}",
            {"filename": filename},
        )

    projects_root = _resolve_projects_root(cfg)
    project_dir = projects_root / project_id
    try:
        project_dir = safe_resolve_under(project_dir, projects_root, must_exist=True)
    except PathSafetyError as exc:
        return _from_security_error(command, exc)

    target = project_dir / filename
    if filename == "circuit-ir":
        target = project_dir / "circuit.ir.json"
    elif filename == "netlist":
        target = project_dir / "circuit.cir"

    try:
        assert_no_raw_path(target)
    except PathSafetyError as exc:
        return _from_security_error(command, exc)

    if not target.exists():
        return _err(
            command,
            f"{filename} for project {project_id} does not exist",
            "RESOURCE_NOT_FOUND",
            f"{target} does not exist",
            {"projectId": project_id, "expected": str(target)},
        )

    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        return _err(
            command,
            f"failed to read {filename}",
            "RESOURCE_READ_FAILED",
            str(exc),
            {"path": str(target)},
        )
    return _ok(
        command,
        f"{filename} for {project_id}",
        {"projectId": project_id, "filename": filename, "content": content},
    )


def _list_templates(cfg: Config) -> HandlerResult:
    from .templates import list_templates

    templates_root = _resolve_templates_root(cfg)
    templates = list_templates(templates_root)
    return _ok(
        "templates.list",
        f"{len(templates)} templates",
        {"templates": [_to_jsonable(t) for t in templates]},
    )


def _read_template_metadata(cfg: Config, template_id: str) -> HandlerResult:
    from .templates import TemplateError, load_manifest

    validate_slug(template_id, kind="template id")
    templates_root = _resolve_templates_root(cfg)
    for status in ("official", "candidates", "rejected"):
        manifest_path = templates_root / status / template_id / "manifest.json"
        try:
            manifest_path = safe_resolve_under(
                manifest_path, templates_root, must_exist=False
            )
        except PathSafetyError:
            continue
        if manifest_path.exists():
            try:
                manifest = load_manifest(manifest_path)
            except (OSError, TemplateError, ValueError) as exc:
                return _err(
                    "templates.metadata",
                    "failed to load manifest",
                    "MANIFEST_READ_FAILED",
                    str(exc),
                    {"templateId": template_id, "status": status},
                )
            return _ok(
                "templates.metadata",
                f"manifest for {template_id}",
                {
                    "templateId": template_id,
                    "status": status,
                    "manifest": _to_jsonable(manifest),
                },
            )

    return _err(
        "templates.metadata",
        f"template {template_id} not found",
        "TEMPLATE_NOT_FOUND",
        "no manifest under official/, candidates/, or rejected/",
        {"templateId": template_id},
    )


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    """Construct the FastMCP server and register all tools/resources."""
    if _FastMCP is None:
        raise RuntimeError(MCP_SDK_ERROR_CODE)

    mcp = _FastMCP(
        "ltagent",
        instructions=(
            "ltagent MCP server (Phase 10). Exposes curated tools for "
            "creating, inspecting, simulating, and reusing LTspice "
            "circuits through the ltagent Python core. All operations "
            "are bounded by the configured workspace; raw waveform "
            "files are never exposed. See docs/mcp_setup.md."
        ),
    )

    mcp.tool(
        name="create_project",
        description=(
            "Create a complete ltagent project from an IR file path or a "
            "natural-language prompt. Mirrors `ltagent create`."
        ),
    )(tool_create_project)
    mcp.tool(
        name="inspect_project",
        description=(
            "Return a project's metadata.json + result.json as a "
            "combined view."
        ),
    )(tool_inspect_project)
    mcp.tool(
        name="generate_netlist",
        description=(
            "Generate a .cir netlist from a Circuit IR JSON file. "
            "Optionally write it to --out inside the workspace."
        ),
    )(tool_generate_netlist)
    mcp.tool(
        name="generate_schematic",
        description=(
            "Generate a .asc schematic from a Circuit IR JSON file. "
            "Returns the layout score from the layout checker."
        ),
    )(tool_generate_schematic)
    mcp.tool(
        name="run_simulation",
        description=(
            "Run LTspice on a project's circuit.cir via the configured "
            "runner. Returns the RunResult."
        ),
    )(tool_run_simulation)
    mcp.tool(
        name="read_measurements",
        description=(
            "Parse a project's circuit.log and return .meas results plus "
            "any saved result.json."
        ),
    )(tool_read_measurements)
    mcp.tool(
        name="check_layout",
        description=(
            "Score an .asc rendered from a Circuit IR file."
        ),
    )(tool_check_layout)
    mcp.tool(
        name="find_template",
        description=(
            "Find an official template matching a Circuit IR or "
            "topology. If neither is given, returns the full "
            "official template catalogue."
        ),
    )(tool_find_template)
    mcp.tool(
        name="evaluate_template_candidate",
        description=(
            "Run the Phase 9 evaluator on a candidate template."
        ),
    )(tool_evaluate_template_candidate)
    mcp.tool(
        name="promote_template",
        description=(
            "Promote a candidate template to official/. --force "
            "overrides gates (audit-logged in the manifest)."
        ),
    )(tool_promote_template)

    @mcp.resource(
        "ltagent://projects",
        name="projects",
        description="Collection of all project directories in the configured workspace.",
        mime_type="application/json",
    )
    def _res_projects() -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(_list_projects(cfg), sort_keys=False)

    @mcp.resource(
        "ltagent://projects/{project_id}/metadata",
        name="project-metadata",
        description="Project metadata.json.",
        mime_type="application/json",
    )
    def _res_project_meta(project_id: str) -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(
            _read_project_file(cfg, project_id, "metadata.json", command="projects.metadata"),
            sort_keys=False,
        )

    @mcp.resource(
        "ltagent://projects/{project_id}/result",
        name="project-result",
        description="Project result.json.",
        mime_type="application/json",
    )
    def _res_project_result(project_id: str) -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(
            _read_project_file(cfg, project_id, "result.json", command="projects.result"),
            sort_keys=False,
        )

    @mcp.resource(
        "ltagent://projects/{project_id}/circuit-ir",
        name="project-circuit-ir",
        description="Project circuit.ir.json (validated Circuit IR).",
        mime_type="application/json",
    )
    def _res_project_ir(project_id: str) -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(
            _read_project_file(cfg, project_id, "circuit-ir", command="projects.circuit-ir"),
            sort_keys=False,
        )

    @mcp.resource(
        "ltagent://projects/{project_id}/netlist",
        name="project-netlist",
        description="Project circuit.cir (SPICE netlist).",
        mime_type="text/plain",
    )
    def _res_project_netlist(project_id: str) -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(
            _read_project_file(cfg, project_id, "netlist", command="projects.netlist"),
            sort_keys=False,
        )

    @mcp.resource(
        "ltagent://projects/{project_id}/log",
        name="project-log",
        description="Project circuit.log (LTspice simulation log).",
        mime_type="text/plain",
    )
    def _res_project_log(project_id: str) -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(
            _read_project_file(cfg, project_id, "circuit.log", command="projects.log"),
            sort_keys=False,
        )

    @mcp.resource(
        "ltagent://templates",
        name="templates",
        description="Collection of all known templates.",
        mime_type="application/json",
    )
    def _res_templates() -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(_list_templates(cfg), sort_keys=False)

    @mcp.resource(
        "ltagent://templates/{template_id}/metadata",
        name="template-metadata",
        description="Template manifest.json (official/, candidates/, rejected/).",
        mime_type="application/json",
    )
    def _res_template_meta(template_id: str) -> str:
        cfg, _ = _resolve_config(None)
        if cfg is None:
            cfg = load_config(None)
        return json.dumps(_read_template_metadata(cfg, template_id), sort_keys=False)

    return mcp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ltagent-mcp",
        description=(
            "Run the ltspice-ai-agent MCP server on stdio. "
            "Requires the optional [mcp] extra: "
            'pip install "ltspice-ai-agent[mcp]".'
        ),
    )
    p.add_argument("--version", action="store_true", help="print the ltagent version and exit")
    p.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="path to a config.toml file (overrides the search order)",
    )
    p.add_argument(
        "--list-tools",
        action="store_true",
        help="print the curated MCP tool names as JSON and exit",
    )
    p.add_argument(
        "--list-resources",
        action="store_true",
        help="print the curated MCP resource URIs as JSON and exit",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="validate that the MCP SDK is importable; exit 0 if OK",
    )
    return p


_TOOL_NAMES: tuple[str, ...] = (
    "create_project",
    "inspect_project",
    "generate_netlist",
    "generate_schematic",
    "run_simulation",
    "read_measurements",
    "check_layout",
    "find_template",
    "evaluate_template_candidate",
    "promote_template",
)
_RESOURCE_URIS: tuple[str, ...] = (
    "ltagent://projects",
    "ltagent://projects/{project_id}/metadata",
    "ltagent://projects/{project_id}/result",
    "ltagent://projects/{project_id}/circuit-ir",
    "ltagent://projects/{project_id}/netlist",
    "ltagent://projects/{project_id}/log",
    "ltagent://templates",
    "ltagent://templates/{template_id}/metadata",
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    if args.version:
        payload = _ok(
            "ltagent-mcp",
            f"ltagent-mcp {__version__}",
            {"version": __version__, "transport": "stdio"},
        )
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    if _FastMCP is None:
        return _emit_sdk_missing()

    if args.list_tools:
        payload = _ok(
            "ltagent-mcp.list-tools",
            f"{len(_TOOL_NAMES)} curated tools",
            {"tools": list(_TOOL_NAMES), "transport": "stdio"},
        )
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    if args.list_resources:
        payload = _ok(
            "ltagent-mcp.list-resources",
            f"{len(_RESOURCE_URIS)} curated resources",
            {"resources": list(_RESOURCE_URIS), "transport": "stdio"},
        )
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    if args.check:
        payload = _ok(
            "ltagent-mcp.check",
            "MCP SDK importable",
            {"sdk": "mcp", "fastmcp": True, "transport": "stdio"},
        )
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        server = _build_server()
    except RuntimeError as exc:
        if str(exc) == MCP_SDK_ERROR_CODE:
            return _emit_sdk_missing()
        raise

    server.run(transport="stdio")
    return 0


__all__ = [
    "main",
    "tool_check_layout",
    "tool_create_project",
    "tool_evaluate_template_candidate",
    "tool_find_template",
    "tool_generate_netlist",
    "tool_generate_schematic",
    "tool_inspect_project",
    "tool_promote_template",
    "tool_read_measurements",
    "tool_run_simulation",
]
