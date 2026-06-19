"""Path-traversal tests for ``ltagent.security`` and MCP wrappers.

Plan section 22 mandates that every path-bearing input be resolved
and rejected if it would escape the configured root. The Phase 10
MCP server reuses ``ltagent.security`` so a path the CLI refuses is
also refused by an MCP tool call.

These tests pin:

* ``safe_resolve_under`` rejects ``..`` escapes, absolute escapes,
  and symlink-based escapes.
* ``parse_resource_uri`` rejects ``..`` and ``.`` segments.
* MCP tool wrappers return structured ``PATH_TRAVERSAL`` payloads
  rather than raising.
* ``assert_no_raw_path`` blocks every ``*.raw`` file.
* ``tool_create_project`` has no public workspace escape hatch.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from ltagent import mcp_server
from ltagent.security import (
    ALLOWED_PROJECT_RESOURCE_NAMES,
    ALLOWED_TEMPLATE_RESOURCE_NAMES,
    ERR_PATH_OUTSIDE_ROOT,
    ERR_PATH_TRAVERSAL,
    ERR_RESOURCE_KIND_UNKNOWN,
    ERR_RESOURCE_RAW_BLOCKED,
    ERR_RESOURCE_SUBPATH_INVALID,
    ERR_RESOURCE_URI_INVALID,
    PathSafetyError,
    assert_no_raw_path,
    is_within,
    parse_resource_uri,
    safe_resolve_under,
)


def test_safe_resolve_rejects_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    target = root / ".." / "escape"
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under(target, root)
    assert exc.value.code == ERR_PATH_TRAVERSAL


def test_safe_resolve_rejects_absolute_escape(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under("/etc/passwd", root)
    assert exc.value.code == ERR_PATH_TRAVERSAL


def test_safe_resolve_rejects_double_dot_segments(tmp_path: Path) -> None:
    """``a/b/../../etc`` collapses to ``etc`` and must still be rejected."""
    root = tmp_path / "projects"
    root.mkdir()
    target = root / "rc_lowpass_1khz" / ".." / ".." / "etc"
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under(target, root)
    assert exc.value.code == ERR_PATH_TRAVERSAL


def test_safe_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside the project root pointing outside must be
    rejected after resolution, not before.
    """
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link_dir = root / "linkdir"
    link_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under(link_dir, root)
    assert exc.value.code == ERR_PATH_TRAVERSAL


def test_safe_resolve_accepts_normal_inside(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    sub = root / "rc_lowpass_1khz"
    sub.mkdir()
    file = sub / "circuit.ir.json"
    file.write_text("{}", encoding="utf-8")
    resolved = safe_resolve_under(file, root)
    assert resolved == file.resolve()


def test_safe_resolve_must_exist(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    target = root / "nope"
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under(target, root, must_exist=True)
    assert exc.value.code != ERR_PATH_TRAVERSAL


def test_safe_resolve_rejects_non_string_root() -> None:
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under("foo", 12345)  # type: ignore[arg-type]
    assert exc.value.code == ERR_PATH_OUTSIDE_ROOT


def test_safe_resolve_rejects_non_string_candidate() -> None:
    with pytest.raises(PathSafetyError) as exc:
        safe_resolve_under(12345, "/tmp")  # type: ignore[arg-type]
    assert exc.value.code == ERR_PATH_OUTSIDE_ROOT


def test_path_safety_error_has_stable_code(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    try:
        safe_resolve_under(root / ".." / "escape", root)
    except PathSafetyError as exc:
        assert exc.code == ERR_PATH_TRAVERSAL
        assert "candidate" in exc.data
        assert "root" in exc.data


def test_is_within_helper(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    inside = root / "x"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert is_within(inside, root) is True
    assert is_within(outside, root) is False
    assert is_within(root / ".." / "outside", root) is False


def test_resource_uri_accepts_collection_root() -> None:
    kind, ident, sub = parse_resource_uri("ltagent://projects")
    assert kind == "projects"
    assert ident == ""
    assert sub is None


def test_resource_uri_accepts_single_item() -> None:
    kind, ident, sub = parse_resource_uri("ltagent://projects/rc_lowpass_1khz")
    assert kind == "projects"
    assert ident == "rc_lowpass_1khz"
    assert sub is None


def test_resource_uri_accepts_item_subpath() -> None:
    kind, ident, sub = parse_resource_uri(
        "ltagent://projects/rc_lowpass_1khz/result"
    )
    assert kind == "projects"
    assert ident == "rc_lowpass_1khz"
    assert sub == "result"


def test_resource_uri_rejects_traversal() -> None:
    with pytest.raises(Exception) as exc:
        parse_resource_uri("ltagent://projects/..")
    assert exc.value.code in (
        ERR_RESOURCE_URI_INVALID,
        "IDENTIFIER_INVALID",
    )


def test_resource_uri_rejects_dot_segment() -> None:
    with pytest.raises(Exception) as exc:
        parse_resource_uri("ltagent://projects/.")
    assert exc.value.code in (
        ERR_RESOURCE_URI_INVALID,
        "IDENTIFIER_INVALID",
    )


def test_resource_uri_rejects_unknown_kind() -> None:
    with pytest.raises(Exception) as exc:
        parse_resource_uri("ltagent://secrets/foo")
    assert exc.value.code == ERR_RESOURCE_KIND_UNKNOWN


def test_resource_uri_rejects_unknown_subpath_when_allowed_set_given() -> None:
    with pytest.raises(Exception) as exc:
        parse_resource_uri(
            "ltagent://projects/rc_lowpass_1khz/.raw",
            allowed_subpaths={"projects": ALLOWED_PROJECT_RESOURCE_NAMES},
        )
    assert exc.value.code == ERR_RESOURCE_SUBPATH_INVALID


def test_resource_uri_allowed_subpath_set_is_pinned() -> None:
    assert frozenset(
        {"metadata", "result", "circuit-ir", "netlist", "log"}
    ) == ALLOWED_PROJECT_RESOURCE_NAMES
    assert frozenset({"metadata"}) == ALLOWED_TEMPLATE_RESOURCE_NAMES


@pytest.mark.parametrize(
    "name",
    [
        "circuit.raw",
        "circuit.RAW",
        "circuit.Raw",
        "/tmp/circuit.raw",
    ],
)
def test_assert_no_raw_path_blocks(name: str) -> None:
    p = Path(name)
    with pytest.raises(PathSafetyError) as exc:
        assert_no_raw_path(p)
    assert exc.value.code == ERR_RESOURCE_RAW_BLOCKED


def test_assert_no_raw_path_passes_through_safe() -> None:
    p = Path("/tmp/circuit.log")
    assert assert_no_raw_path(p) == p
    p = Path("/tmp/circuit.cir")
    assert assert_no_raw_path(p) == p


@pytest.fixture()
def mcp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "projects").mkdir()
    (cwd / "templates").mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield cwd


def _write_ir(target_dir: Path, name: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    p = target_dir / f"{name}.ir.json"
    p.write_text(
        json.dumps(
            {
                "schemaVersion": "0.1",
                "name": name,
                "topology": "rc_lowpass",
                "nodes": ["in", "out", "0"],
                "components": [
                    {
                        "id": "Vin",
                        "kind": "voltage_source",
                        "spicePrefix": "V",
                        "nodes": ["in", "0"],
                        "value": "SINE(0 1 1k)",
                    },
                    {
                        "id": "R1",
                        "kind": "resistor",
                        "spicePrefix": "R",
                        "nodes": ["in", "out"],
                        "value": "1.59k",
                    },
                    {
                        "id": "C1",
                        "kind": "capacitor",
                        "spicePrefix": "C",
                        "nodes": ["out", "0"],
                        "value": "100n",
                    },
                ],
                "analysis": [{"kind": "tran", "stopTime": "5m"}],
            }
        ),
        encoding="utf-8",
    )
    return p


def test_mcp_inspect_rejects_traversal_id(mcp_workspace: Path) -> None:
    result = mcp_server.tool_inspect_project(
        project_id="../../etc/passwd",
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert result.get("command") == "inspect_project"
    errors = result.get("errors") or []
    codes = {e.get("code") for e in errors}
    assert codes & {
        ERR_PATH_TRAVERSAL,
        "IDENTIFIER_INVALID",
        "PROJECT_NOT_FOUND",
    }, result


def test_mcp_inspect_rejects_missing_project(mcp_workspace: Path) -> None:
    result = mcp_server.tool_inspect_project(project_id="nope")
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert result.get("command") == "inspect_project"
    errors = result.get("errors") or []
    codes = {e.get("code") for e in errors}
    assert codes & {"PATH_NOT_FOUND", "PROJECT_NOT_FOUND"}


def test_mcp_create_rejects_escape(mcp_workspace: Path) -> None:
    ir = _write_ir(mcp_workspace, "rc_lowpass_1khz")
    outside = mcp_workspace / ".." / "leak"
    result = mcp_server.tool_create_project(
        ir_source=str(ir),
        out=str(outside),
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    errors = result.get("errors") or []
    codes = {e.get("code") for e in errors}
    assert ERR_PATH_TRAVERSAL in codes, result


def test_mcp_create_cannot_escape_workspace(mcp_workspace: Path) -> None:
    ir = _write_ir(mcp_workspace, "rc_lowpass_1khz")
    outside = mcp_workspace / ".." / "leak_target"
    outside.mkdir(parents=True, exist_ok=True)

    result = mcp_server.tool_create_project(
        ir_source=str(ir),
        out=str(outside),
    )
    assert isinstance(result, dict)
    errors = result.get("errors") or []
    codes = {e.get("code") for e in errors}
    assert ERR_PATH_TRAVERSAL in codes, result
    assert result.get("command") == "create_project"
