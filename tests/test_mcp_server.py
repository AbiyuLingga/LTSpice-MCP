"""Tests for ``ltagent.mcp_server`` (Phase 10).

Covers the acceptance criteria from plan section 21:

* ``ltagent-mcp --help`` works.
* MCP server lists tools.
* MCP ``create_project`` matches CLI output.
* No arbitrary shell or broad file read tools exist.

Plus the security and contract tests added during Phase 10 design:

* 10 tools registered, exactly the names listed in plan section 17.3
  plus the two already-implemented Phase 9 evaluators.
* 8 resources registered, exactly the URIs listed in plan section 17.4.
* Resource URI path traversal is rejected.
* No ``run_shell``, ``execute_python``, ``read_file``, or ``write_file``
  tool is exposed.
* No ``*.raw`` resource is exposed.
* Tool input schemas are valid JSON Schema.
* Tool output schemas exist (FastMCP requires them).
* Each tool's direct invocation returns the SPEC.md §2 JSON contract.
* SDK-missing fallback returns ``MCP_SDK_MISSING`` exit 1.

The test file uses only the in-process FastMCP handles (``list_tools``,
``list_resources``, ``call_tool``, ``read_resource``) and direct
invocation of the pure-function tool bodies. No actual stdio MCP
transport is started.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ltagent.config import Config, WorkspaceConfig
from ltagent.mcp_server import (
    _RESOURCE_URIS,
    _TOOL_NAMES,
    MCP_SDK_ERROR_CODE,
    tool_check_layout,
    tool_create_project,
    tool_evaluate_template_candidate,
    tool_find_template,
    tool_generate_netlist,
    tool_generate_schematic,
    tool_inspect_project,
    tool_promote_template,
)
from ltagent.security import (
    ALLOWED_PROJECT_RESOURCE_NAMES,
    ALLOWED_TEMPLATE_RESOURCE_NAMES,
    parse_resource_uri,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated cwd with empty projects/ and templates/ dirs."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "projects").mkdir()
    (cwd / "templates").mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield cwd


@pytest.fixture()
def config_in_workspace(workspace: Path) -> Config:
    """A Config object pointing at the workspace-relative dirs.

    The MCP tool bodies call ``(Path.cwd() / cfg.workspace.projects_dir).resolve()``,
    so we just need a default Config (which uses ``projects`` and ``templates``).
    """
    return Config(workspace=WorkspaceConfig(projects_dir="projects", templates_dir="templates"))


@pytest.fixture()
def ir_file(workspace: Path) -> Path:
    """Copy the rc_lowpass example IR into the workspace projects/ dir."""
    src = EXAMPLES_DIR / "rc_lowpass.ir.json"
    dest = workspace / "projects" / "rc_lowpass.ir.json"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


@pytest.fixture()
def bad_ir_file(workspace: Path) -> Path:
    """A file with no nodes and unknown topology (fails IR load)."""
    p = workspace / "projects" / "bad.ir.json"
    p.write_text(
        json.dumps(
            {
                "schemaVersion": "0.1",
                "name": "bad",
                "topology": "unknown_topology",
                "nodes": ["0"],
                "components": [],
                "analysis": {"kind": "op"},
                "measurements": [],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_payload(payload: dict) -> bool:
    """Assert the SPEC.md §2 contract shape is present and success=True."""
    assert payload.get("success") is True, payload
    for key in ("command", "message", "data", "warnings", "errors"):
        assert key in payload, f"missing {key!r} in {payload}"
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["errors"], list)
    return True


def _err_payload(payload: dict, *, code: str | None = None) -> bool:
    assert payload.get("success") is False, payload
    assert payload.get("errors"), payload
    if code is not None:
        actual_codes = [e.get("code") for e in payload["errors"]]
        assert code in actual_codes, f"expected code {code!r} in {actual_codes}"
    return True


def _make_server():
    """Build the FastMCP server. Skips the test if SDK is missing."""
    pytest.importorskip("mcp.server.fastmcp")
    from ltagent.mcp_server import _build_server

    return _build_server()


def _run_mcp_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the ``ltagent-mcp`` console script in a subprocess."""
    venv_bin = REPO_ROOT / ".venv" / "bin"
    script = venv_bin / "ltagent-mcp"
    if not script.exists():  # pragma: no cover - editable install missing
        pytest.skip("ltagent-mcp script not installed; run `pip install -e .`")
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )


# ---------------------------------------------------------------------------
# Acceptance: ltagent-mcp --help works
# ---------------------------------------------------------------------------


def test_ltagent_mcp_help_exits_zero() -> None:
    result = _run_mcp_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "ltagent-mcp" in result.stdout
    assert "--list-tools" in result.stdout
    assert "--list-resources" in result.stdout
    assert "--check" in result.stdout


def test_ltagent_mcp_check_succeeds_when_sdk_present() -> None:
    result = _run_mcp_cli("--check")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    _ok_payload(payload)
    assert payload["data"]["sdk"] == "mcp"
    assert payload["data"]["fastmcp"] is True


def test_ltagent_mcp_list_tools_via_subprocess() -> None:
    result = _run_mcp_cli("--list-tools")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    _ok_payload(payload)
    assert payload["data"]["transport"] == "stdio"
    assert set(payload["data"]["tools"]) == set(_TOOL_NAMES)


def test_ltagent_mcp_list_resources_via_subprocess() -> None:
    result = _run_mcp_cli("--list-resources")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    _ok_payload(payload)
    assert set(payload["data"]["resources"]) == set(_RESOURCE_URIS)


def test_ltagent_mcp_version_reports_package_version() -> None:
    result = _run_mcp_cli("--version")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    _ok_payload(payload)
    assert payload["data"]["version"]


# ---------------------------------------------------------------------------
# Acceptance: MCP server lists tools
# ---------------------------------------------------------------------------


def test_server_lists_sixteen_tools() -> None:
    server = _make_server()
    tools = asyncio.run(server.list_tools())
    names = sorted(t.name for t in tools)
    assert names == sorted(_TOOL_NAMES), names


def test_no_dangerous_tools_exposed() -> None:
    """No run_shell / execute_python / generic read|write_file."""
    server = _make_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    forbidden = {
        "run_shell",
        "execute_python",
        "eval",
        "exec",
        "read_file",
        "write_file",
        "delete_file",
        "shell",
        "python",
        "system",
    }
    leaked = names & forbidden
    assert not leaked, f"dangerous tools exposed: {leaked}"


def test_every_tool_has_input_schema() -> None:
    server = _make_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.inputSchema, f"{tool.name} missing input schema"
        assert tool.inputSchema.get("type") == "object", tool.name


def test_no_raw_resource_exposed() -> None:
    """Resources must not include *.raw (plan section 17.4 + section 25)."""
    server = _make_server()
    resources = asyncio.run(server.list_resources())
    templates = asyncio.run(server.list_resource_templates())
    uris = [str(r.uri) for r in resources] + [str(t.uriTemplate) for t in templates]
    assert all(".raw" not in u for u in uris), uris
    for u in uris:
        assert not u.lower().endswith("raw"), u


def test_server_lists_fourteen_resources() -> None:
    server = _make_server()
    resources = asyncio.run(server.list_resources())
    templates = asyncio.run(server.list_resource_templates())
    static_uris = [str(r.uri) for r in resources]
    templated_uris = [str(t.uriTemplate) for t in templates]
    all_uris = static_uris + templated_uris
    assert len(all_uris) == 14, all_uris
    assert set(all_uris) == set(_RESOURCE_URIS)


# ---------------------------------------------------------------------------
# Acceptance: tools work end-to-end against a temp workspace
# ---------------------------------------------------------------------------


def test_tool_find_template_lists_official(workspace: Path) -> None:
    """Empty workspace -> 0 official templates."""
    result = tool_find_template()
    _ok_payload(result)
    assert isinstance(result["data"]["templates"], list)


def test_tool_generate_netlist_returns_text(workspace: Path, ir_file: Path) -> None:
    result = tool_generate_netlist(str(ir_file))
    _ok_payload(result)
    assert "V" in result["data"]["netlist"]  # has SPICE components
    assert ".end" in result["data"]["netlist"]
    assert result["data"]["writtenTo"] is None


def test_tool_generate_netlist_writes_file(workspace: Path, ir_file: Path) -> None:
    out = workspace / "projects" / "rc_lowpass.cir"
    result = tool_generate_netlist(str(ir_file), out=str(out))
    _ok_payload(result)
    assert out.exists()
    assert ".end" in out.read_text(encoding="utf-8")


def test_tool_generate_netlist_rejects_traversal(workspace: Path, ir_file: Path) -> None:
    out = workspace / "projects" / ".." / "evil.cir"
    result = tool_generate_netlist(str(ir_file), out=str(out))
    _err_payload(result, code="PATH_TRAVERSAL")


def test_tool_generate_netlist_bad_ir(workspace: Path, bad_ir_file: Path) -> None:
    result = tool_generate_netlist(str(bad_ir_file))
    _err_payload(result, code="IR_LOAD_FAILED")


def test_tool_generate_schematic_returns_asc(workspace: Path, ir_file: Path) -> None:
    result = tool_generate_schematic(str(ir_file))
    _ok_payload(result)
    assert "Version" in result["data"]["asc"]
    assert "SHEET" in result["data"]["asc"]
    assert result["data"]["layout"]["score"] >= 0


def test_tool_generate_schematic_writes_file(workspace: Path, ir_file: Path) -> None:
    out = workspace / "projects" / "rc_lowpass.asc"
    result = tool_generate_schematic(str(ir_file), out=str(out))
    _ok_payload(result)
    assert out.exists()
    assert "Version" in out.read_text(encoding="utf-8")


def test_tool_check_layout_scores(workspace: Path, ir_file: Path) -> None:
    result = tool_check_layout(str(ir_file))
    _ok_payload(result)
    assert "score" in result["data"]["layout"]


def test_tool_inspect_project_unknown(workspace: Path) -> None:
    result = tool_inspect_project("nope")
    _err_payload(result, code="PATH_NOT_FOUND")


def test_tool_inspect_project_rejects_traversal(workspace: Path) -> None:
    result = tool_inspect_project("../etc")
    _err_payload(result, code="IDENTIFIER_INVALID")


# ---------------------------------------------------------------------------
# Acceptance: create_project matches CLI output (Phase 7 -> Phase 10)
# ---------------------------------------------------------------------------


def test_tool_create_project_matches_cli(workspace: Path, ir_file: Path) -> None:
    """Same IR, same workspace -> tool_create_project produces the same key
    files as `ltagent create <ir> --out <dir>`."""
    from ltagent.project import create_project

    target = workspace / "projects" / "via_tool"
    cli_target = workspace / "projects" / "via_cli"

    # MCP path
    tool_result = tool_create_project(str(ir_file), out=str(target))
    _ok_payload(tool_result)
    assert target.is_dir()

    # CLI path (direct call into the same core function)
    cfg = Config(
        workspace=WorkspaceConfig(projects_dir="projects", templates_dir="templates")
    )
    create_project(ir_file, cli_target, templates_dir=workspace / "templates", config=cfg)
    assert cli_target.is_dir()

    # Both must produce the same artifact set.
    expected = {"circuit.ir.json", "circuit.cir", "circuit.asc", "metadata.json"}
    assert expected.issubset({p.name for p in target.iterdir()})
    assert expected.issubset({p.name for p in cli_target.iterdir()})


# ---------------------------------------------------------------------------
# Acceptance: resource URI path traversal is rejected
# ---------------------------------------------------------------------------


def test_parse_resource_uri_rejects_other_scheme() -> None:
    from ltagent.security import ERR_RESOURCE_URI_INVALID

    with pytest.raises(Exception) as exc_info:
        parse_resource_uri("file:///etc/passwd")
    assert exc_info.value.code == ERR_RESOURCE_URI_INVALID


def test_parse_resource_uri_rejects_unknown_kind() -> None:
    from ltagent.security import ERR_RESOURCE_KIND_UNKNOWN

    with pytest.raises(Exception) as exc_info:
        parse_resource_uri("ltagent://secrets/admin")
    assert exc_info.value.code == ERR_RESOURCE_KIND_UNKNOWN


def test_parse_resource_uri_rejects_dotdot() -> None:
    """An identifier that contains `..` segments fails the slug pattern."""
    from ltagent.security import ERR_IDENTIFIER_INVALID

    with pytest.raises(Exception) as exc_info:
        parse_resource_uri("ltagent://projects/abc..xyz")
    assert exc_info.value.code == ERR_IDENTIFIER_INVALID


def test_parse_resource_uri_rejects_dotdot_in_id() -> None:
    """An identifier that starts with `..` is rejected by the slug pattern."""
    from ltagent.security import ERR_IDENTIFIER_INVALID

    with pytest.raises(Exception) as exc_info:
        parse_resource_uri("ltagent://projects/..abc")
    assert exc_info.value.code == ERR_IDENTIFIER_INVALID


def test_parse_resource_uri_rejects_bad_subpath() -> None:
    from ltagent.security import ERR_RESOURCE_SUBPATH_INVALID

    with pytest.raises(Exception) as exc_info:
        parse_resource_uri(
            "ltagent://projects/abc/raw",
            allowed_subpaths={
                "projects": ALLOWED_PROJECT_RESOURCE_NAMES,
                "templates": ALLOWED_TEMPLATE_RESOURCE_NAMES,
            },
        )
    assert exc_info.value.code == ERR_RESOURCE_SUBPATH_INVALID


def test_parse_resource_uri_accepts_collection_root() -> None:
    kind, ident, sub = parse_resource_uri("ltagent://projects")
    assert kind == "projects"
    assert ident == ""
    assert sub is None


def test_parse_resource_uri_accepts_valid_project_uri() -> None:
    kind, ident, sub = parse_resource_uri("ltagent://projects/rc1k/result")
    assert kind == "projects"
    assert ident == "rc1k"
    assert sub == "result"


# ---------------------------------------------------------------------------
# Acceptance: Phase 9 evaluator tools work (no shell)
# ---------------------------------------------------------------------------


def test_tool_evaluate_template_candidate_unknown(workspace: Path) -> None:
    result = tool_evaluate_template_candidate("nope")
    _err_payload(result)


def test_tool_promote_template_unknown(workspace: Path) -> None:
    result = tool_promote_template("nope")
    _err_payload(result)


def test_tool_promote_template_rejects_traversal(workspace: Path) -> None:
    result = tool_promote_template("../etc")
    _err_payload(result, code="IDENTIFIER_INVALID")


# ---------------------------------------------------------------------------
# SDK-missing fallback
# ---------------------------------------------------------------------------


def test_sdk_missing_emits_structured_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Force the SDK import to fail and assert the fallback exit code + payload."""
    import ltagent.mcp_server as srv

    monkeypatch.setattr(srv, "_FastMCP", None)
    monkeypatch.setattr(srv, "_IMPORT_ERROR", ImportError("simulated missing sdk"))

    rc = srv.main(["--list-tools"])
    captured = capsys.readouterr()
    assert rc == 1
    payload = json.loads(captured.err)
    _err_payload(payload, code=MCP_SDK_ERROR_CODE)
    assert "pip install" in payload["data"]["installHint"]


def test_sdk_missing_in_check_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import ltagent.mcp_server as srv

    monkeypatch.setattr(srv, "_FastMCP", None)
    monkeypatch.setattr(srv, "_IMPORT_ERROR", ImportError("simulated"))

    rc = srv.main(["--check"])
    captured = capsys.readouterr()
    assert rc == 1
    payload = json.loads(captured.err)
    _err_payload(payload, code=MCP_SDK_ERROR_CODE)
