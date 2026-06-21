"""Tests for :mod:`ltagent.math_core.formulas`.

The formulas module is the *only* place where the math core is allowed
to evaluate closed-form circuit equations. The tests below pin every
equation to a worked-out numerical example so a refactor cannot
quietly change the math.
"""

from __future__ import annotations

import math

import pytest

from ltagent.math_core.formulas import (
    CODE_DIVISION_BY_ZERO,
    CODE_INVALID_INPUT,
    CODE_INVERTING_GAIN_SIGN,
    CODE_NON_FINITE,
    CODE_OK,
    FORMULA_REGISTRY,
    BuckResult,
    FormulaResult,
    boost_ideal,
    buck_ideal,
    inverting_opamp_feedback,
    inverting_opamp_gain,
    led_resistor,
    noninverting_opamp_feedback,
    noninverting_opamp_gain,
    rc_highpass_cutoff,
    rc_highpass_resistor,
    rc_lowpass_cutoff,
    rc_lowpass_resistor,
    voltage_divider_vout,
)

# ---------------------------------------------------------------------------
# Voltage divider
# ---------------------------------------------------------------------------


def test_voltage_divider_12v_to_5v_7k_5k() -> None:
    """``12V * 5 / (7+5) = 5V``."""
    result = voltage_divider_vout(vin=12.0, r1=7_000.0, r2=5_000.0)
    assert isinstance(result, FormulaResult)
    assert result.ok
    assert result.result == pytest.approx(5.0)
    assert result.expression == "Vout = Vin * R2 / (R1 + R2)"


def test_voltage_divider_rejects_zero_resistor() -> None:
    result = voltage_divider_vout(vin=12.0, r1=0.0, r2=5_000.0)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


def test_voltage_divider_rejects_negative_resistor() -> None:
    result = voltage_divider_vout(vin=12.0, r1=-1.0, r2=5_000.0)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


def test_voltage_divider_handles_negative_vin() -> None:
    """A negative input voltage is allowed and the sign is preserved."""
    result = voltage_divider_vout(vin=-12.0, r1=7_000.0, r2=5_000.0)
    assert result.ok
    assert result.result == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# RC low-pass
# ---------------------------------------------------------------------------


def test_rc_lowpass_resistor_1khz_100nf_ideal() -> None:
    """``1kHz, 100nF`` → R ≈ 1591.55 Ω (the spec example)."""
    result = rc_lowpass_resistor(fc=1_000.0, c=100e-9)
    assert result.ok
    assert result.result == pytest.approx(1_591.549, rel=1e-3)
    assert result.expression == "R = 1 / (2 * pi * fc * C)"


def test_rc_lowpass_cutoff_with_ideal_resistor() -> None:
    """Round-trip: R = 1/(2π·1k·100n) → fc back to 1 kHz."""
    r = rc_lowpass_resistor(fc=1_000.0, c=100e-9)
    assert r.ok and r.result is not None
    cutoff = rc_lowpass_cutoff(r=r.result, c=100e-9)
    assert cutoff.ok
    assert cutoff.result == pytest.approx(1_000.0, rel=1e-9)


def test_rc_lowpass_rejects_zero_inputs() -> None:
    assert not rc_lowpass_resistor(fc=0.0, c=100e-9).ok
    assert not rc_lowpass_resistor(fc=1_000.0, c=0.0).ok
    assert rc_lowpass_resistor(fc=0.0, c=100e-9).code == CODE_INVALID_INPUT


def test_rc_lowpass_rejects_negative_inputs() -> None:
    assert not rc_lowpass_resistor(fc=-1_000.0, c=100e-9).ok
    assert not rc_lowpass_resistor(fc=1_000.0, c=-100e-9).ok


def test_rc_lowpass_rejects_non_finite_inputs() -> None:
    result = rc_lowpass_resistor(fc=math.inf, c=100e-9)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


# ---------------------------------------------------------------------------
# RC high-pass
# ---------------------------------------------------------------------------


def test_rc_highpass_resistor_500hz_1uf_ideal() -> None:
    """``500Hz, 1uF`` → R ≈ 318.31 Ω."""
    result = rc_highpass_resistor(fc=500.0, c=1e-6)
    assert result.ok
    assert result.result == pytest.approx(318.309, rel=1e-3)


def test_rc_highpass_cutoff_matches_lowpass_formula() -> None:
    """The cutoff formula is identical for low- and high-pass."""
    r = 10_000.0
    c = 10e-9
    lp = rc_lowpass_cutoff(r=r, c=c)
    hp = rc_highpass_cutoff(r=r, c=c)
    assert lp.ok and hp.ok
    assert lp.result == pytest.approx(hp.result)
    assert lp.result == pytest.approx(1.0 / (2 * math.pi * r * c), rel=1e-9)


# ---------------------------------------------------------------------------
# Inverting op-amp
# ---------------------------------------------------------------------------


def test_inverting_opamp_gain_is_negative() -> None:
    result = inverting_opamp_gain(rf=10_000.0, rin=1_000.0)
    assert result.ok
    assert result.result == pytest.approx(-10.0)
    assert result.extra["abs_gain"] == pytest.approx(10.0)


def test_inverting_opamp_feedback_gain_minus_10_rin_1k() -> None:
    """Spec: |Av|=10, Rin=1k → Rf = 10 kΩ."""
    result = inverting_opamp_feedback(gain=-10.0, rin=1_000.0)
    assert result.ok
    assert result.result == pytest.approx(10_000.0)


def test_inverting_opamp_feedback_rejects_positive_gain() -> None:
    """The inverting topology has negative gain by construction."""
    result = inverting_opamp_feedback(gain=10.0, rin=1_000.0)
    assert not result.ok
    assert result.code == CODE_INVERTING_GAIN_SIGN


def test_inverting_opamp_rejects_zero_resistor() -> None:
    result = inverting_opamp_gain(rf=0.0, rin=1_000.0)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


# ---------------------------------------------------------------------------
# Non-inverting op-amp
# ---------------------------------------------------------------------------


def test_noninverting_opamp_gain_10_rg_1k() -> None:
    """Spec: gain 10 with Rg=1k → Rf = 9kΩ."""
    result = noninverting_opamp_feedback(gain=10.0, rg=1_000.0)
    assert result.ok
    assert result.result == pytest.approx(9_000.0)


def test_noninverting_opamp_gain_2_rg_1k() -> None:
    """Spec: gain 2 → Rf = Rg."""
    result = noninverting_opamp_feedback(gain=2.0, rg=1_000.0)
    assert result.ok
    assert result.result == pytest.approx(1_000.0)


def test_noninverting_opamp_rejects_unity_gain() -> None:
    """Gain must be > 1; unity is a buffer with Rf=0."""
    result = noninverting_opamp_feedback(gain=1.0, rg=1_000.0)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


def test_noninverting_opamp_forward_computation() -> None:
    """``Av = 1 + Rf / Rg`` for the typical 11x stage (Rf=10k, Rg=1k)."""
    result = noninverting_opamp_gain(rf=10_000.0, rg=1_000.0)
    assert result.ok
    assert result.result == pytest.approx(11.0)


# ---------------------------------------------------------------------------
# LED resistor
# ---------------------------------------------------------------------------


def test_led_resistor_5v_2v_10ma_is_300_ohm() -> None:
    """``(5 - 2) / 0.01 = 300 Ω``."""
    result = led_resistor(v_supply=5.0, v_forward=2.0, i_led=10e-3)
    assert result.ok
    assert result.result == pytest.approx(300.0)
    # P = I² · R = 0.01² · 300 = 0.03 W = 30 mW
    assert result.extra["p_dissipated"] == pytest.approx(0.03)
    assert result.extra["headroom"] == pytest.approx(3.0)


def test_led_resistor_rejects_zero_or_negative() -> None:
    assert not led_resistor(v_supply=0.0, v_forward=2.0, i_led=10e-3).ok
    assert not led_resistor(v_supply=5.0, v_forward=0.0, i_led=10e-3).ok
    assert not led_resistor(v_supply=5.0, v_forward=2.0, i_led=0.0).ok


def test_led_resistor_rejects_vsupply_le_vforward() -> None:
    """If the supply is at or below Vf the LED does not conduct at all."""
    result = led_resistor(v_supply=2.0, v_forward=2.0, i_led=10e-3)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


# ---------------------------------------------------------------------------
# Switched-mode power supplies
# ---------------------------------------------------------------------------


def test_buck_ideal_12v_to_5v_1a() -> None:
    """``D = 5/12 ≈ 0.4167``, ``R = 5/1 = 5 Ω``, ``P = 5 W``."""
    result = buck_ideal(vin=12.0, vout=5.0, iout=1.0)
    assert isinstance(result, BuckResult)
    assert result.duty == pytest.approx(5.0 / 12.0)
    assert result.r_load == pytest.approx(5.0)
    assert result.p_out == pytest.approx(5.0)


def test_buck_rejects_vout_greater_than_vin() -> None:
    """A buck cannot step up."""
    result = buck_ideal(vin=12.0, vout=24.0, iout=1.0)
    assert not isinstance(result, BuckResult)
    assert result.code == CODE_INVALID_INPUT


def test_buck_rejects_non_positive_vin() -> None:
    result = buck_ideal(vin=0.0, vout=5.0, iout=1.0)
    assert not isinstance(result, BuckResult)
    assert result.code == CODE_INVALID_INPUT


def test_boost_ideal_5v_to_12v_0p5a() -> None:
    """``D = 1 - 5/12 ≈ 0.5833``, ``R = 12/0.5 = 24 Ω``, ``P = 6 W``."""
    result = boost_ideal(vin=5.0, vout=12.0, iout=0.5)
    assert isinstance(result, BuckResult)
    assert result.duty == pytest.approx(1.0 - 5.0 / 12.0)
    assert result.r_load == pytest.approx(24.0)
    assert result.p_out == pytest.approx(6.0)


def test_boost_rejects_vout_le_vin() -> None:
    """A boost cannot step down."""
    result = boost_ideal(vin=12.0, vout=5.0, iout=1.0)
    assert not isinstance(result, BuckResult)
    assert result.code == CODE_INVALID_INPUT


def test_boost_rejects_non_positive_iout() -> None:
    result = boost_ideal(vin=5.0, vout=12.0, iout=0.0)
    assert not isinstance(result, BuckResult)
    assert result.code == CODE_INVALID_INPUT


# ---------------------------------------------------------------------------
# Division-by-zero / non-finite guards
# ---------------------------------------------------------------------------


def test_rc_lowpass_resistor_rejects_division_by_zero_after_structural_check() -> None:
    """A zero input is rejected by the structural check before the math runs."""
    result = rc_lowpass_resistor(fc=1.0, c=0.0)
    assert not result.ok
    assert result.code == CODE_INVALID_INPUT


def test_voltage_divider_rejects_both_resistors_zero() -> None:
    """``r1 + r2 == 0`` cannot be reached with positive resistors, but the
    formula still defends against it (programming-error safety net)."""
    # The structural check rejects r1=0 or r2=0 first, but we want to
    # cover the division-by-zero path on a future formula that might
    # not have a structural guard. We simulate it by patching
    # ``_ensure_positive`` to be a no-op.
    import ltagent.math_core.formulas as f

    original = f._ensure_positive

    def always_ok(name: str, value: float) -> str | None:
        return None

    try:
        f._ensure_positive = always_ok  # type: ignore[assignment]
        result = voltage_divider_vout(vin=12.0, r1=0.0, r2=0.0)
        assert not result.ok
        assert result.code == CODE_DIVISION_BY_ZERO
    finally:
        f._ensure_positive = original  # type: ignore[assignment]


def test_voltage_divider_rejects_nan_vin() -> None:
    result = voltage_divider_vout(vin=math.nan, r1=1_000.0, r2=1_000.0)
    assert not result.ok
    assert result.code in {CODE_INVALID_INPUT, CODE_NON_FINITE}


# ---------------------------------------------------------------------------
# FormulaResult serialisation
# ---------------------------------------------------------------------------


def test_formula_result_to_dict_is_jsonable() -> None:
    result = rc_lowpass_resistor(fc=1_000.0, c=100e-9)
    payload = result.to_dict()
    assert payload["name"] == "rc_lowpass_resistor"
    assert payload["ok"] is True
    assert payload["code"] == CODE_OK
    assert "expression" in payload
    assert "result" in payload
    assert isinstance(payload["inputs"], dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topology",
    [
        "voltage_divider",
        "rc_lowpass",
        "rc_highpass",
        "inverting_opamp",
        "noninv_opamp",
        "led_resistor",
        "buck_ideal",
        "boost_ideal",
    ],
)
def test_registry_covers_all_topologies(topology: str) -> None:
    """Every formula the plan lists in section 14.3 is registered."""
    assert topology in FORMULA_REGISTRY
    entry = FORMULA_REGISTRY[topology]
    assert entry.name == topology
    assert entry.expression
    assert entry.description
    # Every declared variable must carry a (quantity, unit) pair.
    for var_name, (quantity, unit) in entry.variables.items():
        assert var_name
        assert quantity
        # unit may be "" for dimensionless quantities (e.g. duty, gain).
        assert isinstance(unit, str)


# ---------------------------------------------------------------------------
# Calculation report integration
# ---------------------------------------------------------------------------

# The calculation_report module is in a separate file but the report
# builders exercise the full formulas → standard_values → report
# pipeline. Tests for the report itself live here so we keep the
# count of test files down to the three the math-core agent owns.
from ltagent.math_core.calculation_report import (  # noqa: E402
    build_boost_report,
    build_buck_report,
    build_inverting_opamp_report,
    build_led_resistor_report,
    build_noninverting_opamp_report,
    build_rc_report,
    render_markdown,
)


def test_rc_lowpass_report_1khz_100nf_e24_picks_1p6k() -> None:
    """End-to-end: 1kHz / 100nF / E24 → R selected = 1.6kΩ."""
    report = build_rc_report(fc=1_000.0, c=100e-9, series="E24")
    assert report.success
    assert report.ideal_values["R"].value == pytest.approx(1_591.549, rel=1e-3)
    assert report.selected_values["R"].value == pytest.approx(1_600.0)
    assert report.predicted["fc"].value == pytest.approx(994.718, rel=1e-3)
    # The percent error is signed; the plan example shows magnitude
    # (0.528 %), so we accept both signs but only the 1.6kΩ direction.
    assert report.error_percent is not None
    assert abs(report.error_percent) == pytest.approx(0.528, abs=1e-2)
    # The report carries formula, substitution, ideal, selected, predicted.
    assert report.formulas
    assert report.assumptions
    assert "R" in report.ideal_values
    assert "R" in report.selected_values


def test_noninverting_opamp_report_gain_10_rg_1k() -> None:
    """End-to-end: gain 10, Rg 1kΩ, E24 → Rf selected ≈ 9.1kΩ (1 % error)."""
    report = build_noninverting_opamp_report(gain=10.0, rg=1_000.0)
    assert report.success
    assert report.ideal_values["Rf"].value == pytest.approx(9_000.0)
    # E24's nearest standard value above 9k is 9.1k.
    assert report.selected_values["Rf"].value == pytest.approx(9_100.0)
    assert report.predicted["gain"].value == pytest.approx(10.1, rel=1e-2)
    assert report.error_percent is not None
    assert abs(report.error_percent) == pytest.approx(1.0, rel=1e-2)


def test_led_resistor_report_5v_2v_10ma() -> None:
    report = build_led_resistor_report(
        v_supply=5.0,
        v_forward=2.0,
        i_led=10e-3,
    )
    assert report.success
    assert report.ideal_values["R"].value == pytest.approx(300.0)
    assert report.selected_values["R"].value == pytest.approx(300.0)
    # Power dissipation in the resistor (I²R) is reported.
    assert "p_dissipated" in report.predicted
    assert report.predicted["p_dissipated"].value == pytest.approx(0.03)


def test_inverting_opamp_report_gain_minus_10_rin_1k() -> None:
    report = build_inverting_opamp_report(gain=-10.0, rin=1_000.0)
    assert report.success
    assert report.ideal_values["Rf"].value == pytest.approx(10_000.0)
    assert report.predicted["gain"].value == pytest.approx(-10.0, abs=1e-2)


def test_buck_report_12v_to_5v_1a() -> None:
    report = build_buck_report(vin=12.0, vout=5.0, iout=1.0)
    assert report.success
    assert "duty" in report.predicted
    assert report.predicted["duty"].value == pytest.approx(5.0 / 12.0, rel=1e-9)
    assert "R_load" in report.selected_values


def test_boost_report_5v_to_12v_0p5a() -> None:
    report = build_boost_report(vin=5.0, vout=12.0, iout=0.5)
    assert report.success
    assert report.predicted["duty"].value == pytest.approx(1.0 - 5.0 / 12.0, rel=1e-9)


def test_report_handles_invalid_inputs() -> None:
    """An impossible request (boost with vout <= vin) returns a
    failed report rather than raising."""
    report = build_boost_report(vin=12.0, vout=5.0, iout=1.0)
    assert not report.success
    assert report.code != CODE_OK
    assert report.detail  # must carry a human-readable explanation


def test_report_json_dict_shape() -> None:
    report = build_rc_report(fc=1_000.0, c=100e-9)
    payload = report.to_dict()
    # Stable keys for the JSON contract — agents and MCP tools switch on these.
    for key in (
        "schemaVersion",
        "success",
        "topology",
        "formulas",
        "idealValues",
        "selectedValues",
        "predicted",
        "errorPercent",
        "assumptions",
        "warnings",
    ):
        assert key in payload, f"missing {key!r} in report payload"
    assert payload["formulas"], "formulas block must not be empty"
    assert "rc_lowpass" in payload["topology"]


def test_report_markdown_contains_required_sections() -> None:
    report = build_rc_report(fc=1_000.0, c=100e-9)
    md = render_markdown(report)
    # Section headings are part of the contract.
    for section in (
        "# Calculation Report",
        "## Formulas",
        "## Ideal Values",
        "## Standard Value Selection",
        "## Predicted Result",
        "## Assumptions",
    ):
        assert section in md, f"missing markdown section {section!r}"
    # The numeric values appear in the report body.
    assert "1.6k" in md
    assert "100nF" in md


def test_report_markdown_for_failed_case_is_honest() -> None:
    report = build_boost_report(vin=12.0, vout=5.0, iout=1.0)
    md = render_markdown(report)
    assert "FAILED" in md
    assert report.detail in md
