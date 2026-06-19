from __future__ import annotations

import pytest

from ltagent.math_core import calculate, explain


def test_calculate_rc_lowpass_selects_e24_and_predicts_cutoff() -> None:
    result = calculate("rc_lowpass", {"fc": "1kHz", "C": "100nF"})
    assert result["idealValues"]["R"]["value"] == pytest.approx(1591.549, rel=1e-5)
    assert result["selectedValues"]["R"]["value"] == pytest.approx(1600.0)
    assert result["predicted"]["fc"]["value"] == pytest.approx(994.718, rel=1e-5)


def test_calculate_noninverting_opamp_gain_ten() -> None:
    result = calculate("noninv_opamp", {"gain": 10, "rg": "1kohm"})
    assert result["idealValues"]["rf"]["value"] == pytest.approx(9000.0)


def test_calculate_led_resistor() -> None:
    result = calculate(
        "led_resistor", {"vsupply": "5V", "vf": "2V", "iled": "20mA"}
    )
    assert result["idealValues"]["R"]["value"] == pytest.approx(150.0)
    assert result["idealValues"]["P_R"]["value"] == pytest.approx(0.06)


def test_explain_returns_formula_without_calculating() -> None:
    result = explain("rc_lowpass")
    assert result["formulas"][0]["expression"] == "fc = 1 / (2*pi*R*C)"
    assert any("ideal capacitor" in item for item in result["assumptions"])


def test_calculate_unknown_topology_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported topology"):
        calculate("magic", {})
