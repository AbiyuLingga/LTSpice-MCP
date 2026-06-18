"""Smoke tests for the smaller pure-function modules."""

from __future__ import annotations

import pytest

from ltagent import ir, units


def test_ir_schema_version_is_string() -> None:
    assert isinstance(ir.SCHEMA_VERSION, str)
    assert ir.SCHEMA_VERSION == "0.1"


def test_ir_supported_topologies_are_three_for_mvp() -> None:
    # Phase 1 renamed the public constant from SUPPORTED_TOPOLOGIES to
    # MVP_TOPOLOGIES to match the plan's terminology.
    assert set(ir.MVP_TOPOLOGIES) == {
        "voltage_divider",
        "rc_lowpass",
        "rc_highpass",
    }


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
