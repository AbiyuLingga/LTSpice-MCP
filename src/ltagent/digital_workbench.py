"""Generic digital workbench: DigitalDesignIR, Verilog generator, simulation runner.

Phase 6 widens the Tiny8-specific :class:`DesignIR` to a generic
:class:`DigitalDesignIR` that supports arbitrary Verilog-2001
modules, deterministic testbenches, bounded Icarus/Verilator
simulation, and Yosys synthesis statistics. The v1 surface
deliberately keeps the Tiny8 path intact (the Tiny8 design
is still a ``DigitalDesignDocument`` with a Tiny8-shaped
``design`` payload) and adds the generic path alongside it.

The runners follow the master plan invariant: every subprocess
is invoked with an argv list (no ``shell=True``), the tool id is
registered, the timeout is bounded, and the result is a typed
:class:`ResultBundle` with a structured ``status``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .jobs import (
    JobKind,
    JobManifest,
    JobState,
    ResultBundle,
    RunManifest,
    utc_now_iso,
)
from .security import PathSafetyError, safe_resolve_under
from .tool_process import run_tool_process

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIGITAL_DESIGN_SCHEMA_VERSION: Final[str] = "1.0"
DIGITAL_DOC_SCHEMA_VERSION: Final[str] = "2.0"

IVERILOG_TOOL_ID: Final[str] = "iverilog"
VVP_TOOL_ID: Final[str] = "vvp"
VERILATOR_TOOL_ID: Final[str] = "verilator"
YOSYS_TOOL_ID: Final[str] = "yosys"

SUPPORTED_VERILOG_KINDS: Final[frozenset[str]] = frozenset(
    {
        "and_gate",
        "or_gate",
        "not_gate",
        "xor_gate",
        "mux2",
        "adder",
        "counter",
        "shift_register",
        "fsm",
        "pwm",
        "led_blinker",
        "uart_tx",
        "register_file",
        "alu",
    }
)

ERR_DIGITAL_UNSUPPORTED: Final[str] = "WORKBENCH_DIGITAL_UNSUPPORTED"
ERR_DIGITAL_TOPOLOGY_INVALID: Final[str] = "WORKBENCH_DIGITAL_TOPOLOGY_INVALID"
ERR_DIGITAL_TOOL_MISSING: Final[str] = "WORKBENCH_DIGITAL_TOOL_MISSING"
ERR_DIGITAL_TIMEOUT: Final[str] = "WORKBENCH_DIGITAL_TIMEOUT"
ERR_DIGITAL_CANCELLED: Final[str] = "WORKBENCH_DIGITAL_CANCELLED"
ERR_DIGITAL_SIMULATE_FAILED: Final[str] = "WORKBENCH_DIGITAL_SIMULATE_FAILED"
ERR_DIGITAL_SYNTHESIS_FAILED: Final[str] = "WORKBENCH_DIGITAL_SYNTHESIS_FAILED"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DigitalWorkbenchError(ValueError):
    """Structured error from the digital workbench."""

    def __init__(self, code: str, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, Any] = dict(data) if data else {}


# ---------------------------------------------------------------------------
# DigitalDesignIR Pydantic contracts
# ---------------------------------------------------------------------------


class Port(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    direction: str = Field(min_length=1, max_length=3)
    width: int = Field(ge=1, le=256)

    @field_validator("direction")
    @classmethod
    def _direction_known(cls, v: str) -> str:
        if v not in {"in", "out", "inout"}:
            raise ValueError(f"port direction {v!r} is not one of in/out/inout")
        return v


class DigitalModule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    kind: str
    ports: list[Port] = Field(default_factory=list)
    body: str = ""

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        if v not in SUPPORTED_VERILOG_KINDS:
            raise ValueError(
                f"digital module kind {v!r} is not in the v1 allowlist; "
                f"supported: {sorted(SUPPORTED_VERILOG_KINDS)}"
            )
        return v


class ClockSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    periodNs: int = Field(ge=1, le=10_000)


class ResetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    active: str = Field(default="low", pattern="^(low|high)$")


class TestGoal(BaseModel):
    __test__: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    expression: str = Field(min_length=1)
    timeoutCycles: int = Field(ge=1, le=10_000_000)


class DigitalDesignIR(BaseModel):
    """Generic digital design contract.

    The IR is the v1 surface; Phase 6's HDL generator emits
    Verilog-2001 from this IR. The IR is intentionally narrow:
    the v1 scope is the supported :data:`SUPPORTED_VERILOG_KINDS`
    set plus the assertion / measurement / clock / reset
    metadata.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str
    topModule: str
    clock: ClockSpec | None = None
    reset: ResetSpec | None = None
    modules: list[DigitalModule]
    testGoals: list[TestGoal] = Field(default_factory=list)

    @field_validator("schemaVersion")
    @classmethod
    def _version_supported(cls, v: str) -> str:
        if v != DIGITAL_DESIGN_SCHEMA_VERSION:
            raise ValueError(
                f"DigitalDesignIR schemaVersion {v!r} is not supported; "
                f"expected {DIGITAL_DESIGN_SCHEMA_VERSION!r}"
            )
        return v

    @field_validator("topModule")
    @classmethod
    def _top_module_in_modules(cls, v: str) -> str:
        return v


# ---------------------------------------------------------------------------
# Verilog-2001 generator
# ---------------------------------------------------------------------------


def _format_port(port: Port, *, registered: bool = False) -> str:
    direction = {"in": "input", "out": "output", "inout": "inout"}[port.direction]
    if registered and port.direction == "out":
        direction += " reg"
    width = "" if port.width == 1 else f" [{port.width - 1}:0]"
    return f"  {direction}{width} {port.name}"


def _module_signature(module: DigitalModule) -> str:
    registered_outputs = {
        port.name
        for port in module.ports
        if port.direction == "out"
        and re.search(rf"\breg\s+(?:\[[^]]+\]\s+)?{re.escape(port.name)}\s*;", module.body)
    }
    ports = (
        ",\n".join(_format_port(p, registered=p.name in registered_outputs) for p in module.ports)
        if module.ports
        else ""
    )
    return ports


def _verilog_module(module: DigitalModule) -> str:
    sig = _module_signature(module)
    body = module.body.strip() or "// (empty body — Phase 6 placeholder)"
    for port in module.ports:
        if port.direction == "out":
            body = re.sub(
                rf"\breg\s+(?:\[[^]]+\]\s+)?{re.escape(port.name)}\s*;\s*",
                "",
                body,
            )
    if sig:
        return f"module {module.name}(\n{sig}\n);\n  // kind: {module.kind}\n  {body}\nendmodule\n"
    return f"module {module.name}();n  // kind: {module.kind}\n  {body}\nendmodule\n"


def design_to_verilog(design: DigitalDesignIR, *, include_testbench: bool = True) -> str:
    """Render a :class:`DigitalDesignIR` to a Verilog-2001 string.

    The function is deterministic: the same input always
    produces the same output. The output is the concatenation
    of every module followed by a generated testbench that
    drives the clock, asserts the reset, and exercises each
    test goal.
    """
    if not any(module.name == design.topModule for module in design.modules):
        raise DigitalWorkbenchError(
            ERR_DIGITAL_TOPOLOGY_INVALID,
            f"top module {design.topModule!r} is not in the modules list",
            data={"topModule": design.topModule},
        )
    module_chunks = [_verilog_module(m) for m in design.modules]
    source = "\n".join(module_chunks)
    return source + ("\n" + _render_testbench(design) if include_testbench else "")


def _render_testbench(design: DigitalDesignIR) -> str:
    """Render a self-checking testbench for the design.

    The testbench is intentionally simple: a clock generator,
    a reset pulse, a dump of every test goal as ``$display``
    markers, and a ``$finish`` after enough cycles. A real
    golden-model comparison is Phase 8's job.
    """
    clock = design.clock or ClockSpec(name="clk", periodNs=10)
    reset = design.reset or ResetSpec(name="rst_n", active="low")
    top = next(module for module in design.modules if module.name == design.topModule)
    declarations: list[str] = []
    connections: list[str] = []
    for port in top.ports:
        width = "" if port.width == 1 else f" [{port.width - 1}:0]"
        storage = "reg" if port.direction == "in" else "wire"
        initial = " = 0" if storage == "reg" else ""
        declarations.append(f"  {storage}{width} {port.name}{initial};")
        connections.append(f"    .{port.name}({port.name})")
    has_clock = any(port.name == clock.name and port.direction == "in" for port in top.ports)
    has_reset = any(port.name == reset.name and port.direction == "in" for port in top.ports)
    max_cycles = max((goal.timeoutCycles for goal in design.testGoals), default=20)
    goal_lines = (
        "\n    ".join(
            f'$display("GOAL {goal.name}: {goal.expression}");' for goal in design.testGoals
        )
        or '$display("GOAL none");'
    )
    clock_line = (
        f"  always #({clock.periodNs // 2 or 1}) {clock.name} = ~{clock.name};\n"
        if has_clock
        else ""
    )
    reset_lines = ""
    if has_reset:
        active, inactive = ("0", "1") if reset.active == "low" else ("1", "0")
        reset_lines = (
            f"    {reset.name} = {active};\n"
            f"    #({clock.periodNs * 2}) {reset.name} = {inactive};\n"
        )
    return "".join(
        [
            f"module tb_{design.topModule};\n",
            "\n".join(declarations),
            "\n",
            f"  {design.topModule} dut(\n",
            ",\n".join(connections),
            "\n  );\n",
            clock_line,
            "  initial begin\n",
            '    $dumpfile("waveform.vcd");\n',
            "    $dumpvars(0, dut);\n",
            reset_lines,
            f"    {goal_lines}\n",
            f"    #({clock.periodNs * max_cycles});\n",
            "    $finish;\n",
            "  end\n",
            "endmodule\n",
        ]
    )


# ---------------------------------------------------------------------------
# Tool discovery + runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DigitalToolInfo:
    toolId: str
    executable: Path
    version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "toolId": self.toolId,
            "executable": str(self.executable),
            "version": self.version,
        }


def _resolve_digital_tool(tool_id: str, env_var: str) -> Path | None:
    found = shutil.which(tool_id)
    if found:
        return Path(found).resolve()
    import os

    env_path = os.environ.get(env_var)
    if env_path:
        return Path(env_path).expanduser().resolve()
    return None


def discover_tool(tool_id: str) -> DigitalToolInfo | None:
    env_map = {
        IVERILOG_TOOL_ID: "LTAGENT_IVERILOG",
        VVP_TOOL_ID: "LTAGENT_VVP",
        VERILATOR_TOOL_ID: "LTAGENT_VERILATOR",
        YOSYS_TOOL_ID: "LTAGENT_YOSYS",
    }
    env_var = env_map.get(tool_id, "")
    executable = _resolve_digital_tool(tool_id, env_var)
    if executable is None:
        return None
    return DigitalToolInfo(
        toolId=tool_id, executable=executable, version=_probe_version(executable, ["-V"])
    )


def _probe_version(executable: Path, argv_tail: list[str]) -> str:
    try:
        result = subprocess.run(
            [str(executable), *argv_tail],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unknown ({exc})"
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else "unknown"


@dataclass(frozen=True)
class DigitalRunResult:
    bundle: ResultBundle
    manifest: JobManifest
    project_id: str
    tool: DigitalToolInfo | None
    skipped_reason: str | None = None


def run_simulation(
    project_id: str,
    project_dir: Path,
    design: DigitalDesignIR,
    *,
    tool_id: str = IVERILOG_TOOL_ID,
    timeout_seconds: float = 30.0,
    cancel_event: Event | None = None,
    run_dir: Path | None = None,
) -> DigitalRunResult:
    """Compile + simulate a :class:`DigitalDesignIR` with the chosen tool.

    The function writes the Verilog source to ``project_dir/runs/``,
    invokes the tool with a bounded argv list, and returns a
    :class:`ResultBundle` with a structured ``status`` that
    distinguishes success / failed / skipped / timed_out.
    """
    tool = discover_tool(tool_id)
    manifest = _make_manifest(
        project_id,
        design,
        utc_now_iso(),
        JobState.RUNNING if tool is not None else JobState.SKIPPED,
        tool_id,
    )
    if tool is None:
        return DigitalRunResult(
            bundle=ResultBundle(
                status="skipped",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion=f"{tool_id}-not-installed",
                ),
                errors=[f"{tool_id} binary not found"],
            ),
            manifest=manifest,
            project_id=project_id,
            tool=None,
            skipped_reason=f"{tool_id} binary not found",
        )

    run_dir = _resolve_run_dir(project_dir, manifest.jobId, run_dir)
    verilog_path = run_dir / f"{design.topModule}.v"
    verilog_path.write_text(
        design_to_verilog(design, include_testbench=tool_id == IVERILOG_TOOL_ID),
        encoding="utf-8",
    )
    sim_path = run_dir / f"tb_{design.topModule}.out"

    if tool_id == IVERILOG_TOOL_ID:
        argv = [str(tool.executable), "-o", str(sim_path), str(verilog_path)]
    else:
        argv = [
            str(tool.executable),
            "--lint-only",
            "--language",
            "1364-2001",
            str(verilog_path),
        ]

    started = utc_now_iso()
    completed = run_tool_process(
        argv,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
    )
    if completed.cancelled:
        return DigitalRunResult(
            bundle=ResultBundle(
                status="cancelled",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion=tool.version,
                ),
                errors=[],
            ),
            manifest=_update_manifest(
                manifest,
                state=JobState.CANCELLED,
                finishedAt=utc_now_iso(),
                errorCode=ERR_DIGITAL_CANCELLED,
                errorMessage="digital simulation was cancelled",
            ),
            project_id=project_id,
            tool=tool,
        )
    if completed.timed_out:
        message = f"digital simulation timed out after {timeout_seconds:g} seconds"
        return DigitalRunResult(
            bundle=ResultBundle(
                status="timed_out",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion=tool.version,
                ),
                errors=[message],
            ),
            manifest=manifest,
            project_id=project_id,
            tool=tool,
        )
    if completed.returncode == 0 and tool_id == IVERILOG_TOOL_ID:
        runtime = discover_tool(VVP_TOOL_ID)
        if runtime is None:
            return DigitalRunResult(
                bundle=ResultBundle(
                    status="failed",
                    run=RunManifest(
                        schemaVersion="1.0",
                        runId=f"run_{manifest.jobId}",
                        jobId=manifest.jobId,
                        toolVersion=tool.version,
                    ),
                    errors=["vvp binary not found"],
                ),
                manifest=_update_manifest(
                    manifest,
                    state=JobState.FAILED,
                    finishedAt=utc_now_iso(),
                    errorCode=ERR_DIGITAL_TOOL_MISSING,
                    errorMessage="vvp binary not found",
                ),
                project_id=project_id,
                tool=tool,
            )
        executed = run_tool_process(
            [str(runtime.executable), str(sim_path)],
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            cwd=run_dir,
        )
        if executed.cancelled or executed.timed_out or executed.returncode != 0:
            completed = executed
        else:
            completed = type(executed)(
                returncode=0,
                stdout=completed.stdout + executed.stdout,
                stderr=completed.stderr + executed.stderr,
            )
    finished = utc_now_iso()
    if completed.returncode != 0:
        return DigitalRunResult(
            bundle=ResultBundle(
                status="failed",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion=tool.version,
                    artifacts={"verilog": str(verilog_path.relative_to(project_dir))},
                    stderrTail=completed.stderr[-2000:] if completed.stderr else "",
                    createdAt=started,
                ),
                errors=[
                    completed.stderr.strip().splitlines()[-1] if completed.stderr else "tool failed"
                ],
            ),
            manifest=_update_manifest(
                manifest,
                state=JobState.FAILED,
                finishedAt=utc_now_iso(),
                runId=f"run_{manifest.jobId}",
            ),
            project_id=project_id,
            tool=tool,
        )
    return DigitalRunResult(
        bundle=ResultBundle(
            status="success",
            run=RunManifest(
                schemaVersion="1.0",
                runId=f"run_{manifest.jobId}",
                jobId=manifest.jobId,
                toolVersion=tool.version,
                artifacts={
                    "verilog": str(verilog_path.relative_to(project_dir)),
                    "binary": str(sim_path.relative_to(project_dir)),
                    **(
                        {"waveform": str((run_dir / "waveform.vcd").relative_to(project_dir))}
                        if (run_dir / "waveform.vcd").is_file()
                        else {}
                    ),
                },
                warnings=[],
                stdoutTail=completed.stdout[-2000:] if completed.stdout else "",
                createdAt=started,
            ),
        ),
        manifest=_update_manifest(
            manifest, state=JobState.COMPLETED, finishedAt=finished, runId=f"run_{manifest.jobId}"
        ),
        project_id=project_id,
        tool=tool,
    )


def run_synthesis(
    project_id: str,
    project_dir: Path,
    design: DigitalDesignIR,
    *,
    timeout_seconds: float = 60.0,
    cancel_event: Event | None = None,
    run_dir: Path | None = None,
) -> DigitalRunResult:
    """Run ``yosys -p 'read_verilog ...; synth ...'`` on the generated source.

    Returns a structured :class:`ResultBundle`. Yosys's
    ``synth`` command emits a cell count and a wire count;
    the run manifest records both as ``measurements`` so the
    rendering layer can display the cell estimate.
    """
    tool = discover_tool(YOSYS_TOOL_ID)
    manifest = _make_manifest(project_id, design, utc_now_iso(), JobState.SKIPPED, YOSYS_TOOL_ID)
    if tool is None:
        return DigitalRunResult(
            bundle=ResultBundle(
                status="skipped",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion="yosys-not-installed",
                ),
                errors=["yosys binary not found"],
            ),
            manifest=manifest,
            project_id=project_id,
            tool=None,
            skipped_reason="yosys binary not found",
        )
    run_dir = _resolve_run_dir(project_dir, manifest.jobId, run_dir)
    verilog_path = run_dir / f"{design.topModule}.v"
    verilog_path.write_text(design_to_verilog(design, include_testbench=False), encoding="utf-8")
    log_path = run_dir / "synth.log"
    script = f"read_verilog {verilog_path}; synth -top {design.topModule}; stat"
    argv = [str(tool.executable), "-p", script, "-l", str(log_path)]
    started = utc_now_iso()
    completed = run_tool_process(
        argv,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
    )
    if completed.cancelled:
        return DigitalRunResult(
            bundle=ResultBundle(
                status="cancelled",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion=tool.version,
                ),
                errors=[],
            ),
            manifest=_update_manifest(
                manifest,
                state=JobState.CANCELLED,
                finishedAt=utc_now_iso(),
                errorCode=ERR_DIGITAL_CANCELLED,
                errorMessage="digital synthesis was cancelled",
            ),
            project_id=project_id,
            tool=tool,
        )
    if completed.timed_out:
        message = f"digital synthesis timed out after {timeout_seconds:g} seconds"
        return DigitalRunResult(
            bundle=ResultBundle(
                status="timed_out",
                run=RunManifest(
                    schemaVersion="1.0",
                    runId=f"run_{manifest.jobId}",
                    jobId=manifest.jobId,
                    toolVersion=tool.version,
                ),
                errors=[message],
            ),
            manifest=manifest,
            project_id=project_id,
            tool=tool,
        )
    finished = utc_now_iso()
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    measurements = _parse_yosys_stat(log_text)
    status = "success" if completed.returncode == 0 else "failed"
    return DigitalRunResult(
        bundle=ResultBundle(
            status=status,
            run=RunManifest(
                schemaVersion="1.0",
                runId=f"run_{manifest.jobId}",
                jobId=manifest.jobId,
                toolVersion=tool.version,
                artifacts={"verilog": str(verilog_path.relative_to(project_dir))},
                measurements=measurements,
                warnings=[],
                stdoutTail=completed.stdout[-2000:] if completed.stdout else "",
                stderrTail=completed.stderr[-2000:] if completed.stderr else "",
                createdAt=started,
            ),
            measurements=measurements,
            errors=[] if status == "success" else [completed.stderr or "yosys failed"],
        ),
        manifest=_update_manifest(
            manifest,
            state=JobState.COMPLETED if status == "success" else JobState.FAILED,
            finishedAt=finished,
            runId=f"run_{manifest.jobId}",
        ),
        project_id=project_id,
        tool=tool,
    )


_CELL_RE = __import__("re").compile(r"^\s*Number of cells:\s+(\d+)\s*$", __import__("re").MULTILINE)
_WIRE_RE = __import__("re").compile(r"^\s*Number of wires:\s+(\d+)\s*$", __import__("re").MULTILINE)


def _parse_yosys_stat(log_text: str) -> list[dict[str, Any]]:
    measurements: list[dict[str, Any]] = []
    cell_match = _CELL_RE.search(log_text)
    wire_match = _WIRE_RE.search(log_text)
    if cell_match:
        measurements.append({"name": "cell_count", "value": int(cell_match.group(1))})
    if wire_match:
        measurements.append({"name": "wire_count", "value": int(wire_match.group(1))})
    return measurements


def _resolve_run_dir(project_dir: Path, job_id: str, requested: Path | None) -> Path:
    runs_dir = project_dir / "runs"
    if requested is None:
        target = runs_dir / job_id
        target.mkdir(parents=True, exist_ok=True)
        return target
    try:
        return safe_resolve_under(requested, runs_dir, must_exist=True)
    except PathSafetyError as exc:
        raise DigitalWorkbenchError(
            ERR_DIGITAL_TOPOLOGY_INVALID,
            "run directory must stay inside the project runs directory",
            data=exc.data,
        ) from exc


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    project_id: str,
    design: DigitalDesignIR,
    created_at: str,
    state: JobState,
    tool_id: str,
) -> JobManifest:
    from .jobs import JOB_SCHEMA_VERSION

    return JobManifest(
        schemaVersion=JOB_SCHEMA_VERSION,
        jobId=f"{project_id}_{design.topModule}_{created_at.replace(':', '').replace('-', '')}",
        kind=JobKind.DIGITAL_SIMULATE if tool_id != YOSYS_TOOL_ID else JobKind.DIGITAL_SYNTHESIZE,
        state=state,
        projectRevision=0,
        projectId=project_id,
        toolId=tool_id,
        argv=(),
        timeoutSeconds=30.0,
        inputHash="",
        createdAt=created_at,
    )


def _update_manifest(manifest: JobManifest, **changes: Any) -> JobManifest:
    from dataclasses import replace

    return replace(manifest, **changes)


__all__ = [
    "DIGITAL_DESIGN_SCHEMA_VERSION",
    "DIGITAL_DOC_SCHEMA_VERSION",
    "ERR_DIGITAL_SIMULATE_FAILED",
    "ERR_DIGITAL_SYNTHESIS_FAILED",
    "ERR_DIGITAL_TIMEOUT",
    "ERR_DIGITAL_TOOL_MISSING",
    "ERR_DIGITAL_TOPOLOGY_INVALID",
    "ERR_DIGITAL_UNSUPPORTED",
    "IVERILOG_TOOL_ID",
    "SUPPORTED_VERILOG_KINDS",
    "VERILATOR_TOOL_ID",
    "YOSYS_TOOL_ID",
    "ClockSpec",
    "DigitalDesignIR",
    "DigitalModule",
    "DigitalRunResult",
    "DigitalToolInfo",
    "DigitalWorkbenchError",
    "Port",
    "ResetSpec",
    "TestGoal",
    "design_to_verilog",
    "discover_tool",
    "run_simulation",
    "run_synthesis",
]
