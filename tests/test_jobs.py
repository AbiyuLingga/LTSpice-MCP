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

import pytest

from ltagent.jobs import (
    JOB_SCHEMA_VERSION,
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
