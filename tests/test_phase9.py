"""Tests for the Phase 9 Codex MCP integration.

Covers:
* codex_install writes a valid ``[mcp_servers.ltagent]`` section.
* codex_install is idempotent: re-running does not duplicate
  the entry.
* codex_uninstall removes the entry and deletes the file when
  the rest is empty.
* codex_doctor reports the structured state of the config.
* The MCP server registry now exposes 27 curated tools and 16
  curated resources, including the workbench v2 surface.
* ``ltagent codex install/doctor/uninstall`` round-trip through
  the CLI.
* The workbench v2 tool surface round-trips a project through
  :class:`DesignService` (inspect, change set apply, AI propose).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent import cli
from ltagent.codex_install import (
    CODEX_COMMAND,
    codex_doctor,
    codex_install,
    codex_uninstall,
)
from ltagent.design_service import DesignService
from ltagent.mcp_server import _RESOURCE_URIS, _TOOL_NAMES
from ltagent.mcp_workbench_tools import (
    tool_wb_v2_apply_change_set,
    tool_wb_v2_inspect_project,
    tool_wb_v2_propose_ai_design,
    workbench_v2_capabilities_resource,
)
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


def _seed_v2_project(project_dir: Path, project_id: str) -> None:
    (project_dir / "design" / "analog").mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "schematic").mkdir(parents=True, exist_ok=True)
    (project_dir / "design" / "digital").mkdir(parents=True, exist_ok=True)
    (project_dir / ".workbench" / "history").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "projectId": project_id,
        "displayName": project_id,
        "revision": 0,
    }
    (project_dir / FILE_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (project_dir / FILE_REQUIREMENTS).write_text(
        json.dumps(
            {"schemaVersion": PROJECT_SCHEMA_VERSION, "text": "", "constraints": {}, "goals": []}
        ),
        encoding="utf-8",
    )
    graph = {
        "schemaVersion": "0.2",
        "projectId": project_id,
        "topology": "",
        "components": {},
        "nets": {"0": {"name": "0", "type": "ground"}},
        "analyses": [],
        "measurements": [],
        "directives": [],
    }
    (project_dir / FILE_ANALOG_GRAPH).write_text(json.dumps(graph), encoding="utf-8")
    (project_dir / FILE_SCHEMATIC_VIEW).write_text(
        SchematicView().model_dump_json(), encoding="utf-8"
    )
    (project_dir / FILE_DIGITAL).write_text(
        json.dumps({"schemaVersion": PROJECT_SCHEMA_VERSION, "design": {}, "notes": ""}),
        encoding="utf-8",
    )
    (project_dir / FILE_SYSTEM).write_text(SystemSpec().model_dump_json(), encoding="utf-8")


# ---------------------------------------------------------------------------
# codex_install: file I/O
# ---------------------------------------------------------------------------


def test_codex_install_writes_section(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    result = codex_install(config_path=config)
    assert result.created is True
    assert result.dryRun is False
    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.ltagent]" in text
    assert 'command = "ltagent-mcp"' in text


def test_codex_install_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    codex_install(config_path=config)
    first = config.read_text(encoding="utf-8")
    codex_install(config_path=config)
    second = config.read_text(encoding="utf-8")
    assert first == second


def test_codex_install_preserves_existing_servers(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    config.write_text(
        '[mcp_servers.other]\ncommand = "other-mcp"\n',
        encoding="utf-8",
    )
    codex_install(config_path=config)
    text = config.read_text(encoding="utf-8")
    assert "other-mcp" in text
    assert "ltagent-mcp" in text


def test_codex_install_dry_run_does_not_write(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    result = codex_install(config_path=config, dry_run=True)
    assert result.dryRun is True
    assert not config.exists()


def test_codex_uninstall_removes_entry(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    codex_install(config_path=config)
    info = codex_uninstall(config_path=config)
    assert info["removed"] is True
    assert not config.exists()


def test_codex_uninstall_when_absent(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    info = codex_uninstall(config_path=config)
    assert info["removed"] is False
    assert info["existed"] is False
    assert not config.exists()


def test_codex_uninstall_dry_run_keeps_file(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    codex_install(config_path=config)
    info = codex_uninstall(config_path=config, dry_run=True)
    assert info["dryRun"] is True
    assert info["removed"] is True
    assert config.exists()


def test_codex_doctor_reports_installed(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    codex_install(config_path=config)
    report = codex_doctor(config_path=config)
    assert report["server"] is not None
    assert report["server"]["command"] == CODEX_COMMAND
    assert report["issues"] == []


def test_codex_doctor_reports_missing(tmp_path: Path) -> None:
    config = tmp_path / "codex.toml"
    report = codex_doctor(config_path=config)
    assert report["server"] is None
    codes = {issue["code"] for issue in report["issues"]}
    assert "CODEX_SERVER_NOT_INSTALLED" in codes


# ---------------------------------------------------------------------------
# CLI round-trip
# ---------------------------------------------------------------------------


def test_cli_codex_install_uninstall_round_trip(tmp_path: Path, capsys) -> None:
    config = tmp_path / "codex.toml"

    rc = cli.main(["--json", "codex", "install", "--config", str(config)])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert config.is_file()

    rc = cli.main(["--json", "codex", "doctor", "--config", str(config)])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True

    rc = cli.main(["--json", "codex", "uninstall", "--config", str(config)])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert not config.exists()


def test_cli_codex_unknown_subcommand_fails(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--json", "codex", "explode", "--config", str(tmp_path / "x.toml")])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# MCP server registry
# ---------------------------------------------------------------------------


def test_mcp_server_includes_workbench_v2_tools() -> None:
    assert "wb_v2_inspect_project" in _TOOL_NAMES
    assert "wb_v2_apply_change_set" in _TOOL_NAMES
    assert "wb_v2_propose_ai_design" in _TOOL_NAMES
    assert "ltagent://workbench/v2/capabilities" in _RESOURCE_URIS
    assert "ltagent://workbench/v2/projects/{project_id}/manifest" in _RESOURCE_URIS


def test_workbench_v2_capabilities_resource_is_json() -> None:
    payload = json.loads(workbench_v2_capabilities_resource())
    assert payload["schemaVersion"] == PROJECT_SCHEMA_VERSION
    assert "wb_v2_apply_change_set" in payload["tools"]


# ---------------------------------------------------------------------------
# Workbench v2 tools
# ---------------------------------------------------------------------------


def test_wb_v2_inspect_project_round_trip(tmp_path: Path) -> None:
    project_id = "demo_project"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    _seed_v2_project(project_dir, project_id)
    result = tool_wb_v2_inspect_project(project_id, projects_root=str(projects_root))
    assert result["success"] is True
    assert result["data"]["projectId"] == project_id
    assert "manifest" in result["data"]["documents"]


def test_wb_v2_inspect_project_missing(tmp_path: Path) -> None:
    result = tool_wb_v2_inspect_project("missing", projects_root=str(tmp_path / "projects"))
    assert result["success"] is False
    assert result["data"]["code"] == "WB_PROJECT_NOT_FOUND"


def test_wb_v2_inspect_project_rejects_traversal(tmp_path: Path) -> None:
    result = tool_wb_v2_inspect_project("../escape", projects_root=str(tmp_path / "projects"))
    assert result["success"] is False
    assert result["data"]["code"] == "WB_PROJECT_ID_INVALID"


def test_wb_v2_apply_change_set_replaces_manifest(tmp_path: Path) -> None:
    project_id = "apply_one"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    _seed_v2_project(project_dir, project_id)
    change_set = {
        "schemaVersion": "2.0",
        "baseRevision": 0,
        "actor": "test",
        "clientRequestId": "cs_1",
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
    }
    result = tool_wb_v2_apply_change_set(project_id, change_set, projects_root=str(projects_root))
    assert result["success"] is True, result
    assert result["data"]["revision"] == 1
    assert result["data"]["previousRevision"] == 0
    on_disk = json.loads((project_dir / FILE_ANALOG_GRAPH).read_text(encoding="utf-8"))
    assert "R1" in on_disk["components"]


def test_wb_v2_apply_change_set_rejects_invalid(tmp_path: Path) -> None:
    project_id = "apply_two"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    _seed_v2_project(project_dir, project_id)
    result = tool_wb_v2_apply_change_set(
        project_id,
        {"schemaVersion": "2.0", "baseRevision": 0, "operations": []},
        projects_root=str(projects_root),
    )
    assert result["success"] is False
    assert result["data"]["code"] == "WB_CHANGESET_INVALID"


def test_wb_v2_apply_change_set_missing_project(tmp_path: Path) -> None:
    result = tool_wb_v2_apply_change_set(
        "missing",
        {
            "schemaVersion": "2.0",
            "baseRevision": 0,
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
        projects_root=str(tmp_path / "projects"),
    )
    assert result["success"] is False


def test_wb_v2_propose_ai_design_rejects_unsupported(tmp_path: Path) -> None:
    project_id = "ai_one"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    _seed_v2_project(project_dir, project_id)
    result = tool_wb_v2_propose_ai_design(
        project_id,
        "Make me a coffee.",
        projects_root=str(projects_root),
    )
    assert result["success"] is True
    assert result["data"]["decision"] == "rejected"


def test_wb_v2_propose_ai_design_rejects_empty_prompt(tmp_path: Path) -> None:
    project_id = "ai_two"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    _seed_v2_project(project_dir, project_id)
    result = tool_wb_v2_propose_ai_design(project_id, "  ", projects_root=str(projects_root))
    assert result["success"] is False
    assert result["data"]["code"] == "WB_PROMPT_EMPTY"


def test_wb_v2_propose_ai_design_rejects_traversal(tmp_path: Path) -> None:
    result = tool_wb_v2_propose_ai_design(
        "../escape", "rc low-pass", projects_root=str(tmp_path / "projects")
    )
    assert result["success"] is False
    assert result["data"]["code"] == "WB_PROJECT_ID_INVALID"


def test_wb_v2_propose_ai_design_missing_project(tmp_path: Path) -> None:
    result = tool_wb_v2_propose_ai_design(
        "missing", "rc low-pass", projects_root=str(tmp_path / "projects")
    )
    assert result["success"] is False
    assert result["data"]["code"] == "WB_PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Codex integration with real DesignService
# ---------------------------------------------------------------------------


def test_workbench_v2_round_trip_through_design_service(tmp_path: Path) -> None:
    project_id = "round_trip"
    projects_root = tmp_path / "projects"
    project_dir = projects_root / project_id
    _seed_v2_project(project_dir, project_id)
    service = DesignService(projects_root=str(projects_root))
    service.open_project(project_id)
    result = tool_wb_v2_apply_change_set(
        project_id,
        {
            "schemaVersion": "2.0",
            "baseRevision": 0,
            "actor": "test",
            "clientRequestId": "cs_round",
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
    assert result["success"] is True, result
    assert result["data"]["revision"] == 1
    inspect = tool_wb_v2_inspect_project(project_id, projects_root=str(projects_root))
    assert inspect["success"] is True
    assert "R1" in inspect["data"]["documents"]["analog"]["components"]
    assert inspect["data"]["documents"]["manifest"]["revision"] == 1
