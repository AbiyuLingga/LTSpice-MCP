"""Verification API for the file-based live editing workflow.

The verification pipeline answers one question: *did the simulation
match the target?* The pipeline is intentionally small and
declarative -- every check is a pure function over a numeric ``actual``
value and a numeric bound.

Public surface
--------------

* :class:`CheckKind` -- the closed set of comparison kinds.
* :class:`VerificationCheck` -- one comparison (target + tolerance,
  or one-sided bound).
* :class:`VerificationResult` -- a bundle of :class:`VerificationCheck`
  values plus an ``overall_passed`` flag and a confidence score.
* :func:`check_near_target` -- pass if ``actual`` is within
  ``tolerance_percent`` of ``target``.
* :func:`check_max` -- pass if ``actual <= max_value``.
* :func:`check_min` -- pass if ``actual >= min_value``.
* :func:`aggregate_verification` -- roll a list of checks up into a
  :class:`VerificationResult` with confidence.

Design notes
------------

* Every function is pure. There is no filesystem or subprocess
  coupling; the inputs are scalars and the outputs are dataclasses.
* The pipeline is total. ``None`` or non-finite ``actual`` values
  produce a structured ``ACTUAL_MISSING`` / ``ACTUAL_INVALID`` code
  rather than raising. The verification result is still well-formed
  so the rest of the project (CLI, MCP, the ``run_and_verify``
  orchestrator) does not have to special-case missing data.
* The confidence score is a fixed-step ladder (0.0, 0.25, 0.5,
  0.75, 1.0) for ease of reasoning. It is *not* a probability; it
  is a "how much evidence do we have that the design is correct"
  signal derived from the number of checks and how many passed.
* The JSON shape produced by :meth:`VerificationResult.to_dict` is
  the contract consumed by ``verification.json`` (see plan section
  17.1).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Final

# ---------------------------------------------------------------------------
# Stable codes
# ---------------------------------------------------------------------------

CODE_OK: Final = "OK"
CODE_TARGET_MISSING: Final = "TARGET_MISSING"
CODE_TOLERANCE_INVALID: Final = "TOLERANCE_INVALID"
CODE_BOUND_INVALID: Final = "BOUND_INVALID"
CODE_ACTUAL_MISSING: Final = "ACTUAL_MISSING"
CODE_ACTUAL_INVALID: Final = "ACTUAL_INVALID"

#: Maximum value of the confidence score. 1.0 == "every check passed,
#: no missing inputs". Anything below is interpolated.
MAX_CONFIDENCE: Final = 1.0

#: The confidence score advances in steps of 0.25. 0.0 means "no
#: usable checks ran"; 0.25 means "ran but every check failed or was
#: missing"; 0.5 means "some checks passed"; 0.75 means "more passed
#: than not"; 1.0 means "all checks passed cleanly".
CONFIDENCE_STEP: Final = 0.25

#: Maximum confidence penalty applied when a check is missing its
#: actual value but its target is well-formed. A single missing
#: check should not collapse the overall score to zero.
MISSING_ACTUAL_PENALTY: Final = 0.25


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CheckKind(str, Enum):  # noqa: UP042
    """Closed set of comparison kinds.

    The enum is referenced by name (its ``.value``), so adding a new
    kind in a future phase is backwards compatible: existing
    serialised payloads will simply not contain the new value, and
    readers can switch on the string form.
    """

    NEAR_TARGET = "near_target"
    MAX = "max"
    MIN = "min"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationCheck:
    """One comparison result.

    Attributes:
        name: Stable, human-readable identifier for this check
            (e.g. ``"cutoff_within_tolerance"``).
        kind: The :class:`CheckKind` used for the comparison.
        actual: The measured value (from the simulation). ``None``
            when the value was not present in the log.
        target: The numeric target. ``None`` when the check kind
            does not use a target (one-sided max / min).
        bound: The numeric one-sided bound for ``max`` / ``min``
            kinds. ``None`` for ``near_target`` checks.
        tolerance_percent: The percentage tolerance for
            ``near_target``; ignored for one-sided kinds. ``None``
            when not applicable.
        passed: ``True`` if the check succeeded.
        code: One of the ``CODE_*`` constants. Drives the JSON
            shape in ``verification.json`` and the
            ``run_and_verify`` orchestrator's error / warning list.
        detail: Human-readable description (used in the markdown
            report).
    """

    name: str
    kind: CheckKind
    actual: float | None
    target: float | None
    bound: float | None
    tolerance_percent: float | None
    passed: bool
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    """Bundle of :class:`VerificationCheck` values plus aggregates.

    The dataclass is mutable only because it is built incrementally
    by :func:`aggregate_verification`. Once serialised via
    :meth:`to_dict` the shape is frozen.

    Attributes:
        checks: The individual :class:`VerificationCheck` results,
            in insertion order.
        overall_passed: ``True`` iff every check passed.
        confidence: Fixed-step score in the closed interval
            ``[0.0, 1.0]`` describing how much evidence supports
            the design. See :data:`CONFIDENCE_STEP`.
        reason_codes: Stable codes (subset of the ``CODE_*`` set
            plus check-level ``code`` values) that drove the
            confidence score. Useful for downstream ``calculation.md``
            reports.
    """

    checks: list[VerificationCheck] = field(default_factory=list)
    overall_passed: bool = True
    confidence: float = MAX_CONFIDENCE
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "overallPassed": self.overall_passed,
            "confidence": round(self.confidence, 4),
            "reasonCodes": list(self.reason_codes),
        }


# ---------------------------------------------------------------------------
# Public check helpers
# ---------------------------------------------------------------------------


def check_near_target(
    actual: float | int | str | None,
    target: float | int | str | None,
    tolerance_percent: float | int | str,
    *,
    name: str = "check_near_target",
) -> VerificationCheck:
    """Pass when ``actual`` is within ``tolerance_percent`` of ``target``.

    The check is symmetric around ``target``: a 5 % tolerance
    accepts values in the range
    ``[target * (1 - tol/100), target * (1 + tol/100)]`` provided
    ``target`` is positive. A negative ``target`` uses the absolute
    value to keep the percentage meaningful, mirroring how engineers
    usually reason about tolerances ("the cutoff is 1 kHz ± 5 %",
    not "the cutoff is 1 kHz ± 50 Hz" -- the latter is implied by
    the former).

    Parameters:
        actual: The measured value. ``None`` or non-finite produces
            ``code == ACTUAL_MISSING`` / ``ACTUAL_INVALID``.
        target: The desired value. ``None`` produces
            ``code == TARGET_MISSING``.
        tolerance_percent: Allowed percentage deviation. Must be
            non-negative; negative values produce
            ``code == TOLERANCE_INVALID``.
        name: Stable identifier embedded in the result.

    Returns:
        A :class:`VerificationCheck` describing the comparison.
    """
    actual_f = _coerce_finite_float(actual)
    target_f = _coerce_finite_float(target)
    tol_f = _coerce_finite_float(tolerance_percent)

    if target_f is None:
        return VerificationCheck(
            name=name,
            kind=CheckKind.NEAR_TARGET,
            actual=actual_f,
            target=None,
            bound=None,
            tolerance_percent=tol_f,
            passed=False,
            code=CODE_TARGET_MISSING,
            detail=f"target value is missing or non-finite (got {target!r})",
        )
    if tol_f is None or tol_f < 0:
        return VerificationCheck(
            name=name,
            kind=CheckKind.NEAR_TARGET,
            actual=actual_f,
            target=target_f,
            bound=None,
            tolerance_percent=tol_f,
            passed=False,
            code=CODE_TOLERANCE_INVALID,
            detail=(
                f"tolerance_percent must be a non-negative finite number "
                f"(got {tolerance_percent!r})"
            ),
        )
    if actual_f is None:
        return VerificationCheck(
            name=name,
            kind=CheckKind.NEAR_TARGET,
            actual=None,
            target=target_f,
            bound=None,
            tolerance_percent=tol_f,
            passed=False,
            code=(
                CODE_ACTUAL_INVALID
                if isinstance(actual, float) and not math.isfinite(actual)
                else CODE_ACTUAL_MISSING
            ),
            detail=(
                f"actual value is missing or non-finite (got {actual!r}); "
                f"cannot compare to target {target_f:g}"
            ),
        )

    # Use the magnitude of the target so a negative target still
    # produces a meaningful percentage window. An exact zero target
    # would make the percentage undefined; we fall back to the
    # absolute error so the caller still gets a clean pass/fail.
    if target_f == 0.0:
        abs_err = abs(actual_f - target_f)
        passed = abs_err == 0.0
        return VerificationCheck(
            name=name,
            kind=CheckKind.NEAR_TARGET,
            actual=actual_f,
            target=target_f,
            bound=None,
            tolerance_percent=tol_f,
            passed=passed,
            code=CODE_OK,
            detail=(
                f"target is 0; absolute error = {abs_err:g}. "
                f"Tolerance {tol_f:g}% is not evaluable for zero targets."
            ),
        )

    pct_err = abs(actual_f - target_f) / abs(target_f) * 100.0
    passed = pct_err <= tol_f
    return VerificationCheck(
        name=name,
        kind=CheckKind.NEAR_TARGET,
        actual=actual_f,
        target=target_f,
        bound=None,
        tolerance_percent=tol_f,
        passed=passed,
        code=CODE_OK,
        detail=(
            f"actual={actual_f:g}, target={target_f:g}, |error|={pct_err:.4f}% "
            f"vs tolerance {tol_f:g}%"
        ),
    )


def check_max(
    actual: float | int | str | None,
    max_value: float | int | str,
    *,
    name: str = "check_max",
) -> VerificationCheck:
    """Pass when ``actual <= max_value``.

    Parameters:
        actual: The measured value. ``None`` or non-finite values
            produce a structured failure rather than raising.
        max_value: The upper bound (inclusive). Must be a finite
            number; ``None`` produces ``code == BOUND_INVALID``.
        name: Stable identifier embedded in the result.

    Returns:
        A :class:`VerificationCheck` describing the comparison.
    """
    actual_f = _coerce_finite_float(actual)
    max_f = _coerce_finite_float(max_value)

    if max_f is None:
        return VerificationCheck(
            name=name,
            kind=CheckKind.MAX,
            actual=actual_f,
            target=None,
            bound=None,
            tolerance_percent=None,
            passed=False,
            code=CODE_BOUND_INVALID,
            detail=f"max_value must be a finite number (got {max_value!r})",
        )
    if actual_f is None:
        return VerificationCheck(
            name=name,
            kind=CheckKind.MAX,
            actual=None,
            target=None,
            bound=max_f,
            tolerance_percent=None,
            passed=False,
            code=(
                CODE_ACTUAL_INVALID
                if isinstance(actual, float) and not math.isfinite(actual)
                else CODE_ACTUAL_MISSING
            ),
            detail=(
                f"actual value is missing or non-finite (got {actual!r}); "
                f"cannot compare to max {max_f:g}"
            ),
        )

    passed = actual_f <= max_f
    return VerificationCheck(
        name=name,
        kind=CheckKind.MAX,
        actual=actual_f,
        target=None,
        bound=max_f,
        tolerance_percent=None,
        passed=passed,
        code=CODE_OK,
        detail=(
            f"actual={actual_f:g}, max={max_f:g}, "
            + ("within bound" if passed else "exceeds bound")
        ),
    )


def check_min(
    actual: float | int | str | None,
    min_value: float | int | str,
    *,
    name: str = "check_min",
) -> VerificationCheck:
    """Pass when ``actual >= min_value``.

    Parameters mirror :func:`check_max` with the comparison inverted.
    """
    actual_f = _coerce_finite_float(actual)
    min_f = _coerce_finite_float(min_value)

    if min_f is None:
        return VerificationCheck(
            name=name,
            kind=CheckKind.MIN,
            actual=actual_f,
            target=None,
            bound=None,
            tolerance_percent=None,
            passed=False,
            code=CODE_BOUND_INVALID,
            detail=f"min_value must be a finite number (got {min_value!r})",
        )
    if actual_f is None:
        return VerificationCheck(
            name=name,
            kind=CheckKind.MIN,
            actual=None,
            target=None,
            bound=min_f,
            tolerance_percent=None,
            passed=False,
            code=(
                CODE_ACTUAL_INVALID
                if isinstance(actual, float) and not math.isfinite(actual)
                else CODE_ACTUAL_MISSING
            ),
            detail=(
                f"actual value is missing or non-finite (got {actual!r}); "
                f"cannot compare to min {min_f:g}"
            ),
        )

    passed = actual_f >= min_f
    return VerificationCheck(
        name=name,
        kind=CheckKind.MIN,
        actual=actual_f,
        target=None,
        bound=min_f,
        tolerance_percent=None,
        passed=passed,
        code=CODE_OK,
        detail=(
            f"actual={actual_f:g}, min={min_f:g}, " + ("within bound" if passed else "below bound")
        ),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_verification(
    checks: Iterable[VerificationCheck],
) -> VerificationResult:
    """Roll a list of checks up into a :class:`VerificationResult`.

    Aggregation rules:

    * ``overall_passed`` is ``True`` iff every check passed. A check
      that failed because its actual was missing still counts as a
      failure -- a missing measurement is not a "pass" condition.
    * ``confidence`` is a fixed-step score in ``{0.0, 0.25, 0.5,
      0.75, 1.0}`` derived from the proportion of checks that
      passed. Each check that passed contributes a full step; a
      missing actual penalises the score by
      :data:`MISSING_ACTUAL_PENALTY` (one step). The score is
      clamped to ``[0.0, 1.0]`` and snapped to the nearest
      :data:`CONFIDENCE_STEP` boundary so downstream consumers
      never see a number like ``0.833``.
    * ``reason_codes`` collects the ``code`` of every check that
      drove a non-OK outcome (failures and missing data), so the
      ``calculation.md`` report can enumerate them.

    Empty input returns a result with ``confidence == 1.0``,
    ``overall_passed == True``, and no checks -- there is nothing
    to fail. The caller is expected to add at least one
    ``measurement missing`` sentinel if "no checks" is itself a
    meaningful failure.
    """
    result = VerificationResult()
    check_list = list(checks)
    if not check_list:
        return result

    n = len(check_list)
    passes = 0
    missing = 0
    reasons: list[str] = []
    overall_passed = True
    for c in check_list:
        if c.passed:
            passes += 1
        else:
            overall_passed = False
            if c.code != CODE_OK:
                missing += 1
                reasons.append(c.code)

    # Build the confidence score.
    #   - Base: pass ratio * 1.0
    #   - Penalty: each missing/invalid actual subtracts MISSING_ACTUAL_PENALTY
    #   - Snap to nearest CONFIDENCE_STEP boundary in [0, 1].
    base = passes / n
    penalty = missing * MISSING_ACTUAL_PENALTY
    score = max(0.0, min(1.0, base - penalty))
    snapped = _snap_to_step(score)

    result.checks = check_list
    result.overall_passed = overall_passed
    result.confidence = snapped
    result.reason_codes = reasons
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_finite_float(value: Any) -> float | None:
    """Return ``value`` as a finite ``float`` or ``None``."""
    if value is None:
        return None
    # bool is a subclass of int; reject it because True/False are not
    # meaningful numeric measurements and would silently coerce to
    # 1.0 / 0.0.
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _snap_to_step(score: float) -> float:
    """Snap ``score`` to the nearest multiple of :data:`CONFIDENCE_STEP`.

    The result is always in the closed interval ``[0.0, 1.0]``.
    """
    step = CONFIDENCE_STEP
    snapped = round(score / step) * step
    # Floating-point cleanup: round to 4 decimals to avoid artefacts
    # like 0.7500000000000001 leaking into the JSON output.
    return round(max(0.0, min(1.0, snapped)), 4)


__all__ = [
    "CODE_ACTUAL_INVALID",
    "CODE_ACTUAL_MISSING",
    "CODE_BOUND_INVALID",
    "CODE_OK",
    "CODE_TARGET_MISSING",
    "CODE_TOLERANCE_INVALID",
    "CONFIDENCE_STEP",
    "MAX_CONFIDENCE",
    "MISSING_ACTUAL_PENALTY",
    "CheckKind",
    "VerificationCheck",
    "VerificationResult",
    "aggregate_verification",
    "check_max",
    "check_min",
    "check_near_target",
]
