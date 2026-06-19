"""Command-line interface for ``ltagent``.

Every subcommand supports a ``--json`` flag and returns the JSON contract
documented in ``docs/SPEC.md``. The contract is:

::

    {
      "success": bool,
      "command": str,
      "message": str,
      "data": {...},
      "warnings": [{"code", "detail", "data"}, ...],
      "errors":   [{"code", "detail", "data"}, ...]
    }

Top-level exit codes:

* ``0`` — success (success=True, no errors)
* ``1`` — operational error (success=False, errors non-empty)
* ``2`` — usage error (bad arguments, missing subcommand, etc.)
* ``130`` — interrupted (SIGINT)

The CLI is a thin layer over the Python core. Business logic lives in
``config.py``, ``doctor.py``, ``ir.py``, and ``netlist.py``; the CLI
only handles argument parsing and JSON / human output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError as PydanticValidationError

from . import __version__
from .asc import ASCError, render_asc, write_asc
from .config import Config, ConfigError, load_config
from .doctor import CheckResult, run_doctor, to_json_payload
from .evaluator import (
    ERR_EVAL_INPUT_INVALID,
    ERR_EVAL_NOT_FOUND,
    ERR_EVAL_PROMOTE_BLOCKED,
    EvaluationError,
    PromotionDecision,
    audit_promotability,
    evaluate_candidate,
    promote_candidate,
)
from .ir import CircuitIR, format_errors, load_ir
from .layout_checker import (
    OFFICIAL_THRESHOLD,
    PROJECT_THRESHOLD,
    LayoutResult,
    score_layout,
)
from .log_parser import ParseReport, parse_log, parse_log_text
from .netlist import (
    DIRECTIVE_ALLOWLIST,
    GENERATOR_NAME,
    NetlistError,
    render_netlist,
    write_netlist,
)
from .planner import PlannerRefusal, plan_prompt
from .project import (
    FILE_ASC,
    FILE_CIR,
    FILE_IR,
    FILE_METADATA,
    FILE_RESULT,
    PRJ_WARN_LTSPICE_UNAVAILABLE,
    PRJ_WARN_RUN_FAILED,
    PRJ_WARN_TEMPLATE_NOT_FOUND,
    RUN_STATUS_ATTEMPTED,
    ProjectResult,
    build_project_id,
    create_project,
)
from .result import (
    RESULT_SCHEMA_VERSION,
    FileMap,
    build_result_from_run,
    write_result,
)
from .runner import run_from_config
from .serialization import to_jsonable
from .templates import (
    TemplateError,
    TemplateStatus,
    audit_templates,
    create_candidate_from_project,
    ensure_default_templates,
    list_templates,
    match_template,
    seed_default_templates,
    show_template,
)

# ---- output helpers ------------------------------------------------------


def _emit(payload: Mapping[str, Any], as_json: bool) -> None:
    # Sentinel for commands that have already written to stdout (e.g.
    # `ir schema --text` prints the raw schema body directly).
    if payload.get("_ltagent_raw_output"):
        return
    if as_json:
        # ``to_jsonable`` is the last line of defence: it walks the
        # payload and converts any non-JSON-native value (dataclass,
        # ``Path``, ``Enum``, etc.) into something ``json.dump`` will
        # accept. Without this, a single ``Point`` in a layout warning
        # crashes the whole subcommand.
        safe = to_jsonable(dict(payload))
        json.dump(safe, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    else:
        _emit_human(payload)


def _emit_human(payload: Mapping[str, Any]) -> None:
    success = payload.get("success")
    command = payload.get("command", "?")
    message = payload.get("message", "")
    print(f"[{command}] {'OK' if success else 'FAIL'} — {message}")
    for w in payload.get("warnings") or []:
        print(f"  warn  {w.get('code')}: {w.get('detail')}")
    for e in payload.get("errors") or []:
        print(f"  err   {e.get('code')}: {e.get('detail')}")
    data = payload.get("data") or {}
    if "checks" in data and isinstance(data["checks"], list):
        for c in data["checks"]:
            print(f"  {c['status']:<5} {c['name']} — {c['detail']}")


def _exit_for(payload: Mapping[str, Any]) -> NoReturn:
    sys.exit(0 if payload.get("success") else 1)


def _ok(command: str, message: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "command": command,
        "message": message,
        "data": dict(data) if data else {},
        "warnings": [],
        "errors": [{"code": code, "detail": detail, "data": dict(data) if data else {}}],
    }


# ---- subcommand implementations -----------------------------------------


def cmd_version(_: argparse.Namespace) -> dict[str, Any]:
    return _ok(
        "version",
        f"ltagent {__version__}",
        {"version": __version__},
    )


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err(
            "doctor",
            "Invalid configuration",
            "CONFIG_INVALID",
            str(exc),
        )

    checks = run_doctor(
        config,
        simulate=bool(args.simulate),
        projects_dir=Path(args.workspace) if args.workspace else None,
    )
    overall = _overall_status(checks)
    message = _doctor_message(overall, simulate=bool(args.simulate))
    return to_json_payload(
        "doctor",
        message,
        checks,
        data={
            "simulateAttempted": bool(args.simulate),
            "configPath": str(config.source_path) if config.source_path else None,
        },
    )


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("init", "Invalid configuration", "CONFIG_INVALID", str(exc))

    projects_root = (Path.cwd() / config.workspace.projects_dir).resolve()

    raw = args.dir
    candidate = Path(raw).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (projects_root / raw).resolve()

    if not _is_within(target, projects_root) and not args.allow_outside_workspace:
        return _err(
            "init",
            "Refusing to create project outside workspace",
            "PATH_OUTSIDE_WORKSPACE",
            f"{target} is not under {projects_root}",
            {"target": str(target), "workspace": str(projects_root)},
        )

    if target.exists():
        if any(target.iterdir()) and not args.force:
            return _err(
                "init",
                "Target directory is not empty",
                "TARGET_NOT_EMPTY",
                f"{target} already contains files; use --force to proceed",
                {"target": str(target)},
            )
    else:
        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return _err(
                "init",
                "Failed to create project directory",
                "MKDIR_FAILED",
                str(exc),
                {"target": str(target)},
            )

    # Phase 0 only writes a minimal set of placeholder artifacts.
    try:
        (target / "circuit.ir.json").write_text(
            json.dumps(_placeholder_ir(target.name), indent=2) + "\n",
            encoding="utf-8",
        )
        (target / "metadata.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "0.1",
                    "projectId": target.name,
                    "createdBy": "ltagent",
                    "phase": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (target / ".gitignore").write_text(
            "*.raw\n*.log\n*.tmp\n.snapshots/\n", encoding="utf-8"
        )
    except OSError as exc:
        return _err(
            "init",
            "Failed to write project artifacts",
            "WRITE_FAILED",
            str(exc),
            {"target": str(target)},
        )

    return _ok(
        "init",
        f"Project created at {target}",
        {
            "target": str(target),
            "files": ["circuit.ir.json", "metadata.json", ".gitignore"],
        },
    )


def cmd_config_show(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("config.show", "Invalid configuration", "CONFIG_INVALID", str(exc))
    return _ok(
        "config.show",
        "Configuration resolved",
        {
            "config": config.to_dict(),
            "searchPaths": _config_search_report(),
        },
    )


def cmd_config_validate(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("config.validate", "Invalid configuration", "CONFIG_INVALID", str(exc))
    warnings: list[dict[str, Any]] = []
    if config.source_path is None:
        warnings.append(
            {
                "code": "CONFIG_USING_DEFAULTS",
                "detail": "no config file found; defaults in use",
                "data": {},
            }
        )
    if not config.ltspice.executable:
        warnings.append(
            {
                "code": "LTSPICE_EXECUTABLE_NOT_SET",
                "detail": "ltspice.executable is empty",
                "data": {},
            }
        )
    return {
        "success": True,
        "command": "config.validate",
        "message": "Configuration is well-formed",
        "data": {
            "sourcePath": str(config.source_path) if config.source_path else None,
            "mode": config.ltspice.mode,
        },
        "warnings": warnings,
        "errors": [],
    }


# ---- Phase 1/2 subcommands ---------------------------------------------


def cmd_ir_validate(args: argparse.Namespace) -> dict[str, Any]:
    """Validate a Circuit IR JSON file. Phase 1/2 surface."""
    path = Path(args.path).expanduser()
    if not path.is_file():
        return _err(
            "ir.validate",
            "Input file not found",
            "IR_FILE_NOT_FOUND",
            f"{path} does not exist or is not a regular file",
            {"path": str(path)},
        )
    try:
        ir = load_ir(path)
    except json.JSONDecodeError as exc:
        return _err(
            "ir.validate",
            "Invalid JSON",
            "IR_JSON_DECODE",
            f"{exc.msg} at line {exc.lineno} column {exc.colno}",
            {"path": str(path), "line": exc.lineno, "column": exc.colno},
        )
    except PydanticValidationError as exc:
        errs = format_errors(exc)
        return {
            "success": False,
            "command": "ir.validate",
            "message": f"IR has {len(errs)} validation error(s)",
            "data": {
                "path": str(path),
                "errorCount": len(errs),
                "errors": [
                    {"code": e.code, "path": e.path, "detail": e.detail} for e in errs
                ],
            },
            "warnings": [],
            "errors": [
                {
                    "code": e.code,
                    "detail": e.detail,
                    "data": {"path": e.path},
                }
                for e in errs
            ],
        }
    return _ok(
        "ir.validate",
        f"IR is valid: {ir.name} ({ir.topology})",
        {
            "path": str(path),
            "name": ir.name,
            "topology": ir.topology,
            "schemaVersion": ir.schemaVersion,
            "componentCount": len(ir.components),
            "analysisCount": len(ir.analysis),
            "measurementCount": len(ir.measurements),
        },
    )


def _load_ir_schema_text() -> tuple[str, str]:
    """Read the bundled Circuit IR JSON Schema from the package resource.

    Returns ``(text, source_label)``. The source label is a stable
    string used for the JSON contract's ``data.path``; the file
    location depends on the install (wheel resource vs. source
    checkout) and is intentionally not exposed as a filesystem path
    once installed.

    Raises :class:`FileNotFoundError` when the packaged resource is
    missing (e.g. an incomplete wheel build). Callers translate that
    into a structured error.
    """
    try:
        resource = importlib_resources.files("ltagent.resources").joinpath(
            "circuit_ir.schema.json"
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        raise FileNotFoundError(
            "ltagent.resources package is not importable"
        ) from exc
    text = resource.read_text(encoding="utf-8")
    return text, "ltagent.resources:circuit_ir.schema.json"


def cmd_ir_schema(args: argparse.Namespace) -> dict[str, Any]:
    """Print the bundled JSON Schema. Phase 1/2 surface.

    With ``--text``, the raw schema body is written to stdout verbatim
    (so the user can pipe it into a file). With ``--json`` (the default
    for this subcommand), the schema is wrapped in the standard output
    contract under ``data.schema``.

    The schema is read from the ``ltagent.resources`` package resource
    so this command works after a wheel install, not just from a
    source checkout.
    """
    try:
        text, source = _load_ir_schema_text()
    except (FileNotFoundError, OSError) as exc:
        return _err(
            "ir.schema",
            "Schema resource not found",
            "IR_SCHEMA_MISSING",
            str(exc),
            {"source": "ltagent.resources:circuit_ir.schema.json"},
        )
    # When --text, the caller wants the raw schema body. We write it
    # directly to stdout here and return a sentinel payload so the
    # outer _emit() in main() knows to skip the envelope.
    if getattr(args, "text", False):
        sys.stdout.write(text)
        sys.stdout.write("\n")
        return {
            "_ltagent_raw_output": True,
            "_ltagent_stdout_written": True,
            "success": True,
            "command": "ir.schema",
            "message": f"Schema at {source}",
            "data": {"source": source},
            "warnings": [],
            "errors": [],
        }
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _err(
            "ir.schema",
            "Schema resource is not valid JSON",
            "IR_SCHEMA_INVALID",
            str(exc),
            {"source": source},
        )
    return _ok(
        "ir.schema",
        f"Schema at {source}",
        {"source": source, "schema": parsed},
    )


def cmd_netlist(args: argparse.Namespace) -> dict[str, Any]:
    """Generate a ``.cir`` netlist from a Circuit IR JSON. Phase 2 surface."""
    src = Path(args.path).expanduser()
    if not src.is_file():
        return _err(
            "netlist",
            "Input file not found",
            "IR_FILE_NOT_FOUND",
            f"{src} does not exist or is not a regular file",
            {"path": str(src)},
        )
    try:
        ir = load_ir(src)
    except json.JSONDecodeError as exc:
        return _err(
            "netlist",
            "Invalid JSON",
            "IR_JSON_DECODE",
            f"{exc.msg} at line {exc.lineno} column {exc.colno}",
            {"path": str(src), "line": exc.lineno, "column": exc.colno},
        )
    except PydanticValidationError as exc:
        errs = format_errors(exc)
        return {
            "success": False,
            "command": "netlist",
            "message": f"IR has {len(errs)} validation error(s); netlist not generated",
            "data": {
                "path": str(src),
                "errorCount": len(errs),
                "errors": [
                    {"code": e.code, "path": e.path, "detail": e.detail} for e in errs
                ],
            },
            "warnings": [],
            "errors": [
                {"code": e.code, "detail": e.detail, "data": {"path": e.path}}
                for e in errs
            ],
        }
    except NetlistError as exc:
        return _err(
            "netlist",
            "Netlist generation rejected by safety policy",
            exc.code,
            exc.detail,
            exc.data,
        )

    out_target = Path(args.out).expanduser() if args.out else None
    if out_target is None:
        # Print to stdout. We render the text but wrap it in the JSON
        # contract so the consumer (LLM agent) can still parse
        # structured output.
        result = render_netlist(ir, allow_unknown_directives=args.allow_unsafe_directives)
        return {
            "success": True,
            "command": "netlist",
            "message": f"Rendered {src.name} to stdout",
            "data": {
                "path": str(src),
                "writtenTo": None,
                "componentCount": result.component_count,
                "analysisCount": result.analysis_count,
                "measurementCount": result.measurement_count,
                "lineCount": result.line_count,
                "header": list(result.header),
                "rejectedDirectives": result.rejected_directives,
                "allowlist": sorted(DIRECTIVE_ALLOWLIST),
                "generator": GENERATOR_NAME,
                "netlist": result.text,
            },
            "warnings": [],
            "errors": [],
        }

    if not out_target.is_absolute():
        out_target = (Path.cwd() / out_target).resolve()
    else:
        out_target = out_target.resolve()

    try:
        result = write_netlist(ir, out_target, allow_unknown_directives=args.allow_unsafe_directives)
    except OSError as exc:
        return _err(
            "netlist",
            "Failed to write netlist",
            "NETLIST_WRITE_FAILED",
            str(exc),
            {"path": str(out_target)},
        )
    except NetlistError as exc:
        return _err(
            "netlist",
            "Netlist generation rejected by safety policy",
            exc.code,
            exc.detail,
            exc.data,
        )

    payload = _ok(
        "netlist",
        f"Netlist written to {out_target}",
        {
            "path": str(src),
            "writtenTo": str(out_target),
            "componentCount": result.component_count,
            "analysisCount": result.analysis_count,
            "measurementCount": result.measurement_count,
            "lineCount": result.line_count,
            "header": list(result.header),
            "rejectedDirectives": result.rejected_directives,
            "allowlist": sorted(DIRECTIVE_ALLOWLIST),
            "generator": GENERATOR_NAME,
            "bytes": out_target.stat().st_size,
        },
    )
    if result.rejected_directives:
        payload["warnings"] = [
            {
                "code": "DIR_REJECTED",
                "detail": (
                    f"dropped {len(result.rejected_directives)} unsafe directive(s) "
                    "because allow_unknown_directives=False"
                ),
                "data": {"rejected": result.rejected_directives},
            }
        ]
    return payload


# ---- asc subcommand (Phase 5) -------------------------------------------


def _layout_warnings_to_dict(layout: LayoutResult) -> list[dict[str, Any]]:
    """Render :class:`LayoutWarning` records as JSON-friendly dicts.

    The layout checker stores raw :class:`Point` instances inside each
    warning's ``data`` field. ``json.dump`` does not know how to
    serialise them, so we walk the structure and replace every
    :class:`Point` with ``{"x": ..., "y": ...}`` before the payload
    reaches :func:`_emit`.
    """
    out: list[dict[str, Any]] = []
    for w in layout.warnings:
        out.append(
            {
                "code": w.code,
                "detail": w.detail,
                "data": to_jsonable(w.data),
            }
        )
    return out


def cmd_asc(args: argparse.Namespace) -> dict[str, Any]:
    """Generate a ``.asc`` schematic from a Circuit IR JSON (Phase 5).

    The layout is deterministic and produced entirely by
    :mod:`ltagent.asc`. The agent may not write production
    coordinate lines (AGENTS.md hard rule 1). The layout checker
    score is reported in the JSON output so the agent can decide
    whether the schematic is fit for an official template.
    """
    src = Path(args.path).expanduser()
    if not src.is_file():
        return _err(
            "asc",
            "Input file not found",
            "IR_FILE_NOT_FOUND",
            f"{src} does not exist or is not a regular file",
            {"path": str(src)},
        )
    try:
        ir = load_ir(src)
    except json.JSONDecodeError as exc:
        return _err(
            "asc",
            "Invalid JSON",
            "IR_JSON_DECODE",
            f"{exc.msg} at line {exc.lineno} column {exc.colno}",
            {"path": str(src), "line": exc.lineno, "column": exc.colno},
        )
    except PydanticValidationError as exc:
        errs = format_errors(exc)
        return {
            "success": False,
            "command": "asc",
            "message": f"IR has {len(errs)} validation error(s); schematic not generated",
            "data": {
                "path": str(src),
                "errorCount": len(errs),
                "errors": [
                    {"code": e.code, "path": e.path, "detail": e.detail} for e in errs
                ],
            },
            "warnings": [],
            "errors": [
                {"code": e.code, "detail": e.detail, "data": {"path": e.path}}
                for e in errs
            ],
        }
    except ASCError as exc:
        return _err(
            "asc",
            "Schematic generation rejected by layout policy",
            exc.code,
            exc.detail,
            exc.data,
        )

    out_target = Path(args.out).expanduser() if args.out else None
    if out_target is None:
        # Print to stdout. The agent-facing contract includes the
        # full schematic text and the layout score so downstream
        # tooling can decide what to do without a second pass.
        result = render_asc(ir)
        layout = score_layout(result)
        layout_warnings = _layout_warnings_to_dict(layout)
        return {
            "success": True,
            "command": "asc",
            "message": f"Rendered {src.name} to stdout",
            "data": {
                "path": str(src),
                "writtenTo": None,
                "topology": result.topology,
                "componentCount": result.component_count,
                "wireCount": result.wire_count,
                "flagCount": result.flag_count,
                "lineCount": result.line_count,
                "layoutScore": layout.score,
                "layoutClassification": layout.classification,
                "layoutWarnings": layout_warnings,
                "officialThreshold": OFFICIAL_THRESHOLD,
                "projectThreshold": PROJECT_THRESHOLD,
                "schematic": result.text,
            },
            "warnings": layout_warnings,
            "errors": [],
        }

    if not out_target.is_absolute():
        out_target = (Path.cwd() / out_target).resolve()
    else:
        out_target = out_target.resolve()

    try:
        result = write_asc(ir, out_target)
    except OSError as exc:
        return _err(
            "asc",
            "Failed to write schematic",
            "ASC_WRITE_FAILED",
            str(exc),
            {"path": str(out_target)},
        )
    except ASCError as exc:
        return _err(
            "asc",
            "Schematic generation rejected by layout policy",
            exc.code,
            exc.detail,
            exc.data,
        )

    layout = score_layout(result)
    payload = _ok(
        "asc",
        f"Schematic written to {out_target}",
        {
            "path": str(src),
            "writtenTo": str(out_target),
            "topology": result.topology,
            "componentCount": result.component_count,
            "wireCount": result.wire_count,
            "flagCount": result.flag_count,
            "lineCount": result.line_count,
            "layoutScore": layout.score,
            "layoutClassification": layout.classification,
            "officialThreshold": OFFICIAL_THRESHOLD,
            "projectThreshold": PROJECT_THRESHOLD,
            "bytes": out_target.stat().st_size,
        },
    )
    if layout.warnings:
        payload["warnings"] = _layout_warnings_to_dict(layout)
    return payload


# ---- template subcommand (Phase 6) --------------------------------------


def _resolve_templates_dir(args: argparse.Namespace, config: Config) -> Path:
    """Return the absolute templates directory, expanded from the config."""
    raw = getattr(args, "templates_dir", None) or config.workspace.templates_dir
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def cmd_template_list(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("template.list", "Invalid configuration", "CONFIG_INVALID", str(exc))

    templates_dir = _resolve_templates_dir(args, config)
    try:
        ensure_default_templates(templates_dir)
    except TemplateError as exc:
        return _err("template.list", exc.detail, exc.code, exc.detail, exc.data)
    try:
        status_arg = getattr(args, "status", None)
        status = (
            None
            if status_arg is None
            else TemplateStatus.from_str(status_arg)
        )
        manifests = list_templates(templates_dir, status=status)
    except TemplateError as exc:
        return _err("template.list", exc.detail, exc.code, exc.detail, exc.data)

    items = [m.to_dict() for m in manifests]
    return _ok(
        "template.list",
        f"Found {len(items)} template(s)",
        {
            "templatesDir": str(templates_dir),
            "status": status.value if status else "all",
            "count": len(items),
            "templates": items,
        },
    )


def cmd_template_show(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("template.show", "Invalid configuration", "CONFIG_INVALID", str(exc))

    templates_dir = _resolve_templates_dir(args, config)
    try:
        ensure_default_templates(templates_dir)
    except TemplateError as exc:
        return _err("template.show", exc.detail, exc.code, exc.detail, exc.data)
    try:
        status_arg = getattr(args, "status", None) or TemplateStatus.OFFICIAL
        status = TemplateStatus.from_str(status_arg)
        manifest = show_template(
            templates_dir, args.id, status=status
        )
    except TemplateError as exc:
        return _err("template.show", exc.detail, exc.code, exc.detail, exc.data)

    return _ok(
        "template.show",
        f"Template {manifest.templateId!r}",
        {
            "templatesDir": str(templates_dir),
            "status": status.value,
            "template": manifest.to_dict(),
        },
    )


def cmd_template_match(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("template.match", "Invalid configuration", "CONFIG_INVALID", str(exc))

    templates_dir = _resolve_templates_dir(args, config)
    try:
        ensure_default_templates(templates_dir)
    except TemplateError as exc:
        return _err("template.match", exc.detail, exc.code, exc.detail, exc.data)
    ir_path = Path(args.ir).expanduser().resolve()
    if not ir_path.is_file():
        return _err(
            "template.match",
            "IR file not found",
            "IR_NOT_FOUND",
            f"{ir_path} does not exist",
            {"irPath": str(ir_path)},
        )
    try:
        ir = load_ir(ir_path)
    except Exception as exc:
        return _err(
            "template.match",
            "Failed to load IR",
            "IR_INVALID",
            f"{type(exc).__name__}: {exc}",
            {"irPath": str(ir_path)},
        )

    try:
        status_arg = getattr(args, "status", None) or TemplateStatus.OFFICIAL
        status = TemplateStatus.from_str(status_arg)
        bump = not args.no_bump
        result = match_template(templates_dir, ir, status=status, bump=bump)
    except TemplateError as exc:
        return _err("template.match", exc.detail, exc.code, exc.detail, exc.data)

    matched_id = result.template.templateId if result.template else None
    if result.matched and result.isValueVariant and matched_id is not None:
        message = f"Matched {matched_id!r} (value variant)"
    elif result.matched and matched_id is not None:
        message = f"Matched {matched_id!r}"
    else:
        message = "No matching template"
    return _ok(
        "template.match",
        message,
        {
            "templatesDir": str(templates_dir),
            "status": status.value,
            "irPath": str(ir_path),
            "topology": ir.topology,
            "result": result.to_dict(),
        },
    )


def cmd_template_audit(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("template.audit", "Invalid configuration", "CONFIG_INVALID", str(exc))

    templates_dir = _resolve_templates_dir(args, config)
    try:
        ensure_default_templates(templates_dir)
    except TemplateError as exc:
        return _err("template.audit", exc.detail, exc.code, exc.detail, exc.data)
    try:
        report = audit_templates(templates_dir)
    except TemplateError as exc:
        return _err("template.audit", exc.detail, exc.code, exc.detail, exc.data)

    duplicates = list(report.duplicates)
    success = not duplicates and not any(
        w["code"] in ("TEMPLATE_INVALID", "TEMPLATE_IR_INVALID") for w in report.warnings
    )
    payload = _ok(
        "template.audit",
        (
            f"{report.totals['manifests']} template(s); "
            f"{len(duplicates)} duplicate topology group(s); "
            f"{len(report.warnings)} warning(s)"
        ),
        report.to_dict(),
    )
    if not success:
        payload["success"] = False
        if not payload["errors"]:
            payload["errors"] = []
        # Surface duplicates as structured errors.
        for topo, ids in duplicates:
            payload["errors"].append(
                {
                    "code": "TEMPLATE_DUPLICATE_TOPOLOGY",
                    "detail": (
                        f"topology {topo!r} is shared by {len(ids)} templates: "
                        f"{', '.join(ids)}"
                    ),
                    "data": {"topology": topo, "templateIds": list(ids)},
                }
            )
    return payload


def cmd_template_seed(args: argparse.Namespace) -> dict[str, Any]:
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("template.seed", "Invalid configuration", "CONFIG_INVALID", str(exc))

    templates_dir = _resolve_templates_dir(args, config)
    try:
        written = seed_default_templates(templates_dir)
    except TemplateError as exc:
        return _err("template.seed", exc.detail, exc.code, exc.detail, exc.data)

    return _ok(
        "template.seed",
        (
            f"seeded {len(written)} template(s) into {templates_dir}"
            if written
            else f"all default templates already present in {templates_dir}"
        ),
        {
            "templatesDir": str(templates_dir),
            "written": [m.to_dict() for m in written],
            "count": len(written),
        },
    )


# ---- Phase 9 evaluator / promoter (template evaluate|promote) -----------


def _status_from_str_or_default(
    raw: str | None,
    *,
    default: TemplateStatus = TemplateStatus.CANDIDATE,
) -> TemplateStatus:
    if not raw:
        return default
    return TemplateStatus.from_str(raw)


def cmd_template_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    """Score one template and report the result.

    Mirrors :func:`ltagent.evaluator.evaluate_candidate`. Returns a
    structured payload with the score, the applied rules, the gate
    checks, and the final promotion decision. A template that scores
    high enough and passes every gate is reported as
    ``promotionEligible=true``; the actual move to ``official/`` is
    a separate step (see :func:`cmd_template_promote`) so the agent
    or human can review the score before mutating the library.
    """
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err(
            "template.evaluate",
            "Invalid configuration",
            "CONFIG_INVALID",
            str(exc),
        )

    templates_dir = _resolve_templates_dir(args, config)
    status = _status_from_str_or_default(args.status, default=TemplateStatus.CANDIDATE)
    try:
        evaluation = evaluate_candidate(
            templates_dir,
            args.id,
            status=status,
        )
    except EvaluationError as exc:
        if exc.code == ERR_EVAL_INPUT_INVALID:
            return _err(
                "template.evaluate",
                exc.detail,
                exc.code,
                exc.detail,
                exc.data,
            )
        if exc.code == ERR_EVAL_NOT_FOUND:
            return _err(
                "template.evaluate",
                exc.detail,
                exc.code,
                exc.detail,
                exc.data,
            )
        return _err("template.evaluate", exc.detail, exc.code, exc.detail, exc.data)
    except TemplateError as exc:
        return _err("template.evaluate", exc.detail, exc.code, exc.detail, exc.data)

    success = evaluation.decision != PromotionDecision.PROJECT
    message = (
        f"{evaluation.template_id}: score={evaluation.score}, "
        f"decision={evaluation.decision.value}"
    )
    if evaluation.duplicate_of:
        message += f" (value-variant of {evaluation.duplicate_of!r})"
    payload = _ok(
        "template.evaluate",
        message,
        {
            "templatesDir": str(templates_dir),
            "evaluation": evaluation.to_dict(),
        },
    )
    if not success:
        # A "project" decision is a real failure for the library
        # workflow; surface it as success=false so agents do not
        # silently think the template is a usable candidate.
        payload["success"] = False
        payload.setdefault("errors", []).append(
            {
                "code": "EVAL_PROJECT_ONLY",
                "detail": (
                    f"template scored {evaluation.score}; below "
                    f"candidate threshold and not eligible for the "
                    f"library"
                ),
                "data": {
                    "score": evaluation.score,
                    "decision": evaluation.decision.value,
                },
            }
        )
    if evaluation.failed_gates:
        # Gates are not a hard fail of the evaluate command itself
        # but they are exactly the data the caller needs to decide
        # whether to ``promote`` and how to fix the template. We
        # surface them as warnings so the JSON output contract is
        # satisfied without flipping ``success``.
        for g in evaluation.failed_gates:
            payload.setdefault("warnings", []).append(
                {
                    "code": g.code,
                    "detail": g.detail,
                    "data": g.data,
                }
            )
    return payload


def cmd_template_promote(args: argparse.Namespace) -> dict[str, Any]:
    """Manually promote a candidate template to ``official``.

    The gate policy from :mod:`ltagent.evaluator` is enforced
    unless ``--force`` is supplied. With ``--force`` the override
    is recorded in the manifest's tags so future audits can find
    it. A failed gate with ``--force`` is still reported in the
    output so the human can see what they overrode.
    """
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err(
            "template.promote",
            "Invalid configuration",
            "CONFIG_INVALID",
            str(exc),
        )

    templates_dir = _resolve_templates_dir(args, config)
    try:
        manifest, evaluation = promote_candidate(
            templates_dir,
            args.id,
            force=bool(args.force),
        )
    except EvaluationError as exc:
        if exc.code == ERR_EVAL_PROMOTE_BLOCKED:
            return _err(
                "template.promote",
                exc.detail,
                exc.code,
                exc.detail,
                exc.data,
            )
        return _err("template.promote", exc.detail, exc.code, exc.detail, exc.data)
    except TemplateError as exc:
        return _err("template.promote", exc.detail, exc.code, exc.detail, exc.data)

    warnings: list[dict[str, Any]] = []
    if args.force and evaluation.failed_gates:
        warnings.append(
            {
                "code": "EVAL_PROMOTE_FORCED",
                "detail": (
                    "promotion was forced despite blocked gates; "
                    "the override is recorded in the manifest tags"
                ),
                "data": {
                    "blockingGates": [g.to_dict() for g in evaluation.failed_gates],
                },
            }
        )

    success = manifest.status == TemplateStatus.OFFICIAL
    message = (
        f"{manifest.templateId!r} is now official"
        if success
        else f"{manifest.templateId!r} stays in {manifest.status.value}"
    )
    payload: dict[str, Any] = {
        "success": success,
        "command": "template.promote",
        "message": message,
        "data": {
            "templatesDir": str(templates_dir),
            "manifest": manifest.to_dict(),
            "evaluation": evaluation.to_dict(),
        },
        "warnings": warnings,
        "errors": [],
    }
    return payload


def cmd_template_promotability(args: argparse.Namespace) -> dict[str, Any]:
    """Audit the promotability of every candidate.

    Runs :func:`ltagent.evaluator.audit_promotability` over the
    candidate directory by default; pass ``--status`` to audit
    other directories (e.g. ``rejected``).
    """
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err(
            "template.audit-promotability",
            "Invalid configuration",
            "CONFIG_INVALID",
            str(exc),
        )

    templates_dir = _resolve_templates_dir(args, config)
    statuses: list[TemplateStatus | str] = [TemplateStatus.CANDIDATE]
    if args.status:
        for raw in args.status:
            statuses.append(raw)
    try:
        report = audit_promotability(templates_dir, status=statuses)
    except EvaluationError as exc:
        return _err(
            "template.audit-promotability",
            exc.detail,
            exc.code,
            exc.detail,
            exc.data,
        )
    except TemplateError as exc:
        return _err(
            "template.audit-promotability",
            exc.detail,
            exc.code,
            exc.detail,
            exc.data,
        )

    blocking = list(report.blocking)
    success = not blocking
    message = (
        f"{len(report.evaluated)} template(s); {report.counts[PromotionDecision.OFFICIAL.value]} "
        f"ready for promotion, {report.counts[PromotionDecision.CANDIDATE.value]} "
        f"candidate, {report.counts[PromotionDecision.PROJECT.value]} project-only"
    )
    payload = _ok(
        "template.audit-promotability",
        message,
        report.to_dict(),
    )
    if not success:
        payload["success"] = False
        for entry in blocking:
            payload.setdefault("errors", []).append(
                {
                    "code": "EVAL_BLOCKING_GATE",
                    "detail": f"{entry} has failing gates",
                    "data": {"entry": entry},
                }
            )
    return payload


# ---- Phase 4 subcommands (log parser + result builder) ------------------


def cmd_parse_log(args: argparse.Namespace) -> dict[str, Any]:
    """Parse a single ``.log`` file and return a :class:`ParseReport` dict.

    Accepts either ``PATH`` to read from disk or ``--log-text`` to parse
    a literal string. Used by agents that want a structured view of an
    existing log without going through the full ``run`` flow.
    """
    if bool(args.log_text) == bool(args.path):
        return _err(
            "parse-log",
            "Provide exactly one of PATH or --log-text",
            "PARSE_LOG_USAGE",
            "usage: ltagent parse-log PATH | ltagent parse-log --log-text STRING",
        )

    if args.log_text:
        try:
            report = parse_log_text(args.log_text)
        except Exception as exc:  # pragma: no cover - parser is pure
            return _err(
                "parse-log",
                "Failed to parse text",
                "PARSE_LOG_TEXT_FAILED",
                f"{type(exc).__name__}: {exc}",
            )
        source = "<inline>"
    else:
        path = Path(args.path).expanduser()
        if not path.is_file():
            return _err(
                "parse-log",
                "Log file not found",
                "LOG_FILE_NOT_FOUND",
                f"{path} does not exist or is not a regular file",
                {"path": str(path)},
            )
        try:
            report = parse_log(path)
        except OSError as exc:
            return _err(
                "parse-log",
                "Failed to read log",
                "LOG_READ_FAILED",
                str(exc),
                {"path": str(path)},
            )
        source = str(path)

    success = report.is_simulation_success
    payload = _ok(
        "parse-log",
        (
            f"Parsed {report.line_count} line(s); "
            f"{len(report.measurements)} measurement(s), "
            f"{len(report.findings)} finding(s)"
        ),
        {
            "source": source,
            "report": report.to_dict(),
            "measurements": {
                k: v.to_dict() for k, v in report.measurements.items()
            },
            "errors": [f.to_dict() for f in report.errors],
            "warnings": [f.to_dict() for f in report.warnings],
            "isSimulationSuccess": success,
        },
    )
    # Mirror fatal findings to top-level errors so the JSON contract
    # is honest about a failed simulation.
    if not success:
        payload["success"] = False
        for f in report.errors:
            payload["errors"].append(
                {"code": f.code, "detail": f.line.strip(), "data": {"lineNo": f.line_no}}
            )
    return payload


def cmd_result(args: argparse.Namespace) -> dict[str, Any]:
    """Build a ``result.json`` from a log + optional run payload.

    The command takes the same inputs the project workflow will use
    later (a log file, an optional run payload via ``--run-payload``,
    and a project id) and emits the same artefact the rest of the
    project reads. The ``--out`` flag writes the artefact to disk in
    addition to echoing it via the standard JSON contract.
    """
    project_id = args.project_id
    if not project_id:
        return _err(
            "result",
            "Missing project id",
            "RESULT_PROJECT_ID_REQUIRED",
            "pass --project-id <id>",
        )

    log_path = Path(args.log).expanduser() if args.log else None
    if log_path is not None and not log_path.is_file():
        return _err(
            "result",
            "Log file not found",
            "LOG_FILE_NOT_FOUND",
            f"{log_path} does not exist or is not a regular file",
            {"path": str(log_path)},
        )

    run_payload: dict[str, Any] | None = None
    if args.run_payload:
        try:
            run_payload = json.loads(args.run_payload)
            if not isinstance(run_payload, dict):
                raise ValueError("--run-payload must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return _err(
                "result",
                "Invalid --run-payload",
                "RESULT_RUN_PAYLOAD_INVALID",
                f"{type(exc).__name__}: {exc}",
            )

    parse_report: ParseReport | None = None
    if log_path is not None:
        try:
            parse_report = parse_log(log_path)
        except OSError as exc:
            return _err(
                "result",
                "Failed to read log",
                "LOG_READ_FAILED",
                str(exc),
                {"path": str(log_path)},
            )

    result = build_result_from_run(
        project_id=project_id,
        run_payload=run_payload,
        parse_report=parse_report,
        files=FileMap(
            ir=args.ir_file or "circuit.ir.json",
            cir=args.cir_file or "circuit.cir",
            asc=args.asc_file,
            log=str(log_path) if log_path else None,
            raw=None,
            result=args.out or "result.json",
        ),
        template_used=args.template,
        template_promoted=bool(args.template_promoted),
        layout_score=args.layout_score,
        layout_warnings=list(args.layout_warning) if args.layout_warning else None,
    )

    written_to: str | None = None
    if args.out:
        out_path = Path(args.out).expanduser()
        if not out_path.is_absolute():
            out_path = (Path.cwd() / out_path).resolve()
        else:
            out_path = out_path.resolve()
        try:
            write_result(result, out_path)
        except OSError as exc:
            return _err(
                "result",
                "Failed to write result.json",
                "RESULT_WRITE_FAILED",
                str(exc),
                {"path": str(out_path)},
            )
        written_to = str(out_path)

    payload = _ok(
        "result",
        (
            f"Result built for {project_id}; "
            f"{'written to ' + written_to if written_to else 'not written to disk'}"
        ),
        {
            "projectId": project_id,
            "schemaVersion": RESULT_SCHEMA_VERSION,
            "writtenTo": written_to,
            "result": result.to_dict(),
        },
    )
    if not result.success:
        payload["success"] = False
    return payload


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    """Run an LTspice batch simulation against a ``.cir`` file.

    Wraps :func:`ltagent.runner.run_from_config` and reshapes the
    :class:`RunResult` into the standard JSON contract.
    """
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("run", "Invalid configuration", "CONFIG_INVALID", str(exc))

    cir_path = Path(args.cir).expanduser()
    if not cir_path.is_absolute():
        cir_path = (Path.cwd() / cir_path).resolve()
    else:
        cir_path = cir_path.resolve()

    workdir = (
        Path(args.workdir).expanduser().resolve()
        if args.workdir
        else cir_path.parent
    )

    extra_args: tuple[str, ...] = tuple(args.ltspice_arg or ())

    run = run_from_config(
        cir_path,
        workdir=workdir,
        timeout_seconds=args.timeout,
        mode=config.ltspice.mode,
        executable=config.ltspice.executable,
        wine_command=config.ltspice.wine_command,
        extra_args=extra_args,
    )
    return run.to_dict()


# ---- Phase 7: create project workflow -----------------------------------


def cmd_create(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 7: create a complete project from an IR or a prompt.

    The command resolves the source (file path vs. natural-language
    prompt), matches the IR against the template library, renders the
    netlist and the schematic, optionally runs LTspice, and writes the
    standard project artifacts into a target directory.

    The target defaults to ``<projects_dir>/<project_id>`` where
    ``project_id`` is the date-prefixed IR name. The user can override
    with ``--out``.

    If LTspice is not configured, the project is still created and the
    simulation status is reported as ``not_requested`` (without
    ``--run``) or ``attempted`` with ``run.success=False`` (with
    ``--run``). In neither case is the orchestrator's success flag
    flipped: the project exists either way.
    """
    try:
        config = _load_config_or(args.config)
    except ConfigError as exc:
        return _err("create", "Invalid configuration", "CONFIG_INVALID", str(exc))

    # ---- resolve IR source --------------------------------------------
    ir_source, ir_kind, ir_error = _resolve_create_source(args)
    if ir_error is not None:
        return ir_error

    # ---- resolve target directory -------------------------------------
    target_path, target_error = _resolve_create_target(args, config, ir_source)
    if target_error is not None:
        return target_error

    # ---- resolve templates dir ----------------------------------------
    templates_dir = _resolve_templates_dir_for_create(args, config)
    # Auto-seed the bundled official library if the workspace is
    # missing it. The orchestrator depends on a populated library to
    # match IRs against existing templates; without this hook the
    # very first ``ltagent create`` in a fresh workspace would always
    # fall through to "no template matched".
    try:
        ensure_default_templates(templates_dir)
    except TemplateError as exc:
        return _err("create", exc.detail, exc.code, exc.detail, exc.data)

    # ---- invoke orchestrator ------------------------------------------
    pr: ProjectResult = create_project(
        ir_source,
        target_path,
        templates_dir=templates_dir,
        config=config,
        run_simulation=bool(args.run),
    )

    payload = _create_payload(pr, ir_kind=ir_kind)

    # ---- bridge: --save-template (Phase 7 <-> Phase 9) -----------------
    if getattr(args, "save_template", False):
        payload = _augment_create_with_template(
            payload, pr.target, templates_dir, args
        )

    return payload


def _augment_create_with_template(
    payload: dict[str, Any],
    project_dir: Path,
    templates_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Bridge a successful project to a candidate template.

    The bridge is additive: the project was already created and
    reported in ``payload``. We append the candidate manifest (or a
    structured error explaining why the candidate could not be
    created) to ``payload.data`` and surface failures as warnings
    so the overall ``create`` command still reports success.

    Hard rules mirrored from
    :func:`ltagent.templates.create_candidate_from_project`:

    * ``result.json`` must exist and report a successful run
    * a layout score must be recorded
    * the resulting template id must not already exist

    The function takes a path instead of a :class:`ProjectResult`
    so the unit tests do not have to construct a full dataclass
    with 17 required fields.
    """
    description = getattr(args, "template_description", None)
    template_id_override = getattr(args, "template_id", None)
    tags: list[str] = list(getattr(args, "template_tag", []) or [])
    if not tags:
        tags = ["user-requested"]

    try:
        candidate = create_candidate_from_project(
            project_dir,
            templates_dir,
            template_id=template_id_override,
            description=description,
            tags=tags,
        )
    except TemplateError as exc:
        # Append a structured warning; the create itself succeeded.
        warnings = payload.setdefault("warnings", [])
        warnings.append(
            {
                "code": exc.code,
                "detail": exc.detail,
                "data": dict(exc.data),
            }
        )
        payload.setdefault("data", {}).setdefault("templateBridge", {})["status"] = (
            "skipped"
        )
        payload["data"]["templateBridge"]["reason"] = exc.code
        return payload

    # Candidate is on disk; now run the evaluator so the user sees
    # the score + gates in the same response.
    try:
        evaluation = evaluate_candidate(
            templates_dir,
            candidate.templateId,
            status=TemplateStatus.CANDIDATE,
        )
    except EvaluationError as exc:
        warnings = payload.setdefault("warnings", [])
        warnings.append(
            {
                "code": exc.code,
                "detail": exc.detail,
                "data": dict(exc.data),
            }
        )
        payload.setdefault("data", {}).setdefault("templateBridge", {})["status"] = (
            "created-no-eval"
        )
        payload["data"]["templateBridge"]["candidate"] = candidate.to_dict()
        return payload

    bridge = payload.setdefault("data", {}).setdefault("templateBridge", {})
    bridge["status"] = "created"
    bridge["candidate"] = candidate.to_dict()
    bridge["evaluation"] = evaluation.to_dict()
    # If the candidate is already promotable, the user can run
    # `ltagent template promote <id>` next. We surface a hint in the
    # message so the workflow is discoverable.
    if evaluation.promotion_eligible:
        bridge["nextStep"] = (
            f"run `ltagent template promote {candidate.templateId}` to move "
            "the candidate to official/"
        )
    else:
        bridge["nextStep"] = (
            f"run `ltagent template evaluate {candidate.templateId}` to see "
            "what would unblock promotion"
        )
    return payload


def _resolve_create_source(
    args: argparse.Namespace,
) -> tuple[Any, str, dict[str, Any] | None]:
    """Return ``(source, kind, error)``.

    ``source`` is a path, a string, or a CircuitIR. ``kind`` is one of
    ``"ir_file"``, ``"prompt"``, ``"ir_dict"`` (for tests). ``error`` is
    a structured error dict when the source cannot be resolved, or
    ``None`` on success.
    """
    explicit_ir = getattr(args, "ir_file", None)
    explicit_prompt = getattr(args, "prompt", None)
    positional = getattr(args, "source", None)

    # Both --ir-file and --prompt set → ambiguous.
    if explicit_ir and explicit_prompt:
        return (
            None,
            "ir_file",
            _err(
                "create",
                "Provide either --ir-file or --prompt, not both",
                "CREATE_USAGE",
                "usage: ltagent create --ir-file PATH | --prompt TEXT | SOURCE",
            ),
        )

    if explicit_ir:
        return (_resolve_path(explicit_ir), "ir_file", None)

    if explicit_prompt:
        return (_plan_prompt_to_ir(explicit_prompt), "prompt", None)

    # Positional: dispatch on shape.
    if positional is None:
        return (
            None,
            "ir_file",
            _err(
                "create",
                "Missing IR source",
                "CREATE_USAGE",
                "usage: ltagent create PATH | --prompt TEXT | --ir-file PATH",
            ),
        )

    if _looks_like_ir_file_path(positional):
        return (_resolve_path(positional), "ir_file", None)

    return (_plan_prompt_to_ir(positional), "prompt", None)


def _resolve_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and resolve against ``Path.cwd()`` if not absolute."""
    path = Path(value).expanduser()
    return (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()


def _plan_prompt_to_ir(prompt: str) -> Any:
    """Run the planner on a prompt and surface refusals as raised RuntimeError.

    Returning the IR directly keeps the orchestrator's signature clean.
    Refusals bubble up so the caller can map them to a structured
    JSON error.
    """
    result = plan_prompt(prompt)
    if isinstance(result, PlannerRefusal):
        # Encode the refusal as a structured RuntimeError so the caller
        # can map it to a JSON error response. The message carries the
        # supported topology list.
        raise CreatePlannerRefused(
            code=result.code,
            message=result.message,
            supported_topologies=list(result.supported_topologies),
            next_step=result.next_step,
        )
    # Re-validate through the IR layer to catch planner ↔ model drift.
    from .ir import validate_dict  # local import to avoid cycle

    rebuilt, _errors = validate_dict(result.model_dump())
    if rebuilt is None:
        raise CreatePlannerRefused(
            code="PLAN_INTERNAL_INVALID_IR",
            message="Planner produced an IR that did not re-validate",
            supported_topologies=[],
            next_step="Fix the planner; the produced IR failed validation.",
        )
    return rebuilt


class CreatePlannerRefused(Exception):
    """Raised internally to surface a planner refusal from cmd_create."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        supported_topologies: list[str],
        next_step: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.supported_topologies = supported_topologies
        self.next_step = next_step


def _looks_like_ir_file_path(value: str) -> bool:
    """Best-effort detection: is ``value`` plausibly an IR file path?

    We do *not* require the file to exist on disk: the user may want to
    point at a relative path that will be resolved later. The rules
    are deliberately conservative so prompts that look like filenames
    ("make rc low pass.ir.json") still go to the planner.
    """
    if not value or " " in value or "\n" in value or "\t" in value:
        return False
    return value.lower().endswith(".ir.json")


def _resolve_create_target(
    args: argparse.Namespace,
    config: Config,
    ir_source: Any,
) -> tuple[Path, dict[str, Any] | None]:
    """Resolve the target directory for a new project.

    Precedence: ``--out`` > ``<projects_dir>/<project_id>``. The
    explicit ``--out`` is allowed inside either the configured
    ``projects_dir`` or the current working directory (the natural
    workspace boundary). ``--allow-outside-workspace`` opts out of the
    containment check entirely.
    """
    explicit_out = getattr(args, "out", None)
    projects_root = (Path.cwd() / config.workspace.projects_dir).resolve()
    cwd_root = Path.cwd().resolve()

    if explicit_out:
        candidate = Path(explicit_out).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if not getattr(args, "allow_outside_workspace", False):
            inside_projects = _is_within(candidate, projects_root)
            inside_cwd = _is_within(candidate, cwd_root)
            if not (inside_projects or inside_cwd):
                return (
                    candidate,
                    _err(
                        "create",
                        "Refusing to create project outside workspace",
                        "PATH_OUTSIDE_WORKSPACE",
                        f"{candidate} is not under {cwd_root} or {projects_root}",
                        {
                            "target": str(candidate),
                            "workspace": str(cwd_root),
                            "projectsRoot": str(projects_root),
                        },
                    ),
                )
        return (candidate, None)

    # Default target: <projects_dir>/<project_id>. We need the project
    # id; if the source is a CircuitIR or a path we can load, derive it
    # now. Otherwise fall back to "unknown" and let the orchestrator
    # surface the real error.
    name = _safe_extract_ir_name(ir_source)
    project_id = build_project_id(name) if name else "unknown"
    target = projects_root / project_id
    return (target, None)


def _safe_extract_ir_name(ir_source: Any) -> str:
    """Best-effort extraction of the IR's name without loading the file.

    Returns an empty string when the source is a string prompt (the
    caller has not yet resolved it) or when the value is opaque.
    """
    if isinstance(ir_source, CircuitIR):
        return ir_source.name
    if isinstance(ir_source, (str, Path)):
        path = Path(ir_source)
        if not path.is_absolute() and not path.exists():
            return ""
        # Try to read just the name field. Avoid loading the whole IR
        # to keep the CLI snappy; failure is acceptable.
        try:
            import json as _json

            data = _json.loads(path.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str):
                return name
        except (OSError, ValueError):
            return ""
    return ""


def _resolve_templates_dir_for_create(
    args: argparse.Namespace, config: Config
) -> Path:
    """Return the templates root for a create invocation."""
    return _resolve_templates_dir(args, config)


def _create_payload(pr: ProjectResult, *, ir_kind: str) -> dict[str, Any]:
    """Build the JSON contract payload for a :class:`ProjectResult`."""
    data: dict[str, Any] = {
        "projectId": pr.project_id,
        "target": str(pr.target),
        "irKind": ir_kind,
        "files": {
            "ir": str(pr.ir_path.relative_to(pr.target)) if pr.ir_path.exists() else FILE_IR,
            "cir": str(pr.cir_path.relative_to(pr.target)) if pr.cir_path.exists() else FILE_CIR,
            "asc": str(pr.asc_path.relative_to(pr.target)) if pr.asc_path.exists() else FILE_ASC,
            "result": FILE_RESULT,
            "metadata": FILE_METADATA,
        },
        "template": {
            "used": pr.template_used,
            "valueVariant": pr.template_value_variant,
            "promoted": pr.result_obj.template_promoted,
        },
        "layout": {
            "score": pr.layout_score,
            "warnings": list(pr.layout_warnings),
        },
        "run": {
            "status": pr.run_status,
            "success": (
                pr.result_obj.run.success
                if pr.run_status == RUN_STATUS_ATTEMPTED
                else None
            ),
            "logPath": str(pr.log_path) if pr.log_path else None,
            "rawPath": str(pr.raw_path) if pr.raw_path else None,
        },
        "warnings": [w.get("code", "WARNING") for w in pr.warnings],
    }

    # The orchestrator surfaces only stable warning codes; preserve the
    # full structured list under warnings.detail for callers that want
    # more than the codes.
    data["warningsDetail"] = list(pr.warnings)

    message = (
        f"Project '{pr.project_id}' created at {pr.target}"
        if pr.success
        else f"Project '{pr.project_id}' created with errors at {pr.target}"
    )
    payload = _ok("create", message, data)

    # Surface a planner-style "no template" hint as a soft warning so
    # users learn that ``ltagent template seed`` is one step away.
    if pr.template_used is None and pr.success:
        payload["warnings"].append(
            {
                "code": PRJ_WARN_TEMPLATE_NOT_FOUND,
                "detail": (
                    "no template matched this IR; run `ltagent template seed` "
                    "to install the default library"
                ),
                "data": {"topology": pr.result_obj.template_used},
            }
        )

    # If the orchestrator captured run-level failures, surface them as
    # warnings so the JSON consumer does not have to dig into the
    # result.json. We do *not* flip success=False for them: the project
    # exists either way and the user asked for it.
    if pr.run_status == RUN_STATUS_ATTEMPTED and not pr.result_obj.run.success:
        existing_codes = {
            w.get("code") for w in payload.get("warnings", []) if isinstance(w, dict)
        }
        # Surface the two distinct reasons separately so agents can
        # tell "no LTspice installed" from "LTspice ran but failed".
        if PRJ_WARN_LTSPICE_UNAVAILABLE not in existing_codes:
            payload["warnings"].append(
                {
                    "code": PRJ_WARN_LTSPICE_UNAVAILABLE,
                    "detail": "LTspice executable not configured",
                    "data": {},
                }
            )
        if PRJ_WARN_RUN_FAILED not in existing_codes:
            payload["warnings"].append(
                {
                    "code": PRJ_WARN_RUN_FAILED,
                    "detail": "LTspice run did not complete successfully",
                    "data": {"errors": pr.result_obj.errors},
                }
            )

    if not pr.success:
        payload["success"] = False
        payload["errors"] = list(pr.errors)
        payload["message"] = (
            f"Project creation failed: {pr.errors[0].get('detail', 'unknown error')}"
        )

    return payload


def cmd_create_safe(args: argparse.Namespace) -> dict[str, Any]:
    """Wrapper around :func:`cmd_create` that maps planner refusals to JSON.

    :func:`cmd_create` raises :class:`CreatePlannerRefused` when the
    user passes a prompt the planner cannot resolve. The dispatcher
    needs a structured error response, so this wrapper translates the
    refusal to the standard JSON contract.
    """
    try:
        return cmd_create(args)
    except CreatePlannerRefused as exc:
        return {
            "success": False,
            "command": "create",
            "message": exc.message,
            "data": {
                "supportedTopologies": list(exc.supported_topologies),
                "nextStep": exc.next_step,
            },
            "warnings": [],
            "errors": [
                {
                    "code": exc.code,
                    "detail": exc.message,
                    "data": {
                        "supportedTopologies": list(exc.supported_topologies),
                        "nextStep": exc.next_step,
                    },
                }
            ],
        }


def cmd_plan(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 8: turn a natural-language prompt into a validated Circuit IR.

    On success the data block contains the full ``CircuitIR`` (serialised
    via ``model_dump(mode="json")``) plus the project name and topology.
    On refusal it returns ``success=False`` with a structured ``errors``
    entry whose ``code`` is one of the planner refusal codes.

    If ``--out`` is provided and the plan succeeded, the IR is also written
    to that path. The path is restricted to the current working directory
    subtree; ``--allow-outside-cwd`` opts out of the guard for advanced use.
    """
    prompt = args.prompt
    result = plan_prompt(prompt)

    if isinstance(result, PlannerRefusal):
        # Plan refused the prompt. Surface a structured failure with the
        # refusal code mapped to the JSON contract. The supported-topology
        # list goes into data so agents can branch on it.
        return {
            "success": False,
            "command": "plan",
            "message": result.message,
            "data": {
                "prompt": prompt,
                "supportedTopologies": list(result.supported_topologies),
                "nextStep": result.next_step,
                **result.data,
            },
            "warnings": [],
            "errors": [
                {
                    "code": result.code,
                    "detail": result.message,
                    "data": dict(result.data),
                }
            ],
        }

    # Success path. Re-validate through the IR layer to catch any drift
    # between planner and IR model.
    from .ir import dump_ir, validate_dict  # local import to avoid cycles

    re_built, errors = validate_dict(result.model_dump())
    if errors:
        return {
            "success": False,
            "command": "plan",
            "message": "Planner produced an IR that did not re-validate",
            "data": {
                "prompt": prompt,
                "topology": result.topology,
                "name": result.name,
            },
            "warnings": [],
            "errors": [
                {
                    "code": "PLAN_INTERNAL_INVALID_IR",
                    "detail": err.detail,
                    "data": {"path": err.path, "code": err.code},
                }
                for err in errors
            ],
        }

    assert re_built is not None
    payload: dict[str, Any] = {
        "success": True,
        "command": "plan",
        "message": f"Planned {re_built.topology} circuit '{re_built.name}'",
        "data": {
            "prompt": prompt,
            "topology": re_built.topology,
            "name": re_built.name,
            "circuit": re_built.model_dump(mode="json"),
        },
        "warnings": [],
        "errors": [],
    }

    # Optional write to disk. Reject paths that escape the CWD unless the
    # caller opts in explicitly (this is a Phase 8 safeguard; Phase 7's
    # create workflow will use the configured workspace instead).
    out_raw = getattr(args, "out", None)
    if out_raw:
        out_path = Path(out_raw).expanduser().resolve()
        cwd = Path.cwd().resolve()
        try:
            out_path.relative_to(cwd)
        except ValueError:
            return {
                "success": False,
                "command": "plan",
                "message": "Refusing to write IR outside the current directory",
                "data": {
                    "prompt": prompt,
                    "outPath": str(out_path),
                    "cwd": str(cwd),
                },
                "warnings": [],
                "errors": [
                    {
                        "code": "PATH_OUTSIDE_CWD",
                        "detail": f"{out_path} is not under {cwd}",
                        "data": {"outPath": str(out_path), "cwd": str(cwd)},
                    }
                ],
            }
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(dump_ir(re_built), encoding="utf-8")
        except OSError as exc:
            return {
                "success": False,
                "command": "plan",
                "message": "Failed to write IR",
                "data": {"prompt": prompt, "outPath": str(out_path)},
                "warnings": [],
                "errors": [
                    {
                        "code": "WRITE_FAILED",
                        "detail": str(exc),
                        "data": {"outPath": str(out_path)},
                    }
                ],
            }
        payload["data"]["writtenTo"] = str(out_path)
        payload["message"] = (
            f"Planned {re_built.topology} circuit '{re_built.name}' "
            f"and wrote IR to {out_path}"
        )

    return payload


# ---- Phase 12: digital subcommands ---------------------------------------


def cmd_digital_plan(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: turn a natural-language prompt into a validated Design IR.

    The result is one of four shapes:

    * ``DesignIR`` — success. The full IR is in ``data.design`` and the
      project ``name`` and ``kind`` are surfaced at the top level.
    * ``PlannerRefusal`` — prompt is unsafe, ambiguous, or malformed.
      The structured error is in ``errors[0]`` and the supported-kinds
      list is in ``data.supportedKinds``.
    * ``ClarificationRequest`` — recognised the direction but missing a
      required field. Surfaced as a non-error with ``needsClarification``
      in ``data``.
    * ``RoadmapSuggestion`` — recognised the direction but it is not
      v1. Surfaced as a non-error with ``roadmap`` in ``data``.

    On success the optional ``--out PATH`` writes the IR JSON. The path
    is restricted to the current working directory subtree.
    """
    from .digital_ir import dump_design, validate_dict
    from .digital_planner import (
        ClarificationRequest,
        PlannerRefusal,
        RoadmapSuggestion,
        plan_digital_prompt,
    )

    prompt = args.prompt
    result = plan_digital_prompt(prompt)

    if isinstance(result, PlannerRefusal):
        return {
            "success": False,
            "command": "digital.plan",
            "message": result.message,
            "data": {
                "prompt": prompt,
                "supportedKinds": list(result.supported_kinds),
                "nextStep": result.next_step,
                **result.data,
            },
            "warnings": [],
            "errors": [
                {
                    "code": result.code,
                    "detail": result.message,
                    "data": dict(result.data),
                }
            ],
        }

    if isinstance(result, ClarificationRequest):
        return {
            "success": False,
            "command": "digital.plan",
            "message": result.message,
            "data": {
                "prompt": prompt,
                "needsClarification": True,
                "question": result.question,
                "options": list(result.options),
                "default": result.default,
                "supportedKinds": list(result.supported_kinds),
            },
            "warnings": [
                {
                    "code": result.code,
                    "detail": result.message,
                    "data": {
                        "question": result.question,
                        "default": result.default,
                    },
                }
            ],
            "errors": [],
        }

    if isinstance(result, RoadmapSuggestion):
        return {
            "success": True,
            "command": "digital.plan",
            "message": result.message,
            "data": {
                "prompt": prompt,
                "roadmap": True,
                "category": result.category,
                "whyNotV1": result.why_not_v1,
                "proposedPhases": list(result.proposed_phases),
                "nextStep": result.next_step,
            },
            "warnings": [
                {
                    "code": result.code,
                    "detail": result.message,
                    "data": {
                        "category": result.category,
                        "proposedPhases": list(result.proposed_phases),
                    },
                }
            ],
            "errors": [],
        }

    # Success: DesignIR. Re-validate through the IR layer to catch
    # any drift between planner and IR model.
    from pydantic import ValidationError as _PydanticValidationError

    try:
        rebuilt = validate_dict(result.model_dump())
    except _PydanticValidationError as exc:
        errors = format_errors(exc)
        return {
            "success": False,
            "command": "digital.plan",
            "message": "Planner produced an IR that did not re-validate",
            "data": {
                "prompt": prompt,
                "kind": result.kind,
                "name": result.name,
            },
            "warnings": [],
            "errors": [
                {
                    "code": "DIGITAL_PLAN_INTERNAL_INVALID_IR",
                    "detail": err.detail,
                    "data": {"path": err.path, "code": err.code},
                }
                for err in errors
            ],
        }

    assert rebuilt is not None
    payload: dict[str, Any] = {
        "success": True,
        "command": "digital.plan",
        "message": (
            f"Planned {rebuilt.kind} design '{rebuilt.name}'"
        ),
        "data": {
            "prompt": prompt,
            "kind": rebuilt.kind,
            "name": rebuilt.name,
            "design": rebuilt.model_dump(mode="json"),
        },
        "warnings": [],
        "errors": [],
    }

    out_raw = getattr(args, "out", None)
    if out_raw:
        out_path = Path(out_raw).expanduser().resolve()
        cwd = Path.cwd().resolve()
        try:
            out_path.relative_to(cwd)
        except ValueError:
            return {
                "success": False,
                "command": "digital.plan",
                "message": "Refusing to write Design IR outside the current directory",
                "data": {
                    "prompt": prompt,
                    "outPath": str(out_path),
                    "cwd": str(cwd),
                },
                "warnings": [],
                "errors": [
                    {
                        "code": "PATH_OUTSIDE_CWD",
                        "detail": f"{out_path} is not under {cwd}",
                        "data": {"outPath": str(out_path), "cwd": str(cwd)},
                    }
                ],
            }
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(dump_design(rebuilt), encoding="utf-8")
        except OSError as exc:
            return {
                "success": False,
                "command": "digital.plan",
                "message": "Failed to write Design IR",
                "data": {"prompt": prompt, "outPath": str(out_path)},
                "warnings": [],
                "errors": [
                    {
                        "code": "WRITE_FAILED",
                        "detail": str(exc),
                        "data": {"outPath": str(out_path)},
                    }
                ],
            }
        payload["data"]["writtenTo"] = str(out_path)
        payload["message"] = (
            f"Planned {rebuilt.kind} design '{rebuilt.name}' "
            f"and wrote Design IR to {out_path}"
        )

    return payload


def _digital_not_implemented(
    subcommand: str, args: argparse.Namespace
) -> dict[str, Any]:
    """Return a structured 'not yet implemented' payload for the
    digital subcommands that ship in later phases.

    Keeps the JSON contract stable: callers can already probe
    `ltagent digital doctor` etc. and see the planned surface
    before the code lands.
    """
    return {
        "success": True,
        "command": f"digital.{subcommand}",
        "message": (
            f"ltagent digital {subcommand} is part of the Phase 12 "
            f"plan but not yet implemented. See "
            f"docs/digital/plan-tiny8-agent.md for the planned "
            f"behaviour."
        ),
        "data": {
            "phase": 12,
            "subcommand": subcommand,
            "planned": True,
            "source": getattr(args, "source", None),
        },
        "warnings": [
            {
                "code": "DIGITAL_NOT_IMPLEMENTED",
                "detail": (
                    f"digital {subcommand} is a stub in this phase; "
                    f"the real implementation lands in the next sub-phase."
                ),
                "data": {"subcommand": subcommand, "phase": 12},
            }
        ],
        "errors": [],
    }


def cmd_digital_create(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: create a Tiny8 project on disk.

    Accepts either a path to a Design IR JSON file or a
    natural-language prompt (resolved by the digital planner).
    Writes the full artefact set under ``--out`` or under the
    configured ``projects_dir`` default.
    """
    from .digital_ir import load_design, validate_dict
    from .digital_planner import (
        ClarificationRequest,
        PlannerRefusal,
        RoadmapSuggestion,
        plan_digital_prompt,
    )
    from .digital_project import ProjectRequest, create_project

    source = getattr(args, "source", None)
    if not source:
        return {
            "success": False,
            "command": "digital.create",
            "message": "Missing source (Design IR file path or prompt).",
            "data": {},
            "warnings": [],
            "errors": [
                {
                    "code": "MISSING_SOURCE",
                    "detail": "Provide a Design IR file or a natural-language prompt.",
                    "data": {},
                }
            ],
        }

    # Detect IR file vs prompt.
    is_ir_file = (
        source.endswith(".design.json")
        or source.endswith(".design.ir.json")
        or Path(source).exists()
    )

    if is_ir_file:
        try:
            ir = load_design(Path(source))
        except Exception as exc:
            return {
                "success": False,
                "command": "digital.create",
                "message": f"Failed to load Design IR: {exc}",
                "data": {"source": source},
                "warnings": [],
                "errors": [
                    {
                        "code": "DESIGN_LOAD_FAILED",
                        "detail": str(exc),
                        "data": {"source": source},
                    }
                ],
            }
    else:
        plan = plan_digital_prompt(source)
        if isinstance(plan, PlannerRefusal):
            return {
                "success": False,
                "command": "digital.create",
                "message": plan.message,
                "data": {
                    "source": source,
                    "supportedKinds": list(plan.supported_kinds),
                    "nextStep": plan.next_step,
                },
                "warnings": [],
                "errors": [
                    {
                        "code": plan.code,
                        "detail": plan.message,
                        "data": dict(plan.data),
                    }
                ],
            }
        if isinstance(plan, ClarificationRequest):
            return {
                "success": False,
                "command": "digital.create",
                "message": plan.message,
                "data": {
                    "source": source,
                    "needsClarification": True,
                    "question": plan.question,
                    "options": list(plan.options),
                    "default": plan.default,
                },
                "warnings": [],
                "errors": [],
            }
        if isinstance(plan, RoadmapSuggestion):
            return {
                "success": True,
                "command": "digital.create",
                "message": plan.message,
                "data": {
                    "source": source,
                    "roadmap": True,
                    "category": plan.category,
                    "whyNotV1": plan.why_not_v1,
                    "proposedPhases": list(plan.proposed_phases),
                },
                "warnings": [
                    {
                        "code": plan.code,
                        "detail": plan.message,
                        "data": {"category": plan.category},
                    }
                ],
                "errors": [],
            }
        try:
            ir = validate_dict(plan.model_dump())
        except Exception as exc:
            return {
                "success": False,
                "command": "digital.create",
                "message": "Planner produced an invalid Design IR",
                "data": {"source": source},
                "warnings": [],
                "errors": [
                    {
                        "code": "DIGITAL_PLAN_INVALID_IR",
                        "detail": str(exc),
                        "data": {"source": source},
                    }
                ],
            }

    # Resolve output directory.
    out_arg = getattr(args, "out", None)
    if out_arg:
        # --out points at the project directory directly. Skip the
        # date-prefix resolution and write into the named path.
        project_dir = Path(out_arg).expanduser().resolve()
        projects_root = project_dir.parent
        # Generate the project into ``project_dir`` directly.
        from .digital_generator import generate_project as _gen

        gen = _gen(ir, project_dir)
        # The on-disk dir name is the resource key (ltagent://projects/<name>/...).
        result = type(
            "PR",
            (),
            {
                "project": gen,
                "project_id": project_dir.name,
                "project_dir": project_dir,
            },
        )()
    else:
        # Default to a sibling projects/ next to the current dir.
        projects_root = (Path.cwd() / "projects").resolve()
        from .digital_project import resolve_project_dir

        _, project_dir = resolve_project_dir(
            name=ir.name, projects_root=projects_root
        )

        try:
            result = create_project(
                ProjectRequest(
                    ir=ir,
                    projects_root=projects_root,
                    program_source=None,
                    program=None,
                )
            )
        except Exception as exc:
            return {
                "success": False,
                "command": "digital.create",
                "message": f"Failed to create project: {exc}",
                "data": {"source": source, "projectDir": str(project_dir)},
                "warnings": [],
                "errors": [
                    {
                        "code": "PROJECT_CREATE_FAILED",
                        "detail": str(exc),
                        "data": {"projectDir": str(project_dir)},
                    }
                ],
            }

    # Phase D will run simulate here if --simulate.
    payload = {
        "success": True,
        "command": "digital.create",
        "message": (
            f"Created Tiny8 project '{result.project_id}' with "
            f"{len(result.project.files)} files"
        ),
        "data": {
            "source": source,
            "projectId": result.project_id,
            "projectDir": str(result.project_dir),
            "kind": ir.kind,
            "name": ir.name,
            "files": [
                {
                    "path": f.relative_path,
                    "bytes": f.byte_size,
                    "sha256Short": f.sha256_short,
                }
                for f in result.project.files
            ],
        },
        "warnings": [
            {"code": "CREATE_WARNING", "detail": w, "data": {}}
            for w in result.project.warnings
        ],
        "errors": [],
    }
    return payload


def cmd_digital_assemble(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: assemble a .asm file into a .mem image."""
    from .digital_asm import _ROM_MAX, assemble_program

    source = getattr(args, "source", None)
    if not source:
        return {
            "success": False,
            "command": "digital.assemble",
            "message": "Missing .asm source path",
            "data": {},
            "warnings": [],
            "errors": [
                {
                    "code": "MISSING_SOURCE",
                    "detail": "Provide a path to a .asm file.",
                    "data": {},
                }
            ],
        }

    src_path = Path(source).expanduser().resolve()
    if not src_path.exists():
        return {
            "success": False,
            "command": "digital.assemble",
            "message": f"Source not found: {src_path}",
            "data": {"source": str(src_path)},
            "warnings": [],
            "errors": [
                {
                    "code": "ASM_SOURCE_NOT_FOUND",
                    "detail": f"file not found: {src_path}",
                    "data": {"source": str(src_path)},
                }
            ],
        }

    out_arg = getattr(args, "out", None)
    out_path = (
        Path(out_arg).expanduser().resolve()
        if out_arg
        else src_path.with_suffix(".mem")
    )

    try:
        text = src_path.read_text(encoding="utf-8")
        result = assemble_program(text, rom_size=_ROM_MAX + 1)
    except Exception as exc:
        errs = getattr(exc, "errors", None)
        if errs is not None:
            return {
                "success": False,
                "command": "digital.assemble",
                "message": "Assembler rejected the program",
                "data": {"source": str(src_path)},
                "warnings": [],
                "errors": [
                    {"code": e.code, "detail": e.detail, "data": {"line": e.line}}
                    for e in errs
                ],
            }
        return {
            "success": False,
            "command": "digital.assemble",
            "message": str(exc),
            "data": {"source": str(src_path)},
            "warnings": [],
            "errors": [
                {
                    "code": "ASM_INTERNAL_ERROR",
                    "detail": str(exc),
                    "data": {"source": str(src_path)},
                }
            ],
        }

    # The assembler pads to rom_size. The CLI reports the
    # *non-padding* instruction count, which is what the user
    # actually wrote.
    instr_count = sum(1 for w in result.words if w != 0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.to_mem_text(), encoding="utf-8")

    return {
        "success": True,
        "command": "digital.assemble",
        "message": (
            f"Assembled {instr_count} instructions ({len(result.words)} "
            f"words including padding) into {out_path}"
        ),
        "data": {
            "source": str(src_path),
            "outPath": str(out_path),
            "instructionCount": instr_count,
            "wordCount": len(result.words),
            "labelCount": len(result.labels),
            "labels": dict(result.labels),
        },
        "warnings": [],
        "errors": [],
    }


def cmd_digital_doctor(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: report digital toolchain status.

    Checks ``iverilog``, ``vvp``, ``verilator``, ``yosys``,
    ``gtkwave``. Missing tools are reported as
    ``status: "missing"``; present tools include a ``path``
    and ``version`` string.
    """
    from .digital_runner import _TOOL_ALLOWLIST, doctor_status

    statuses = doctor_status()
    payload_tools: dict[str, dict[str, Any]] = {}
    any_missing = False
    for name in sorted(_TOOL_ALLOWLIST):
        st = statuses[name]
        if st.available:
            payload_tools[name] = {
                "status": "ok",
                "path": st.path,
                "version": st.version,
            }
        else:
            any_missing = True
            payload_tools[name] = {
                "status": "missing",
                "path": None,
                "version": None,
            }

    overall = "ok" if not any_missing else "warn"
    return {
        "success": True,
        "command": "digital.doctor",
        "message": (
            "Digital toolchain complete"
            if not any_missing
            else "Digital toolchain has missing tools (warnings, not errors)"
        ),
        "data": {
            "tools": payload_tools,
            "recommendedInstall": {
                "ubuntu": "sudo apt install -y iverilog verilator yosys gtkwave",
                "fedora": "sudo dnf install -y iverilog verilator yosys gtkwave",
                "macos": "brew install icarus-verilog verilator yosys gtkwave",
            },
        },
        "warnings": (
            [
                {
                    "code": "DIGITAL_TOOL_MISSING",
                    "detail": f"{name} not on PATH",
                    "data": {"tool": name},
                }
                for name, st in statuses.items()
                if not st.available
            ]
        ),
        "errors": [],
    } | {"_ltagent_overall": overall}


def cmd_digital_simulate(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: run an Icarus simulation of the project's testbench.

    ``source`` is the project directory. Looks for
    ``tb/tb_tiny8_top.v`` and all files in ``rtl/``.

    If Icarus is missing:
    * ``--strict`` -> success=false, code ``DIGITAL_TOOL_MISSING``.
    * default -> success=true, status="skipped".
    """
    from .digital_reports import (
        ProjectResult,
        SimulationReport,
        parse_simulation_observation,
        write_result_json,
        write_simulation_report,
    )
    from .digital_runner import (
        compile_iverilog,
        doctor_status,
        run_vvp,
        simulation_passed,
    )

    src = Path(getattr(args, "source", None) or "").expanduser().resolve()
    if not src.is_dir():
        return {
            "success": False,
            "command": "digital.simulate",
            "message": f"Project directory not found: {src}",
            "data": {"source": str(src)},
            "warnings": [],
            "errors": [
                {
                    "code": "PROJECT_NOT_FOUND",
                    "detail": f"{src} is not a directory",
                    "data": {"source": str(src)},
                }
            ],
        }

    tb = src / "tb" / "tb_tiny8_top.v"
    rtl = src / "rtl"
    if not tb.exists() or not rtl.is_dir():
        return {
            "success": False,
            "command": "digital.simulate",
            "message": "Not a Tiny8 project (tb/tb_tiny8_top.v or rtl/ missing)",
            "data": {"projectDir": str(src)},
            "warnings": [],
            "errors": [
                {
                    "code": "NOT_A_TINY8_PROJECT",
                    "detail": "tb/tb_tiny8_top.v and rtl/ required",
                    "data": {"projectDir": str(src)},
                }
            ],
        }

    status = doctor_status()
    strict = bool(getattr(args, "strict", False))
    if not status["iverilog"].available or not status["vvp"].available:
        if strict:
            return {
                "success": False,
                "command": "digital.simulate",
                "message": "Icarus (iverilog / vvp) not on PATH",
                "data": {"tools": {n: s.available for n, s in status.items()}},
                "warnings": [],
                "errors": [
                    {
                        "code": "DIGITAL_TOOL_MISSING",
                        "detail": "iverilog and vvp are required for simulation",
                        "data": {"required": ["iverilog", "vvp"]},
                    }
                ],
            }
        report = ProjectResult(
            status="skipped",
            simulation=SimulationReport(
                status="skipped",
                note="iverilog or vvp not on PATH; use --strict to fail",
            ),
        )
        write_simulation_report(src, report.simulation)
        write_result_json(src, report)
        return {
            "success": True,
            "command": "digital.simulate",
            "message": "Icarus not on PATH; simulation skipped",
            "data": {
                "projectDir": str(src),
                "report": "reports/sim.json",
                "status": "skipped",
            },
            "warnings": [
                {
                    "code": "DIGITAL_TOOL_MISSING",
                    "detail": "iverilog / vvp not on PATH",
                    "data": {"strict": strict},
                }
            ],
            "errors": [],
        }

    src_files = [*sorted(rtl.glob("*.v")), tb]
    out_binary = src / "build" / "tiny8_top.vvp"
    out_binary.parent.mkdir(parents=True, exist_ok=True)

    compile = compile_iverilog(
        src_files=src_files, out_binary=out_binary, cwd=src
    )
    if not compile.ok:
        sim = SimulationReport(
            status="fail",
            duration_ms=compile.duration_ms,
            returncode=compile.returncode,
            note="iverilog compile failed",
            stdout_tail=compile.stdout_tail,
            stderr_tail=compile.stderr_tail,
        )
        result = ProjectResult(status="fail", simulation=sim)
        write_simulation_report(src, sim)
        write_result_json(src, result)
        return {
            "success": False,
            "command": "digital.simulate",
            "message": "iverilog compile failed",
            "data": {
                "projectDir": str(src),
                "report": "reports/sim.json",
                "status": "fail",
            },
            "warnings": [],
            "errors": [
                {
                    "code": "SIM_COMPILE_FAILED",
                    "detail": compile.stderr_tail[:1024],
                    "data": {"returncode": compile.returncode},
                }
            ],
        }

    sim_run = run_vvp(binary=out_binary, cwd=src)
    cycles, halted, acc, mem = parse_simulation_observation(sim_run.stdout_tail)
    passed = simulation_passed(sim_run)
    sreport = SimulationReport(
        status="pass" if passed else "fail",
        cycles=cycles,
        halted=halted,
        observed_acc=acc,
        observed_memory=mem,
        duration_ms=sim_run.duration_ms,
        returncode=sim_run.returncode,
        stdout_tail=sim_run.stdout_tail,
        stderr_tail=sim_run.stderr_tail,
    )
    result = ProjectResult(
        status="pass" if passed else "fail",
        simulation=sreport,
    )
    write_simulation_report(src, sreport)
    write_result_json(src, result)
    return {
        "success": passed,
        "command": "digital.simulate",
        "message": (
            f"Simulation {'passed' if passed else 'failed'} at cycle {cycles}"
        ),
        "data": {
            "projectDir": str(src),
            "report": "reports/sim.json",
            "result": "result.json",
            "status": "pass" if passed else "fail",
            "cycles": cycles,
            "halted": halted,
            "acc": acc,
        },
        "warnings": [],
        "errors": (
            []
            if passed
            else [
                {
                    "code": "SIM_TESTBENCH_FAILED",
                    "detail": sim.stdout_tail[-512:],
                    "data": {"returncode": sim.returncode},
                }
            ]
        ),
    }


def cmd_digital_synth_check(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: run a Yosys synthesis sanity check on the HDL.

    Same tool-missing semantics as ``simulate``.
    """
    from .digital_reports import (
        ProjectResult,
        SynthesisReport,
        write_result_json,
        write_synthesis_report,
    )
    from .digital_runner import doctor_status, synth_yosys

    src = Path(getattr(args, "source", None) or "").expanduser().resolve()
    if not src.is_dir():
        return {
            "success": False,
            "command": "digital.synth-check",
            "message": f"Project directory not found: {src}",
            "data": {"source": str(src)},
            "warnings": [],
            "errors": [
                {
                    "code": "PROJECT_NOT_FOUND",
                    "detail": f"{src} is not a directory",
                    "data": {"source": str(src)},
                }
            ],
        }

    rtl = src / "rtl"
    if not rtl.is_dir():
        return {
            "success": False,
            "command": "digital.synth-check",
            "message": "Not a Tiny8 project (rtl/ missing)",
            "data": {"projectDir": str(src)},
            "warnings": [],
            "errors": [
                {
                    "code": "NOT_A_TINY8_PROJECT",
                    "detail": "rtl/ required",
                    "data": {"projectDir": str(src)},
                }
            ],
        }

    status = doctor_status()
    strict = bool(getattr(args, "strict", False))
    if not status["yosys"].available:
        if strict:
            return {
                "success": False,
                "command": "digital.synth-check",
                "message": "Yosys not on PATH",
                "data": {"tools": {n: s.available for n, s in status.items()}},
                "warnings": [],
                "errors": [
                    {
                        "code": "DIGITAL_TOOL_MISSING",
                        "detail": "yosys is required for synth-check",
                        "data": {"required": ["yosys"]},
                    }
                ],
            }
        sreport = SynthesisReport(
            status="skipped",
            note="yosys not on PATH; use --strict to fail",
        )
        result = ProjectResult(status="skipped", synthesis=sreport)
        write_synthesis_report(src, sreport)
        write_result_json(src, result)
        return {
            "success": True,
            "command": "digital.synth-check",
            "message": "Yosys not on PATH; synthesis skipped",
            "data": {
                "projectDir": str(src),
                "report": "reports/synth.json",
                "status": "skipped",
            },
            "warnings": [
                {
                    "code": "DIGITAL_TOOL_MISSING",
                    "detail": "yosys not on PATH",
                    "data": {"strict": strict},
                }
            ],
            "errors": [],
        }

    src_files = sorted(rtl.glob("*.v"))
    res = synth_yosys(top="tiny8_top", src_files=src_files, cwd=src)
    passed = res.ok
    sreport = SynthesisReport(
        status="pass" if passed else "fail",
        duration_ms=res.duration_ms,
        returncode=res.returncode,
        stdout_tail=res.stdout_tail,
        stderr_tail=res.stderr_tail,
    )
    result = ProjectResult(
        status="pass" if passed else "fail", synthesis=sreport
    )
    write_synthesis_report(src, sreport)
    write_result_json(src, result)
    return {
        "success": passed,
        "command": "digital.synth-check",
        "message": (
            f"Synthesis {'passed' if passed else 'failed'} "
            f"({res.duration_ms}ms)"
        ),
        "data": {
            "projectDir": str(src),
            "report": "reports/synth.json",
            "result": "result.json",
            "status": "pass" if passed else "fail",
        },
        "warnings": [],
        "errors": (
            []
            if passed
            else [
                {
                    "code": "SYNTH_FAILED",
                    "detail": res.stderr_tail[:1024] or res.stdout_tail[:1024],
                    "data": {"returncode": res.returncode},
                }
            ]
        ),
    }


def cmd_digital_inspect(args: argparse.Namespace) -> dict[str, Any]:
    """Phase 12: inspect a project and return its manifest + result."""
    import json

    src = Path(getattr(args, "source", None) or "").expanduser().resolve()
    if not src.is_dir():
        return {
            "success": False,
            "command": "digital.inspect",
            "message": f"Project directory not found: {src}",
            "data": {"source": str(src)},
            "warnings": [],
            "errors": [
                {
                    "code": "PROJECT_NOT_FOUND",
                    "detail": f"{src} is not a directory",
                    "data": {"source": str(src)},
                }
            ],
        }

    manifest_path = src / "manifest.json"
    result_path = src / "result.json"
    design_path = src / "design.ir.json"

    payload: dict[str, Any] = {
        "projectDir": str(src),
        "files": sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()),
    }

    if manifest_path.exists():
        try:
            payload["manifest"] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            payload["manifestError"] = str(exc)

    if result_path.exists():
        try:
            payload["result"] = json.loads(
                result_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            payload["resultError"] = str(exc)

    if design_path.exists():
        try:
            payload["design"] = json.loads(
                design_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            payload["designError"] = str(exc)

    return {
        "success": True,
        "command": "digital.inspect",
        "message": f"Inspected project at {src}",
        "data": payload,
        "warnings": [],
        "errors": [],
    }


# ---- helpers -------------------------------------------------------------


def _load_config_or(path: str | None) -> Config:
    return load_config(Path(path).expanduser() if path else None)


def _is_within(child: Path, parent: Path) -> bool:
    from .security import is_within

    return is_within(child, parent)


def _overall_status(checks: Sequence[CheckResult]) -> str:
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    if statuses == {"skip"}:
        return "skip"
    return "ok"


def _doctor_message(overall: str, *, simulate: bool) -> str:
    if overall == "ok":
        return f"Doctor OK{', smoke sim ran' if simulate else ''}"
    if overall == "warn":
        return "Doctor completed with warnings"
    if overall == "skip":
        return "Doctor skipped checks (missing prerequisites)"
    return "Doctor found problems"


def _placeholder_ir(project_name: str) -> dict[str, Any]:
    return {
        "schemaVersion": "0.1",
        "name": project_name,
        "topology": "voltage_divider",
        "description": "Placeholder IR. Phase 0 only. Real circuits land in Phase 1.",
        "nodes": ["in", "out", "0"],
        "components": [],
        "analysis": {"kind": "op"},
        "measurements": [],
        "metadata": {"createdBy": "ltagent", "phase": 0},
    }


def _config_search_report() -> list[str]:
    from .config import search_paths_report

    return search_paths_report()


# ---- argparse wiring -----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ltagent",
        description=(
            "Local CLI and MCP adapter for safely generating and "
            "simulating LTspice circuits. See docs/SPEC.md for the current phase."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the ltagent version and exit",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="path to a config.toml file (overrides the search order)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=None,
        help="emit structured JSON output (overrides config.agent.json_output_default)",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="force human-readable output",
    )
    subparsers = parser.add_subparsers(dest="command", required=False, metavar="COMMAND")

    def _add_output_flags(p: argparse.ArgumentParser) -> None:
        # Subcommands inherit the top-level --json/--text if the user puts
        # them after the subcommand. The argparse value resolution picks the
        # rightmost occurrence, so this is fine.
        # Idempotent: skip --text if a subparser already declared its own
        # (Phase 4's `parse-log` adds `--text STRING` for inline log bodies;
        # we must not collide with it).
        existing = {opt for action in p._actions for opt in action.option_strings}
        p.add_argument(
            "--json",
            action="store_true",
            default=None,
            help=argparse.SUPPRESS,
        )
        # Only add --text as a global boolean flag if the subparser
        # doesn't already reserve --text for its own (possibly
        # different-signature) purpose. Subparsers that want to add a
        # custom --text later should set the ``_ltagent_skip_text_flag``
        # attribute on themselves *before* calling _add_output_flags.
        if "--text" not in existing and not getattr(
            p, "_ltagent_skip_text_flag", False
        ):
            p.add_argument(
                "--text",
                action="store_true",
                help=argparse.SUPPRESS,
            )

    p_doctor = subparsers.add_parser(
        "doctor",
        help="diagnose the local LTspice / Wine environment",
    )
    _add_output_flags(p_doctor)
    p_doctor.add_argument(
        "--simulate",
        action="store_true",
        help="also attempt a tiny .op smoke simulation (slow, may time out)",
    )
    p_doctor.add_argument(
        "--workspace",
        metavar="DIR",
        help="workspace directory to check (defaults to config.workspace.projects_dir)",
    )

    p_init = subparsers.add_parser(
        "init",
        help="create a new project workspace under the configured projects_dir",
    )
    _add_output_flags(p_init)
    p_init.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="project directory name (default: current directory)",
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="write artifacts even if the target directory is not empty",
    )
    p_init.add_argument(
        "--allow-outside-workspace",
        action="store_true",
        help="permit creating a project outside the configured projects_dir",
    )

    p_config = subparsers.add_parser(
        "config",
        help="inspect the resolved configuration",
    )
    _add_output_flags(p_config)
    config_sub = p_config.add_subparsers(dest="config_command", required=True, metavar="SUBCOMMAND")
    p_config_show = config_sub.add_parser("show", help="print the resolved config as JSON")
    _add_output_flags(p_config_show)
    p_config_validate = config_sub.add_parser("validate", help="validate the config and report issues")
    _add_output_flags(p_config_validate)

    # --- Phase 1/2: ir and netlist subcommands -------------------------
    p_ir = subparsers.add_parser(
        "ir",
        help="validate or inspect Circuit IR documents",
    )
    _add_output_flags(p_ir)
    ir_sub = p_ir.add_subparsers(dest="ir_command", required=True, metavar="SUBCOMMAND")

    p_ir_validate = ir_sub.add_parser(
        "validate",
        help="validate a Circuit IR JSON file",
    )
    _add_output_flags(p_ir_validate)
    p_ir_validate.add_argument(
        "path",
        metavar="PATH",
        help="path to a .ir.json file",
    )

    p_ir_schema = ir_sub.add_parser(
        "schema",
        help="print the bundled Circuit IR JSON Schema",
    )
    _add_output_flags(p_ir_schema)

    p_netlist = subparsers.add_parser(
        "netlist",
        help="generate a .cir netlist from a Circuit IR JSON file",
    )
    _add_output_flags(p_netlist)
    p_netlist.add_argument(
        "path",
        metavar="PATH",
        help="path to a .ir.json file",
    )
    p_netlist.add_argument(
        "--out",
        metavar="PATH",
        help=(
            "write the netlist to PATH (resolved relative to the current "
            "directory); if omitted, the netlist is included in the JSON "
            "output instead of being written to disk"
        ),
    )
    p_netlist.add_argument(
        "--allow-unsafe-directives",
        action="store_true",
        help=(
            "drop raw directives not in the allowlist instead of raising "
            "(Phase 2 escape hatch; the rejected list is reported in "
            "data.rejectedDirectives)"
        ),
    )

    p_asc = subparsers.add_parser(
        "asc",
        help="generate a .asc schematic from a Circuit IR JSON file (Phase 5)",
    )
    _add_output_flags(p_asc)
    p_asc.add_argument(
        "path",
        metavar="PATH",
        help="path to a .ir.json file",
    )
    p_asc.add_argument(
        "--out",
        metavar="PATH",
        help=(
            "write the schematic to PATH (resolved relative to the current "
            "directory); if omitted, the schematic is included in the JSON "
            "output instead of being written to disk"
        ),
    )

    p_run = subparsers.add_parser(
        "run",
        help="execute an LTspice batch simulation against a .cir file (Phase 3)",
    )
    _add_output_flags(p_run)
    p_run.add_argument(
        "cir",
        help="path to the .cir netlist to simulate",
    )
    p_run.add_argument(
        "--workdir",
        metavar="DIR",
        default=None,
        help="working directory for the simulation (default: directory of the .cir file)",
    )
    p_run.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=int,
        default=None,
        help="timeout in seconds (default: from config.toml, clamped to >= 5)",
    )
    p_run.add_argument(
        "--ltspice-arg",
        action="append",
        metavar="ARG",
        help="extra argument to pass to LTspice (e.g. --ltspice-arg=-ascii). Repeatable.",
    )

    # --- Phase 7: create project workflow -------------------------------
    p_create = subparsers.add_parser(
        "create",
        help="create a complete project from an IR file or a natural-language prompt (Phase 7)",
    )
    _add_output_flags(p_create)
    p_create.add_argument(
        "source",
        nargs="?",
        default=None,
        help=(
            "either a path to a .ir.json file or a natural-language prompt "
            "(detected by the .ir.json extension or by the presence of spaces)"
        ),
    )
    p_create.add_argument(
        "--ir-file",
        metavar="PATH",
        default=None,
        help="explicit path to the .ir.json file (overrides positional source)",
    )
    p_create.add_argument(
        "--prompt",
        metavar="TEXT",
        default=None,
        help="explicit natural-language prompt (overrides positional source)",
    )
    p_create.add_argument(
        "--out",
        metavar="DIR",
        default=None,
        help=(
            "target project directory (default: <projects_dir>/<date>_<name>); "
            "must be inside the configured workspace unless --allow-outside-workspace"
        ),
    )
    p_create.add_argument(
        "--templates-dir",
        metavar="DIR",
        default=None,
        help="templates root (default: config.workspace.templates_dir)",
    )
    p_create.add_argument(
        "--run",
        action="store_true",
        help=(
            "attempt an LTspice run after generating the netlist; "
            "the project is still created if the run fails or LTspice "
            "is unavailable (status reported in result.json)"
        ),
    )
    p_create.add_argument(
        "--allow-outside-workspace",
        action="store_true",
        help="permit creating a project outside the configured projects_dir",
    )
    p_create.add_argument(
        "--save-template",
        action="store_true",
        help=(
            "after creating the project, also write a candidate template "
            "manifest under <templates_dir>/candidates/<id>/. Requires a "
            "successful run (use --run) and a recorded layout score. "
            "The candidate can then be evaluated and promoted with "
            "`ltagent template evaluate` / `ltagent template promote`."
        ),
    )
    p_create.add_argument(
        "--template-id",
        metavar="ID",
        default=None,
        help=(
            "override the candidate's id (default: the IR's name); "
            "only valid with --save-template"
        ),
    )
    p_create.add_argument(
        "--template-description",
        metavar="TEXT",
        default=None,
        help=(
            "human-readable description recorded in the candidate manifest; "
            "only valid with --save-template"
        ),
    )
    p_create.add_argument(
        "--template-tag",
        action="append",
        metavar="TAG",
        default=None,
        help=(
            "tag to record on the candidate manifest (repeatable); "
            "the special tag `user-requested` is added by default if no "
            "tags are supplied, which earns the +3 RULE_USER_REQUESTED "
            "score in the Phase 9 evaluator"
        ),
    )

    # --- Phase 8: rule-based planner ------------------------------------
    p_plan = subparsers.add_parser(
        "plan",
        help="convert a natural-language prompt into a validated Circuit IR (Phase 8)",
    )
    _add_output_flags(p_plan)
    p_plan.add_argument(
        "prompt",
        help="natural-language prompt in English or Indonesian",
    )
    p_plan.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help=(
            "optional path to also write the resulting IR JSON; "
            "skipped on refusal"
        ),
    )

    # --- Phase 12: digital design (Tiny8 CPU) ---------------------------
    p_digital = subparsers.add_parser(
        "digital",
        help="Tiny8 CPU digital design subcommands (Phase 12)",
    )
    _add_output_flags(p_digital)
    digital_sub = p_digital.add_subparsers(
        dest="digital_command", required=True, metavar="SUBCOMMAND"
    )

    p_digital_plan = digital_sub.add_parser(
        "plan",
        help="convert a natural-language prompt into a Design IR (Phase 12)",
    )
    _add_output_flags(p_digital_plan)
    p_digital_plan.add_argument(
        "prompt",
        help="natural-language prompt in English or Indonesian",
    )
    p_digital_plan.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help=(
            "optional path to also write the resulting Design IR JSON; "
            "skipped on refusal / clarification / roadmap"
        ),
    )

    # Phase C / D / E subcommand stubs. Each one returns a structured
    # "not yet implemented" payload in the JSON contract so the parser
    # is complete from day one; the actual handlers land in Phases C-E.
    for sub_name, sub_help in (
        ("create", "create a Tiny8 project from a Design IR or a prompt (Phase C)"),
        ("assemble", "assemble a .asm program into a .mem image (Phase C)"),
        ("doctor", "report digital toolchain status (Phase D)"),
        ("simulate", "run an Icarus simulation of the generated HDL (Phase D)"),
        ("synth-check", "run a Yosys synthesis sanity check on the HDL (Phase D)"),
        ("inspect", "inspect a Tiny8 project artefact (Phase E)"),
    ):
        p = digital_sub.add_parser(sub_name, help=sub_help)
        _add_output_flags(p)
        if sub_name in ("create", "assemble", "simulate", "synth-check", "inspect"):
            p.add_argument(
                "source",
                help=(
                    "path to a Design IR (.design.json), a .asm program, "
                    "or a project directory (depending on the subcommand)"
                ),
            )
        if sub_name in ("create",):
            p.add_argument(
                "--out",
                metavar="DIR",
                default=None,
                help="project directory (default: <projects_dir>/<date>_<name>)",
            )
            p.add_argument(
                "--simulate",
                action="store_true",
                help="also attempt simulation after generation",
            )
        if sub_name in ("assemble",):
            p.add_argument(
                "--out",
                metavar="PATH",
                default=None,
                help="output .mem path (default: <source>.mem next to source)",
            )
        if sub_name in ("simulate", "synth-check"):
            p.add_argument(
                "--strict",
                action="store_true",
                help=(
                    "fail the command if the required tool is missing "
                    "(default: structured skip with success=true)"
                ),
            )

    # --- Phase 4: log parser + result builder --------------------------
    p_parse_log = subparsers.add_parser(
        "parse-log",
        help="parse a .log file into measurements and findings (Phase 4)",
    )
    # Phase 4 declares its own `--text STRING` for inline log bodies; we
    # must not let the generic output flag collide with it.
    p_parse_log._ltagent_skip_text_flag = True  # type: ignore[attr-defined]
    _add_output_flags(p_parse_log)
    p_parse_log.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path to the .log file (mutually exclusive with --log-text)",
    )
    p_parse_log.add_argument(
        "--log-text",
        metavar="STRING",
        default=None,
        help="parse the given log text instead of reading from a file",
    )

    p_result = subparsers.add_parser(
        "result",
        help="build a result.json from a log file and optional run payload (Phase 4)",
    )
    _add_output_flags(p_result)
    p_result.add_argument(
        "log",
        help="path to the .log file produced by LTspice",
    )
    p_result.add_argument(
        "--project-id",
        metavar="ID",
        default=None,
        help="project id (required unless --project-id is supplied)",
    )
    p_result.add_argument(
        "--run-payload",
        metavar="JSON",
        default=None,
        help=(
            "JSON object with the runner payload "
            "(e.g. '{\"success\": true, \"exitCode\": 0, \"timeoutSeconds\": 30, "
            "\"durationMs\": 812}'); merged into the result.json run block"
        ),
    )
    p_result.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="write the result.json to PATH (in addition to JSON output)",
    )
    p_result.add_argument(
        "--ir-file",
        metavar="PATH",
        default=None,
        help="relative path recorded in result.json under files.ir",
    )
    p_result.add_argument(
        "--cir-file",
        metavar="PATH",
        default=None,
        help="relative path recorded in result.json under files.cir",
    )
    p_result.add_argument(
        "--asc-file",
        metavar="PATH",
        default=None,
        help="relative path recorded in result.json under files.asc",
    )
    p_result.add_argument(
        "--template",
        metavar="ID",
        default=None,
        help="template id used for this project (recorded in result.json)",
    )
    p_result.add_argument(
        "--template-promoted",
        action="store_true",
        help="mark the result as having promoted a template candidate",
    )
    p_result.add_argument(
        "--layout-score",
        metavar="SCORE",
        type=int,
        default=None,
        help="layout checker score (0-100) recorded in result.json",
    )
    p_result.add_argument(
        "--layout-warning",
        action="append",
        metavar="TEXT",
        help="layout warning string (repeatable)",
    )

    p_template = subparsers.add_parser(
        "template",
        help="manage the local circuit template library (Phase 6)",
    )
    _add_output_flags(p_template)
    p_template.add_argument(
        "--templates-dir",
        metavar="DIR",
        help="templates directory (defaults to config.workspace.templates_dir)",
    )
    template_sub = p_template.add_subparsers(
        dest="template_command", required=True, metavar="SUBCOMMAND"
    )

    def _add_templates_dir_flag(p: argparse.ArgumentParser) -> None:
        # Mirror the parent --templates-dir on every leaf so the flag can
        # appear either before or after the sub-subcommand (e.g.
        # `ltagent template seed --templates-dir templates`).
        p.add_argument(
            "--templates-dir",
            metavar="DIR",
            default=None,
            help=argparse.SUPPRESS,
        )

    p_template_list = template_sub.add_parser(
        "list", help="list templates (default status: all)"
    )
    _add_output_flags(p_template_list)
    _add_templates_dir_flag(p_template_list)
    p_template_list.add_argument(
        "--status",
        choices=("official", "candidate", "candidates", "rejected"),
        help="filter by status (default: all)",
    )

    p_template_show = template_sub.add_parser(
        "show", help="show one template by id"
    )
    _add_output_flags(p_template_show)
    _add_templates_dir_flag(p_template_show)
    p_template_show.add_argument("id", help="template id (e.g. rc_lowpass)")
    p_template_show.add_argument(
        "--status",
        choices=("official", "candidate", "candidates", "rejected"),
        default="official",
        help="status directory to look in (default: official)",
    )

    p_template_match = template_sub.add_parser(
        "match", help="find an existing template for a Circuit IR"
    )
    _add_output_flags(p_template_match)
    _add_templates_dir_flag(p_template_match)
    p_template_match.add_argument("ir", help="path to a Circuit IR JSON file")
    p_template_match.add_argument(
        "--status",
        choices=("official", "candidate", "candidates", "rejected"),
        default="official",
        help="status directory to search (default: official)",
    )
    p_template_match.add_argument(
        "--no-bump",
        action="store_true",
        help="do not increment the use count on match",
    )

    p_template_audit = template_sub.add_parser(
        "audit", help="summarise the template library and surface duplicates"
    )
    _add_output_flags(p_template_audit)
    _add_templates_dir_flag(p_template_audit)

    p_template_seed = template_sub.add_parser(
        "seed", help="idempotently install the 3 MVP default templates"
    )
    _add_output_flags(p_template_seed)
    _add_templates_dir_flag(p_template_seed)

    p_template_evaluate = template_sub.add_parser(
        "evaluate",
        help="score a template and report gates (Phase 9)",
    )
    _add_output_flags(p_template_evaluate)
    _add_templates_dir_flag(p_template_evaluate)
    p_template_evaluate.add_argument("id", help="template id (e.g. rc_lowpass)")
    p_template_evaluate.add_argument(
        "--status",
        choices=("official", "candidate", "candidates", "rejected"),
        default="candidates",
        help="status directory to look in (default: candidates)",
    )

    p_template_promote = template_sub.add_parser(
        "promote",
        help=(
            "manually promote a candidate to official; refuses when "
            "gates fail unless --force is supplied (Phase 9)"
        ),
    )
    _add_output_flags(p_template_promote)
    _add_templates_dir_flag(p_template_promote)
    p_template_promote.add_argument("id", help="template id (e.g. rc_lowpass)")
    p_template_promote.add_argument(
        "--force",
        action="store_true",
        help=(
            "override failing gates; the override is recorded in "
            "the manifest tags so future audits can find it"
        ),
    )

    p_template_audit_promote = template_sub.add_parser(
        "audit-promotability",
        help=(
            "evaluate every candidate and report which are eligible "
            "for promotion (Phase 9)"
        ),
    )
    _add_output_flags(p_template_audit_promote)
    _add_templates_dir_flag(p_template_audit_promote)
    p_template_audit_promote.add_argument(
        "--status",
        choices=("official", "candidate", "candidates", "rejected"),
        action="append",
        default=None,
        help=(
            "additional status directory to include in the audit "
            "(repeatable); default is candidates only"
        ),
    )

    return parser


def _resolve_output_mode(args: argparse.Namespace) -> bool:
    """Return True if JSON output is requested."""
    if args.json:
        return True
    if args.text:
        return False
    return bool(
        args.command in (
            "init", "doctor", "config", "run", "create", "template", "ir",
            "netlist", "asc", "parse-log", "result", "plan",
        )
        or getattr(args, "config_command", None)
        or getattr(args, "template_command", None)
        or getattr(args, "ir_command", None)
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = _resolve_output_mode(args)

    try:
        if args.version:
            payload = cmd_version(args)
        elif args.command == "doctor":
            payload = cmd_doctor(args)
        elif args.command == "init":
            payload = cmd_init(args)
        elif args.command == "config" and args.config_command == "show":
            payload = cmd_config_show(args)
        elif args.command == "config" and args.config_command == "validate":
            payload = cmd_config_validate(args)
        elif args.command == "ir" and args.ir_command == "validate":
            payload = cmd_ir_validate(args)
        elif args.command == "ir" and args.ir_command == "schema":
            payload = cmd_ir_schema(args)
        elif args.command == "netlist":
            payload = cmd_netlist(args)
        elif args.command == "asc":
            payload = cmd_asc(args)
        elif args.command == "run":
            payload = cmd_run(args)
        elif args.command == "create":
            payload = cmd_create_safe(args)
        elif args.command == "plan":
            payload = cmd_plan(args)
        elif args.command == "digital" and args.digital_command == "plan":
            payload = cmd_digital_plan(args)
        elif args.command == "digital" and args.digital_command == "create":
            payload = cmd_digital_create(args)
        elif args.command == "digital" and args.digital_command == "assemble":
            payload = cmd_digital_assemble(args)
        elif args.command == "digital" and args.digital_command == "doctor":
            payload = cmd_digital_doctor(args)
        elif args.command == "digital" and args.digital_command == "simulate":
            payload = cmd_digital_simulate(args)
        elif args.command == "digital" and args.digital_command == "synth-check":
            payload = cmd_digital_synth_check(args)
        elif args.command == "digital" and args.digital_command == "inspect":
            payload = cmd_digital_inspect(args)
        elif args.command == "parse-log":
            payload = cmd_parse_log(args)
        elif args.command == "result":
            payload = cmd_result(args)
        elif args.command == "template" and args.template_command == "list":
            payload = cmd_template_list(args)
        elif args.command == "template" and args.template_command == "show":
            payload = cmd_template_show(args)
        elif args.command == "template" and args.template_command == "match":
            payload = cmd_template_match(args)
        elif args.command == "template" and args.template_command == "audit":
            payload = cmd_template_audit(args)
        elif args.command == "template" and args.template_command == "seed":
            payload = cmd_template_seed(args)
        elif args.command == "template" and args.template_command == "evaluate":
            payload = cmd_template_evaluate(args)
        elif args.command == "template" and args.template_command == "promote":
            payload = cmd_template_promote(args)
        elif (
            args.command == "template"
            and args.template_command == "audit-promotability"
        ):
            payload = cmd_template_promotability(args)
        else:
            parser.print_help(sys.stderr)
            return 2
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        payload = _err(
            args.command or "ltagent",
            "Unexpected internal error",
            "INTERNAL_ERROR",
            f"{type(exc).__name__}: {exc}",
        )
        _emit(payload, as_json)
        return 1

    _emit(payload, as_json)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
