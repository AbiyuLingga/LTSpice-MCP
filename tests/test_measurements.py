"""Unit tests for ``ltagent.live.measurements`` and
``ltagent.math_core.verification_math``.

The two modules are co-located because the verification-math
helper (``compare_formula_vs_simulation``) is the natural bridge
between a measurement-derived value and a formula prediction. The
test scenarios the task specification calls out are covered here:

* near-target pass / fail (covered by :mod:`ltagent.live.verification`
  tests; this file focuses on the measurement-side helpers)
* ripple check (covered by :func:`ripple_from_max_min`)
* aggregate confidence (covered by the verification test file)
* fake-runner success / failure (covered by the verification test
  file)
* formula vs simulation comparison (covered by
  :func:`ltagent.math_core.verification_math.compare_formula_vs_simulation`)

In addition we cover:

* ``MeasurementRequest`` validation: identifier pattern, expression
  pattern, AT clause only on ``FIND``;
* ``.meas`` directive formatting for DC, AC, transient, and
  transient-windowed variants;
* parsing of LTspice log ``.meas`` result lines;
* edge cases of :func:`ripple_from_max_min` (swapped inputs,
  non-finite inputs).
"""

from __future__ import annotations

import pytest

from ltagent.live.measurements import (
    AnalysisKind,
    MeasFunction,
    MeasurementRequest,
    ac_gain_at_frequency,
    dc_voltage,
    format_meas_directive,
    generate_meas_directives,
    parse_measurement_lines,
    ripple_from_max_min,
    transient_max,
    transient_min,
    transient_ripple,
)
from ltagent.math_core.verification_math import (
    CODE_FORMULA_INPUT_INVALID,
    CODE_OK,
    CODE_ZERO_PREDICTION,
    FormulaVsSimResult,
    compare_formula_vs_simulation,
)

# ---------------------------------------------------------------------------
# MeasurementRequest validation
# ---------------------------------------------------------------------------


class TestMeasurementRequest:
    """The dataclass must reject malformed inputs at construction time."""

    def test_dc_find_directive(self) -> None:
        r = MeasurementRequest(
            name="VOUT",
            analysis=AnalysisKind.OP,
            function=MeasFunction.FIND,
            expression="v(out)",
        )
        assert r.analysis is AnalysisKind.OP
        assert r.function is MeasFunction.FIND
        assert r.expression == "v(out)"
        assert r.from_value is None
        assert r.to_value is None
        assert r.at_value is None

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="measurement name"):
            MeasurementRequest(
                name="1bad",
                analysis=AnalysisKind.OP,
                function=MeasFunction.FIND,
                expression="v(out)",
            )
        with pytest.raises(ValueError, match="measurement name"):
            MeasurementRequest(
                name="bad name",
                analysis=AnalysisKind.OP,
                function=MeasFunction.FIND,
                expression="v(out)",
            )

    def test_invalid_expression_rejected(self) -> None:
        with pytest.raises(ValueError, match="expression"):
            MeasurementRequest(
                name="X",
                analysis=AnalysisKind.OP,
                function=MeasFunction.FIND,
                expression="",
            )
        with pytest.raises(ValueError, match="expression"):
            MeasurementRequest(
                name="X",
                analysis=AnalysisKind.OP,
                function=MeasFunction.FIND,
                expression="not_a_probe",
            )

    def test_at_clause_requires_find(self) -> None:
        with pytest.raises(ValueError, match="AT clause"):
            MeasurementRequest(
                name="X",
                analysis=AnalysisKind.AC,
                function=MeasFunction.MAX,
                expression="v(out)",
                at_value="1k",
            )

    def test_to_dict_is_stable(self) -> None:
        r = MeasurementRequest(
            name="AV",
            analysis=AnalysisKind.AC,
            function=MeasFunction.FIND,
            expression="v(out)/v(in)",
            at_value="1k",
        )
        d = r.to_dict()
        assert d == {
            "name": "AV",
            "analysis": "ac",
            "function": "FIND",
            "expression": "v(out)/v(in)",
            "from": None,
            "to": None,
            "at": "1k",
        }

    def test_type_errors_at_construction(self) -> None:
        # Use the dataclass's __init__ directly to bypass the
        # frozen-ness guarantee; this is a structural test that
        # the type narrowers fire.
        with pytest.raises(TypeError, match="analysis"):
            MeasurementRequest(
                name="X",
                analysis="op",  # type: ignore[arg-type]
                function=MeasFunction.FIND,
                expression="v(out)",
            )


# ---------------------------------------------------------------------------
# Directive generation
# ---------------------------------------------------------------------------


class TestDirectiveGeneration:
    """``generate_meas_directives`` plus the convenience constructors."""

    def test_dc_voltage(self) -> None:
        r = dc_voltage("VOUT", "out")
        line = format_meas_directive(r)
        assert line == ".meas op VOUT FIND v(out)\n"

    def test_ac_gain_at_frequency(self) -> None:
        r = ac_gain_at_frequency("AV", "out", "in", "1k")
        line = format_meas_directive(r)
        assert line == ".meas ac AV FIND v(out)/v(in) AT 1k\n"

    def test_transient_max(self) -> None:
        r = transient_max("VOUT_MAX", "v(out)")
        line = format_meas_directive(r)
        assert line == ".meas tran VOUT_MAX MAX v(out)\n"

    def test_transient_min(self) -> None:
        r = transient_min("VOUT_MIN", "v(out)")
        line = format_meas_directive(r)
        assert line == ".meas tran VOUT_MIN MIN v(out)\n"

    def test_transient_ripple(self) -> None:
        r = transient_ripple(
            "VOUT_RIPPLE", "v(out)", from_value="10m", to_value="20m"
        )
        line = format_meas_directive(r)
        assert line == ".meas tran VOUT_RIPPLE PP v(out) FROM 10m TO 20m\n"

    def test_transient_partial_window(self) -> None:
        # When only from_value is set, the directive should still
        # emit a TO clause (defaulting to 0) so SPICE does not
        # silently use the full window. We choose this trade-off
        # over "emit nothing" because the latter is ambiguous.
        r = transient_max("V_MAX", "v(out)", from_value="1m")
        line = format_meas_directive(r)
        assert line == ".meas tran V_MAX MAX v(out) FROM 1m TO 0\n"

    def test_generate_meas_directives_batch(self) -> None:
        reqs = [
            dc_voltage("VOUT", "out"),
            ac_gain_at_frequency("AV", "out", "in", "1k"),
            transient_max("VMAX", "v(out)"),
            transient_min("VMIN", "v(out)"),
            transient_ripple("VRIP", "v(out)", from_value="10m", to_value="20m"),
        ]
        lines = generate_meas_directives(reqs)
        assert lines == [
            ".meas op VOUT FIND v(out)\n",
            ".meas ac AV FIND v(out)/v(in) AT 1k\n",
            ".meas tran VMAX MAX v(out)\n",
            ".meas tran VMIN MIN v(out)\n",
            ".meas tran VRIP PP v(out) FROM 10m TO 20m\n",
        ]

    def test_empty_input_returns_empty_list(self) -> None:
        assert generate_meas_directives([]) == []


# ---------------------------------------------------------------------------
# parse_measurement_lines
# ---------------------------------------------------------------------------


class TestParseMeasurementLines:
    """Round-trip log -> measurements -> struct."""

    def test_parses_typical_tran_log(self) -> None:
        log = (
            "Circuit: RC low-pass\n"
            "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\n"
            "vout_min: MIN(v(out))=-0.70710678 FROM 0 TO 0.005\n"
            "Elapsed time: 0.1 seconds\n"
        )
        result = parse_measurement_lines(log)
        assert "vout_max" in result
        assert "vout_min" in result
        assert result["vout_max"].value == pytest.approx(0.70710678)
        assert result["vout_max"].function == "MAX"
        assert result["vout_min"].value == pytest.approx(-0.70710678)

    def test_empty_log_returns_empty(self) -> None:
        assert parse_measurement_lines("") == {}

    def test_handles_garbage_gracefully(self) -> None:
        # Non-measurement text is ignored; we don't blow up.
        result = parse_measurement_lines("random text\nnot a measurement\n")
        assert result == {}

    def test_round_trip_with_dc_voltage(self) -> None:
        # A line that matches the standard ``.meas op FIND`` shape.
        log = "vout: FIND(v(out))=4.987\n"
        result = parse_measurement_lines(log)
        assert "vout" in result
        assert result["vout"].value == pytest.approx(4.987)


# ---------------------------------------------------------------------------
# ripple_from_max_min
# ---------------------------------------------------------------------------


class TestRippleFromMaxMin:
    """The ripple check covers the 'ripple' scenario in the task spec."""

    def test_clean_pass(self) -> None:
        r = ripple_from_max_min(5.05, 4.95)
        assert r["code"] == "OK"
        assert r["ripple"] == pytest.approx(0.1)
        assert r["vmax"] == pytest.approx(5.05)
        assert r["vmin"] == pytest.approx(4.95)
        assert r["passed"] is True

    def test_swapped_inputs(self) -> None:
        r = ripple_from_max_min(4.95, 5.05)
        assert r["code"] == "MEAS_RIPPLE_INPUT_SWAPPED"
        assert r["ripple"] < 0
        assert r["passed"] is False

    def test_missing_inputs(self) -> None:
        r = ripple_from_max_min(None, 5.0)
        assert r["code"] == "MEAS_RIPPLE_INPUT_INVALID"
        assert r["passed"] is False
        r = ripple_from_max_min(5.0, None)
        assert r["code"] == "MEAS_RIPPLE_INPUT_INVALID"

    def test_non_finite_inputs(self) -> None:
        r = ripple_from_max_min(float("inf"), 5.0)
        assert r["code"] == "MEAS_RIPPLE_INPUT_INVALID"
        r = ripple_from_max_min(float("nan"), 5.0)
        assert r["code"] == "MEAS_RIPPLE_INPUT_INVALID"

    def test_string_inputs_coerced(self) -> None:
        r = ripple_from_max_min("5.05", "4.95")
        assert r["ripple"] == pytest.approx(0.1)

    def test_equal_inputs_zero_ripple(self) -> None:
        # A constant output has zero ripple. vmax == vmin is fine
        # (not "swapped" because the swap rule is strictly
        # vmin > vmax).
        r = ripple_from_max_min(3.3, 3.3)
        assert r["code"] == "OK"
        assert r["ripple"] == pytest.approx(0.0)
        assert r["passed"] is True


# ---------------------------------------------------------------------------
# compare_formula_vs_simulation
# ---------------------------------------------------------------------------


class TestCompareFormulaVsSimulation:
    """The math-core helper for formula-vs-simulation reconciliation."""

    def test_within_tolerance(self) -> None:
        # Plan section 17.1: fc predicted 994.7 Hz, target 1000 Hz.
        r = compare_formula_vs_simulation(994.7, 1000.0, tolerance_percent=2.0)
        assert isinstance(r, FormulaVsSimResult)
        assert r.passed is True
        assert r.code == CODE_OK
        assert r.percent_error is not None
        assert r.percent_error < 2.0
        assert r.absolute_error == pytest.approx(5.3)

    def test_outside_tolerance(self) -> None:
        r = compare_formula_vs_simulation(900.0, 1000.0, tolerance_percent=2.0)
        assert r.passed is False
        # 100/900 = 11.111% error, well outside 2% tolerance.
        assert r.percent_error == pytest.approx(11.1111, rel=1e-3)
        assert r.code == CODE_OK  # OK is the *structural* code; passed
        # is the consumer-facing verdict.

    def test_zero_formula_prediction(self) -> None:
        r = compare_formula_vs_simulation(0.0, 0.0, tolerance_percent=5.0)
        assert r.code == CODE_ZERO_PREDICTION
        assert r.percent_error is None
        assert r.passed is True
        assert r.absolute_error == 0.0

    def test_zero_formula_with_nonzero_simulation(self) -> None:
        r = compare_formula_vs_simulation(0.0, 1e-3, tolerance_percent=5.0)
        assert r.code == CODE_ZERO_PREDICTION
        assert r.percent_error is None
        # Absolute error is the only signal we have; the pass/fail
        # is left to the caller (default is strict equality).
        assert r.passed is False

    def test_missing_formula(self) -> None:
        r = compare_formula_vs_simulation(None, 1000.0, tolerance_percent=5.0)
        assert r.code == CODE_FORMULA_INPUT_INVALID
        assert r.passed is False
        assert r.absolute_error is None

    def test_missing_simulation(self) -> None:
        r = compare_formula_vs_simulation(994.7, None, tolerance_percent=5.0)
        assert r.code == CODE_FORMULA_INPUT_INVALID
        assert r.passed is False

    def test_both_missing(self) -> None:
        r = compare_formula_vs_simulation(None, None, tolerance_percent=5.0)
        assert r.code == CODE_FORMULA_INPUT_INVALID
        assert r.passed is False

    def test_nan_formula(self) -> None:
        r = compare_formula_vs_simulation(
            float("nan"), 1000.0, tolerance_percent=5.0
        )
        assert r.code == CODE_FORMULA_INPUT_INVALID
        assert r.passed is False

    def test_inf_simulation(self) -> None:
        r = compare_formula_vs_simulation(
            994.7, float("inf"), tolerance_percent=5.0
        )
        assert r.code == CODE_FORMULA_INPUT_INVALID
        assert r.passed is False

    def test_string_inputs_coerced(self) -> None:
        r = compare_formula_vs_simulation("994.7", "1000", tolerance_percent=2.0)
        assert r.passed is True
        assert r.formula == pytest.approx(994.7)

    def test_negative_tolerance_clamped_to_zero(self) -> None:
        # Negative tolerance is meaningless; we clamp to 0 so the
        # check degenerates to "values must be bit-exact".
        r = compare_formula_vs_simulation(1000.0, 1000.0, tolerance_percent=-1.0)
        assert r.tolerance_percent == 0.0
        assert r.passed is True
        r = compare_formula_vs_simulation(1000.0, 1000.001, tolerance_percent=-1.0)
        assert r.passed is False

    def test_to_dict_is_stable(self) -> None:
        r = compare_formula_vs_simulation(994.7, 1000.0, tolerance_percent=2.0)
        d = r.to_dict()
        # The dataclass asdict produces the same key set every time.
        assert set(d.keys()) == {
            "formula",
            "simulation",
            "tolerance_percent",
            "absolute_error",
            "percent_error",
            "passed",
            "code",
            "detail",
        }

    def test_detail_message_includes_numbers(self) -> None:
        r = compare_formula_vs_simulation(994.7, 1000.0, tolerance_percent=2.0)
        assert "994.7" in r.detail
        assert "1000" in r.detail
        assert "0.53" in r.detail or "0.5300" in r.detail


# ---------------------------------------------------------------------------
# Plan section 17.1 smoke
# ---------------------------------------------------------------------------


class TestPlanScenarios:
    """Lift scenarios from plan section 17.1 (verification schema)."""

    def test_verification_shape_matches_plan(self) -> None:
        # Plan: cutoff 994.7 Hz vs 1000 Hz, 2% tolerance.
        r = compare_formula_vs_simulation(994.7, 1000.0, tolerance_percent=2.0)
        d = r.to_dict()
        # The plan's verification.json shape is:
        #   { "checks": [{"name", "target", "actual", "unit",
        #                 "tolerancePercent", "errorPercent", "passed"}],
        #     "overallPassed", "confidence" }
        # We don't enforce the exact JSON layout here -- that is the
        # job of the higher-level VerificationResult -- but the math
        # helper must produce the underlying numbers the plan calls
        # out.
        assert d["passed"] is True
        # The math helper uses "formula" / "simulation" naming; the
        # plan's "target" / "actual" is the alias used by
        # VerificationCheck above.
        assert d["formula"] == pytest.approx(994.7)
        assert d["simulation"] == pytest.approx(1000.0)
        assert d["percent_error"] is not None
        assert d["tolerance_percent"] == pytest.approx(2.0)
