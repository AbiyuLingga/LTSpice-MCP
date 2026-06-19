"""Structured ``.meas`` request representation, directive generation,
and log-line parsing for Agent 5.

LTspice has two distinct concepts that we model as one:

* A ``.meas`` *directive* embedded in the netlist that asks the
  simulator to compute a value (``.meas tran VOUT_MAX MAX v(out)``).
* A ``.meas`` *result line* in the log file that the simulator prints
  after the run (``vout_max: MAX(v(out))=0.7071 FROM 0 TO 0.005``).

Both sides of the contract are covered here:

* :class:`MeasurementRequest` is the canonical description of what
  the caller wants to measure. It is fully typed and immutable.
* :func:`generate_meas_directives` turns a list of requests into the
  raw ``.meas`` lines that should be appended to the netlist.
* :func:`parse_measurement_lines` is a thin wrapper over
  :mod:`ltagent.log_parser` that returns a flat
  ``{name: value}`` dict, the same shape that
  :mod:`ltagent.live.verification` consumes.
* :func:`ripple_from_max_min` derives a peak-to-peak ripple value
  from a pair of transient ``MAX`` / ``MIN`` measurements.

The module is intentionally small and free of filesystem / subprocess
side effects so it is straightforward to test with hand-written
fixtures. The accepted grammar for ``expression`` strings is the same
subset that :mod:`ltagent.log_parser` already understands
(``v(out)``, ``i(R1)``, etc.).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from ltagent import log_parser  # re-use the canonical regexes

# ---------------------------------------------------------------------------
# Stable codes (for use in VerificationCheck.code)
# ---------------------------------------------------------------------------

CODE_INVALID_REQUEST: Final = "MEAS_INVALID_REQUEST"
CODE_INVALID_NAME: Final = "MEAS_INVALID_NAME"
CODE_INVALID_EXPRESSION: Final = "MEAS_INVALID_EXPRESSION"
CODE_RIPPLE_INPUT_INVALID: Final = "MEAS_RIPPLE_INPUT_INVALID"
CODE_RIPPLE_INPUT_SWAPPED: Final = "MEAS_RIPPLE_INPUT_SWAPPED"

#: SPICE identifier pattern. Same as :data:`ltagent.ir.IDENTIFIER_PATTERN`
#: (locally redefined to avoid importing ir -- we want this module
#: usable from the math core too).
_MEAS_NAME_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Expression pattern: at least one ``v(<ident>)`` or ``i(<ident>)``
#: probe, optionally combined with arithmetic operators ``+ - * /``,
#: parentheses, and literal numbers. We deliberately accept a wider
#: set than LTspice supports so the rest of the verification pipeline
#: is not blocked by upstream edge cases; if LTspice rejects the
#: expression the .log parser will surface it as
#: ``LTSPICE_PARSE_ERROR`` and the run will fail. The pattern below
#: is *not* a full parser -- it only rejects obvious mistakes
#: (empty string, no probe, trailing comma / operator).
_EXPR_PATTERN: Final = re.compile(
    r"""
    ^
    (?=.*[vViI]\s*\([^()]*\))     # at least one probe somewhere
    [A-Za-z0-9_+\-*/().,\s]+
    $
    """,
    re.VERBOSE,
)
#: Probe pattern used by :func:`_EXPR_PATTERN` to assert that at least
#: one ``v(<node>)`` or ``i(<device>)`` is present. Compiled separately
#: for readability.
_PROBE_PATTERN: Final = re.compile(r"[vViI]\s*\([^()]*\)")


# ---------------------------------------------------------------------------
# Analysis kind
# ---------------------------------------------------------------------------


class AnalysisKind(str, Enum):  # noqa: UP042
    """Analysis section a ``.meas`` directive applies to.

    ``OP`` is a DC operating point, ``DC`` a DC sweep, ``AC`` a small-
    signal AC analysis, and ``TRAN`` a transient analysis. These
    mirror the SPICE directive names (``".meas op"``, ``".meas dc"``,
    ``".meas ac"``, ``".meas tran"``). We do not accept raw SPICE
    strings as a value -- the enum is closed.
    """

    OP = "op"
    DC = "dc"
    AC = "ac"
    TRAN = "tran"


# ---------------------------------------------------------------------------
# Function kind
# ---------------------------------------------------------------------------


class MeasFunction(str, Enum):  # noqa: UP042
    """Supported ``.meas`` functions.

    The enum is a closed set. The MVP supports the four functions that
    the rest of the verification pipeline actually consumes:

    * ``FIND`` -- a single operating-point value, optionally ``AT`` a
      time (transient) or frequency (AC).
    * ``MAX`` / ``MIN`` -- peak / trough of a waveform across the
      analysis window.
    * ``PP`` -- peak-to-peak ripple (max - min). SPICE emits this as
      its own line so we keep the enum explicit; callers that only
      have raw max/min can derive ``PP`` via
      :func:`ripple_from_max_min`.

    Other SPICE functions (``AVG``, ``RMS``, ``INTEG``, ``WHEN``)
    exist but are not in scope for Agent 5. They can be added
    later without breaking the public contract because the enum is
    referenced by name, not by ordinal.
    """

    FIND = "FIND"
    MAX = "MAX"
    MIN = "MIN"
    PP = "PP"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementRequest:
    """Canonical description of a single ``.meas`` request.

    The dataclass is frozen so it can be hashed (useful for test
    fixtures) and embedded inside other structured results. The
    ``name``, ``expression``, ``function`` triple fully determines the
    resulting ``.meas`` line, so the same input always produces the
    same output across runs.

    Attributes:
        name: The SPICE identifier used in the directive and the log
            line. Must match ``^[A-Za-z][A-Za-z0-9_]*$``.
        analysis: The analysis section the directive belongs to
            (OP / DC / AC / TRAN).
        function: The ``.meas`` function to apply.
        expression: A probe expression of the form ``v(out)`` or
            ``i(R1)``. Must match the simple balanced-paren grammar.
        from_value: Optional ``FROM`` bound for transient / AC
            measurements. ``None`` means "use the full window".
        to_value: Optional ``TO`` bound. ``None`` means "use the full
            window". ``from_value`` and ``to_value`` are stored as
            strings because the SPICE syntax accepts both raw
            numbers and time-frequency expressions like ``0.1m``.
        at_value: Optional ``AT`` point for ``FIND`` directives
            (transient time, AC frequency). ``None`` means no ``AT``
            clause.
    """

    name: str
    analysis: AnalysisKind
    function: MeasFunction
    expression: str
    from_value: str | None = None
    to_value: str | None = None
    at_value: str | None = None

    def __post_init__(self) -> None:
        # Type narrow for mypy strict mode. AnalysisKind and MeasFunction
        # are enums; dataclass field types already enforce the types at
        # construction, so the only thing to do here is sanity-check
        # the strings.
        if not isinstance(self.analysis, AnalysisKind):
            raise TypeError(
                f"analysis must be an AnalysisKind, got {type(self.analysis).__name__}"
            )
        if not isinstance(self.function, MeasFunction):
            raise TypeError(
                f"function must be a MeasFunction, got {type(self.function).__name__}"
            )
        if not _MEAS_NAME_PATTERN.match(self.name):
            raise ValueError(
                f"measurement name {self.name!r} is not a valid SPICE identifier"
            )
        if not _EXPR_PATTERN.match(self.expression) or not _PROBE_PATTERN.search(
            self.expression
        ):
            raise ValueError(
                f"measurement expression {self.expression!r} is not a valid probe"
            )
        if self.at_value is not None and self.function is not MeasFunction.FIND:
            raise ValueError(
                "AT clause is only valid for .meas FIND directives; "
                f"function is {self.function.value!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "analysis": self.analysis.value,
            "function": self.function.value,
            "expression": self.expression,
            "from": self.from_value,
            "to": self.to_value,
            "at": self.at_value,
        }


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def dc_voltage(name: str, node: str) -> MeasurementRequest:
    """Build a ``.meas op FIND v(<node>)`` request."""
    return MeasurementRequest(
        name=name,
        analysis=AnalysisKind.OP,
        function=MeasFunction.FIND,
        expression=f"v({node})",
    )


def ac_gain_at_frequency(
    name: str,
    output_node: str,
    input_source: str,
    frequency: str,
) -> MeasurementRequest:
    """Build a ``.meas ac FIND v(<out>)/v(<src>) AT <frequency>`` request.

    The frequency is a SPICE literal (e.g. ``"1k"``, ``"10Meg"``) so
    callers can pass whatever their analysis sweep already uses. The
    function is preserved as a literal expression; LTspice will
    evaluate it once at the requested frequency.

    The returned request is a :class:`MeasurementRequest` whose
    ``at_value`` is set to ``frequency``. The expression is the
    ratio ``v(out)/v(in)`` rather than a single probe so the result
    is the actual small-signal gain in V/V.
    """
    return MeasurementRequest(
        name=name,
        analysis=AnalysisKind.AC,
        function=MeasFunction.FIND,
        expression=f"v({output_node})/v({input_source})",
        at_value=frequency,
    )


def transient_max(
    name: str,
    expression: str,
    *,
    from_value: str | None = None,
    to_value: str | None = None,
) -> MeasurementRequest:
    """Build a ``.meas tran MAX <expr> [FROM x TO y]`` request."""
    return MeasurementRequest(
        name=name,
        analysis=AnalysisKind.TRAN,
        function=MeasFunction.MAX,
        expression=expression,
        from_value=from_value,
        to_value=to_value,
    )


def transient_min(
    name: str,
    expression: str,
    *,
    from_value: str | None = None,
    to_value: str | None = None,
) -> MeasurementRequest:
    """Build a ``.meas tran MIN <expr> [FROM x TO y]`` request."""
    return MeasurementRequest(
        name=name,
        analysis=AnalysisKind.TRAN,
        function=MeasFunction.MIN,
        expression=expression,
        from_value=from_value,
        to_value=to_value,
    )


def transient_ripple(
    name: str,
    expression: str,
    *,
    from_value: str | None = None,
    to_value: str | None = None,
) -> MeasurementRequest:
    """Build a ``.meas tran PP <expr> [FROM x TO y]`` request.

    SPICE computes peak-to-peak natively, so this is the preferred
    form when the user wants ripple. When only raw ``MAX`` / ``MIN``
    measurements are available (for example, from a third-party log
    that does not include ``PP``), :func:`ripple_from_max_min` can
    derive the same number post-run.
    """
    return MeasurementRequest(
        name=name,
        analysis=AnalysisKind.TRAN,
        function=MeasFunction.PP,
        expression=expression,
        from_value=from_value,
        to_value=to_value,
    )


# ---------------------------------------------------------------------------
# Directive generator
# ---------------------------------------------------------------------------


def generate_meas_directives(
    requests: Iterable[MeasurementRequest],
) -> list[str]:
    """Return the ``.meas`` netlist lines for the given requests.

    The output is a list of strings, one per request, in the order
    they were provided. Empty input returns an empty list. The
    directive text follows the SPICE grammar::

        .meas <analysis> <name> <function> <expr> [FROM <from> TO <to>]
        .meas <analysis> <name> FIND <expr> AT <at>

    The generator does not insert blank lines or comments. Callers
    that want to wrap the block can join with ``"\\n"``.
    """
    return [format_meas_directive(req) for req in requests]


def format_meas_directive(request: MeasurementRequest) -> str:
    """Format a single :class:`MeasurementRequest` as a SPICE directive.

    The function is the source of truth for the on-disk shape; both
    :func:`generate_meas_directives` and the unit tests call it.
    """
    parts: list[str] = [
        ".meas",
        request.analysis.value,
        request.name,
        request.function.value,
        request.expression,
    ]
    if request.from_value is not None or request.to_value is not None:
        from_v = request.from_value if request.from_value is not None else "0"
        to_v = request.to_value if request.to_value is not None else "0"
        parts.append(f"FROM {from_v} TO {to_v}")
    if request.at_value is not None:
        parts.append(f"AT {request.at_value}")
    return " ".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------


def parse_measurement_lines(
    text: str,
) -> dict[str, log_parser.MeasurementResult]:
    """Parse ``.meas`` result lines from a log text.

    Thin wrapper over :func:`ltagent.log_parser.parse_log_text` that
    returns the *full* mapping (name -> :class:`MeasurementResult`).
    Callers that only need a flat ``{name: value}`` dict should use
    :func:`ltagent.log_parser.parse_meas_lines` directly; this
    function exists so Agent 5 can keep all measurement-related
    imports in one module.

    The function is total: malformed or empty input returns an empty
    dict. Any non-measurement findings (errors, warnings) are not
    surfaced here; use :func:`ltagent.log_parser.parse_log_text` if
    you need them.
    """
    report = log_parser.parse_log_text(text)
    return dict(report.measurements)


# ---------------------------------------------------------------------------
# Ripple derivation
# ---------------------------------------------------------------------------


def ripple_from_max_min(
    vmax: float | int | str | None,
    vmin: float | int | str | None,
) -> dict[str, Any]:
    """Compute peak-to-peak ripple from a pair of max / min measurements.

    Returns a structured dict that mirrors the verification pipeline
    shape::

        {
            "code": "OK" | "MEAS_RIPPLE_INPUT_INVALID" | "MEAS_RIPPLE_INPUT_SWAPPED",
            "ripple": float,
            "vmax": float,
            "vmin": float,
            "passed": bool,
            "detail": str,
        }

    The function never raises. Missing or non-finite inputs produce
    a structured invalid response. When ``vmin > vmax`` (which can
    happen if the inputs are swapped), the response tags itself with
    ``MEAS_RIPPLE_INPUT_SWAPPED`` and emits a negative ``ripple`` so
    callers can decide whether to flag or correct.
    """
    vmax_f = _coerce_finite_float(vmax)
    vmin_f = _coerce_finite_float(vmin)
    if vmax_f is None or vmin_f is None:
        return {
            "code": CODE_RIPPLE_INPUT_INVALID,
            "ripple": 0.0,
            "vmax": vmax_f if vmax_f is not None else 0.0,
            "vmin": vmin_f if vmin_f is not None else 0.0,
            "passed": False,
            "detail": (
                "ripple_from_max_min requires two finite numbers; "
                f"got vmax={vmax!r}, vmin={vmin!r}"
            ),
        }
    ripple = vmax_f - vmin_f
    if vmin_f > vmax_f:
        return {
            "code": CODE_RIPPLE_INPUT_SWAPPED,
            "ripple": ripple,
            "vmax": vmax_f,
            "vmin": vmin_f,
            "passed": False,
            "detail": (
                f"vmin ({vmin_f:g}) is greater than vmax ({vmax_f:g}); "
                "inputs appear to be swapped. ripple = vmax - vmin is negative."
            ),
        }
    return {
        "code": "OK",
        "ripple": ripple,
        "vmax": vmax_f,
        "vmin": vmin_f,
        "passed": True,
        "detail": f"vmax={vmax_f:g}, vmin={vmin_f:g}, ripple={ripple:g}",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_finite_float(value: Any) -> float | None:
    """Return ``value`` as a finite ``float`` or ``None``."""
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
    "AnalysisKind",
    "MeasFunction",
    "MeasurementRequest",
    "ac_gain_at_frequency",
    "dc_voltage",
    "format_meas_directive",
    "generate_meas_directives",
    "parse_measurement_lines",
    "ripple_from_max_min",
    "transient_max",
    "transient_min",
    "transient_ripple",
]
