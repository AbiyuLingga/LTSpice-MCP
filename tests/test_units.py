"""Smoke tests for the smaller pure-function modules."""

from __future__ import annotations

import pytest

from ltagent import ir, units


def test_ir_schema_version_is_string() -> None:
    assert isinstance(ir.SCHEMA_VERSION, str)
    assert ir.SCHEMA_VERSION == "0.1"


def test_ir_supported_topologies_include_mvp_and_phase11() -> None:
    # Phase 1 renamed the public constant from SUPPORTED_TOPOLOGIES to
    # MVP_TOPOLOGIES to match the plan's terminology. Phase 11
    # extended the set to include the seven analog templates. The MVP
    # passive trio (voltage_divider, rc_lowpass, rc_highpass) must
    # still be present.
    assert {"voltage_divider", "rc_lowpass", "rc_highpass"} <= set(ir.MVP_TOPOLOGIES)
    assert "inverting_opamp" in ir.MVP_TOPOLOGIES
    assert "noninv_opamp" in ir.MVP_TOPOLOGIES
    assert "comparator" in ir.MVP_TOPOLOGIES
    assert "diode_clipper" in ir.MVP_TOPOLOGIES
    assert "halfwave_rectifier" in ir.MVP_TOPOLOGIES
    assert "bridge_rectifier" in ir.MVP_TOPOLOGIES
    assert "transistor_switch" in ir.MVP_TOPOLOGIES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1k", 1e3),
        ("2.2k", 2.2e3),
        ("10meg", 10e6),
        ("100n", 100e-9),
        ("1u", 1e-6),
        ("1.5m", 1.5e-3),
        ("3.3", 3.3),
        ("0", 0.0),
    ],
)
def test_units_parse_spice_value_ok(raw: str, expected: float) -> None:
    assert units.parse_spice_value(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "k", "1kk"])
def test_units_parse_spice_value_returns_none_for_garbage(raw: str) -> None:
    assert units.parse_spice_value(raw) is None
