"""Phase 12: digital report writers.

Render ``reports/sim.json`` and ``reports/synth.json`` to a
project directory. The shapes are documented in
``docs/digital/plan-tiny8-agent.md`` §10.2.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .digital_runner import RunResult

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class LintReport:
    status: str = "skipped"  # pass / fail / skipped
    tool: str = "verilator"
    duration_ms: int = 0
    returncode: int = -1
    note: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "tool": self.tool,
            "durationMs": self.duration_ms,
            "returncode": self.returncode,
            "note": self.note,
            "stdoutTail": self.stdout_tail,
            "stderrTail": self.stderr_tail,
        }


@dataclass
class SimulationReport:
    status: str = "skipped"  # pass / fail / skipped
    tool: str = "iverilog+vvp"
    cycles: int = 0
    halted: bool = False
    observed_acc: int | None = None
    observed_memory: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    returncode: int = -1
    note: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "tool": self.tool,
            "cycles": self.cycles,
            "halted": self.halted,
            "observed": {
                "acc": self.observed_acc,
                "memory": dict(self.observed_memory),
            },
            "durationMs": self.duration_ms,
            "returncode": self.returncode,
            "note": self.note,
            "stdoutTail": self.stdout_tail,
            "stderrTail": self.stderr_tail,
        }


@dataclass
class SynthesisReport:
    status: str = "skipped"
    tool: str = "yosys"
    duration_ms: int = 0
    returncode: int = -1
    note: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "tool": self.tool,
            "durationMs": self.duration_ms,
            "returncode": self.returncode,
            "note": self.note,
            "stdoutTail": self.stdout_tail,
            "stderrTail": self.stderr_tail,
        }


@dataclass
class ProjectResult:
    """The top-level ``result.json`` payload."""

    status: str = "skipped"  # pass / fail / skipped / partial
    lint: LintReport = field(default_factory=LintReport)
    simulation: SimulationReport = field(default_factory=SimulationReport)
    synthesis: SynthesisReport = field(default_factory=SynthesisReport)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "0.1",
            "projectKind": "digital",
            "status": self.status,
            "lint": self.lint.to_dict(),
            "simulation": self.simulation.to_dict(),
            "synthesis": self.synthesis.to_dict(),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_lint_report(project_dir: Path, report: LintReport) -> Path:
    out = project_dir / "reports" / "lint.json"
    write_json(out, report.to_dict())
    return out


def write_simulation_report(project_dir: Path, report: SimulationReport) -> Path:
    out = project_dir / "reports" / "sim.json"
    write_json(out, report.to_dict())
    return out


def write_synthesis_report(project_dir: Path, report: SynthesisReport) -> Path:
    out = project_dir / "reports" / "synth.json"
    write_json(out, report.to_dict())
    return out


def write_result_json(project_dir: Path, report: ProjectResult) -> Path:
    out = project_dir / "result.json"
    write_json(out, report.to_dict())
    return out


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


_CYCLE_RE = __import__("re").compile(r"halted at cycle\s+(\d+)")
_ACC_RE = __import__("re").compile(r"TB_PASS halted at cycle\s+(\d+)\s+acc=([0-9a-fA-F]+)")


def parse_simulation_observation(stdout: str) -> tuple[int, bool, int | None, dict[str, int]]:
    """Extract the cycle count, halted flag, acc, and memory dumps from
    a testbench stdout. Returns ``(cycles, halted, acc, mem)``.

    The v1 testbench prints ``TB_PASS halted at cycle <N> acc=<HH>``;
    the memory map is read from the hierarchy if the user wires it.
    For v1 we only extract what the v1 testbench prints.
    """
    cycles = 0
    halted = False
    acc: int | None = None
    mem: dict[str, int] = {}

    m = _ACC_RE.search(stdout)
    if m:
        cycles = int(m.group(1))
        halted = True
        acc = int(m.group(2), 16)
        return cycles, halted, acc, mem

    m = _CYCLE_RE.search(stdout)
    if m:
        cycles = int(m.group(1))
        halted = True
    return cycles, halted, acc, mem


def run_result_to_lint(result: RunResult) -> LintReport:
    if result.timed_out:
        return LintReport(
            status="fail",
            duration_ms=result.duration_ms,
            returncode=result.returncode,
            note="timeout",
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
        )
    if result.returncode == 0:
        return LintReport(
            status="pass",
            duration_ms=result.duration_ms,
            returncode=result.returncode,
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
        )
    return LintReport(
        status="fail",
        duration_ms=result.duration_ms,
        returncode=result.returncode,
        note="verilator reported errors",
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
    )


__all__ = [
    "LintReport",
    "ProjectResult",
    "SimulationReport",
    "SynthesisReport",
    "parse_simulation_observation",
    "run_result_to_lint",
    "write_json",
    "write_lint_report",
    "write_result_json",
    "write_simulation_report",
    "write_synthesis_report",
]
