"""Structured, workspace-confined ngspice batch runner.

ngspice is the preferred cross-platform analog backend.  This adapter only
constructs fixed batch arguments and delegates process execution to the
existing no-shell runner; it never accepts an executable, directives, or raw
paths from a caller.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .digital_runner import RunRequest, RunResult, run
from .security import PathSafetyError, safe_resolve_under

ERR_MISSING: Final[str] = "NGSPICE_MISSING"
ERR_TIMEOUT: Final[str] = "NGSPICE_TIMEOUT"
ERR_FAILED: Final[str] = "NGSPICE_FAILED"
ERR_OK: Final[str] = "NGSPICE_OK"


class NgspiceError(ValueError):
    """Structured request validation failure for the analog backend."""

    def __init__(self, code: str, message: str, *, data: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = dict(data) if data else {}


@dataclass(frozen=True)
class NgspiceRequest:
    """A fixed ngspice batch simulation request within one workspace."""

    netlist: Path
    workspace: Path
    timeout_s: int = 30


@dataclass(frozen=True)
class NgspiceResult:
    """Stable status independent from whether ngspice is installed."""

    status: str
    code: str
    argv: tuple[str, ...]
    log_path: Path
    raw_path: Path
    run: RunResult | None


def build_ngspice_argv(request: NgspiceRequest) -> tuple[str, ...]:
    """Build a non-interactive batch argv after all path safety checks."""
    workspace = _workspace(request.workspace)
    netlist = _under_workspace(request.netlist, workspace, must_exist=True)
    if netlist.suffix.lower() not in {".cir", ".net", ".sp", ".spi"}:
        raise NgspiceError(
            "NGSPICE_NETLIST_EXTENSION_INVALID",
            "netlist must use a supported SPICE extension",
            data={"netlist": str(netlist)},
        )
    log_path, raw_path = _artifact_paths(netlist, workspace)
    return (
        "ngspice",
        "-b",
        "-o",
        str(log_path),
        "-r",
        str(raw_path),
        str(netlist),
    )


def run_ngspice(request: NgspiceRequest) -> NgspiceResult:
    """Run ngspice when available, otherwise emit a structured skipped result."""
    argv = build_ngspice_argv(request)
    workspace = _workspace(request.workspace)
    log_path, raw_path = _artifact_paths(_under_workspace(request.netlist, workspace, must_exist=True), workspace)
    if shutil.which("ngspice") is None:
        return NgspiceResult(
            status="skipped",
            code=ERR_MISSING,
            argv=argv,
            log_path=log_path,
            raw_path=raw_path,
            run=None,
        )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise NgspiceError(
            "NGSPICE_ARTIFACT_IO",
            f"cannot create ngspice run directory: {exc}",
            data={"path": str(log_path.parent)},
        ) from exc
    process_result = run(
        RunRequest(argv=argv, cwd=workspace, timeout_s=max(5, request.timeout_s))
    )
    if process_result.timed_out:
        status, code = "failed", ERR_TIMEOUT
    elif process_result.ok:
        status, code = "passed", ERR_OK
    else:
        status, code = "failed", ERR_FAILED
    return NgspiceResult(
        status=status,
        code=code,
        argv=argv,
        log_path=log_path,
        raw_path=raw_path,
        run=process_result,
    )


def _workspace(path: Path) -> Path:
    workspace = path.expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise NgspiceError(
            "NGSPICE_WORKSPACE_NOT_FOUND",
            f"workspace {workspace} does not exist or is not a directory",
            data={"workspace": str(workspace)},
        )
    return workspace


def _under_workspace(path: Path, workspace: Path, *, must_exist: bool) -> Path:
    try:
        return safe_resolve_under(path, workspace, must_exist=must_exist)
    except PathSafetyError as exc:
        raise NgspiceError(exc.code, exc.message, data=exc.data) from exc


def _artifact_paths(netlist: Path, workspace: Path) -> tuple[Path, Path]:
    run_dir = _under_workspace(workspace / "runs", workspace, must_exist=False)
    return run_dir / f"{netlist.stem}.log", run_dir / f"{netlist.stem}.raw"


__all__ = [
    "ERR_FAILED",
    "ERR_MISSING",
    "ERR_OK",
    "ERR_TIMEOUT",
    "NgspiceError",
    "NgspiceRequest",
    "NgspiceResult",
    "build_ngspice_argv",
    "run_ngspice",
]
