"""Phase 12: digital toolchain runner.

Wraps the optional external tools that Phase 12 uses:

* ``iverilog`` + ``vvp`` for compile + simulate.
* ``verilator`` for optional lint.
* ``yosys`` for synthesis sanity check.

The runner never executes shell. Every command is a list of args.
Tool names come from a config allowlist. Missing tools produce
structured skip / fail, never a crash.

Captured stdout / stderr is capped at 64 KiB per stream; tails
are kept for the result JSON.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Output cap. Per stream, per run.
_MAX_BYTES_PER_STREAM: int = 64 * 1024
_TAIL_BYTES: int = 4 * 1024


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolStatus:
    """One tool's status."""

    name: str
    available: bool
    path: str | None = None
    version: str | None = None


def which_tool(name: str) -> ToolStatus:
    """Find ``name`` on PATH. ``name`` must match the allowlist."""
    if name not in _TOOL_ALLOWLIST:
        raise ValueError(f"tool {name!r} not in allowlist {sorted(_TOOL_ALLOWLIST)}")
    path = shutil.which(name)
    if path is None:
        return ToolStatus(name=name, available=False)
    version = _safe_version(name, path)
    return ToolStatus(name=name, available=True, path=path, version=version)


def doctor_status() -> dict[str, ToolStatus]:
    """Return one ToolStatus per tool in the allowlist."""
    return {name: which_tool(name) for name in _TOOL_ALLOWLIST}


_TOOL_ALLOWLIST: frozenset[str] = frozenset({"iverilog", "vvp", "verilator", "yosys", "gtkwave"})

_VERSION_FLAGS: Mapping[str, Sequence[str]] = {
    "iverilog": ("-V",),
    "vvp": ("-V",),
    "verilator": ("--version",),
    "yosys": ("-V",),
    "gtkwave": ("--version",),
}


def _safe_version(name: str, path: str) -> str | None:
    flags = _VERSION_FLAGS.get(name, ("--version",))
    try:
        proc = subprocess.run(
            [path, *flags],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    # Take the first non-empty line.
    for line in out.splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return None


# ---------------------------------------------------------------------------
# Run requests / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRequest:
    """What the caller wants the runner to do.

    ``argv`` is the **list-args** command. ``cwd`` is the working
    directory. ``timeout_s`` is the per-run timeout (clamped to
    >= 5s).
    """

    argv: tuple[str, ...]
    cwd: Path
    timeout_s: int = 30

    def with_timeout(self, t: int) -> RunRequest:
        return dataclasses.replace(self, timeout_s=max(5, t))


@dataclass(frozen=True)
class RunResult:
    """The result of one tool invocation.

    ``returncode`` is the process exit code. ``timed_out`` is
    True if the timeout fired. ``stdout_tail`` and
    ``stderr_tail`` are the last ``_TAIL_BYTES`` of each stream.
    ``stdout_truncated`` / ``stderr_truncated`` indicate
    whether the full stream was longer.
    """

    returncode: int
    timed_out: bool
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def ok(self) -> bool:
        return (not self.timed_out) and self.returncode == 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run(req: RunRequest) -> RunResult:
    """Execute ``req.argv`` under ``req.cwd`` with a timeout.

    The command is launched with ``shell=False``; argv is a list
    of program + args. The first argv element must be on PATH
    (or be an absolute path) — the caller is responsible for the
    allowlist check.
    """
    if not req.argv:
        raise ValueError("RunRequest.argv must be non-empty")
    program = req.argv[0]
    if os.path.isabs(program) or os.sep in program:
        # absolute / relative path
        if not Path(program).exists():
            return RunResult(
                returncode=-1,
                timed_out=False,
                duration_ms=0,
                stdout_tail="",
                stderr_tail=f"program not found: {program}",
                stdout_truncated=False,
                stderr_truncated=False,
            )
    else:
        resolved = shutil.which(program)
        if resolved is None:
            return RunResult(
                returncode=-1,
                timed_out=False,
                duration_ms=0,
                stdout_tail="",
                stderr_tail=f"program not on PATH: {program}",
                stdout_truncated=False,
                stderr_truncated=False,
            )

    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(req.argv),
            cwd=str(req.cwd),
            capture_output=True,
            timeout=req.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        out_b = exc.stdout or b""
        err_b = exc.stderr or b""
        return RunResult(
            returncode=-1,
            timed_out=True,
            duration_ms=duration_ms,
            stdout_tail=_tail(out_b),
            stderr_tail=_tail(err_b),
            stdout_truncated=len(out_b) > _MAX_BYTES_PER_STREAM,
            stderr_truncated=len(err_b) > _MAX_BYTES_PER_STREAM,
        )
    except OSError as exc:
        return RunResult(
            returncode=-1,
            timed_out=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout_tail="",
            stderr_tail=f"launch error: {exc}",
            stdout_truncated=False,
            stderr_truncated=False,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    return RunResult(
        returncode=int(proc.returncode),
        timed_out=False,
        duration_ms=duration_ms,
        stdout_tail=_tail(proc.stdout or b""),
        stderr_tail=_tail(proc.stderr or b""),
        stdout_truncated=len(proc.stdout or b"") > _MAX_BYTES_PER_STREAM,
        stderr_truncated=len(proc.stderr or b"") > _MAX_BYTES_PER_STREAM,
    )


def _tail(data: bytes) -> str:
    if not data:
        return ""
    if len(data) > _TAIL_BYTES:
        data = data[-_TAIL_BYTES:]
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return data.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Domain-specific helpers
# ---------------------------------------------------------------------------


def compile_iverilog(
    *,
    src_files: Sequence[Path],
    out_binary: Path,
    cwd: Path,
    timeout_s: int = 30,
) -> RunResult:
    """Compile ``src_files`` with ``iverilog -o out_binary``."""
    argv = (
        "iverilog",
        "-g2001",  # Verilog-2001 only
        "-o",
        str(out_binary),
        *[str(p) for p in src_files],
    )
    return run(RunRequest(argv=argv, cwd=cwd, timeout_s=timeout_s))


def run_vvp(*, binary: Path, cwd: Path, timeout_s: int = 30) -> RunResult:
    """Run a compiled vvp binary."""
    argv = ("vvp", str(binary))
    return run(RunRequest(argv=argv, cwd=cwd, timeout_s=timeout_s))


def lint_verilator(*, src_files: Sequence[Path], cwd: Path, timeout_s: int = 30) -> RunResult:
    """Run ``verilator --lint-only`` over the source files."""
    argv = (
        "verilator",
        "--lint-only",
        "-Wall",
        "-Wno-DECLFILENAME",
        *[str(p) for p in src_files],
    )
    return run(RunRequest(argv=argv, cwd=cwd, timeout_s=timeout_s))


def synth_yosys(
    *,
    top: str,
    src_files: Sequence[Path],
    cwd: Path,
    timeout_s: int = 60,
) -> RunResult:
    """Run a Yosys synthesis script that reads, checks, and stats the design."""
    script = (
        f"read_verilog {' '.join(str(p) for p in src_files)}\n"
        f"hierarchy -check -top {top}\n"
        "proc; opt; check -assert\n"
        f"stat -top {top}\n"
    )
    argv = ("yosys", "-q", "-p", script)
    return run(RunRequest(argv=argv, cwd=cwd, timeout_s=timeout_s))


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------


# A simulation passes when the testbench prints ``TB_PASS overall``
# and the runner exit code is 0. Icarus returns 0 on $finish; non-
# zero on $fatal.
def simulation_passed(result: RunResult) -> bool:
    if not result.ok:
        return False
    return "TB_PASS overall" in result.stdout_tail


__all__ = [
    "_TOOL_ALLOWLIST",
    "RunRequest",
    "RunResult",
    "ToolStatus",
    "compile_iverilog",
    "doctor_status",
    "lint_verilator",
    "run",
    "run_vvp",
    "simulation_passed",
    "synth_yosys",
    "which_tool",
]
