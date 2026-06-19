"""Unit parsing and formatting for the math core.

The math core is the only place in the project that is allowed to
interpret literal values such as ``"10k"``, ``"100nF"`` or ``"1mA"``
into SI base units. The rest of the project asks the math core for a
fully resolved float (in ohms, farads, volts, amps, hertz, seconds) and
treats the result as a deterministic input to its own logic.

The functions in this module never print and never raise bare
``ValueError`` / ``TypeError`` to the caller. All failure modes return a
:data:`UnitError` value with a stable ``code`` field so MCP / CLI layers
can surface a structured error payload.

Quantity model
--------------

The parser distinguishes *quantities* (resistance, capacitance, voltage,
current, frequency, time) and uses the trailing letter as the unit hint:

* ``"10k"`` alone is ambiguous — it is treated as a plain numeric value
  with the SI prefix applied and no quantity attached (so callers can
  interpret the result in whatever domain they like).
* ``"10kohm"`` or ``"10kR"`` resolves to resistance.
* ``"100nF"`` resolves to capacitance in farads.
* ``"3.3V"`` resolves to voltage in volts.
* ``"1mA"`` resolves to current in amps.
* ``"1kHz"`` resolves to frequency in hertz.
* ``"10ms"`` resolves to time in seconds.

The unit letter is optional and only used to label the result and to
flag contradictions (e.g. ``"10kHz"`` parsed as resistance is rejected).
This keeps the LLM layer from having to reason about prefixes — it can
just say "the user wants 1k of resistance" and the math core does the
rest.

SI prefix table
---------------

``f``  femto  1e-15
``p``  pico   1e-12
``n``  nano   1e-9
``u``  micro  1e-6     (Unicode ``µ`` is normalised to ``u``)
``m``  milli  1e-3
``(empty)`` unity
``k``  kilo   1e3
``K``  kilo   1e3      (SPICE convention: ``K`` is the same as ``k``)
``Meg`` mega  1e6      (bare ``M`` is rejected as ambiguous; ``MHz``
                       remains valid because the quantity is explicit)
``G``  giga   1e9
``T``  tera   1e12
"""

from __future__ import annotations

import math as _math
from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Quantity / unit registry
# ---------------------------------------------------------------------------

#: Quantities the parser understands. Each entry maps to the SI base unit
#: that ``si_value`` is expressed in. The trailing unit letter is matched
#: case-insensitively.
Quantity = Literal[
    "resistance",
    "capacitance",
    "inductance",
    "voltage",
    "current",
    "frequency",
    "time",
    "dimensionless",
]

_QUANTITY_UNIT: Final[dict[str, Quantity]] = {
    # Resistance
    "ohm": "resistance",
    "ohms": "resistance",
    "r": "resistance",
    # Capacitance
    "f": "capacitance",
    # Inductance
    "h": "inductance",
    # Voltage
    "v": "voltage",
    # Current
    "a": "current",
    # Frequency
    "hz": "frequency",
    # Time
    "s": "time",
    "sec": "time",
}

#: Canonical SI base unit symbol used to label ``si_unit``.
SI_UNIT_BY_QUANTITY: Final[dict[Quantity, str]] = {
    "resistance": "ohm",
    "capacitance": "F",
    "inductance": "H",
    "voltage": "V",
    "current": "A",
    "frequency": "Hz",
    "time": "s",
    "dimensionless": "",
}

#: SI prefix multipliers, longest-suffix first so that ``meg`` is matched
#: before ``m``. The lookup is case-insensitive.
SI_PREFIXES: Final[tuple[tuple[str, float], ...]] = (
    ("meg", 1e6),
    ("T", 1e12),
    ("G", 1e9),
    ("M", 1e6),
    ("K", 1e3),
    ("k", 1e3),
    ("m", 1e-3),
    ("u", 1e-6),
    ("n", 1e-9),
    ("p", 1e-12),
    ("f", 1e-15),
)

#: The display symbol that re-formats a value back to a human string.
_PREFIX_SYMBOL: Final[dict[float, str]] = {
    1e12: "T",
    1e9: "G",
    1e6: "M",
    1e3: "k",
    1.0: "",
    1e-3: "m",
    1e-6: "u",
    1e-9: "n",
    1e-12: "p",
    1e-15: "f",
}


# ---------------------------------------------------------------------------
# Structured error
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitError:
    """Structured, MCP-friendly error returned by every parser call.

    The dataclass is frozen so it can be hashed / cached. The ``code``
    field is a stable string identifier that callers can switch on; the
    ``message`` field is human-readable.

    The parser functions return ``UnitError`` instances instead of
    raising. This is deliberate: the MCP / CLI layers want to embed the
    error in a JSON payload, and Python tracebacks are noisy and unsafe
    to serialise.
    """

    code: str
    message: str
    raw: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "raw": self.raw}


# ---------------------------------------------------------------------------
# Parse result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedValue:
    """Result of parsing a literal engineering value.

    Attributes:
        raw: The original string, unchanged.
        si_value: Numeric value in SI base units (ohms, farads, volts, …).
        si_unit: SI base unit symbol (e.g. ``"ohm"`` for resistance).
        quantity: The inferred quantity (or ``"dimensionless"``).
        prefix: Multiplier that was applied to the mantissa (e.g. ``1e-9``
            for ``"n"``); ``1.0`` if no prefix was present.
        mantissa: The numeric portion of the input, before the prefix.
    """

    raw: str
    si_value: float
    si_unit: str
    quantity: Quantity
    prefix: float
    mantissa: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "raw": self.raw,
            "siValue": self.si_value,
            "siUnit": self.si_unit,
            "quantity": self.quantity,
            "prefix": self.prefix,
            "mantissa": self.mantissa,
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Strip whitespace, convert ``µ`` to ``u``, but preserve case.

    Case preservation matters: the parser distinguishes ``M`` (mega)
    from ``m`` (milli) only when the original character is preserved.
    Mantissa digits are case-insensitive so the lower-level float
    parser works on either form.
    """
    return text.strip().replace("\u00b5", "u")


def _split_mantissa_and_rest(numeric_part: str) -> tuple[float, str] | UnitError:
    """Parse the leading mantissa and return the remaining text.

    The mantissa is anything that :class:`float` accepts. The remaining
    text is the unit/prefix tail.
    """
    if not numeric_part:
        return UnitError("UNIT_EMPTY", "empty value", numeric_part)
    last_good_index = 0
    last_good_value: float | None = None
    for i in range(1, len(numeric_part) + 1):
        chunk = numeric_part[:i]
        try:
            last_good_value = float(chunk)
            last_good_index = i
        except ValueError:
            if last_good_value is not None:
                break
            continue
    if last_good_value is None:
        return UnitError(
            "UNIT_NOT_NUMERIC",
            f"could not parse numeric portion of {numeric_part!r}",
            numeric_part,
        )
    return last_good_value, numeric_part[last_good_index:]


def _parse_tail(
    tail: str, raw: str
) -> tuple[float, str, Quantity] | UnitError:
    """Resolve the suffix into (multiplier, unit_letter, quantity).

    The tail is the post-mantissa text (e.g. ``"k"``, ``"nF"``,
    ``"megohm"``). The function performs a longest-match against the SI
    prefix list and the unit-letter list.

    Matching is *case-sensitive* for the ``M`` (mega) vs ``m`` (milli)
    distinction — SPICE treats them as different prefixes. Every other
    prefix is matched case-insensitively because ``K`` and ``k`` both
    mean kilo, ``U`` and ``u`` both mean micro, and so on.
    """
    if not tail:
        return 1.0, "", "dimensionless"

    if tail == "M":
        return UnitError(
            "UNIT_AMBIGUOUS_PREFIX",
            "bare 'M' is ambiguous; use 'Meg' for mega or 'm' for milli",
            raw,
        )

    for prefix_str, multiplier in SI_PREFIXES:
        # Case-sensitive match for the M/m disambiguation.
        if prefix_str in {"M", "m"}:
            if tail.startswith(prefix_str):
                unit_part = tail[len(prefix_str):]
                if not unit_part:
                    return multiplier, "", "dimensionless"
                quantity = _match_unit(unit_part)
                if quantity is None:
                    return UnitError(
                        "UNIT_UNKNOWN",
                        f"unknown unit suffix {unit_part!r} in {raw!r}",
                        raw,
                    )
                return multiplier, unit_part, quantity
            continue
        # Case-insensitive match for every other prefix.
        if tail.lower().startswith(prefix_str.lower()):
            unit_part = tail[len(prefix_str):]
            if not unit_part:
                return multiplier, "", "dimensionless"
            quantity = _match_unit(unit_part)
            if quantity is None:
                return UnitError(
                    "UNIT_UNKNOWN",
                    f"unknown unit suffix {unit_part!r} in {raw!r}",
                    raw,
                )
            return multiplier, unit_part, quantity

    quantity = _match_unit(tail)
    if quantity is None:
        return UnitError(
            "UNIT_UNKNOWN",
            f"unknown unit suffix {tail!r} in {raw!r}",
            raw,
        )
    return 1.0, tail, quantity


def _match_unit(text: str) -> Quantity | None:
    """Return the quantity for a unit-letter string, or ``None``."""
    return _QUANTITY_UNIT.get(text.lower())


def parse_value(value: str) -> ParsedValue | UnitError:
    """Parse an engineering literal into a :class:`ParsedValue`.

    Args:
        value: A string such as ``"10k"``, ``"100nF"``, ``"3.3V"``,
            ``"1mA"``, ``"1kHz"`` or ``"1.5Meg"``.

    Returns:
        A :class:`ParsedValue` on success, or a :class:`UnitError` with
        a stable code on failure. Never raises.

    Examples:
        ``parse_value("10k").si_value == 10000.0``
        ``parse_value("100nF").si_value == 1e-7``
        ``parse_value("3.3V").si_value == 3.3``
    """
    if not isinstance(value, str):
        return UnitError(
            "UNIT_NOT_STRING",
            f"parse_value expects str, got {type(value).__name__}",
            repr(value),
        )

    normalised = _normalise(value)
    if not normalised:
        return UnitError("UNIT_EMPTY", "empty value", value)

    parsed_mantissa = _split_mantissa_and_rest(normalised)
    if isinstance(parsed_mantissa, UnitError):
        return UnitError(parsed_mantissa.code, parsed_mantissa.message, value)
    mantissa, tail = parsed_mantissa

    parsed_tail = _parse_tail(tail, value)
    if isinstance(parsed_tail, UnitError):
        return parsed_tail
    multiplier, _unit_letter, quantity = parsed_tail

    si_unit = SI_UNIT_BY_QUANTITY[quantity]
    return ParsedValue(
        raw=value,
        si_value=mantissa * multiplier,
        si_unit=si_unit,
        quantity=quantity,
        prefix=multiplier,
        mantissa=mantissa,
    )


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def _best_prefix(value: float) -> tuple[float, str]:
    """Return (scaled_value, prefix_symbol) for the largest SI prefix that
    keeps the mantissa in ``[1, 1000)``.

    Zero maps to the unity prefix with a scaled value of zero.
    """
    if value == 0:
        return 0.0, ""
    abs_value = abs(value)
    ordered: tuple[float, ...] = (
        1e12, 1e9, 1e6, 1e3, 1.0, 1e-3, 1e-6, 1e-9, 1e-12, 1e-15,
    )
    for mult in ordered:
        if abs_value >= mult and abs_value < mult * 1000:
            return value / mult, _PREFIX_SYMBOL[mult]
    return value / 1e-15, "f"


def format_value(value: float, unit: str = "") -> str:
    """Render ``value`` as a short engineering string.

    Args:
        value: Numeric value to format. Must be finite.
        unit: Optional unit symbol to append (e.g. ``"ohm"``, ``"F"``,
            ``"V"``). The string is appended verbatim — no SI
            transformation is applied to it.

    Returns:
        A human-readable representation such as ``"1.6k"``,
        ``"100nF"``, or ``"3.3V"``. Negative values are prefixed with
        ``"-"`` and ``value == 0`` always renders as ``"0"``.

    Raises:
        ValueError: If ``value`` is NaN or infinite. (This is a true
            programming error: a calculation that produces NaN means the
            caller fed bad inputs, and silent formatting would just
            produce ``"nan"`` strings that the next stage cannot parse.)
    """
    if value is None or (
        isinstance(value, float) and not _math.isfinite(value)
    ):
        raise ValueError(f"format_value requires a finite value, got {value!r}")
    if value == 0:
        return f"0{unit}"
    scaled, prefix = _best_prefix(value)
    body = f"{int(scaled)}" if scaled == int(scaled) else f"{scaled:.4f}".rstrip("0").rstrip(".")
    return f"{body}{prefix}{unit}"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def parse_to_si(
    value: str, expected_quantity: Quantity | None = None
) -> float | UnitError:
    """Parse ``value`` and return the SI number, optionally checking quantity.

    This is a thin convenience wrapper used by :mod:`formulas` when the
    caller already knows which quantity the value represents. If
    ``expected_quantity`` is given and the parsed quantity disagrees,
    a :class:`UnitError` with code ``UNIT_QUANTITY_MISMATCH`` is
    returned. Bare prefixes (e.g. ``"10k"`` parsed with no unit
    letter) are *rejected* when ``expected_quantity`` is set — the
    caller should be explicit about the unit so silent wrong-domain
    conversions cannot slip through.
    """
    parsed = parse_value(value)
    if isinstance(parsed, UnitError):
        return parsed
    if expected_quantity is not None and parsed.quantity != expected_quantity:
        return UnitError(
            "UNIT_QUANTITY_MISMATCH",
            (
                f"expected {expected_quantity!r} but got "
                f"{parsed.quantity!r} for {value!r}"
            ),
            value,
        )
    return parsed.si_value


__all__ = [
    "SI_PREFIXES",
    "SI_UNIT_BY_QUANTITY",
    "ParsedValue",
    "Quantity",
    "UnitError",
    "format_value",
    "parse_to_si",
    "parse_value",
]
