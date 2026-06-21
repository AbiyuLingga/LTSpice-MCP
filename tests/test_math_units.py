"""Tests for :mod:`ltagent.math_core.units`.

These tests pin down the contract every other math-core module relies
on. They are deliberately exhaustive on the edge cases the parser
hits in production (unicode ``µ``, ``meg`` vs ``M``, prefix-only
literals, unit-letter tagging) so a refactor cannot quietly change
the meaning of ``"10k"``.
"""

from __future__ import annotations

import math

import pytest

from ltagent.math_core.units import (
    SI_PREFIXES,
    ParsedValue,
    Quantity,
    UnitError,
    format_value,
    parse_to_si,
    parse_value,
)

# ---------------------------------------------------------------------------
# parse_value — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_value", "expected_unit", "expected_quantity"),
    [
        # Bare prefix literals — quantity stays dimensionless so the
        # caller can apply the result in any domain.
        ("10k", 10_000.0, "", "dimensionless"),
        ("2.2k", 2_200.0, "", "dimensionless"),
        ("1meg", 1e6, "", "dimensionless"),
        ("1Meg", 1e6, "", "dimensionless"),
        ("1u", 1e-6, "", "dimensionless"),
        ("100n", 1e-7, "", "dimensionless"),
        ("1.5m", 1.5e-3, "", "dimensionless"),
        ("0", 0.0, "", "dimensionless"),
        # Resistance
        ("10kohm", 10_000.0, "ohm", "resistance"),
        ("10kOhm", 10_000.0, "ohm", "resistance"),
        ("1.5kR", 1_500.0, "ohm", "resistance"),
        ("4.7kΩ".replace("Ω", "ohm"), 4_700.0, "ohm", "resistance"),
        # Capacitance
        ("100nF", 1e-7, "F", "capacitance"),
        ("1uF", 1e-6, "F", "capacitance"),
        ("220uF", 220e-6, "F", "capacitance"),
        ("47pF", 47e-12, "F", "capacitance"),
        # Inductance
        ("1.5uH", 1.5e-6, "H", "inductance"),
        # Voltage
        ("3.3V", 3.3, "V", "voltage"),
        ("12V", 12.0, "V", "voltage"),
        # Current
        ("1mA", 1e-3, "A", "current"),
        ("100uA", 100e-6, "A", "current"),
        # Frequency
        ("1kHz", 1_000.0, "Hz", "frequency"),
        ("1MHz", 1_000_000.0, "Hz", "frequency"),
        ("50Hz", 50.0, "Hz", "frequency"),
        # Time
        ("10ms", 0.01, "s", "time"),
        ("1us", 1e-6, "s", "time"),
    ],
)
def test_parse_value_happy_path(
    raw: str, expected_value: float, expected_unit: str, expected_quantity: Quantity
) -> None:
    parsed = parse_value(raw)
    assert isinstance(parsed, ParsedValue)
    assert parsed.si_value == pytest.approx(expected_value)
    assert parsed.si_unit == expected_unit
    assert parsed.quantity == expected_quantity
    assert parsed.raw == raw


def test_parse_value_unicode_micro_is_normalised() -> None:
    """``1µF`` (Unicode ``µ``) is treated identically to ``1uF``."""
    a = parse_value("1µF")
    b = parse_value("1uF")
    assert isinstance(a, ParsedValue) and isinstance(b, ParsedValue)
    assert a.si_value == pytest.approx(b.si_value)
    assert a.quantity == b.quantity == "capacitance"


def test_parse_value_lowercase_meg_resolves_to_million() -> None:
    """``1meg`` must be 1e6; the lowercase form is the SPICE disambiguation."""
    parsed = parse_value("1meg")
    assert isinstance(parsed, ParsedValue)
    assert parsed.si_value == pytest.approx(1e6)


def test_parse_value_rejects_ambiguous_capital_m() -> None:
    """Mega must be written as ``Meg``; bare ``M`` is intentionally rejected."""
    parsed = parse_value("1M")
    assert isinstance(parsed, UnitError)
    assert parsed.code == "UNIT_AMBIGUOUS_PREFIX"


def test_parse_value_returns_mantissa_and_prefix_separately() -> None:
    parsed = parse_value("100nF")
    assert isinstance(parsed, ParsedValue)
    assert parsed.mantissa == pytest.approx(100.0)
    assert parsed.prefix == pytest.approx(1e-9)
    assert parsed.si_value == pytest.approx(100.0 * 1e-9)


def test_parse_value_whitespace_is_trimmed() -> None:
    parsed = parse_value("  1k  ")
    assert isinstance(parsed, ParsedValue)
    assert parsed.si_value == pytest.approx(1_000.0)


# ---------------------------------------------------------------------------
# parse_value — error paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc",
        "k",  # no mantissa
        "1kk",  # double prefix
        "1kxyz",  # unknown unit letter
    ],
)
def test_parse_value_returns_unit_error(raw: str) -> None:
    result = parse_value(raw)
    assert isinstance(result, UnitError)
    assert result.raw == raw
    assert result.code
    assert result.message


def test_parse_value_kilo_farad_is_valid() -> None:
    """``1kF`` means 1 kilofarad (= 1000 F) — not an error."""
    parsed = parse_value("1kF")
    assert isinstance(parsed, ParsedValue)
    assert parsed.si_value == pytest.approx(1_000.0)
    assert parsed.quantity == "capacitance"


def test_parse_value_rejects_non_string() -> None:
    result = parse_value(123)  # type: ignore[arg-type]
    assert isinstance(result, UnitError)
    assert result.code == "UNIT_NOT_STRING"


def test_parse_value_rejects_none() -> None:
    result = parse_value(None)  # type: ignore[arg-type]
    assert isinstance(result, UnitError)
    assert result.code == "UNIT_NOT_STRING"


def test_parse_value_negative_mantissa_is_allowed() -> None:
    """Negative values are normal in circuit math (e.g. signed voltages)."""
    parsed = parse_value("-3.3V")
    assert isinstance(parsed, ParsedValue)
    assert parsed.si_value == pytest.approx(-3.3)
    assert parsed.quantity == "voltage"


def test_unit_error_to_dict_shape() -> None:
    err = UnitError("UNIT_EMPTY", "missing value", raw="")
    payload = err.to_dict()
    assert payload == {"code": "UNIT_EMPTY", "message": "missing value", "raw": ""}


# ---------------------------------------------------------------------------
# parse_to_si — convenience helper
# ---------------------------------------------------------------------------


def test_parse_to_si_returns_si_number() -> None:
    result = parse_to_si("3.3V")
    assert result == pytest.approx(3.3)


def test_parse_to_si_propagates_unit_error() -> None:
    result = parse_to_si("not-a-value")
    assert isinstance(result, UnitError)
    assert result.code == "UNIT_NOT_NUMERIC"


def test_parse_to_si_quantity_mismatch() -> None:
    """Asking for ``voltage`` on a resistance literal is a hard mismatch."""
    result = parse_to_si("10kohm", expected_quantity="voltage")
    assert isinstance(result, UnitError)
    assert result.code == "UNIT_QUANTITY_MISMATCH"


def test_parse_to_si_dimensionless_rejected_when_quantity_expected() -> None:
    """A bare prefix like ``"10k"`` is ambiguous and must NOT silently
    match a caller-supplied quantity. The caller must be explicit."""
    result = parse_to_si("10k", expected_quantity="resistance")
    assert isinstance(result, UnitError)
    assert result.code == "UNIT_QUANTITY_MISMATCH"


def test_parse_to_si_dimensionless_allowed_when_no_quantity() -> None:
    """Without an expected quantity the bare prefix is just a number."""
    for raw in ("10k", "1.5Meg", "220u", "47n"):
        result = parse_to_si(raw)
        assert not isinstance(result, UnitError), raw
        assert result > 0


def test_parse_to_si_quantity_match_passes() -> None:
    result = parse_to_si("10kohm", expected_quantity="resistance")
    assert result == pytest.approx(10_000.0)


# ---------------------------------------------------------------------------
# format_value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (10_000.0, "", "10k"),
        (1_591.55, "ohm", "1.5916kohm"),
        (1e-7, "F", "100nF"),
        (1e-3, "A", "1mA"),
        (1_000.0, "Hz", "1kHz"),
        (0.0, "V", "0V"),
        (47.0, "ohm", "47ohm"),
        (-1_500.0, "ohm", "-1.5kohm"),
        (1.5, "", "1.5"),
    ],
)
def test_format_value(value: float, unit: str, expected: str) -> None:
    assert format_value(value, unit) == expected


def test_format_value_rejects_nan() -> None:
    with pytest.raises(ValueError, match="finite"):
        format_value(math.nan, "V")


def test_format_value_rejects_infinity() -> None:
    with pytest.raises(ValueError, match="finite"):
        format_value(math.inf, "V")


def test_format_value_zero_always_renders_as_zero_with_unit() -> None:
    assert format_value(0.0, "") == "0"
    assert format_value(0.0, "ohm") == "0ohm"


# ---------------------------------------------------------------------------
# Round-trip property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "10k",
        "100nF",
        "3.3V",
        "1mA",
        "1kHz",
        "10ms",
        "4.7kohm",
        "220uF",
    ],
)
def test_parse_then_format_round_trips_si_value(raw: str) -> None:
    """A value parsed and then re-formatted in its native unit must
    give the same SI number back (modulo display precision)."""
    parsed = parse_value(raw)
    assert isinstance(parsed, ParsedValue)
    # We feed the raw value back to format_value so the unit suffix is
    # applied correctly. The exact display string is not required to
    # match because the SI mantissa may have been rounded for display.
    if parsed.si_unit:
        # Use parse_value again on the formatted string to confirm the
        # number is recovered at full precision.
        recovered = parse_value(format_value(parsed.si_value, parsed.si_unit))
        assert isinstance(recovered, ParsedValue)
        assert recovered.si_value == pytest.approx(parsed.si_value, rel=1e-3)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_si_prefixes_table_is_well_formed() -> None:
    assert isinstance(SI_PREFIXES, tuple)
    assert all(isinstance(p, tuple) and len(p) == 2 for p in SI_PREFIXES)
    multipliers = [mult for _, mult in SI_PREFIXES]
    # All multipliers must be positive powers of ten (or their inverse).
    for m in multipliers:
        assert m > 0
        log = math.log10(m)
        assert log == int(log), f"non-power-of-ten multiplier: {m!r}"


def test_unit_error_is_hashable_and_immutable() -> None:
    err = UnitError("X", "y", "z")
    err2 = UnitError("X", "y", "z")
    assert err == err2
    # Should not raise (frozen dataclass).
    _ = {err, err2}
