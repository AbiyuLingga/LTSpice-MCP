"""Job manifest, run manifest, and result bundle contracts.

The v2 engine exposes a job broker. Every simulator or AI
invocation is wrapped in a :class:`JobManifest`; every run
produces a :class:`RunManifest` plus a :class:`ResultBundle` that
lists the artifacts the caller can fetch via
``artifact.readSlice`` and ``artifact.read``.

The contracts are deliberately small: the v2 engine does not
invert a job broker protocol, it just emits structured records
that the desktop, CLI, engine, and MCP can render uniformly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

JOB_SCHEMA_VERSION: Final[str] = "1.0"
RUN_SCHEMA_VERSION: Final[str] = "1.0"
RESULT_SCHEMA_VERSION: Final[str] = "1.0"


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"


TERMINAL_JOB_STATES: Final[frozenset[JobState]] = frozenset(
    {
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.TIMED_OUT,
        JobState.SKIPPED,
        JobState.UNSUPPORTED,
    }
)


class JobKind(StrEnum):
    ANALOG_SIMULATE = "analog.simulate"
    DIGITAL_SIMULATE = "digital.simulate"
    DIGITAL_SYNTHESIZE = "digital.synthesize"
    LINT_NETLIST = "lint.netlist"
    AI_CONTEXT_PREVIEW = "ai.contextPreview"
    AI_PLAN = "ai.plan"
    AI_REPAIR = "ai.repair"


@dataclass(frozen=True)
class JobManifest:
    """A single in-flight or terminal job.

    The manifest is written under ``runs/<jobId>/job.json`` so the
    caller can poll status without re-running the simulator. The
    manifest is the source of truth for the job's state and the
    argv that ran; the actual artifact files live alongside it.
    """

    schemaVersion: str
    jobId: str
    kind: JobKind | str
    state: JobState
    projectRevision: int
    projectId: str
    toolId: str
    argv: tuple[str, ...]
    timeoutSeconds: float
    inputHash: str
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    runId: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schemaVersion,
            "jobId": self.jobId,
            "kind": self.kind.value if isinstance(self.kind, JobKind) else self.kind,
            "state": self.state.value,
            "projectRevision": self.projectRevision,
            "projectId": self.projectId,
            "toolId": self.toolId,
            "argv": list(self.argv),
            "timeoutSeconds": self.timeoutSeconds,
            "inputHash": self.inputHash,
            "createdAt": self.createdAt,
            "startedAt": self.startedAt,
            "finishedAt": self.finishedAt,
            "errorCode": self.errorCode,
            "errorMessage": self.errorMessage,
            "runId": self.runId,
        }


@dataclass(frozen=True)
class RunManifest:
    """The result of a single simulator / tool invocation.

    ``artifacts`` is a map of logical name to relative path inside
    ``runs/<jobId>/``. ``measurements`` is the structured list of
    .meas values (analog) or assertions (digital). ``assertions``
    is the optional list of named pass / fail checks. The
    manifest is the only thing the engine reports to the
    caller; the artifacts are fetched lazily via
    :func:`ltagent.artifact.read_artifact_slice`.
    """

    schemaVersion: str
    runId: str
    jobId: str
    toolVersion: str
    artifacts: dict[str, str] = field(default_factory=dict)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stdoutTail: str = ""
    stderrTail: str = ""
    createdAt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResultBundle:
    """The bundle a caller receives when a job completes.

    The bundle combines the :class:`RunManifest` with the parsed
    measurements and assertion results. ``status`` distinguishes
    success / failed / skipped / unsupported / timed-out so the
    caller's rendering layer can surface each case distinctly.
    """

    status: str
    run: RunManifest
    measurements: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    waveform: WaveformBundle | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run": self.run.to_dict(),
            "measurements": list(self.measurements),
            "assertions": list(self.assertions),
            "errors": list(self.errors),
            "waveform": self.waveform.to_dict() if self.waveform is not None else None,
        }


@dataclass(frozen=True)
class WaveformBundle:
    """A normalised waveform trace set.

    ``traces`` is a list of :class:`WaveformTrace` records. Each
    trace carries the signal name, the unit, the sample count,
    and the chunks that hold the actual samples. ``min`` and
    ``max`` carry the visible-range extrema so a chart can
    auto-scale without reading every sample.
    """

    schemaVersion: str
    sampleRateHz: float
    domain: str  # "time" or "frequency"
    traces: list[WaveformTrace]
    chunks: list[WaveformChunk] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schemaVersion,
            "sampleRateHz": self.sampleRateHz,
            "domain": self.domain,
            "traces": [t.to_dict() for t in self.traces],
            "chunks": [c.to_dict() for c in self.chunks],
        }


@dataclass(frozen=True)
class WaveformTrace:
    name: str
    unit: str
    sampleCount: int
    min: float
    max: float
    chunkRef: str  # relative path inside the run

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WaveformChunk:
    """A contiguous block of waveform samples for a single trace.

    Chunks are the unit of streaming. The caller requests
    ``(traceId, start, length)`` and the artifact helper returns
    the matching ``samples`` window. ``min`` / ``max`` are the
    extrema inside the chunk so the chart can render a min-max
    band without re-reading the samples.
    """

    traceName: str
    start: int
    length: int
    samples: tuple[float, ...]
    min: float
    max: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceName": self.traceName,
            "start": self.start,
            "length": self.length,
            "samples": list(self.samples),
            "min": self.min,
            "max": self.max,
        }


def utc_now_iso() -> str:
    from datetime import UTC

    return datetime.now(UTC).isoformat()


def chunk_trace(
    trace_name: str,
    samples: list[float],
    chunk_size: int = 1024,
) -> tuple[list[WaveformTrace], list[WaveformChunk]]:
    """Split ``samples`` into :class:`WaveformChunk` records.

    The function returns a single :class:`WaveformTrace` and a
    list of chunks; the helper also returns the global min / max
    so the chart can pick a y-axis without streaming the whole
    trace. The chunk count is ``ceil(len / chunk_size)``.
    """
    if not samples:
        return [
            WaveformTrace(
                name=trace_name,
                unit="V",
                sampleCount=0,
                min=0.0,
                max=0.0,
                chunkRef="",
            )
        ], []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    chunks: list[WaveformChunk] = []
    for start in range(0, len(samples), chunk_size):
        window = samples[start : start + chunk_size]
        chunks.append(
            WaveformChunk(
                traceName=trace_name,
                start=start,
                length=len(window),
                samples=tuple(window),
                min=min(window),
                max=max(window),
            )
        )
    trace = WaveformTrace(
        name=trace_name,
        unit="V",
        sampleCount=len(samples),
        min=min(samples),
        max=max(samples),
        chunkRef=f"waveform/{trace_name}.json",
    )
    return [trace], chunks


def read_chunk(
    chunks: list[WaveformChunk],
    *,
    start: int,
    length: int,
) -> list[float]:
    """Return the windowed samples starting at ``start`` for ``length`` samples.

    Mirrors the runtime ``artifact.readSlice`` contract: the
    caller requests a window, the helper stitches together
    whichever chunks cover it.
    """
    if start < 0 or length <= 0:
        return []
    end = start + length
    samples: list[float] = []
    for chunk in chunks:
        if chunk.start + chunk.length <= start:
            continue
        if chunk.start >= end:
            break
        offset = max(start - chunk.start, 0)
        available = min(end, chunk.start + chunk.length) - (chunk.start + offset)
        samples.extend(chunk.samples[offset : offset + available])
    return samples


__all__ = [
    "JOB_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "TERMINAL_JOB_STATES",
    "JobKind",
    "JobManifest",
    "JobState",
    "ResultBundle",
    "ResultBundle",
    "WaveformBundle",
    "WaveformChunk",
    "WaveformTrace",
    "chunk_trace",
    "read_chunk",
    "utc_now_iso",
]
