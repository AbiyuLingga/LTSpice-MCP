"""Workbench v2 MCP tools (Phase 9).

Adds three curated tools that expose the v2 workbench contract to MCP
clients (notably Codex):

* ``tool_wb_v2_inspect_project`` -- read a v2 project's manifest and
  documents without mutating anything.
* ``tool_wb_v2_apply_change_set`` -- apply a typed ChangeSet through
  :class:`ltagent.design_service.DesignService`, returning the
  new revision and the affected documents.
* ``tool_wb_v2_propose_ai_design`` -- run the AI workflow against a
  prompt and the project's current documents, returning the
  workflow result (capability, proposal, validation, decision).
  The proposal is **never** auto-applied; the rendering layer is
  expected to call ``tool_wb_v2_apply_change_set`` after the user
  approves.

The same handlers are exposed by the workbench v2 resource URIs in
:mod:`ltagent.mcp_server`. All paths stay inside the canonical
projects root resolved by :mod:`ltagent.projects_root`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .ai_provider import (
    AIProviderError,
    ProviderAdapter,
    ProviderRegistry,
)
from .ai_workflow import (
    AIWorkflow,
    ProposalDecision,
    WorkflowResult,
)
from .design_service import (
    ChangeSet,
    DesignService,
    WorkbenchV2Error,
)
from .projects_root import resolve_projects_root
from .workbench_v2 import (
    FILE_ANALOG_GRAPH,
    FILE_DIGITAL,
    FILE_MANIFEST,
    FILE_REQUIREMENTS,
    FILE_SCHEMATIC_VIEW,
    FILE_SYSTEM,
    PROJECT_SCHEMA_VERSION,
    HardwareProject,
)

ENV_PROJECT_SCOPE = "LTAGENT_PROJECT_SCOPE"


def _workbench_handler_result(
    command: str, success: bool, message: str, data: dict[str, Any]
) -> dict[str, Any]:
    return {
        "success": success,
        "command": command,
        "message": message,
        "data": data,
        "warnings": [],
        "errors": [],
    }


def _resolve_projects_root(
    explicit: str | None,
) -> Path:
    if explicit:
        return resolve_projects_root(explicit)
    return resolve_projects_root(None)


def _scope_error(command: str, project_id: str) -> dict[str, Any] | None:
    scoped_project = os.environ.get(ENV_PROJECT_SCOPE)
    if scoped_project and project_id != scoped_project:
        return _workbench_handler_result(
            command,
            False,
            "Project is outside the Codex MCP scope",
            {
                "code": "WB_PROJECT_SCOPE_DENIED",
                "projectId": project_id,
                "scopedProjectId": scoped_project,
            },
        )
    return None


def _read_documents(project_dir: Path) -> dict[str, Any]:
    docs: dict[str, Any] = {}

    def _load(name: str, key: str) -> None:
        path = project_dir / name
        if not path.is_file():
            return
        try:
            docs[key] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            docs[key] = {"_error": "invalid json", "path": str(path)}

    _load(FILE_MANIFEST, "manifest")
    _load(FILE_ANALOG_GRAPH, "analog")
    _load(FILE_SCHEMATIC_VIEW, "schematic")
    _load(FILE_DIGITAL, "digital")
    _load(FILE_SYSTEM, "system")
    _load(FILE_REQUIREMENTS, "requirements")
    return docs


def tool_wb_v2_inspect_project(
    project_id: str,
    *,
    projects_root: str | None = None,
) -> dict[str, Any]:
    """Return the v2 manifest and documents for a project.

    ``project_id`` is the slug under the canonical projects root.
    No document is mutated.
    """
    command = "wb_v2_inspect_project"
    if denied := _scope_error(command, project_id):
        return denied
    if not project_id or "/" in project_id or project_id.startswith("."):
        return _workbench_handler_result(
            command,
            False,
            "Invalid project id",
            {"code": "WB_PROJECT_ID_INVALID", "projectId": project_id},
        )
    root = _resolve_projects_root(projects_root)
    project_dir = (root / project_id).resolve(strict=False)
    manifest_path = project_dir / FILE_MANIFEST
    if not manifest_path.is_file():
        return _workbench_handler_result(
            command,
            False,
            "Project not found",
            {
                "code": "WB_PROJECT_NOT_FOUND",
                "projectId": project_id,
                "projectsRoot": str(root),
            },
        )
    documents = _read_documents(project_dir)
    return _workbench_handler_result(
        command,
        True,
        "Workbench v2 project inspected",
        {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "projectId": project_id,
            "projectsRoot": str(root),
            "documents": documents,
        },
    )


def tool_wb_v2_apply_change_set(
    project_id: str,
    change_set: dict[str, Any],
    *,
    projects_root: str | None = None,
) -> dict[str, Any]:
    """Apply a typed v2 ChangeSet to a project.

    The change set is validated by the v2 Pydantic contract, then
    applied atomically by :class:`DesignService`. The handler returns
    the new revision and the documents the service wrote so a Codex
    client can update its in-memory view.
    """
    command = "wb_v2_apply_change_set"
    if denied := _scope_error(command, project_id):
        return denied
    if not project_id or "/" in project_id or project_id.startswith("."):
        return _workbench_handler_result(
            command,
            False,
            "Invalid project id",
            {"code": "WB_PROJECT_ID_INVALID", "projectId": project_id},
        )
    try:
        change_set_model = ChangeSet.model_validate(change_set)
    except Exception as exc:
        return _workbench_handler_result(
            command,
            False,
            "ChangeSet validation failed",
            {
                "code": "WB_CHANGESET_INVALID",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    root = _resolve_projects_root(projects_root)
    service = DesignService(projects_root=str(root))
    try:
        project = service.open_project(project_id)
    except WorkbenchV2Error as exc:
        return _workbench_handler_result(
            command,
            False,
            exc.message,
            {"code": exc.code, **exc.data},
        )
    try:
        result = service.apply_change_set(project_id, change_set_model.model_dump(mode="json"))
    except WorkbenchV2Error as exc:
        return _workbench_handler_result(
            command,
            False,
            exc.message,
            {"code": exc.code, **exc.data},
        )
    project_dir = service._project_dir(project_id)
    documents = _read_documents(project_dir)
    return _workbench_handler_result(
        command,
        True,
        "ChangeSet applied",
        {
            "projectId": project_id,
            "schemaVersion": project.schemaVersion,
            "previousRevision": project.revision,
            "revision": result.revision,
            "historyStep": result.history_step,
            "changedDocuments": list(result.changed_documents),
            "affectedComponentIds": list(result.affected_component_ids),
            "documents": documents,
        },
    )


def tool_wb_v2_propose_ai_design(
    project_id: str,
    prompt: str,
    *,
    projects_root: str | None = None,
    body_override: Any | None = None,
) -> dict[str, Any]:
    """Run the AI workflow and return its structured result.

    The workflow never auto-applies. The proposal is returned
    alongside the validation verdict so a Codex or desktop client
    can present a diff and ask the user to accept or reject.
    """
    command = "wb_v2_propose_ai_design"
    if denied := _scope_error(command, project_id):
        return denied
    if not project_id or "/" in project_id or project_id.startswith("."):
        return _workbench_handler_result(
            command,
            False,
            "Invalid project id",
            {"code": "WB_PROJECT_ID_INVALID", "projectId": project_id},
        )
    if not prompt or not prompt.strip():
        return _workbench_handler_result(
            command,
            False,
            "Prompt is empty",
            {"code": "WB_PROMPT_EMPTY"},
        )
    root = _resolve_projects_root(projects_root)
    project_dir = (root / project_id).resolve(strict=False)
    manifest_path = project_dir / FILE_MANIFEST
    if not manifest_path.is_file():
        return _workbench_handler_result(
            command,
            False,
            "Project not found",
            {
                "code": "WB_PROJECT_NOT_FOUND",
                "projectId": project_id,
                "projectsRoot": str(root),
            },
        )
    documents = _read_documents(project_dir)
    service = DesignService(projects_root=str(root))
    provider: ProviderAdapter | None = None
    try:
        registry = ProviderRegistry.open(root)
        profile = registry.get("default")
        if profile is not None:
            provider = ProviderAdapter(profile, registry.keychain)
    except AIProviderError:
        provider = None
    workflow = AIWorkflow(design_service=service, provider=provider)
    try:
        result: WorkflowResult = workflow.run(
            prompt,
            project_id=project_id,
            project_revision=int(documents.get("manifest", {}).get("revision", 0)),
            documents={
                key: value
                for key, value in documents.items()
                if key in {"analog", "schematic", "digital", "system", "requirements"}
                and isinstance(value, dict)
            },
            body_override=body_override,
        )
    except AIProviderError as exc:
        return _workbench_handler_result(
            command,
            False,
            exc.message,
            {"code": exc.code, **exc.data},
        )
    return _workbench_handler_result(
        command,
        True,
        "AI workflow result",
        {
            "requirement": result.requirement.model_dump(mode="json"),
            "proposal": result.proposal.model_dump(mode="json"),
            "validation": {
                "is_valid": result.validation.is_valid,
                "issues": list(result.validation.issues),
                "warnings": list(result.validation.warnings),
                "impact": result.validation.impact,
            },
            "repairs": [
                {
                    "attempt": r.attempt,
                    "feedback": r.feedback,
                    "hasProposal": r.proposal is not None,
                }
                for r in result.repairs
            ],
            "decision": result.decision.value,
        },
    )


def workbench_v2_capabilities_resource() -> str:
    """Static capability document for the workbench v2 surface."""
    payload = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "name": "ltagent workbench v2",
        "tools": [
            "wb_v2_inspect_project",
            "wb_v2_apply_change_set",
            "wb_v2_propose_ai_design",
        ],
        "resources": [
            "ltagent://workbench/v2/capabilities",
            "ltagent://workbench/v2/projects/{project_id}/manifest",
        ],
        "documents": [
            "manifest",
            "analog",
            "schematic",
            "digital",
            "system",
            "requirements",
        ],
        "documents_to_proposal_decision": {
            "unsupported_capability": "rejected (UI shows reason)",
            "valid_proposal": "pending (UI must call wb_v2_apply_change_set)",
            "rejected_proposal": "rejected (UI shows validation issues)",
        },
        "codex_install": "ltagent codex install",
        "codex_uninstall": "ltagent codex uninstall",
        "codex_doctor": "ltagent codex doctor",
        "manifestFile": FILE_MANIFEST,
    }
    return json.dumps(payload, sort_keys=False, indent=2)


__all__ = [
    "tool_wb_v2_apply_change_set",
    "tool_wb_v2_inspect_project",
    "tool_wb_v2_propose_ai_design",
    "workbench_v2_capabilities_resource",
]


# Re-export the decision enum so downstream callers can branch on it.
_DECISION_HELP = ProposalDecision

# Compatibility marker used by ``_emit_sdk_missing`` style tests.
HardwareProject: type = HardwareProject
