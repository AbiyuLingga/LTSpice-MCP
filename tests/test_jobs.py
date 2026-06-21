"""Tests for the job manifest, run manifest, and waveform contracts.

Covers:
* JobManifest round-trips through to_dict with the stable schema
  version.
* ResultBundle distinguishes success / failed / skipped / timed-out
  / unsupported so the rendering layer can branch correctly.
* Waveform chunking + the read_slice stitching helper.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ltagent.jobs import (
    JOB_SCHEMA_VERSION,
    JobBroker,
    JobBrokerError,
    JobKind,
    JobManifest,
    JobState,
    ResultBundle,
    RunManifest,
    WaveformBundle,
    chunk_trace,
    read_chunk,
    utc_now_iso,
)


def _job_manifest() -> JobManifest:
    return JobManifest(
        schemaVersion=JOB_SCHEMA_VERSION,
        jobId="job_001",
        kind=JobKind.ANALOG_SIMULATE,
        state=JobState.RUNNING,
        projectRevision=0,
        projectId="rc_lab",
        toolId="ngspice",
        argv=("/usr/bin/ngspice", "-b", "circuit.cir"),
        timeoutSeconds=30.0,
        inputHash="sha256:deadbeef",
        createdAt=utc_now_iso(),
    )


def test_job_manifest_round_trip() -> None:
    job = _job_manifest()
    payload = job.to_dict()
    assert payload["schemaVersion"] == JOB_SCHEMA_VERSION
    assert payload["state"] == "running"
    assert payload["kind"] == "analog.simulate"
    assert payload["argv"] == ["/usr/bin/ngspice", "-b", "circuit.cir"]
    # JSON-serialisable.
    assert json.dumps(payload) is not None


def test_run_manifest_round_trip() -> None:
    run = RunManifest(
        schemaVersion="1.0",
        runId="run_001",
        jobId="job_001",
        toolVersion="ngspice-37",
        artifacts={"netlist": "netlist.cir", "log": "run.log"},
        measurements=[{"name": "vout_max", "value": 5.0, "unit": "V"}],
        assertions=[{"name": "vout_within_band", "passed": True}],
        warnings=[],
        createdAt=utc_now_iso(),
    )
    payload = run.to_dict()
    assert payload["artifacts"]["netlist"] == "netlist.cir"
    assert payload["measurements"][0]["name"] == "vout_max"


def test_result_bundle_status_distinct() -> None:
    run = RunManifest(
        schemaVersion="1.0",
        runId="run_001",
        jobId="job_001",
        toolVersion="ngspice-37",
    )
    for status in ("success", "failed", "skipped", "unsupported", "timed_out"):
        bundle = ResultBundle(status=status, run=run, errors=[])
        assert bundle.to_dict()["status"] == status


def test_waveform_chunking_round_trip() -> None:
    samples = [0.1 * i for i in range(50)]
    traces, chunks = chunk_trace("vout", samples, chunk_size=16)
    assert len(traces) == 1
    assert traces[0].sampleCount == 50
    assert traces[0].min == 0.0
    assert traces[0].max == pytest.approx(4.9, abs=1e-9)
    # 50 samples / 16 per chunk -> 4 chunks (16 + 16 + 16 + 2)
    assert len(chunks) == 4
    assert chunks[0].start == 0
    assert chunks[-1].length == 2


def test_read_chunk_stitches_window() -> None:
    samples = list(range(50))
    _, chunks = chunk_trace("v", samples, chunk_size=16)
    window = read_chunk(chunks, start=10, length=20)
    assert window == list(range(10, 30))


def test_waveform_bundle_includes_chunk_metadata() -> None:
    samples = [0.0, 0.1, 0.2, 0.3]
    traces, chunks = chunk_trace("v", samples, chunk_size=4)
    bundle = WaveformBundle(
        schemaVersion="1.0",
        sampleRateHz=1.0,
        domain="time",
        traces=traces,
        chunks=chunks,
    )
    payload = bundle.to_dict()
    assert payload["traces"][0]["sampleCount"] == 4
    assert payload["chunks"][0]["min"] == 0.0
    assert payload["chunks"][0]["max"] == 0.3


def _wait_for_terminal(broker: JobBroker, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = broker.status(job_id)
        if status["state"] in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_broker_persists_lifecycle_and_artifact(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "rc_lab"
    project_dir.mkdir(parents=True)
    events: list[tuple[str, dict[str, object]]] = []
    broker = JobBroker(
        tmp_path / "projects", notify=lambda method, payload: events.append((method, payload))
    )

    def work(_cancel, run_dir: Path, progress):  # type: ignore[no-untyped-def]
        progress(50, "halfway")
        (run_dir / "output.txt").write_text("simulation complete", encoding="utf-8")
        return {"artifacts": {"output": "output.txt"}, "status": "success"}

    started = broker.start(
        project_id="rc_lab",
        project_revision=3,
        kind=JobKind.LINT_NETLIST,
        tool_id="internal",
        work=work,
    )
    status = _wait_for_terminal(broker, started["jobId"])

    assert status["state"] == "completed"
    assert (project_dir / "runs" / started["jobId"] / "job.json").is_file()
    assert (project_dir / "runs" / started["jobId"] / "result.json").is_file()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not any(name == "job.completed" for name, _ in events):
        time.sleep(0.01)
    assert [name for name, _ in events] == [
        "job.started",
        "job.progress",
        "job.artifact",
        "job.completed",
    ]
    artifact = broker.read_artifact_slice(started["jobId"], "output.txt", offset=0, limit=256)
    assert artifact["text"] == "simulation complete"
    broker.close()


def test_job_broker_cancels_and_rejects_artifact_escape(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "rc_lab"
    project_dir.mkdir(parents=True)
    broker = JobBroker(tmp_path / "projects")

    def work(cancel, _run_dir: Path, _progress):  # type: ignore[no-untyped-def]
        cancel.wait(1)
        return {"status": "success"}

    started = broker.start(
        project_id="rc_lab",
        project_revision=0,
        kind=JobKind.LINT_NETLIST,
        tool_id="internal",
        work=work,
    )
    assert broker.cancel(started["jobId"])["state"] == "cancelled"
    assert _wait_for_terminal(broker, started["jobId"])["state"] == "cancelled"
    with pytest.raises(JobBrokerError):
        broker.read_artifact_slice(started["jobId"], "../secret", offset=0, limit=10)
    broker.close()


def test_job_broker_recovers_terminal_manifest_after_restart(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    broker = JobBroker(tmp_path / "projects")
    started = broker.start(
        project_id="demo",
        project_revision=2,
        kind=JobKind.ANALOG_SIMULATE,
        tool_id="ngspice",
        work=lambda _cancel, _run_dir, _progress: {"status": "success"},
    )
    job_id = str(started["jobId"])
    assert _wait_for_terminal(broker, job_id)["state"] == "completed"
    broker.close()

    recovered = JobBroker(tmp_path / "projects")
    assert recovered.status(job_id)["state"] == "completed"
    recovered.close()
