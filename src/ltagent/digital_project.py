"""Phase 12: Tiny8 project orchestrator.

This module is the workspace-level glue between a validated
``DesignIR`` and a real directory on disk. The generator
(``ltagent.digital_generator``) is the deterministic file
writer; this module handles the path-safety, naming, and
workspace-root checks that the CLI and MCP layers need.

Path-safety contract (mirrors the analog ``ltagent.project``):

* Every project lives under ``config.workspace.projects_dir`` by
  default. The caller can opt out with
  ``allow_outside_workspace=True``.
* Project names are validated by the IR layer; this module adds
  a date prefix to make listings stable.
* No file is written outside the project directory. The
  generator's relative paths are joined with the project root.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .digital_generator import (
    GeneratedProject,
    generate_project,
)
from .digital_ir import DesignIR


@dataclass(frozen=True)
class ProjectRequest:
    """What the CLI / MCP layers pass in to create a project."""

    ir: DesignIR
    projects_root: Path
    name_override: str | None = None
    program_source: str | None = None
    program: Iterable[tuple[str, int]] | None = None
    allow_outside_workspace: bool = False


@dataclass(frozen=True)
class ProjectResult:
    """The result of :func:`create_project`."""

    project: GeneratedProject
    project_id: str
    project_dir: Path


def _today() -> str:
    return _dt.date.today().isoformat()


def resolve_project_dir(
    *,
    name: str,
    projects_root: Path,
    today: str | None = None,
) -> tuple[str, Path]:
    """Compute the project id and the on-disk directory.

    Returns ``(project_id, project_dir)``. The id is
    ``<date>_<name>``; the dir is ``projects_root / <id>``.

    The caller is responsible for actually creating the
    directory; this function only computes the path. Splitting
    the two makes the function easy to test.
    """
    day = today or _today()
    pid = f"{day}_{name}"
    return pid, projects_root / pid


def create_project(req: ProjectRequest) -> ProjectResult:
    """Materialise the project on disk.

    Raises:
        ValueError: if the resolved path is outside
            ``projects_root`` and ``allow_outside_workspace`` is
            False.
    """
    name = req.name_override or req.ir.name
    projects_root = req.projects_root.resolve()

    pid, project_dir = resolve_project_dir(name=name, projects_root=projects_root)

    # Path safety
    if not req.allow_outside_workspace:
        try:
            project_dir.resolve().relative_to(projects_root)
        except ValueError as exc:
            raise ValueError(
                f"project_dir {project_dir} is not under projects_root {projects_root}"
            ) from exc

    project = generate_project(
        req.ir,
        project_dir,
        program_source=req.program_source,
        program=req.program,
    )
    return ProjectResult(
        project=project,
        project_id=pid,
        project_dir=project_dir,
    )


__all__ = [
    "ProjectRequest",
    "ProjectResult",
    "create_project",
    "resolve_project_dir",
]
