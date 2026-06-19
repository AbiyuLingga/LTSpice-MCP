"""Math helpers that compare a closed-form formula prediction against
the result of an LTspice simulation.

This is the Agent-5-owned math helper inside :mod:`ltagent.math_core`.
The other ``math_core`` submodules (``units``, ``formulas``,
``standard_values``, ``calculation_report``) are owned by other agents
and imported lazily. We deliberately do not import them at module
load time so this file remains self-contained.

The :func:`compare_formula_vs_simulation` function is the only public
entry point. It is pure: it takes two floats (the formula's predicted
value and the value extracted from the simulation log) plus a
percentage tolerance and returns a structured dict that downstream
``VerificationCheck`` consumers can serialise straight into
``verification.json``.

Design notes
------------

* The function never raises. The two values are coerced to ``float``
  via :class:`float`; if the coercion fails or any input is non-finite
  a structured ``FORMULA_INPUT_INVALID`` code is returned instead of
  raising. The verification pipeline in
  :mod:`ltagent.live.verification` treats this as a hard failure of
  that specific check; callers that want exception semantics should
  validate inputs themselves before calling.
* The signed error and percent error are reported separately. A
  formula prediction of ``9.9e-3`` versus a measurement of ``1.0e-2``
  is a 1 % error in percent terms but a ``-1e-3`` signed error in
  absolute terms. Both are useful for confidence scoring (see plan
  section 17.3).
* A formula prediction of exactly zero is not allowed -- the percent
  error is undefined. The function falls back to absolute error
  alone and tags the response with ``"percentError": null`` and
  ``"percentErrorCode": "ZERO_PREDICTION"``. The ``passed`` flag
  still uses the absolute error as a safety net, but downstream
  consumers should treat ``ZERO_PREDICTION`` as a structured warning
  rather than a clean pass.
* All numeric outputs are clamped to ``float`` so the result is
  JSON-serialisable without going through
  :func:`ltagent.serialization.to_jsonable`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Final

#: Stable error / warning codes. Kept module-local so Agent 5 can
#: extend the catalog without colliding with other agents' codes in
#: the math core.
CODE_OK: Final = "OK"
CODE_FORMULA_INPUT_INVALID: Final = "FORMULA_INPUT_INVALID"
CODE_ZERO_PREDICTION: Final = "ZERO_PREDICTION"
CODE_BOTH_INPUTS_INVALID: Final = "FORMULA_INPUT_INVALID"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormulaVsSimResult:
    """Structured comparison of a formula prediction to a measurement.

    The dataclass is frozen so it is safe to embed inside other
    structured results. All numeric fields are plain ``float`` (never
    ``None``) unless the comparison itself is undefined; the
    ``code`` field carries the structured reason.

    Attributes:
        formula: The closed-form prediction (units assumed to match
            ``simulation``).
        simulation: The measured value (units assumed to match
            ``formula``).
        tolerance_percent: The percentage tolerance that was used to
            decide pass/fail.
        absolute_error: ``|simulation - formula|``. ``None`` when at
            least one input could not be coerced to a finite float.
        percent_error: ``absolute_error / |formula| * 100`` when
            ``formula`` is non-zero; otherwise ``None``.
        passed: ``True`` iff the comparison is within tolerance.
            ``False`` for any structured error or for an out-of-
            tolerance comparison.
        code: One of :data:`CODE_OK`, :data:`CODE_ZERO_PREDICTION`,
            or :data:`CODE_FORMULA_INPUT_INVALID`. Drives the
            ``verification.json`` consumer's error / warning shape.
        detail: Human-readable description suitable for the
            ``calculation.md`` report.
    """

    formula: float
    simulation: float
    tolerance_percent: float
    absolute_error: float | None
    percent_error: float | None
    passed: bool
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare_formula_vs_simulation(
    formula_prediction: float | int | str | None,
    simulation_measurement: float | int | str | None,
    tolerance_percent: float = 5.0,
) -> FormulaVsSimResult:
    """Compare a closed-form prediction to a simulation measurement.

    Parameters:
        formula_prediction: The value predicted by the formula
            (``fc = 1 / (2 * pi * R * C)``, the voltage divider
            output, etc.). Accepts ``float``, ``int``, or a numeric
            string. ``None`` triggers a structured invalid-input
            response.
        simulation_measurement: The value extracted from the
            simulation log (e.g. ``.meas`` output). Same coercion
            rules.
        tolerance_percent: Maximum permitted error in percent. Must
            be non-negative. Values below zero are clamped to zero,
            which effectively requires the simulation to match the
            formula to floating-point precision.

    Returns:
        A :class:`FormulaVsSimResult` describing the comparison.

    Notes:
        The function is total -- it never raises. A ``None`` or
        non-finite input on either side produces
        ``code == "FORMULA_INPUT_INVALID"`` and ``passed == False``.
        A zero formula prediction produces
        ``code == "ZERO_PREDICTION"`` and falls back to absolute
        error comparison; the ``percent_error`` field is ``None``.
    """
    if tolerance_percent < 0:
        # Negative tolerance is meaningless; clamp rather than raise so
        # the verification pipeline can stay declarative.
        tolerance_percent = 0.0

    formula = _coerce_finite_float(formula_prediction)
    simulation = _coerce_finite_float(simulation_measurement)
    if formula is None and simulation is None:
        return FormulaVsSimResult(
            formula=0.0,
            simulation=0.0,
            tolerance_percent=tolerance_percent,
            absolute_error=None,
            percent_error=None,
            passed=False,
            code=CODE_BOTH_INPUTS_INVALID,
            detail=(
                "Both formula prediction and simulation measurement "
                "are missing or non-finite; cannot compare."
            ),
        )
    if formula is None or simulation is None:
        return FormulaVsSimResult(
            formula=formula if formula is not None else 0.0,
            simulation=simulation if simulation is not None else 0.0,
            tolerance_percent=tolerance_percent,
            absolute_error=None,
            percent_error=None,
            passed=False,
            code=CODE_FORMULA_INPUT_INVALID,
            detail=(
                "One of formula prediction or simulation measurement "
                "is missing or non-finite; cannot compare."
            ),
        )

    abs_err = abs(simulation - formula)

    if formula == 0.0:
        # Percent error is undefined. Fall back to absolute error
        # alone; the caller can choose how to weight the result.
        passed = abs_err <= 0.0
        return FormulaVsSimResult(
            formula=formula,
            simulation=simulation,
            tolerance_percent=tolerance_percent,
            absolute_error=abs_err,
            percent_error=None,
            passed=passed,
            code=CODE_ZERO_PREDICTION,
            detail=(
                f"Formula prediction is zero; percent error undefined. "
                f"Absolute error = {abs_err:g}. "
                f"Tolerance ({tolerance_percent:g}%) cannot be evaluated."
            ),
        )

    pct_err = abs_err / abs(formula) * 100.0
    passed = pct_err <= tolerance_percent
    return FormulaVsSimResult(
        formula=formula,
        simulation=simulation,
        tolerance_percent=tolerance_percent,
        absolute_error=abs_err,
        percent_error=pct_err,
        passed=passed,
        code=CODE_OK,
        detail=(
            f"formula={formula:g}, simulation={simulation:g}, "
            f"|error|={abs_err:g} ({pct_err:.4f}% vs tolerance "
            f"{tolerance_percent:g}%)"
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_finite_float(value: Any) -> float | None:
    """Return ``value`` as a finite ``float`` or ``None``.

    Accepts ``int``, ``float``, or a string that ``float()`` can parse.
    Returns ``None`` for anything else (including non-finite floats
    like ``nan`` and ``inf``) so the caller can produce a structured
    error rather than letting NaN propagate into downstream checks.
    """
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


__all__ = [
    "CODE_FORMULA_INPUT_INVALID",
    "CODE_OK",
    "CODE_ZERO_PREDICTION",
    "FormulaVsSimResult",
    "compare_formula_vs_simulation",
]
