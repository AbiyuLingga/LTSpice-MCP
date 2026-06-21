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

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock
from typing import Any, Final
from uuid import uuid4

from .security import PathSafetyError, safe_resolve_under

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


class JobBrokerError(ValueError):
    """Stable error raised by the local job boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


JobProgress = Callable[[int, str], None]
JobWork = Callable[[Event, Path, JobProgress], Mapping[str, Any]]
JobNotifier = Callable[[str, dict[str, object]], None]


class JobBroker:
    """Two-worker, file-backed broker for one local user."""

    max_artifact_slice: Final[int] = 256 * 1024
    _job_id_pattern: Final[re.Pattern[str]] = re.compile(r"^job_[0-9a-f]{32}$")

    def __init__(
        self,
        projects_root: Path | str,
        *,
        notify: JobNotifier | None = None,
        max_workers: int = 2,
    ) -> None:
        self.projects_root = Path(projects_root).expanduser().resolve(strict=False)
        self.notify = notify or (lambda _method, _payload: None)
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ltagent-job"
        )
        self.lock = Lock()
        self.manifests: dict[str, JobManifest] = {}
        self.cancel_events: dict[str, Event] = {}
        self.run_dirs: dict[str, Path] = {}

    def start(
        self,
        *,
        project_id: str,
        project_revision: int,
        kind: JobKind | str,
        tool_id: str,
        work: JobWork,
        argv: tuple[str, ...] = (),
        timeout_seconds: float = 60.0,
    ) -> dict[str, object]:
        try:
            project_dir = safe_resolve_under(
                self.projects_root / project_id, self.projects_root, must_exist=True
            )
        except PathSafetyError as exc:
            raise JobBrokerError("JOB_PROJECT_INVALID", exc.message) from exc
        job_id = f"job_{uuid4().hex}"
        run_dir = project_dir / "runs" / job_id
        run_dir.mkdir(parents=True, exist_ok=False)
        created = utc_now_iso()
        digest = hashlib.sha256(
            f"{project_id}:{project_revision}:{kind}:{created}".encode()
        ).hexdigest()
        manifest = JobManifest(
            schemaVersion=JOB_SCHEMA_VERSION,
            jobId=job_id,
            kind=kind,
            state=JobState.PENDING,
            projectRevision=project_revision,
            projectId=project_id,
            toolId=tool_id,
            argv=argv,
            timeoutSeconds=timeout_seconds,
            inputHash=f"sha256:{digest}",
            createdAt=created,
        )
        with self.lock:
            self.manifests[job_id] = manifest
            self.cancel_events[job_id] = Event()
            self.run_dirs[job_id] = run_dir
        self._persist_manifest(manifest, run_dir)
        self.executor.submit(self._run, job_id, work)
        return {"jobId": job_id, "state": manifest.state.value}

    def _run(self, job_id: str, work: JobWork) -> None:
        cancel = self.cancel_events[job_id]
        if cancel.is_set():
            return
        self._update(job_id, JobState.RUNNING, startedAt=utc_now_iso())
        self.notify("job.started", self.status(job_id))

        def progress(percent: int, message: str) -> None:
            if not cancel.is_set():
                self.notify(
                    "job.progress",
                    {"jobId": job_id, "message": message, "percent": max(0, min(100, percent))},
                )

        try:
            result = dict(work(cancel, self.run_dirs[job_id], progress))
            if cancel.is_set():
                return
            self._write_json_atomic(self.run_dirs[job_id] / "result.json", result)
            artifacts = _result_artifacts(result)
            if isinstance(artifacts, Mapping):
                for name, path in artifacts.items():
                    self.notify(
                        "job.artifact",
                        {"jobId": job_id, "name": str(name), "path": str(path)},
                    )
            state = _result_state(result)
            self._update(
                job_id,
                state,
                finishedAt=utc_now_iso(),
                runId=job_id,
            )
            event = {
                JobState.FAILED: "job.failed",
                JobState.SKIPPED: "job.failed",
                JobState.TIMED_OUT: "job.failed",
                JobState.UNSUPPORTED: "job.failed",
            }.get(state, "job.completed")
            self.notify(event, self.status(job_id))
        except Exception as exc:  # pragma: no cover - exact worker failures vary
            if cancel.is_set():
                return
            self._update(
                job_id,
                JobState.FAILED,
                finishedAt=utc_now_iso(),
                errorCode="JOB_FAILED",
                errorMessage=str(exc),
            )
            self.notify("job.failed", self.status(job_id))

    def _update(self, job_id: str, state: JobState, **changes: Any) -> JobManifest:
        with self.lock:
            current = self._manifest(job_id)
            updated = replace(current, state=state, **changes)
            self.manifests[job_id] = updated
            run_dir = self.run_dirs[job_id]
        self._persist_manifest(updated, run_dir)
        return updated

    def status(self, job_id: str) -> dict[str, object]:
        with self.lock:
            if job_id not in self.manifests:
                self._recover(job_id)
            manifest = self._manifest(job_id)
            run_dir = self.run_dirs[job_id]
        payload = manifest.to_dict()
        result_path = run_dir / "result.json"
        if result_path.is_file():
            payload["result"] = json.loads(result_path.read_text(encoding="utf-8"))
        return payload

    def cancel(self, job_id: str) -> dict[str, object]:
        with self.lock:
            if job_id not in self.manifests:
                self._recover(job_id)
            manifest = self._manifest(job_id)
            cancel = self.cancel_events[job_id]
        if manifest.state in TERMINAL_JOB_STATES:
            return manifest.to_dict()
        cancel.set()
        updated = self._update(job_id, JobState.CANCELLED, finishedAt=utc_now_iso())
        payload = updated.to_dict()
        self.notify("job.cancelled", payload)
        return payload

    def read_artifact_slice(
        self,
        job_id: str,
        artifact: str,
        *,
        offset: int,
        limit: int,
    ) -> dict[str, object]:
        if offset < 0 or not 1 <= limit <= self.max_artifact_slice:
            raise JobBrokerError("ARTIFACT_RANGE_INVALID", "artifact slice range is invalid")
        with self.lock:
            run_dir = self.run_dirs.get(job_id)
        if run_dir is None:
            raise JobBrokerError("JOB_NOT_FOUND", f"job {job_id!r} was not found")
        try:
            path = safe_resolve_under(run_dir / artifact, run_dir, must_exist=True)
        except PathSafetyError as exc:
            raise JobBrokerError("ARTIFACT_PATH_INVALID", exc.message) from exc
        if not path.is_file():
            raise JobBrokerError("ARTIFACT_NOT_FOUND", f"artifact {artifact!r} is not a file")
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        return {
            "base64": base64.b64encode(data).decode("ascii"),
            "eof": offset + len(data) >= path.stat().st_size,
            "jobId": job_id,
            "length": len(data),
            "offset": offset,
            "text": text,
        }

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _manifest(self, job_id: str) -> JobManifest:
        manifest = self.manifests.get(job_id)
        if manifest is None:
            raise JobBrokerError("JOB_NOT_FOUND", f"job {job_id!r} was not found")
        return manifest

    def _recover(self, job_id: str) -> None:
        if not self._job_id_pattern.fullmatch(job_id):
            raise JobBrokerError("JOB_NOT_FOUND", f"job {job_id!r} was not found")
        for project_dir in self.projects_root.iterdir():
            manifest_path = project_dir / "runs" / job_id / "job.json"
            if not manifest_path.is_file():
                continue
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = _manifest_from_dict(payload)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                raise JobBrokerError(
                    "JOB_MANIFEST_INVALID", f"job {job_id!r} has an invalid manifest"
                ) from exc
            if manifest.jobId != job_id:
                raise JobBrokerError(
                    "JOB_MANIFEST_INVALID", f"job {job_id!r} manifest id does not match"
                )
            run_dir = manifest_path.parent.resolve(strict=True)
            if manifest.state not in TERMINAL_JOB_STATES:
                manifest = replace(
                    manifest,
                    state=JobState.FAILED,
                    finishedAt=utc_now_iso(),
                    errorCode="JOB_INTERRUPTED",
                    errorMessage="engine stopped before the job reached a terminal state",
                )
                self._persist_manifest(manifest, run_dir)
            self.manifests[job_id] = manifest
            self.cancel_events[job_id] = Event()
            self.run_dirs[job_id] = run_dir
            return
        raise JobBrokerError("JOB_NOT_FOUND", f"job {job_id!r} was not found")

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _persist_manifest(self, manifest: JobManifest, run_dir: Path) -> None:
        self._write_json_atomic(run_dir / "job.json", manifest.to_dict())


def _result_state(result: Mapping[str, Any]) -> JobState:
    return {
        "failed": JobState.FAILED,
        "cancelled": JobState.CANCELLED,
        "skipped": JobState.SKIPPED,
        "timed_out": JobState.TIMED_OUT,
        "unsupported": JobState.UNSUPPORTED,
    }.get(str(result.get("status")), JobState.COMPLETED)


def _result_artifacts(result: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = result.get("artifacts")
    if isinstance(direct, Mapping):
        return direct
    run = result.get("run")
    if isinstance(run, Mapping):
        nested = run.get("artifacts")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _manifest_from_dict(payload: Mapping[str, Any]) -> JobManifest:
    raw_kind = str(payload["kind"])
    try:
        kind: JobKind | str = JobKind(raw_kind)
    except ValueError:
        kind = raw_kind
    return JobManifest(
        schemaVersion=str(payload["schemaVersion"]),
        jobId=str(payload["jobId"]),
        kind=kind,
        state=JobState(str(payload["state"])),
        projectRevision=int(payload["projectRevision"]),
        projectId=str(payload["projectId"]),
        toolId=str(payload["toolId"]),
        argv=tuple(str(item) for item in payload.get("argv", [])),
        timeoutSeconds=float(payload["timeoutSeconds"]),
        inputHash=str(payload["inputHash"]),
        createdAt=str(payload["createdAt"]),
        startedAt=_optional_manifest_string(payload.get("startedAt")),
        finishedAt=_optional_manifest_string(payload.get("finishedAt")),
        errorCode=_optional_manifest_string(payload.get("errorCode")),
        errorMessage=_optional_manifest_string(payload.get("errorMessage")),
        runId=_optional_manifest_string(payload.get("runId")),
    )


def _optional_manifest_string(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "JOB_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "TERMINAL_JOB_STATES",
    "JobBroker",
    "JobBrokerError",
    "JobKind",
    "JobManifest",
    "JobState",
    "ResultBundle",
    "WaveformBundle",
    "WaveformChunk",
    "WaveformTrace",
    "chunk_trace",
    "read_chunk",
    "utc_now_iso",
]
