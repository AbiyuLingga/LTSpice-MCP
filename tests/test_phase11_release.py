"""Tests for the Phase 11 release artefacts (production release).

Covers:
* The release artefacts (CHANGELOG, RELEASE_NOTES,
  ALPHA_PLAYBOOK) exist and document the expected sections.
* The Codex + workbench v2 + AI workflow surface round-trip
  through the MCP tool surface in a single end-to-end
  scenario.
* The v2 contracts are present in the public API and the
  sidecar script mentions the right entry points.
"""

from __future__ import annotations

import json
from pathlib import Path

from ltagent import (
    ai_provider,
    ai_workflow,
    codex_install,
    design_service,
    workbench_v2,
)
from ltagent.mcp_server import _RESOURCE_URIS, _TOOL_NAMES
from ltagent.mcp_workbench_tools import (
    tool_wb_v2_apply_change_set,
    tool_wb_v2_inspect_project,
    tool_wb_v2_propose_ai_design,
)
from ltagent.projects_root import resolve_projects_root

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Release artefacts exist
# ---------------------------------------------------------------------------


def test_changelog_documents_workbench_v2() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Workbench v2 contract" in text
    assert "AIWorkflow" in text
    assert "Codex MCP" in text
    assert "wb_v2_apply_change_set" in text


def test_release_notes_documents_signing_keys() -> None:
    text = (REPO_ROOT / "docs" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "ltagent-alpha" in text
    assert "ltagent-beta" in text
    assert "ltagent-release" in text
    assert "GPG" in text or "gpg" in text
    assert "rotation" in text.lower()


def test_alpha_playbook_documents_smoke_test() -> None:
    text = (REPO_ROOT / "docs" / "ALPHA_PLAYBOOK.md").read_text(encoding="utf-8")
    assert "smoke_codex" in text
    assert "twine" in text
    assert "git tag" in text


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_public_api_exposes_v2_modules() -> None:
    for module in (ai_provider, ai_workflow, design_service, workbench_v2):
        assert module.__file__ is not None
    assert hasattr(workbench_v2, "HardwareProject")
    assert hasattr(design_service, "DesignService")
    assert hasattr(ai_workflow, "AIWorkflow")
    assert hasattr(ai_provider, "ProviderRegistry")


def test_projects_root_resolves_to_absolute() -> None:
    root = resolve_projects_root(None)
    assert root.is_absolute()


# ---------------------------------------------------------------------------
# End-to-end: codex install + workbench v2 + AI propose + accept
# ---------------------------------------------------------------------------


def test_end_to_end_codex_workbench_ai_round_trip(tmp_path: Path) -> None:
    # 1. Codex install.
    codex_config = tmp_path / "codex.toml"
    install_result = codex_install.codex_install(config_path=codex_config)
    assert install_result.created is True
    assert codex_config.is_file()

    # 2. Seed a v2 project.
    project_id = "e2e_demo"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    project_dir.mkdir(parents=True)
    (project_dir / "design" / "analog").mkdir(parents=True)
    (project_dir / "design" / "schematic").mkdir(parents=True)
    (project_dir / "design" / "digital").mkdir(parents=True)
    (project_dir / ".workbench" / "history").mkdir(parents=True)
    from ltagent.workbench_v2 import (
        FILE_ANALOG_GRAPH,
        FILE_DIGITAL,
        FILE_MANIFEST,
        FILE_REQUIREMENTS,
        FILE_SCHEMATIC_VIEW,
        FILE_SYSTEM,
        PROJECT_SCHEMA_VERSION,
        SchematicView,
        SystemSpec,
    )

    (project_dir / FILE_MANIFEST).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "projectId": project_id,
                "displayName": project_id,
                "revision": 0,
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_REQUIREMENTS).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "text": "",
                "constraints": {},
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_ANALOG_GRAPH).write_text(
        json.dumps(
            {
                "schemaVersion": "0.2",
                "projectId": project_id,
                "topology": "",
                "components": {},
                "nets": {"0": {"name": "0", "type": "ground"}},
                "analyses": [],
                "measurements": [],
                "directives": [],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_SCHEMATIC_VIEW).write_text(
        SchematicView().model_dump_json(), encoding="utf-8"
    )
    (project_dir / FILE_DIGITAL).write_text(
        json.dumps(
            {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "design": {},
                "notes": "",
            }
        ),
        encoding="utf-8",
    )
    (project_dir / FILE_SYSTEM).write_text(SystemSpec().model_dump_json(), encoding="utf-8")

    # 3. Inspect (no mutation).
    inspect = tool_wb_v2_inspect_project(project_id, projects_root=str(projects_root))
    assert inspect["success"] is True
    assert inspect["data"]["documents"]["manifest"]["revision"] == 0

    # 4. AI workflow returns a rejected proposal for an unsupported
    #    prompt (no provider configured, deterministic rejection).
    propose = tool_wb_v2_propose_ai_design(
        project_id,
        "Make me a coffee.",
        projects_root=str(projects_root),
    )
    assert propose["success"] is True
    assert propose["data"]["decision"] == "rejected"

    # 5. Apply a typed change set through the MCP surface.
    applied = tool_wb_v2_apply_change_set(
        project_id,
        {
            "schemaVersion": "2.0",
            "baseRevision": 0,
            "actor": "e2e",
            "clientRequestId": "e2e_1",
            "operations": [
                {
                    "document": "analog",
                    "type": "add_component",
                    "componentId": "R1",
                    "kind": "resistor",
                    "value": "1k",
                    "pins": {"p1": "vin", "p2": "vout"},
                }
            ],
        },
        projects_root=str(projects_root),
    )
    assert applied["success"] is True, applied
    assert applied["data"]["revision"] == 1

    # 6. Final inspect shows the new component + new revision.
    final = tool_wb_v2_inspect_project(project_id, projects_root=str(projects_root))
    assert final["success"] is True
    assert final["data"]["documents"]["manifest"]["revision"] == 1
    assert "R1" in final["data"]["documents"]["analog"]["components"]

    # 7. Codex uninstall cleans the entry.
    uninstall = codex_install.codex_uninstall(config_path=codex_config)
    assert uninstall["removed"] is True
    assert not codex_config.exists()


# ---------------------------------------------------------------------------
# MCP server registry remains at the Phase 9 counts.
# ---------------------------------------------------------------------------


def test_mcp_server_counts_match_phase_9() -> None:
    assert len(_TOOL_NAMES) == 27
    assert len(_RESOURCE_URIS) == 16
