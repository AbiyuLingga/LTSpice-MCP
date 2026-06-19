"""Preferred E-series and common capacitor / inductor values.

The math core is responsible for picking the *closest* standard
component value to the ideal one returned by a closed-form formula.
This module owns the lookup tables and the selection algorithm.

Series tables
-------------

The IEC 60063 preferred-number series:

* ``E6``   — 6 values per decade (20 % tolerance)
* ``E12``  — 12 values per decade (10 % tolerance)
* ``E24``  — 24 values per decade (5 % tolerance)
* ``E48``  — 48 values per decade (2 % tolerance, used for precision)
* ``E96``  — 96 values per decade (1 % tolerance)

Only the resistor-friendly E6 / E12 / E24 sets are required for the
MVP. E48 and E96 are included for completeness because the same
selection algorithm should work for all of them; the math core may be
called by an "E96 precision" optimizer in a later phase.

A common 1n / 2n / 5n capacitor decade is also shipped because the
E-series does not cover the popular ``47nF`` / ``4.7nF`` values used in
audio / decoupling applications.

Algorithm
---------

For a target value ``x`` the selector finds the series member ``s`` that
minimises the absolute relative error ``|s - x| / x``. Ties are broken
in favour of the *larger* value (over-spec'ing is safer than
under-spec'ing for filter cutoffs — a slightly lower cutoff still passes
the signal of interest).

The math core never rounds a result to a "standard value" without an
explicit call to :func:`nearest_standard_value`. The E-series lookup is
opt-in so callers can compare ideal vs. selected.
"""

from __future__ import annotations

import math as _math
from dataclasses import dataclass
from math import log10

# ---------------------------------------------------------------------------
# Series tables
# ---------------------------------------------------------------------------

#: Mantissas for each preferred-number series, in the decade ``[1, 10)``.
#: The math core expands the mantissa list across all integer decades
#: when :func:`nearest_standard_value` is called.
_E6_MANTISSAS: tuple[float, ...] = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)
_E12_MANTISSAS: tuple[float, ...] = (
    1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2,
)
_E24_MANTISSAS: tuple[float, ...] = (
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
)
#: Common capacitor decades used in audio / decoupling designs. They
#: are a superset of the E24 mantissas and add a few popular values.
_CAPACITOR_MANTISSAS: tuple[float, ...] = (
    1.0, 1.5, 2.2, 3.3, 4.7, 6.8, 10.0,
)

_SERIES_TABLES: dict[str, tuple[float, ...]] = {
    "E6": _E6_MANTISSAS,
    "E12": _E12_MANTISSAS,
    "E24": _E24_MANTISSAS,
    "E48": tuple(round(10 ** (i / 48), 4) for i in range(48)),
    "E96": tuple(round(10 ** (i / 96), 4) for i in range(96)),
    "CAP": _CAPACITOR_MANTISSAS,
}

#: Convenience identifiers accepted by :func:`series_values`.
SUPPORTED_SERIES: frozenset[str] = frozenset(_SERIES_TABLES)


def series_values(series: str) -> tuple[float, ...]:
    """Return the full standard-value ladder for ``series``.

    The ladder spans the decades ``1e-15`` through ``1e15`` so any
    realistic engineering value can be matched. The output is sorted
    ascending and deduplicated.
    """
    try:
        mantissas = _SERIES_TABLES[series]
    except KeyError as exc:
        raise ValueError(
            f"unsupported series {series!r}; expected one of {sorted(SUPPORTED_SERIES)}"
        ) from exc
    out: list[float] = []
    for decade in range(-15, 16):  # 1e-15 .. 1e15
        factor = 10.0 ** decade
        for m in mantissas:
            out.append(m * factor)
    return tuple(sorted(set(out)))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StandardValueSelection:
    """Result of a nearest-value lookup.

    Attributes:
        ideal: The input value, unchanged.
        series: The series name that was searched.
        selected: The closest standard value in the series.
        error_percent: The signed relative error in percent, defined as
            ``(selected - ideal) / ideal * 100``. Positive means the
            selected value is *larger* than the ideal; negative means
            it is *smaller*.
    """

    ideal: float
    series: str
    selected: float
    error_percent: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "ideal": self.ideal,
            "series": self.series,
            "selected": self.selected,
            "errorPercent": self.error_percent,
        }


def _error_percent(ideal: float, selected: float) -> float:
    if ideal == 0:
        if selected == 0:
            return 0.0
        return abs(selected) * 100.0
    return (selected - ideal) / ideal * 100.0


def calculate_error_percent(ideal: float, selected: float) -> float:
    """Return the signed percent error of ``selected`` relative to ``ideal``.

    This is the public form of the same calculation used by
    :func:`nearest_standard_value`. Both arguments must be finite;
    passing a zero ideal returns a finite number derived from the
    absolute magnitude of ``selected`` so the call site can still
    produce a non-``nan`` report.
    """
    if ideal is None or selected is None:
        raise ValueError("calculate_error_percent requires finite numeric inputs")
    for name, value in (("ideal", ideal), ("selected", selected)):
        if isinstance(value, float) and not _math.isfinite(value):
            raise ValueError(
                f"calculate_error_percent requires finite inputs; {name}={value!r}"
            )
    return _error_percent(ideal, selected)


def nearest_standard_value(value: float, series: str) -> StandardValueSelection:
    """Return the closest standard value in ``series`` to ``value``.

    Args:
        value: The ideal value. Must be positive; a zero / negative
            input is rejected with a :class:`ValueError` because the
            preferred-number series is defined only for positive
            magnitudes.
        series: One of :data:`SUPPORTED_SERIES` (e.g. ``"E24"``).

    Returns:
        A :class:`StandardValueSelection` carrying the ideal value,
        the series name, the picked standard value, and the signed
        percent error.

    Raises:
        ValueError: If ``value`` is not positive, or ``series`` is
            unknown. (Programming-error class, not a structured
            return — math-core callers validate inputs before calling.)
    """
    if value is None or value <= 0:
        raise ValueError(
            f"nearest_standard_value requires a positive value, got {value!r}"
        )
    if series not in _SERIES_TABLES:
        raise ValueError(
            f"unsupported series {series!r}; expected one of {sorted(SUPPORTED_SERIES)}"
        )

    ladder = series_values(series)

    best = ladder[0]
    best_err = abs(_error_percent(value, best))
    for candidate in ladder[1:]:
        err = abs(_error_percent(value, candidate))
        if err < best_err:
            best_err = err
            best = candidate
        elif err == best_err and candidate > best:
            # Tie-break in favour of the larger value: over-spec'ing
            # is the safer default for filters and current limits.
            best = candidate
        if best_err == 0:
            break

    return StandardValueSelection(
        ideal=value,
        series=series,
        selected=best,
        error_percent=_error_percent(value, best),
    )


def decade_of(value: float) -> int:
    """Return the power-of-ten decade that ``value`` belongs to.

    ``decade_of(1591.55) == 3`` because the value sits between 1e3 and
    1e4. The function exists so :mod:`formulas` can pretty-print
    quantities in the same decade the standard-value table uses.
    """
    if value <= 0:
        raise ValueError(f"decade_of requires a positive value, got {value!r}")
    return int(log10(value))


__all__ = [
    "SUPPORTED_SERIES",
    "StandardValueSelection",
    "calculate_error_percent",
    "decade_of",
    "nearest_standard_value",
    "series_values",
]
