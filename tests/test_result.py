"""Unit tests for ``ltagent.result`` (Phase 4).

Covers:

* :func:`build_result_from_run` with a successful run + successful log.
* :func:`build_result_from_run` with a failed run; the result must not
  be marked ``success`` even if measurements are present.
* :func:`build_result_from_run` with parser errors; the parser findings
  must propagate to the result-level errors list.
* :func:`add_simulation_assertions` for both ``no log`` and ``clean
  log`` cases.
* :func:`assert_constraints` for the supported ``targetCutoffHz`` case
  and unknown constraint keys.
* :func:`write_result` / :func:`read_result` round-trip.
* :func:`compute_rc_cutoff_hz` for the textbook RC formula.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent import result as result_mod
from ltagent.log_parser import parse_log_text
from ltagent.result import (
    ASSERT_SIM_FINISHED,
    ASSERT_SIM_NO_ERRORS,
    RES_ERR_ASSERTION_FAILED,
    RES_ERR_RUN_NOT_ATTEMPTED,
    RES_ERR_UNSUPPORTED_CONSTRAINT,
    AssertionResult,
    FileMap,
    Result,
    RunInfo,
    add_simulation_assertions,
    assert_constraints,
    build_result_from_run,
    compute_rc_cutoff_hz,
    read_result,
    write_result,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def test_build_result_clean_run_clean_log(tmp_path: Path) -> None:
    log_text = (
        "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\n"
        "Elapsed time: 0.012 seconds.\n"
    )
    report = parse_log_text(log_text)
    run_payload = {
        "success": True,
        "exitCode": 0,
        "durationMs": 12,
        "timeoutSeconds": 30,
    }
    result = build_result_from_run(
        project_id="p1",
        run_payload=run_payload,
        parse_report=report,
    )

    assert result.project_id == "p1"
    assert result.run.attempted is True
    assert result.run.success is True
    assert result.run.exit_code == 0
    assert result.run.duration_ms == 12
    assert result.measurements["vout_max"] == pytest.approx(0.70710678)

    # The two always-on assertions should be present and passing.
    names = {a.name for a in result.assertions}
    assert ASSERT_SIM_NO_ERRORS in names
    assert ASSERT_SIM_FINISHED in names
    for a in result.assertions:
        assert a.passed is True, a
    assert result.success is True
    assert result.errors == []
    assert result.warnings == []


def test_build_result_failed_run_is_not_marked_success() -> None:
    log_text = (
        "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\n"
        "Fatal Error: convergence failure\n"
    )
    report = parse_log_text(log_text)
    run_payload = {
        "success": False,
        "exitCode": 1,
        "durationMs": 8000,
        "timeoutSeconds": 30,
    }
    result = build_result_from_run(
        project_id="p2",
        run_payload=run_payload,
        parse_report=report,
    )
    assert result.run.success is False
    # The simulation_has_no_errors assertion must fail because the
    # parser saw a fatal error.
    no_errors = next(
        a for a in result.assertions if a.name == ASSERT_SIM_NO_ERRORS
    )
    assert no_errors.passed is False
    # And the result-level success flag must be False.
    assert result.success is False
    # The fatal error must be in the errors list.
    codes = {e["code"] for e in result.errors}
    assert "LTSPICE_FATAL" in codes


def test_build_result_keeps_parser_warnings_distinct() -> None:
    log_text = (
        "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\n"
        "Warning: minor timestep adjustment\n"
        "Elapsed time: 0.01 seconds.\n"
    )
    report = parse_log_text(log_text)
    result = build_result_from_run(
        project_id="p3",
        run_payload={"success": True, "timeoutSeconds": 30, "durationMs": 10},
        parse_report=report,
    )
    assert result.success is True
    codes = {w["code"] for w in result.warnings}
    assert "LTSPICE_WARNING" in codes
    assert result.errors == []


def test_build_result_no_log_no_run() -> None:
    result = build_result_from_run(
        project_id="p4",
        run_payload=None,
        parse_report=None,
    )
    assert result.run.attempted is False
    assert result.run.success is False
    # The always-on assertions should fail with a "no log was parsed"
    # code so the consumer can tell.
    for a in result.assertions:
        assert a.passed is False
        assert a.code == RES_ERR_RUN_NOT_ATTEMPTED
    assert result.success is False


# ---------------------------------------------------------------------------
# Assertion engine
# ---------------------------------------------------------------------------


def test_assert_constraints_within_tolerance_passes() -> None:
    result = Result(project_id="x")
    result.measurements = {"fcut_meas": 1010.0}
    assert_constraints(result, "rc_lowpass", {"targetCutoffHz": 1000.0})
    a = result.assertions[0]
    assert a.name == "cutoff_within_tolerance"
    assert a.passed is True
    assert a.observed == pytest.approx(1010.0)
    assert a.expected == 1000.0


def test_assert_constraints_outside_tolerance_fails() -> None:
    result = Result(project_id="x")
    result.measurements = {"fcut_meas": 2000.0}
    assert_constraints(result, "rc_lowpass", {"targetCutoffHz": 1000.0})
    a = result.assertions[0]
    assert a.passed is False
    assert a.code == RES_ERR_ASSERTION_FAILED


def test_assert_constraints_missing_measurement_is_skipped() -> None:
    result = Result(project_id="x")
    assert_constraints(result, "rc_lowpass", {"targetCutoffHz": 1000.0})
    a = result.assertions[0]
    assert a.passed is True
    assert "not validated" in a.detail


def test_assert_constraints_unknown_key_is_warning() -> None:
    result = Result(project_id="x")
    assert_constraints(result, "rc_lowpass", {"madeUpKey": 42})
    assert any(
        w["code"] == RES_ERR_UNSUPPORTED_CONSTRAINT for w in result.warnings
    )
    assert result.success is True  # warnings don't fail the result


def test_assert_constraints_wrong_topology_warns() -> None:
    result = Result(project_id="x")
    assert_constraints(result, "voltage_divider", {"targetCutoffHz": 1000.0})
    assert any(
        w["code"] == RES_ERR_UNSUPPORTED_CONSTRAINT for w in result.warnings
    )


def test_assert_constraints_no_constraints_is_noop() -> None:
    result = Result(project_id="x")
    assert_constraints(result, "rc_lowpass", None)
    assert result.assertions == []


def test_add_simulation_assertions_no_report_fails() -> None:
    result = Result(project_id="x")
    add_simulation_assertions(result, None)
    assert len(result.assertions) == 2
    for a in result.assertions:
        assert a.passed is False
        assert a.code == RES_ERR_RUN_NOT_ATTEMPTED


def test_add_simulation_assertions_missing_trailer_warns() -> None:
    """A log without 'Elapsed time' is still considered run-attempted
    but not cleanly finished."""
    text = "vout_max: MAX(v(out))=0.5 FROM 0 TO 0.005\n"
    report = parse_log_text(text)
    result = Result(project_id="x")
    add_simulation_assertions(result, report)
    finished = next(
        a for a in result.assertions if a.name == ASSERT_SIM_FINISHED
    )
    assert finished.passed is False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


def test_result_success_requires_no_errors_and_passing_assertions() -> None:
    r = Result(project_id="x")
    # No errors, no assertions → still True because "all pass" is vacuous.
    assert r.success is True

    r.assertions.append(AssertionResult(name="x", passed=False))
    assert r.success is False


def test_result_to_dict_has_full_contract() -> None:
    r = Result(
        project_id="2026-06-17_rc_lowpass_1khz",
        files=FileMap(
            ir="circuit.ir.json",
            cir="circuit.cir",
            asc="circuit.asc",
            log="circuit.log",
            raw=None,
            result="result.json",
        ),
        run=RunInfo(attempted=True, success=True, timeout_seconds=30, duration_ms=812),
        measurements={"VOUT_MAX": 0.707},
        layout_score=92,
        layout_warnings=[],
        template_used="rc_lowpass",
    )
    r.assertions.append(AssertionResult(name="simulation_has_no_errors", passed=True))
    d = r.to_dict()
    assert d["schemaVersion"] == result_mod.RESULT_SCHEMA_VERSION
    assert d["success"] is True
    assert d["projectId"] == "2026-06-17_rc_lowpass_1khz"
    assert d["files"]["ir"] == "circuit.ir.json"
    assert d["run"]["attempted"] is True
    assert d["run"]["durationMs"] == 812
    assert d["measurements"]["VOUT_MAX"] == 0.707
    assert d["layout"]["score"] == 92
    assert d["template"]["used"] == "rc_lowpass"
    assert d["template"]["promoted"] is False
    assert d["warnings"] == []
    assert d["errors"] == []


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def test_write_and_read_result_round_trip(tmp_path: Path) -> None:
    r = Result(
        project_id="round-trip",
        run=RunInfo(attempted=True, success=True, duration_ms=42),
        measurements={"vout_max": 0.70710678},
    )
    r.assertions.append(AssertionResult(name="simulation_has_no_errors", passed=True))
    target = tmp_path / "deep" / "result.json"
    write_result(r, target)
    assert target.is_file()
    payload = read_result(target)
    assert payload["projectId"] == "round-trip"
    assert payload["measurements"]["vout_max"] == pytest.approx(0.70710678)
    assert payload["success"] is True
    # Must be pretty-printed (indent=2) so the file is diff-friendly.
    raw = target.read_text(encoding="utf-8")
    assert "\n  " in raw
    # Must end with a trailing newline.
    assert raw.endswith("\n")


def test_write_result_creates_parent_dirs(tmp_path: Path) -> None:
    r = Result(project_id="nested")
    target = tmp_path / "a" / "b" / "c" / "result.json"
    write_result(r, target)
    assert target.is_file()


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------


def test_compute_rc_cutoff_hz_1k_case() -> None:
    # R=1.59k, C=100n → ~1000 Hz
    f = compute_rc_cutoff_hz(1.59e3, 100e-9)
    assert f == pytest.approx(1000.0, rel=1e-3)


def test_compute_rc_cutoff_hz_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        compute_rc_cutoff_hz(0.0, 1e-6)
    with pytest.raises(ValueError):
        compute_rc_cutoff_hz(1e3, 0.0)
    with pytest.raises(ValueError):
        compute_rc_cutoff_hz(-1.0, 1e-6)


# ---------------------------------------------------------------------------
# End-to-end with a real fixture log
# ---------------------------------------------------------------------------


def test_build_result_end_to_end_with_fixture(tmp_path: Path) -> None:
    log_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "logs"
        / "rc_lowpass_tran_ok.log"
    )
    report = parse_log_text(log_path.read_text(encoding="utf-8"))
    run_payload = {
        "success": True,
        "exitCode": 0,
        "durationMs": 25,
        "timeoutSeconds": 30,
    }
    r = build_result_from_run(
        project_id="rc_lowpass_1khz",
        run_payload=run_payload,
        parse_report=report,
    )
    assert r.measurements["vout_max"] == pytest.approx(0.70710678)
    assert r.measurements["vout_min"] == pytest.approx(-0.70710678)
    out = tmp_path / "result.json"
    write_result(r, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["measurements"]["vout_max"] == pytest.approx(0.70710678)
    assert payload["success"] is True


def test_build_result_end_to_end_with_fatal_fixture(tmp_path: Path) -> None:
    log_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "logs"
        / "simulation_fatal.log"
    )
    report = parse_log_text(log_path.read_text(encoding="utf-8"))
    r = build_result_from_run(
        project_id="broken",
        run_payload={"success": False, "exitCode": 1, "timeoutSeconds": 30},
        parse_report=report,
    )
    # The result must not be marked successful.
    assert r.success is False
    # And it must include the fatal error code in the errors list.
    codes = {e["code"] for e in r.errors}
    assert "LTSPICE_FATAL" in codes
    out = tmp_path / "result.json"
    write_result(r, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert any(
        e["code"] == "LTSPICE_FATAL" for e in payload["errors"]
    )
