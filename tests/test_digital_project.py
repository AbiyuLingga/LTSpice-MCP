"""Tests for ``ltagent.digital_project`` (Phase 12, project orchestrator).

Covers the workspace-level glue: project id, project dir resolution,
path safety, full create flow.
"""

from __future__ import annotations

from pathlib import Path

from ltagent.digital_ir import (
    CpuSpec,
    DesignIR,
    IoSpec,
    MemorySpec,
    Metadata,
    ProgramSpec,
    VerificationSpec,
)
from ltagent.digital_project import (
    ProjectRequest,
    create_project,
    resolve_project_dir,
)


def _ir() -> DesignIR:
    return DesignIR(
        schemaVersion="0.1",
        domain="digital",
        kind="tiny8_cpu",
        name="tiny8_test",
        description="",
        cpu=CpuSpec(),
        memory=MemorySpec(),
        io=IoSpec(ports=[]),
        program=ProgramSpec(source="demo.asm", entry=0, expectedHaltCyclesMax=200),
        verification=VerificationSpec(),
        metadata=Metadata(),
    )


def test_resolve_project_dir_uses_date_prefix(tmp_path: Path) -> None:
    pid, pdir = resolve_project_dir(
        name="tiny8_x", projects_root=tmp_path, today="2026-06-19"
    )
    assert pid == "2026-06-19_tiny8_x"
    assert pdir == tmp_path / "2026-06-19_tiny8_x"


def test_resolve_project_dir_default_today(tmp_path: Path) -> None:
    pid, pdir = resolve_project_dir(name="tiny8_x", projects_root=tmp_path)
    # Just check the format
    assert "_tiny8_x" in pid
    assert pdir.parent == tmp_path


def test_create_project_writes_files(tmp_path: Path) -> None:
    result = create_project(ProjectRequest(ir=_ir(), projects_root=tmp_path))
    assert result.project_id.endswith("_tiny8_test")
    assert result.project_dir.is_dir()
    # Manifest exists
    assert (result.project_dir / "manifest.json").exists()
    # All 12 files
    assert len(result.project.files) == 12


def test_create_project_name_override(tmp_path: Path) -> None:
    result = create_project(
        ProjectRequest(ir=_ir(), projects_root=tmp_path, name_override="custom")
    )
    assert result.project_id.endswith("_custom")
    assert (result.project_dir / "rtl" / "tiny8_cpu.v").exists()


def test_create_project_passes_allow_outside_workspace_through() -> None:
    """Smoke check: the request dataclass carries the flag. The
    path-safety guard is defence-in-depth and never fires via
    the normal flow (resolve_project_dir always returns a path
    under projects_root).
    """
    req = ProjectRequest(
        ir=_ir(), projects_root=Path("/tmp"), allow_outside_workspace=True
    )
    assert req.allow_outside_workspace is True
