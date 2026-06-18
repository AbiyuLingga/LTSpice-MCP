"""Phase 12 MCP tests.

Covers the 6 new digital tools, the 6 new digital resources, and
CLI parity. The existing ``test_mcp_server.py`` already covers
the dangerous-tools contract; we re-assert it here for the new
tools.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ltagent.mcp_server import (
    _RESOURCE_URIS,
    _TOOL_NAMES,
)


def _run(coro):
    return asyncio.run(coro)


def _make_server():
    """Build the FastMCP server. Skips the test if SDK is missing."""
    pytest.importorskip("mcp.server.fastmcp")
    from ltagent.mcp_server import _build_server

    return _build_server()


def test_digital_tools_present_in_listing() -> None:
    server = _make_server()
    names = {t.name for t in _run(server.list_tools())}
    expected = {
        "plan_digital_system",
        "create_digital_project",
        "assemble_tiny8_program",
        "simulate_hdl_project",
        "synth_check_hdl_project",
        "inspect_digital_project",
    }
    assert expected.issubset(names)


def test_digital_resources_present() -> None:
    server = _make_server()
    static_uris = {str(r.uri) for r in _run(server.list_resources())}
    templated = {str(t.uriTemplate) for t in _run(server.list_resource_templates())}
    all_uris = static_uris | templated
    expected = {
        "ltagent://digital/capabilities",
        "ltagent://digital/tiny8/spec",
        "ltagent://digital/templates",
        "ltagent://projects/{project_id}/digital-manifest",
        "ltagent://projects/{project_id}/rtl",
        "ltagent://projects/{project_id}/verification-report",
    }
    assert expected.issubset(all_uris)


def test_tool_count_16() -> None:
    assert len(_TOOL_NAMES) == 16


def test_resource_count_14() -> None:
    assert len(_RESOURCE_URIS) == 14


def test_digital_tools_no_dangerous_names() -> None:
    forbidden = {
        "run_shell",
        "execute_python",
        "eval",
        "exec",
        "read_file",
        "write_file",
    }
    server = _make_server()
    names = {t.name for t in _run(server.list_tools())}
    leaked = names & forbidden
    assert not leaked, f"dangerous tools exposed: {leaked}"


def test_tool_plan_digital_system_happy_path() -> None:
    from ltagent.mcp_server import tool_plan_digital_system

    res = tool_plan_digital_system("create tiny 8-bit CPU add 20 22 halt")
    assert res["success"] is True
    assert res["data"]["kind"] == "tiny8_cpu"
    assert res["data"]["design"]["verification"]["expected"]["acc"] == 42


def test_tool_plan_digital_system_refusal() -> None:
    from ltagent.mcp_server import tool_plan_digital_system

    res = tool_plan_digital_system("rm -rf the CPU")
    assert res["success"] is False
    assert res["errors"][0]["code"] == "PROMPT_INJECTION"


def test_tool_plan_digital_system_roadmap() -> None:
    from ltagent.mcp_server import tool_plan_digital_system

    res = tool_plan_digital_system("buat RISC-V processor")
    assert res["success"] is True
    assert res["data"]["roadmap"] is True


def test_tool_plan_digital_system_clarification() -> None:
    from ltagent.mcp_server import tool_plan_digital_system

    res = tool_plan_digital_system("buat mini processor 8-bit sederhana")
    # Clarification requests are surfaced as success with a
    # warningCode; the caller can switch on needsClarification.
    assert res["success"] is True
    assert res["data"]["needsClarification"] is True


def test_tool_assemble_tiny8_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ltagent.mcp_server import tool_assemble_tiny8_program

    monkeypatch.chdir(tmp_path)
    src = tmp_path / "demo.asm"
    src.write_text("LDI 1\nHALT\n", encoding="utf-8")
    out = tmp_path / "demo.mem"
    res = tool_assemble_tiny8_program(str(src), out=str(out))
    assert res["success"] is True
    assert res["data"]["instructionCount"] == 2
    assert out.exists()


def test_tool_create_digital_project_from_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ltagent.mcp_server import tool_create_digital_project

    monkeypatch.chdir(tmp_path)
    out = tmp_path / "myproj"
    res = tool_create_digital_project(
        "create tiny 8-bit CPU add 20 22 halt", out=str(out)
    )
    assert res["success"] is True
    assert out.is_dir()


def test_tool_inspect_digital_project(tmp_path: Path) -> None:
    from ltagent.mcp_server import tool_create_digital_project, tool_inspect_digital_project

    out = tmp_path / "p"
    res = tool_create_digital_project(
        "create tiny 8-bit CPU add 20 22 halt", out=str(out)
    )
    assert res["success"] is True
    insp = tool_inspect_digital_project(str(out))
    assert insp["success"] is True
    assert insp["data"]["manifest"]["designKind"] == "tiny8_cpu"


def test_tool_simulate_hdl_project_missing_icarus(
    tmp_path: Path,
) -> None:
    from ltagent.mcp_server import tool_simulate_hdl_project

    # Create a real project, then simulate. If Icarus is missing
    # the runner returns a structured skip; we just assert the
    # tool returns a well-formed payload.
    proj = tmp_path / "p"
    from ltagent.mcp_server import tool_create_digital_project

    cr = tool_create_digital_project(
        "create tiny 8-bit CPU add 20 22 halt", out=str(proj)
    )
    assert cr["success"] is True
    res = tool_simulate_hdl_project(str(proj))
    # success may be True (skipped) or False (fail) depending on
    # toolchain; the contract shape must be stable either way.
    assert "command" in res
    assert "data" in res


def _read(server, uri):
    """read_resource returns a list of ReadResourceContents; the
    first item has the body in ``.content``."""
    res = asyncio.run(server.read_resource(uri))
    if not res:
        return ""
    item = res[0]
    content = getattr(item, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", "replace")
    return content or ""


def test_resource_digital_capabilities() -> None:
    """ltagent://digital/capabilities returns valid JSON with the
    v1 surface listed."""
    server = _make_server()
    text = _read(server, "ltagent://digital/capabilities")
    payload = json.loads(text)
    assert payload["supportedKinds"] == ["tiny8_cpu", "tiny8_soc"]
    assert "plan_digital_system" in payload["tools"]


def test_resource_tiny8_spec() -> None:
    server = _make_server()
    text = _read(server, "ltagent://digital/tiny8/spec")
    payload = json.loads(text)
    assert payload["isa"] == "tiny8_v0"
    assert "0xF" in payload["opcodes"]


def test_resource_digital_templates() -> None:
    server = _make_server()
    text = _read(server, "ltagent://digital/templates")
    payload = json.loads(text)
    assert "rtl/tiny8_cpu.v" in payload["rtlFiles"]
    assert "tb/tb_tiny8_top.v" in payload["testbenches"]


def test_resource_digital_manifest_for_real_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: create a project, read the manifest resource."""
    from ltagent.mcp_server import tool_create_digital_project

    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "p"
    cr = tool_create_digital_project(
        "create tiny 8-bit CPU add 20 22 halt", out=str(proj)
    )
    assert cr["success"] is True
    project_id = cr["data"]["projectId"]

    server = _make_server()
    text = _read(server, f"ltagent://projects/{project_id}/digital-manifest")
    payload = json.loads(text)
    assert payload["designKind"] == "tiny8_cpu"


def test_resource_digital_manifest_rejects_traversal() -> None:
    server = _make_server()
    with pytest.raises((ValueError, Exception)):
        asyncio.run(
            server.read_resource(
                "ltagent://projects/..%2Fescape/digital-manifest"
            )
        )


def test_every_digital_tool_has_input_schema() -> None:
    server = _make_server()
    tools = asyncio.run(server.list_tools())
    digital_tools = [
        t for t in tools if (t.name.startswith(("plan_", "create_", "assemble_", "simulate_", "synth_", "inspect_")) and "digital" in (t.description or "").lower()) or t.name in {
            "plan_digital_system",
            "create_digital_project",
            "assemble_tiny8_program",
            "simulate_hdl_project",
            "synth_check_hdl_project",
            "inspect_digital_project",
        }
    ]
    for t in digital_tools:
        assert t.inputSchema is not None
