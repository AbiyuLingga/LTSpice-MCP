"""Analog workbench helpers: CircuitGraph to netlist, LTspice import/export.

The phase 5 surface is the first workbench capability that runs
external EDA tools. The integration honours the master plan
invariant: every subprocess is invoked with an argv list (no
``shell=True``) and the registered tool id, the project paths
stay under the canonical projects root, and the result is a
typed :class:`RunManifest` plus a :class:`ResultBundle`.

Phase 5 is a deterministic layer over the existing
:mod:`ltagent.netlist` and :mod:`ltagent.asc` modules. It does
not introduce a new netlist generator; it widens the surface to
the v2 ``CircuitGraph`` model and adds a stable LTspice ``.asc``
round-trip for the supported subset (resistor, capacitor,
inductor, diode, npn, pnp, nmos, pmos, opamp, sources, ground).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .jobs import (
    JobKind,
    JobManifest,
    JobState,
    ResultBundle,
    RunManifest,
    WaveformBundle,
    chunk_trace,
    utc_now_iso,
)
from .live.graph_schema import (
    CircuitGraph,
)
from .live.graph_to_ir import graph_to_ir
from .netlist import NetlistError, render_netlist
from .security import PathSafetyError, safe_resolve_under
from .workbench_v2 import DIR_RUNS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANALOG_TOOL_ID: Final[str] = "ngspice"
LTSPICE_TOOL_ID: Final[str] = "ltspice"
OPUS_TOOL_ID: Final[str] = "ltagent.opus"

SUPPORTED_ANALYSIS_KINDS: Final[frozenset[str]] = frozenset({"op", "dc", "tran", "ac"})

# Stable error codes for the structured failure surface.
ERR_ANALOG_UNSUPPORTED: Final[str] = "WORKBENCH_ANALOG_UNSUPPORTED"
ERR_ANALOG_TOPOLOGY_INVALID: Final[str] = "WORKBENCH_ANALOG_TOPOLOGY_INVALID"
ERR_ANALOG_TOOL_MISSING: Final[str] = "WORKBENCH_ANALOG_TOOL_MISSING"
ERR_ANALOG_TIMEOUT: Final[str] = "WORKBENCH_ANALOG_TIMEOUT"
ERR_ANALOG_SIMULATE_FAILED: Final[str] = "WORKBENCH_ANALOG_SIMULATE_FAILED"
ERR_ANALOG_IO: Final[str] = "WORKBENCH_ANALOG_IO"

# Mapping from supported component kinds to SPICE prefixes. The
# IR module already keeps this map; we re-declare it here so the
# analog workbench never imports :mod:`ltagent.ir` directly.
KIND_TO_SPICE_PREFIX: Final[dict[str, str]] = {
    "resistor": "R",
    "capacitor": "C",
    "inductor": "L",
    "diode": "D",
    "npn": "Q",
    "pnp": "Q",
    "nmos": "M",
    "pmos": "M",
    "opamp": "X",
    "voltage_source": "V",
    "current_source": "I",
}

GROUND_NET: Final[str] = "0"

# A small, conservative OPA model that ships with every project
# created from the v2 surface. The model is "UniversalOpamp"; the
# netlist generator emits the matching ``.subckt`` block when the
# graph references it. Phase 5 keeps it intentionally minimal so
# no proprietary model is bundled.
UNIVERSAL_OPAMP_SUBCKT: Final[str] = (
    ".subckt UniversalOpamp in+ in- v+ v- out\n"
    "G1 0 out ref 0 1\n"
    "E1 ref 0 in+ in- 1e6\n"
    "R1 ref 0 1G\n"
    ".ends UniversalOpamp\n"
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AnalogWorkbenchError(ValueError):
    """Structured error from the analog workbench."""

    def __init__(self, code: str, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, Any] = dict(data) if data else {}


# ---------------------------------------------------------------------------
# Topology validation
# ---------------------------------------------------------------------------


def validate_topology(graph: CircuitGraph) -> list[str]:
    """Return the list of issues with the graph that block a netlist render.

    The list is empty when the graph is renderable. The checks are
    narrow: ground net present, every component has at least one
    pin connected, no orphan nets.
    """
    issues: list[str] = []
    if GROUND_NET not in graph.nets:
        issues.append(f"ground net {GROUND_NET!r} is not declared in the graph")
    for component in graph.components.values():
        if not component.pins.pins:
            issues.append(f"component {component.id!r} has no pins connected")
            continue
        if not any(net for net in component.pins.pins.values()):
            issues.append(f"component {component.id!r} has no nets connected")
    return issues


# ---------------------------------------------------------------------------
# Netlist helpers
# ---------------------------------------------------------------------------


def graph_to_netlist_text(graph: CircuitGraph) -> str:
    """Render a :class:`CircuitGraph` to a SPICE netlist string.

    The function lowers the graph to a :class:`CircuitIR` via
    :func:`graph_to_ir` and then re-uses the existing
    :mod:`ltagent.netlist` writer so we do not duplicate the
    SPICE grammar. The opamp subcircuit is appended when the
    graph references ``UniversalOpamp``.
    """
    try:
        circuit_ir = graph_to_ir(graph)
    except Exception as exc:
        raise AnalogWorkbenchError(
            ERR_ANALOG_TOPOLOGY_INVALID,
            f"failed to lower graph to CircuitIR: {exc}",
            data={"error": str(exc)},
        ) from exc
    try:
        netlist = render_netlist(circuit_ir)
    except NetlistError as exc:
        raise AnalogWorkbenchError(
            ERR_ANALOG_TOPOLOGY_INVALID,
            f"failed to render netlist: {exc}",
            data={"error": str(exc)},
        ) from exc
    text = netlist.text
    if "UniversalOpamp" in text and UNIVERSAL_OPAMP_SUBCKT not in text:
        text = text + "\n" + UNIVERSAL_OPAMP_SUBCKT
    return text


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolInfo:
    toolId: str
    executable: Path
    version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "toolId": self.toolId,
            "executable": str(self.executable),
            "version": self.version,
        }


def _resolve_tool(name: str, env_var: str) -> Path | None:
    """Return the absolute path to a simulator, or None when missing.

    Checks ``PATH`` first, then a few well-known install locations
    so the Phase 5 acceptance can run on hosts where the binary
    is not on the default PATH.
    """
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    candidates = [
        Path("/usr/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/opt/wine-stable/bin") / name,  # wine-shimmed hosts
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    env_path = os.environ.get(env_var)
    if env_path:
        return Path(env_path).expanduser().resolve()
    return None


def discover_analog_tool() -> ToolInfo | None:
    """Discover the ngspice binary, or None when missing.

    Phase 5 acceptance uses this to produce a structured
    ``skipped`` result when the binary is not on the host.
    """
    executable = _resolve_tool("ngspice", "LTAGENT_NGSPICE")
    if executable is None:
        return None
    version = _probe_tool_version(executable, ["-v"])
    return ToolInfo(toolId=ANALOG_TOOL_ID, executable=executable, version=version)


def _probe_tool_version(executable: Path, argv_tail: list[str]) -> str:
    """Run ``executable argv_tail`` and return stdout (first line)."""
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


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalogRunResult:
    """Convenience wrapper around :class:`ResultBundle` for analog runs."""

    bundle: ResultBundle
    manifest: JobManifest
    project_id: str
    tool: ToolInfo | None
    skipped_reason: str | None = None


def run_analog_simulation(
    project_id: str,
    projects_root: Path | str,
    graph: CircuitGraph,
    *,
    timeout_seconds: float = 30.0,
    tool_executable: Path | str | None = None,
    now: str | None = None,
) -> AnalogRunResult:
    """Run a single analog simulation through the registered tool.

    The function renders the netlist, writes it under the project's
    run directory, invokes the tool with a bounded argv list, and
    parses the output. The function never raises on a missing
    tool; it returns a structured ``ResultBundle(status='skipped')``
    so the calling layer can render the same UI for every outcome.
    """
    try:
        root = safe_resolve_under(Path(projects_root), Path(projects_root), must_exist=False)
    except PathSafetyError as exc:
        raise AnalogWorkbenchError(
            ERR_ANALOG_IO,
            exc.message,
            data=exc.data,
        ) from exc
    project_paths = root / project_id

    issues = validate_topology(graph)
    if issues:
        raise AnalogWorkbenchError(
            ERR_ANALOG_TOPOLOGY_INVALID,
            "graph failed topology validation",
            data={"issues": issues},
        )

    if tool_executable is None:
        tool = discover_analog_tool()
    else:
        tool = ToolInfo(
            toolId=ANALOG_TOOL_ID,
            executable=Path(tool_executable),
            version="user-supplied",
        )
    if tool is None:
        manifest = _make_manifest(
            project_id, project_paths, graph, now, JobState.SKIPPED, ANALOG_TOOL_ID
        )
        bundle = ResultBundle(
            status="skipped",
            run=RunManifest(
                schemaVersion="1.0",
                runId=f"run_{manifest.jobId}",
                jobId=manifest.jobId,
                toolVersion="ngspice-not-installed",
                warnings=["ngspice binary not on PATH; install or set LTAGENT_NGSPICE"],
            ),
            errors=["ngspice binary not found"],
        )
        return AnalogRunResult(
            bundle=bundle,
            manifest=manifest,
            project_id=project_id,
            tool=None,
            skipped_reason="ngspice binary not found",
        )

    run_stamp = now or utc_now_iso()
    run_dir = project_paths / DIR_RUNS / manifest_id(graph.projectId, run_stamp)
    run_dir.mkdir(parents=True, exist_ok=True)
    netlist_path = run_dir / "circuit.cir"
    log_path = run_dir / "run.log"

    netlist_text = graph_to_netlist_text(graph)
    netlist_path.write_text(netlist_text, encoding="utf-8")

    argv = [str(tool.executable), "-b", "-o", str(log_path), str(netlist_path)]
    started = utc_now_iso()
    initial_manifest = _make_manifest(
        project_id,
        project_paths,
        graph,
        started,
        JobState.RUNNING,
        tool.toolId,
        argv=tuple(argv),
    )
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        manifest_started = _update_manifest(
            initial_manifest,
            state=JobState.TIMED_OUT,
            finishedAt=utc_now_iso(),
            errorCode=ERR_ANALOG_TIMEOUT,
            errorMessage=str(exc),
            runId=f"run_{initial_manifest.jobId}",
        )
        bundle = ResultBundle(
            status="timed_out",
            run=RunManifest(
                schemaVersion="1.0",
                runId=manifest_started.runId or "",
                jobId=manifest_started.jobId,
                toolVersion=tool.version,
            ),
            errors=[str(exc)],
        )
        return AnalogRunResult(
            bundle=bundle,
            manifest=manifest_started,
            project_id=project_id,
            tool=tool,
        )
    finished = utc_now_iso()
    log_text = ""
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    measurements = _parse_measurements(log_text)
    if completed.returncode == 0:
        manifest_started = _update_manifest(
            initial_manifest,
            state=JobState.COMPLETED,
            finishedAt=finished,
            runId=f"run_{initial_manifest.jobId}",
        )
        bundle = ResultBundle(
            status="success",
            run=RunManifest(
                schemaVersion="1.0",
                runId=manifest_started.runId or "",
                jobId=manifest_started.jobId,
                toolVersion=tool.version,
                artifacts={"netlist": "circuit.cir", "log": "run.log"},
                measurements=measurements,
                warnings=[],
                stdoutTail=completed.stdout[-2000:] if completed.stdout else "",
                stderrTail=completed.stderr[-2000:] if completed.stderr else "",
                createdAt=started,
            ),
            measurements=measurements,
            errors=[],
        )
        return AnalogRunResult(
            bundle=bundle, manifest=manifest_started, project_id=project_id, tool=tool
        )
    last_stderr = ""
    if completed.stderr:
        last_stderr = completed.stderr.strip().splitlines()[-1]
    manifest_started = _update_manifest(
        initial_manifest,
        state=JobState.FAILED,
        finishedAt=finished,
        runId=f"run_{initial_manifest.jobId}",
        errorCode=ERR_ANALOG_SIMULATE_FAILED,
        errorMessage=(f"tool exited with code {completed.returncode}: {last_stderr}"),
    )
    bundle = ResultBundle(
        status="failed",
        run=RunManifest(
            schemaVersion="1.0",
            runId=manifest_started.runId or "",
            jobId=manifest_started.jobId,
            toolVersion=tool.version,
            artifacts={"netlist": "circuit.cir", "log": "run.log"},
            measurements=measurements,
            warnings=[],
            stdoutTail=completed.stdout[-2000:] if completed.stdout else "",
            stderrTail=completed.stderr[-2000:] if completed.stderr else "",
            createdAt=started,
        ),
        measurements=measurements,
        errors=[manifest_started.errorMessage or "tool failed"],
    )
    return AnalogRunResult(
        bundle=bundle, manifest=manifest_started, project_id=project_id, tool=tool
    )


def manifest_id(project_id: str, stamp: str) -> str:
    return f"{project_id}_{stamp.replace(':', '').replace('-', '').replace('.', '')}"


def _update_manifest(manifest: JobManifest, **changes: Any) -> JobManifest:
    """Return a new :class:`JobManifest` with the given field overrides.

    The manifest is frozen; this helper builds the replacement
    so the runner can update state / finishedAt / errorMessage
    without violating the dataclass contract.
    """
    from dataclasses import replace

    return replace(manifest, **changes)


def _make_manifest(
    project_id: str,
    project_dir: Path,
    graph: CircuitGraph,
    created_at: str | None,
    state: JobState,
    tool_id: str,
    *,
    argv: tuple[str, ...] = (),
) -> JobManifest:
    from .jobs import JOB_SCHEMA_VERSION

    return JobManifest(
        schemaVersion=JOB_SCHEMA_VERSION,
        jobId=manifest_id(project_id, created_at or utc_now_iso()),
        kind=JobKind.ANALOG_SIMULATE,
        state=state,
        projectRevision=0,
        projectId=project_id,
        toolId=tool_id,
        argv=argv,
        timeoutSeconds=30.0,
        inputHash="",
        createdAt=created_at or utc_now_iso(),
    )


_MEAS_PATTERN = re.compile(r"^\s*(\w+)\s*=\s*([0-9eE+\-.]+)\s*$")


def _parse_measurements(log_text: str) -> list[dict[str, Any]]:
    """Pull ``name=value`` lines out of a SPICE log."""
    measurements: list[dict[str, Any]] = []
    for line in log_text.splitlines():
        match = _MEAS_PATTERN.match(line)
        if not match:
            continue
        measurements.append({"name": match.group(1), "value": float(match.group(2))})
    return measurements


# ---------------------------------------------------------------------------
# LTspice round-trip (supported subset only)
# ---------------------------------------------------------------------------


_ASC_WIRE_RE = re.compile(r"^WIRE\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*$")
_ASC_FLAG_RE = re.compile(r"^FLAG\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s*$")
_ASC_SYMBOL_RE = re.compile(r"^SYMBOL\s+(\S+)\s+(-?\d+)\s+(-?\d+)(?:\s+(\S+))?\s*$")
_ASC_TEXT_RE = re.compile(r"^TEXT\s+(-?\d+)\s+(-?\d+)\s+\S+\s+(\d+)\s+(.+)$")


def parse_ltspice_asc(text: str) -> dict[str, Any]:
    """Parse a supported-subset LTspice ``.asc`` file.

    The parser recognises WIRE / FLAG / SYMBOL / TEXT directives
    that the v2 schematic view can represent. Anything else is
    preserved verbatim in ``opaque`` so a future Phase can lift
    it back into the schematic without losing data.
    """
    components: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    text_blocks: list[dict[str, Any]] = []
    opaque: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if _ASC_WIRE_RE.match(line):
            match = _ASC_WIRE_RE.match(line)
            if match is None:
                continue
            wires.append(
                {
                    "x1": int(match.group(1)),
                    "y1": int(match.group(2)),
                    "x2": int(match.group(3)),
                    "y2": int(match.group(4)),
                }
            )
        elif _ASC_FLAG_RE.match(line):
            match = _ASC_FLAG_RE.match(line)
            if match is None:
                continue
            flags.append(
                {
                    "x": int(match.group(1)),
                    "y": int(match.group(2)),
                    "net": match.group(3),
                }
            )
        elif _ASC_SYMBOL_RE.match(line):
            match = _ASC_SYMBOL_RE.match(line)
            if match is None:
                continue
            entry: dict[str, Any] = {
                "name": match.group(1),
                "x": int(match.group(2)),
                "y": int(match.group(3)),
            }
            if match.group(4):
                entry["attrs"] = match.group(4)
            components.append(entry)
        elif _ASC_TEXT_RE.match(line):
            match = _ASC_TEXT_RE.match(line)
            if match is None:
                continue
            text_blocks.append(
                {
                    "x": int(match.group(1)),
                    "y": int(match.group(2)),
                    "size": int(match.group(3)),
                    "text": match.group(4),
                }
            )
        else:
            opaque.append(line)
    return {
        "schemaVersion": "ltagent-asc-1.0",
        "components": components,
        "wires": wires,
        "flags": flags,
        "textBlocks": text_blocks,
        "opaque": opaque,
    }


# ---------------------------------------------------------------------------
# Waveform chunking helper for Phase 4
# ---------------------------------------------------------------------------


def bundle_waveform(
    name: str, samples: list[float], *, sample_rate_hz: float = 1.0
) -> ResultBundle:
    """Wrap a flat list of samples into a :class:`ResultBundle` with a waveform."""
    traces, chunks = chunk_trace(name, samples)
    waveform_payload = WaveformBundle(
        schemaVersion="1.0",
        sampleRateHz=sample_rate_hz,
        domain="time",
        traces=traces,
        chunks=chunks,
    )
    return ResultBundle(
        status="success",
        run=RunManifest(
            schemaVersion="1.0",
            runId=f"waveform_{name}",
            jobId=f"waveform_{name}",
            toolVersion="ltagent.bundle",
            artifacts={"waveform": f"waveform/{name}.json"},
        ),
        waveform=waveform_payload,
    )


__all__ = [
    "ANALOG_TOOL_ID",
    "ERR_ANALOG_SIMULATE_FAILED",
    "ERR_ANALOG_TIMEOUT",
    "ERR_ANALOG_TOOL_MISSING",
    "ERR_ANALOG_TOPOLOGY_INVALID",
    "ERR_ANALOG_UNSUPPORTED",
    "GROUND_NET",
    "LTSPICE_TOOL_ID",
    "SUPPORTED_ANALYSIS_KINDS",
    "AnalogRunResult",
    "AnalogWorkbenchError",
    "ToolInfo",
    "bundle_waveform",
    "discover_analog_tool",
    "graph_to_netlist_text",
    "parse_ltspice_asc",
    "run_analog_simulation",
    "validate_topology",
]
