"""Unit tests for ``ltagent.live.verification`` and ``ltagent.live.sim_loop``.

The two modules share the same shape (pure function over a numeric
input) so they are co-located in a single test module. The test
scenarios the task specification calls out are all covered:

* near-target pass / fail (``check_near_target``)
* ripple bound check (``check_max`` / ``check_min``)
* aggregate confidence (``aggregate_verification``)
* fake-runner success (``run_and_verify`` with a healthy log)
* fake-runner failure (``run_and_verify`` with a failed run and a
  raising adapter)

In addition we cover:

* per-check code / detail messages (structured, not print-style);
* edge cases: ``None``, non-finite floats, swapped bounds, zero
  target;
* the ``sim_loop`` short-circuit paths (runner raised, run failed,
  no log produced, log has fatal finding);
* the deterministic, JSON-serialisable contract of
  :meth:`VerificationResult.to_dict` and
  :meth:`SimLoopReport.to_dict`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ltagent.live.sim_loop import (
    CODE_LOG_FATAL,
    CODE_LOG_MISSING,
    CODE_RUNNER_RAISED,
    LogParseOutcome,
    RunPayload,
    SimLoopError,
    SimLoopReport,
    fake_runner,
    fake_runner_raising,
    run_and_verify,
)
from ltagent.live.verification import (
    CODE_ACTUAL_INVALID,
    CODE_ACTUAL_MISSING,
    CODE_BOUND_INVALID,
    CODE_OK,
    CODE_TARGET_MISSING,
    CODE_TOLERANCE_INVALID,
    CheckKind,
    aggregate_verification,
    check_max,
    check_min,
    check_near_target,
)

# ---------------------------------------------------------------------------
# check_near_target
# ---------------------------------------------------------------------------


class TestCheckNearTarget:
    """``check_near_target`` covers most of the verification surface."""

    def test_pass_within_tolerance(self) -> None:
        c = check_near_target(995, 1000, 2.0, name="fc")
        assert c.passed is True
        assert c.code == CODE_OK
        assert c.kind is CheckKind.NEAR_TARGET
        assert c.actual == pytest.approx(995.0)
        assert c.target == pytest.approx(1000.0)
        assert c.tolerance_percent == pytest.approx(2.0)
        assert "0.5000%" in c.detail

    def test_fail_outside_tolerance(self) -> None:
        c = check_near_target(900, 1000, 2.0, name="fc")
        assert c.passed is False
        assert c.code == CODE_OK
        assert "10.0000%" in c.detail

    def test_boundary_inclusive(self) -> None:
        # 5% tolerance around 1000 -> bound is [950, 1050]. 950 is
        # exactly on the boundary, must pass.
        c = check_near_target(950, 1000, 5.0, name="fc")
        assert c.passed is True
        c = check_near_target(1050, 1000, 5.0, name="fc")
        assert c.passed is True

    def test_negative_target_uses_absolute_value(self) -> None:
        # A tolerance around a negative target is still meaningful
        # (it is the magnitude that matters). 5% around -1000 ->
        # bound is [-1050, -950].
        c = check_near_target(-995, -1000, 2.0, name="vneg")
        assert c.passed is True
        c = check_near_target(-900, -1000, 2.0, name="vneg")
        assert c.passed is False

    def test_zero_target_falls_back_to_absolute_error(self) -> None:
        c = check_near_target(0.0, 0.0, 5.0, name="zero")
        assert c.passed is True
        c = check_near_target(1e-9, 0.0, 5.0, name="zero")
        assert c.passed is False

    def test_missing_actual(self) -> None:
        c = check_near_target(None, 1000, 5.0, name="fc")
        assert c.passed is False
        assert c.code == CODE_ACTUAL_MISSING
        assert c.actual is None

    def test_nan_actual_is_invalid(self) -> None:
        c = check_near_target(float("nan"), 1000, 5.0, name="fc")
        assert c.passed is False
        assert c.code == CODE_ACTUAL_INVALID
        assert c.actual is None

    def test_inf_actual_is_invalid(self) -> None:
        c = check_near_target(float("inf"), 1000, 5.0, name="fc")
        assert c.passed is False
        assert c.code == CODE_ACTUAL_INVALID
        assert c.actual is None

    def test_missing_target(self) -> None:
        c = check_near_target(1000, None, 5.0, name="fc")
        assert c.passed is False
        assert c.code == CODE_TARGET_MISSING

    def test_negative_tolerance(self) -> None:
        c = check_near_target(1000, 1000, -1.0, name="fc")
        assert c.passed is False
        assert c.code == CODE_TOLERANCE_INVALID

    def test_string_inputs_are_coerced(self) -> None:
        c = check_near_target("995", "1000", "2.0", name="fc")
        assert c.passed is True
        assert c.actual == pytest.approx(995.0)

    def test_bool_inputs_are_rejected(self) -> None:
        # bool is a subclass of int; we explicitly reject it to
        # avoid silent True -> 1.0 coercion.
        c = check_near_target(True, 1000, 5.0, name="fc")
        assert c.code == CODE_ACTUAL_MISSING

    def test_dict_is_serialisable(self) -> None:
        c = check_near_target(995, 1000, 2.0, name="fc")
        # to_dict() must produce a plain dict suitable for json.dumps
        # without going through ltagent.serialization.to_jsonable.
        d = c.to_dict()
        json.dumps(d)  # must not raise

    def test_custom_name_is_preserved(self) -> None:
        c = check_near_target(995, 1000, 2.0, name="cutoff_1k")
        assert c.name == "cutoff_1k"


# ---------------------------------------------------------------------------
# check_max
# ---------------------------------------------------------------------------


class TestCheckMax:
    """One-sided upper bound."""

    def test_pass_at_boundary(self) -> None:
        c = check_max(0.5, 0.5, name="ripple")
        assert c.passed is True
        assert c.bound == pytest.approx(0.5)

    def test_fail_above(self) -> None:
        c = check_max(0.51, 0.5, name="ripple")
        assert c.passed is False
        assert c.code == CODE_OK
        assert "exceeds bound" in c.detail

    def test_missing_actual(self) -> None:
        c = check_max(None, 0.5, name="ripple")
        assert c.passed is False
        assert c.code == CODE_ACTUAL_MISSING

    def test_nan_actual(self) -> None:
        c = check_max(float("nan"), 0.5, name="ripple")
        assert c.code == CODE_ACTUAL_INVALID

    def test_missing_bound(self) -> None:
        c = check_max(0.5, None, name="ripple")
        assert c.passed is False
        assert c.code == CODE_BOUND_INVALID

    def test_negative_actual_is_a_normal_number(self) -> None:
        # Ripple is usually positive, but a negative "max" check is
        # a valid request (e.g. "output must be at most -1 V").
        c = check_max(-2.0, -1.0, name="neg_rail")
        assert c.passed is True
        c = check_max(0.0, -1.0, name="neg_rail")
        assert c.passed is False


# ---------------------------------------------------------------------------
# check_min
# ---------------------------------------------------------------------------


class TestCheckMin:
    """One-sided lower bound."""

    def test_pass_at_boundary(self) -> None:
        c = check_min(2.0, 2.0, name="vout_min")
        assert c.passed is True

    def test_fail_below(self) -> None:
        c = check_min(1.9, 2.0, name="vout_min")
        assert c.passed is False
        assert "below bound" in c.detail

    def test_missing_actual(self) -> None:
        c = check_min(None, 2.0, name="vout_min")
        assert c.code == CODE_ACTUAL_MISSING

    def test_missing_bound(self) -> None:
        c = check_min(2.0, None, name="vout_min")
        assert c.code == CODE_BOUND_INVALID

    def test_inf_actual(self) -> None:
        # Non-finite actuals are always rejected as ACTUAL_INVALID;
        # we do not want a stray inf to silently pass a min check.
        c = check_min(float("inf"), 2.0, name="vout_min")
        assert c.passed is False
        assert c.code == CODE_ACTUAL_INVALID
        c = check_min(float("-inf"), 2.0, name="vout_min")
        assert c.code == CODE_ACTUAL_INVALID


# ---------------------------------------------------------------------------
# aggregate_verification
# ---------------------------------------------------------------------------


class TestAggregateVerification:
    """Aggregation rules and confidence scoring."""

    def test_all_pass_confidence_one(self) -> None:
        checks = [
            check_near_target(1000, 1000, 1.0, name="a"),
            check_max(0.4, 0.5, name="b"),
            check_min(2.0, 2.0, name="c"),
        ]
        r = aggregate_verification(checks)
        assert r.overall_passed is True
        assert r.confidence == pytest.approx(1.0)
        assert r.reason_codes == []

    def test_some_fail_confidence_partial(self) -> None:
        checks = [
            check_near_target(1000, 1000, 1.0, name="a"),
            check_max(0.6, 0.5, name="b"),
        ]
        r = aggregate_verification(checks)
        assert r.overall_passed is False
        # 1/2 passes = 0.5
        assert r.confidence == pytest.approx(0.5)

    def test_missing_actual_penalises_confidence(self) -> None:
        checks = [
            check_near_target(1000, 1000, 1.0, name="a"),
            check_near_target(None, 1000, 5.0, name="b"),
        ]
        r = aggregate_verification(checks)
        assert r.overall_passed is False
        # 1 pass - 1 missing-actual penalty (0.25) = 0.25
        assert r.confidence == pytest.approx(0.25)
        assert CODE_ACTUAL_MISSING in r.reason_codes

    def test_empty_input_is_a_clean_pass(self) -> None:
        r = aggregate_verification([])
        assert r.overall_passed is True
        assert r.confidence == pytest.approx(1.0)
        assert r.reason_codes == []
        assert r.checks == []

    def test_all_fail_confidence_zero(self) -> None:
        checks = [
            check_max(0.6, 0.5, name="a"),
            check_max(0.7, 0.5, name="b"),
        ]
        r = aggregate_verification(checks)
        assert r.confidence == pytest.approx(0.0)

    def test_result_is_jsonable(self) -> None:
        checks = [check_near_target(995, 1000, 2.0, name="a")]
        r = aggregate_verification(checks)
        # to_dict() must round-trip through json.dumps
        payload = r.to_dict()
        json.dumps(payload)
        assert payload["overallPassed"] is True
        assert "checks" in payload
        assert "confidence" in payload
        assert "reasonCodes" in payload

    def test_confidence_is_step_bounded(self) -> None:
        # 3 checks: 2 pass, 1 fail (no missing). 2/3 ~ 0.667,
        # snapped to nearest 0.25 step -> 0.75.
        checks = [
            check_near_target(1000, 1000, 1.0, name="a"),
            check_near_target(1000, 1000, 1.0, name="b"),
            check_max(0.6, 0.5, name="c"),
        ]
        r = aggregate_verification(checks)
        assert r.confidence == pytest.approx(0.75)

    def test_iterable_input_accepted(self) -> None:
        # aggregate_verification must accept any Iterable, not just
        # list. Use a generator to prove it.
        def _gen() -> Any:
            yield check_near_target(1000, 1000, 1.0, name="a")
            yield check_max(0.4, 0.5, name="b")

        r = aggregate_verification(_gen())
        assert r.overall_passed is True


# ---------------------------------------------------------------------------
# run_and_verify with fake_runner success
# ---------------------------------------------------------------------------


# A small, representative log fragment. The shape matches what
# :mod:`ltagent.log_parser` actually accepts in production.
_OK_LOG = (
    "Circuit: RC low-pass\n"
    "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\n"
    "vout_min: MIN(v(out))=-0.70710678 FROM 0 TO 0.005\n"
    "Elapsed time: 0.1 seconds\n"
)


class TestRunAndVerifySuccess:
    """The healthy path: log has measurements, checks bind, everything passes."""

    def test_fake_runner_success_binds_measurements(self) -> None:
        adapter = fake_runner(_OK_LOG)
        checks = [
            check_near_target(0.0, 0.7071, 1.0, name="vout_max"),
            check_near_target(0.0, -0.7071, 1.0, name="vout_min"),
        ]
        report = run_and_verify({}, adapter, checks)

        assert isinstance(report, SimLoopReport)
        assert report.run_payload.success is True
        assert report.parse_outcome is not None
        assert report.parse_outcome.measurements["vout_max"] == pytest.approx(0.70710678)
        # The first check passed: vout_max actual ~ 0.7071 is within
        # 1% of 0.7071 (== 0.70710678 is well within 0.7% error).
        assert report.result.checks[0].passed is True
        assert report.result.checks[0].actual == pytest.approx(0.70710678)
        assert report.result.checks[1].passed is True
        assert report.result.overall_passed is True
        assert report.result.confidence == pytest.approx(1.0)

    def test_missing_measurement_fails_the_check(self) -> None:
        adapter = fake_runner(_OK_LOG)
        # 'ripple' is not in the log -> should be rewritten as
        # ACTUAL_MISSING and fail.
        checks = [check_max(0.0, 0.5, name="ripple")]
        report = run_and_verify({}, adapter, checks)
        assert report.result.overall_passed is False
        assert report.result.checks[0].actual is None
        assert report.result.checks[0].code == CODE_ACTUAL_MISSING
        assert report.result.checks[0].passed is False
        assert CODE_ACTUAL_MISSING in report.result.reason_codes

    def test_run_payload_survives_round_trip(self) -> None:
        adapter = fake_runner(_OK_LOG, duration_ms=42, exit_code=0)
        report = run_and_verify({}, adapter, [check_near_target(0, 1, 5, name="x")])
        d = report.to_dict()
        json.dumps(d)
        # RunPayload.to_dict() uses snake_case (dataclass asdict).
        # Downstream consumers that want camelCase can translate;
        # the verification block is already in camelCase.
        assert d["run"]["duration_ms"] == 42
        assert d["run"]["exit_code"] == 0

    def test_log_fatal_finding_reported_as_reason_code(self) -> None:
        fatal_log = (
            "vout_max: MAX(v(out))=0.70710678\n"
            "Fatal Error: Singular matrix: foo\n"
            "Elapsed time: 0.1 seconds\n"
        )
        adapter = fake_runner(fatal_log)
        report = run_and_verify(
            {},
            adapter,
            [check_near_target(0.0, 0.7071, 1.0, name="vout_max")],
        )
        assert CODE_LOG_FATAL in report.reason_codes
        # The fatal is *additional* information; the per-check
        # verdict on the still-present measurement is unaffected.
        assert report.result.checks[0].passed is True

    def test_empty_checks_raises(self) -> None:
        adapter = fake_runner(_OK_LOG)
        with pytest.raises(SimLoopError, match="at least one"):
            run_and_verify({}, adapter, [])

    def test_duplicate_check_names_raise(self) -> None:
        adapter = fake_runner(_OK_LOG)
        with pytest.raises(SimLoopError, match="duplicate check name"):
            run_and_verify(
                {},
                adapter,
                [
                    check_near_target(0, 1, 5, name="dup"),
                    check_near_target(0, 2, 5, name="dup"),
                ],
            )

    def test_non_payload_return_raises_type_error(self) -> None:
        def _bad(project: dict[str, Any]) -> str:  # type: ignore[return-value]
            return "not a payload"

        with pytest.raises(TypeError, match="RunPayload"):
            run_and_verify({}, _bad, [check_near_target(0, 1, 5, name="x")])


# ---------------------------------------------------------------------------
# run_and_verify with fake_runner failure
# ---------------------------------------------------------------------------


class TestRunAndVerifyFailure:
    """Failure paths: the orchestrator must stay total."""

    def test_fake_runner_raising_exception(self) -> None:
        adapter = fake_runner_raising(RuntimeError("boom"))
        report = run_and_verify(
            {},
            adapter,
            [check_near_target(0.0, 1.0, 5.0, name="x")],
        )
        assert report.run_payload.success is False
        assert report.run_payload.error is not None
        assert report.run_payload.error["code"] == CODE_RUNNER_RAISED
        assert CODE_RUNNER_RAISED in report.reason_codes
        # No log was parsed; the check was rewritten as
        # ACTUAL_MISSING.
        assert report.parse_outcome is None
        assert report.result.checks[0].code == CODE_ACTUAL_MISSING
        assert report.result.overall_passed is False

    def test_run_reported_as_failed(self) -> None:
        payload = RunPayload(
            success=False,
            error={"code": "LTSPICE_TIMEOUT", "detail": "timed out"},
        )

        def _adapter(project: dict[str, Any]) -> RunPayload:
            return payload

        report = run_and_verify(
            {},
            _adapter,
            [check_near_target(0.0, 1.0, 5.0, name="x")],
        )
        assert report.run_payload.success is False
        # The runner's error code is surfaced in the report's
        # reason_codes so downstream consumers can switch on it.
        assert "LTSPICE_TIMEOUT" in report.reason_codes
        assert report.result.checks[0].code == CODE_ACTUAL_MISSING

    def test_log_missing_path(self) -> None:
        # success=True but neither log_text nor log_path: a malformed
        # payload that real runners should never produce, but the
        # orchestrator must handle it.
        payload = RunPayload(success=True, log_path=None, log_text=None)

        def _adapter(project: dict[str, Any]) -> RunPayload:
            return payload

        report = run_and_verify(
            {},
            _adapter,
            [check_near_target(0.0, 1.0, 5.0, name="x")],
        )
        assert CODE_LOG_MISSING in report.reason_codes
        assert report.parse_outcome is None
        assert report.result.checks[0].code == CODE_ACTUAL_MISSING

    def test_custom_log_parser(self) -> None:
        # When the caller injects a parser, the runner's log_text is
        # ignored. This is the "already-parsed" fast path.
        def _parser(log_path: str) -> LogParseOutcome:
            return LogParseOutcome(
                measurements={"custom_meas": 1.234},
                has_fatal=False,
                simulation_finished=True,
            )

        adapter = fake_runner(_OK_LOG)
        checks = [check_near_target(0.0, 1.234, 1.0, name="custom_meas")]
        report = run_and_verify({}, adapter, checks, log_parser_func=_parser)
        assert report.result.overall_passed is True
        assert report.result.checks[0].actual == pytest.approx(1.234)


# ---------------------------------------------------------------------------
# Result shape contracts
# ---------------------------------------------------------------------------


class TestShapeContracts:
    """Stable JSON shape, used by MCP and CLI consumers."""

    def test_check_to_dict_shape(self) -> None:
        c = check_near_target(995, 1000, 2.0, name="fc")
        d = c.to_dict()
        assert set(d.keys()) == {
            "name",
            "kind",
            "actual",
            "target",
            "bound",
            "tolerance_percent",
            "passed",
            "code",
            "detail",
        }

    def test_verification_result_to_dict_shape(self) -> None:
        r = aggregate_verification([check_near_target(995, 1000, 2.0, name="fc")])
        d = r.to_dict()
        assert set(d.keys()) == {
            "checks",
            "overallPassed",
            "confidence",
            "reasonCodes",
        }

    def test_sim_loop_report_to_dict_shape(self) -> None:
        adapter = fake_runner(_OK_LOG)
        report = run_and_verify(
            {},
            adapter,
            [check_near_target(0.0, 0.7071, 1.0, name="vout_max")],
        )
        d = report.to_dict()
        assert set(d.keys()) == {
            "run",
            "parse",
            "verification",
            "reasonCodes",
        }
        # Run payload contract.
        assert set(d["run"].keys()) == {
            "success",
            "log_path",
            "log_text",
            "duration_ms",
            "exit_code",
            "error",
        }


# ---------------------------------------------------------------------------
# Determinism and purity
# ---------------------------------------------------------------------------


class TestDeterminism:
    """The API is deterministic: same input -> same output."""

    def test_aggregate_is_deterministic(self) -> None:
        checks = [
            check_near_target(995, 1000, 2.0, name="a"),
            check_max(0.4, 0.5, name="b"),
            check_min(2.0, 1.0, name="c"),
        ]
        r1 = aggregate_verification(checks)
        r2 = aggregate_verification(list(checks))
        assert r1.to_dict() == r2.to_dict()

    def test_run_and_verify_is_pure(self) -> None:
        adapter = fake_runner(_OK_LOG)
        checks = [check_near_target(0.0, 0.7071, 1.0, name="vout_max")]
        a = run_and_verify({}, adapter, checks)
        b = run_and_verify({}, adapter, checks)
        # The log_path in the payload is a /tmp/<random> file but
        # the *values* in the dict are deterministic.
        d_a = a.to_dict()
        d_b = b.to_dict()
        d_a["run"].pop("log_path", None)
        d_b["run"].pop("log_path", None)
        d_a["run"].pop("log_text", None)
        d_b["run"].pop("log_text", None)
        assert d_a == d_b


# ---------------------------------------------------------------------------
# Smoke: real edge case from the plan
# ---------------------------------------------------------------------------


class TestPlanScenarios:
    """Scenarios lifted directly from the plan section 17."""

    def test_rc_lowpass_cutoff_within_2pct(self) -> None:
        # Plan section 17.1: cutoff_frequency check with target
        # 1000 Hz, predicted 994.7 Hz, 2% tolerance. The log
        # actually carries a frequency-derived value; here we use
        # a 994.7 Hz measurement named after the check so the
        # orchestrator can bind it.
        log = "cutoff_frequency: FIND(fc)=994.7\nElapsed time: 0.1s\n"
        adapter = fake_runner(log)
        checks = [check_near_target(0.0, 1000.0, 2.0, name="cutoff_frequency")]
        report = run_and_verify({}, adapter, checks)
        assert report.result.overall_passed is True
        assert report.result.checks[0].passed is True
        assert report.result.checks[0].actual == pytest.approx(994.7)
        assert report.result.confidence == pytest.approx(1.0)
