"""Calculation report generator for the math core.

The :mod:`calculation_report` module turns the deterministic output of
:mod:`formulas` and :mod:`standard_values` into the two report formats
the rest of the project consumes:

* :class:`CalculationReport` → ``calculation.json`` (machine-readable,
  embedded in the project artefact set, consumed by agents and the
  verification pipeline).
* ``render_markdown`` → ``calculation.md`` (human-readable, served
  alongside the project so a user can read the derivation without
  re-running the math).

The module is the last stop in the math pipeline:

1. :mod:`units` parses / formats engineering literals.
2. :mod:`formulas` evaluates the closed-form expression.
3. :mod:`standard_values` picks the closest E-series substitute.
4. :mod:`calculation_report` (this module) assembles the two reports.

Everything is pure: no filesystem, no subprocess, no clock. Callers
that want to *write* a report to disk call :func:`build_report` and
serialise the result themselves (so the same builder can be reused
across CLI, MCP, and test paths).

Report shape
------------

The JSON shape follows the example in plan section 16.2:

::

    {
      "schemaVersion": "0.1",
      "success": true,
      "topology": "rc_lowpass",
      "formulas": [
        {"name": "...", "expression": "..."}
      ],
      "idealValues": {"R": {"value": 1591.55, "unit": "ohm"}},
      "selectedValues": {"R": {"value": 1600, "unit": "ohm", "display": "1.6k"}},
      "predicted": {"fc": {"value": 994.718, "unit": "Hz"}, "errorPercent": 0.528},
      "assumptions": ["ideal capacitor", "no parasitic ESR/ESL"],
      "warnings": []
    }

The markdown shape follows plan section 16.1. The headings are stable
so downstream tooling can grep for them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Final

from .formulas import BuckResult, FormulaResult
from .standard_values import (
    StandardValueSelection,
    calculate_error_percent,
    nearest_standard_value,
)
from .units import format_value

#: Schema version of the ``calculation.json`` contract.
CALCULATION_SCHEMA_VERSION: Final = "0.1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


CODE_OK: Final = "OK"
CODE_FORMULA_FAILED: Final = "FORMULA_FAILED"
CODE_UNKNOWN_TOPOLOGY: Final = "UNKNOWN_TOPOLOGY"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NamedQuantity:
    """A single ``{value, unit, display}`` triple in a report.

    The ``value`` is the SI number; ``unit`` is the SI unit symbol;
    ``display`` is the short engineering string (e.g. ``"1.6k"``).
    The ``display`` field is intentionally separate so the report can
    preserve the human form even when the SI number is rounded for
    display.
    """

    value: float
    unit: str
    display: str

    def to_dict(self) -> dict[str, float | str]:
        return {"value": self.value, "unit": self.unit, "display": self.display}


@dataclass(frozen=True)
class FormulaEntry:
    """A formula reference embedded in the JSON report."""

    name: str
    expression: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class CalculationReport:
    """Top-level ``calculation.json`` structure.

    The class is mutable so callers can populate it incrementally. Once
    :meth:`to_dict` is called the shape is frozen and safe to
    serialise through :func:`ltagent.serialization.to_jsonable`.
    """

    topology: str
    description: str = ""
    formulas: list[FormulaEntry] = field(default_factory=list)
    ideal_values: dict[str, NamedQuantity] = field(default_factory=dict)
    selected_values: dict[str, NamedQuantity] = field(default_factory=dict)
    predicted: dict[str, NamedQuantity] = field(default_factory=dict)
    error_percent: float | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    success: bool = True
    code: str = CODE_OK
    detail: str = ""
    schema_version: str = CALCULATION_SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "success": self.success,
            "code": self.code,
            "detail": self.detail,
            "topology": self.topology,
            "description": self.description,
            "formulas": [f.to_dict() for f in self.formulas],
            "idealValues": {k: v.to_dict() for k, v in self.ideal_values.items()},
            "selectedValues": {k: v.to_dict() for k, v in self.selected_values.items()},
            "predicted": {k: v.to_dict() for k, v in self.predicted.items()},
            "errorPercent": self.error_percent,
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
            "extra": dict(self.extra),
        }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RCInput:
    """User-supplied inputs for an RC low-pass / high-pass report."""

    fc: float
    c: float
    series: str = "E24"
    topology: str = "rc_lowpass"
    fixed_resistor: float | None = None


@dataclass(frozen=True)
class OpAmpInput:
    """User-supplied inputs for an op-amp gain report."""

    gain: float
    rg: float
    series: str = "E24"
    topology: str = "noninv_opamp"  # or "inverting_opamp"


def _unit_for_quantity(quantity: str) -> str:
    """Best-effort SI unit lookup for the predicted-result block."""
    return {
        "resistance": "ohm",
        "capacitance": "F",
        "inductance": "H",
        "voltage": "V",
        "current": "A",
        "frequency": "Hz",
        "time": "s",
        "dimensionless": "",
    }.get(quantity, "")


def _make_named_quantity(
    value: float, quantity: str, unit: str | None = None
) -> NamedQuantity:
    """Wrap a numeric value in the ``NamedQuantity`` triple."""
    chosen_unit = unit if unit is not None else _unit_for_quantity(quantity)
    return NamedQuantity(
        value=value,
        unit=chosen_unit,
        display=format_value(value, chosen_unit),
    )


def _make_named_quantity_ohm(value: float) -> NamedQuantity:
    """Convenience: SI ``ohm`` is the natural unit for R and Rf / Rg."""
    return _make_named_quantity(value, "resistance", "ohm")


def _make_named_quantity_f(value: float) -> NamedQuantity:
    return _make_named_quantity(value, "capacitance", "F")


def _make_named_quantity_hz(value: float) -> NamedQuantity:
    return _make_named_quantity(value, "frequency", "Hz")


def _make_named_quantity_v(value: float) -> NamedQuantity:
    return _make_named_quantity(value, "voltage", "V")


def _make_named_quantity_a(value: float) -> NamedQuantity:
    return _make_named_quantity(value, "current", "A")


def _select_ohm(value: float, series: str) -> StandardValueSelection:
    """Pick a standard E-series resistor value; raised on bad series."""
    return nearest_standard_value(value, series)


# ---------------------------------------------------------------------------
# RC low-pass / high-pass report
# ---------------------------------------------------------------------------


def _build_rc_report(
    fc: float,
    c: float,
    series: str,
    topology: str,
    fixed_resistor: float | None,
) -> CalculationReport:
    """Build a report for a first-order RC filter."""
    from .formulas import (
        rc_highpass_cutoff,
        rc_highpass_resistor,
        rc_lowpass_cutoff,
        rc_lowpass_resistor,
    )

    if topology not in {"rc_lowpass", "rc_highpass"}:
        return CalculationReport(
            topology=topology,
            success=False,
            code=CODE_UNKNOWN_TOPOLOGY,
            detail=f"unknown topology {topology!r}; expected rc_lowpass or rc_highpass",
        )

    # Solve the equation for whichever variable the user did not fix.
    if fixed_resistor is None:
        # R is the unknown -> use *_resistor then verify fc.
        if topology == "rc_lowpass":
            resistor = rc_lowpass_resistor(fc=fc, c=c)
        else:
            resistor = rc_highpass_resistor(fc=fc, c=c)
        if not resistor.ok or resistor.result is None:
            return CalculationReport(
                topology=topology,
                success=False,
                code=CODE_FORMULA_FAILED,
                detail=resistor.detail,
            )
        ideal_r = resistor.result
        # The capacitor was a user input; treat it as both ideal and
        # selected (no series re-pick for caps in the MVP).
        ideal_c = c
        selected_c = c
        formula_name = (
            "rc_lowpass_resistor" if topology == "rc_lowpass"
            else "rc_highpass_resistor"
        )
        formula_expression = "R = 1 / (2 * pi * fc * C)"
        # Verify the predicted cutoff using the ideal R so the
        # "predicted" block is meaningful even when the standard-value
        # pick is far from the ideal.
        if topology == "rc_lowpass":
            verify = rc_lowpass_cutoff(r=ideal_r, c=ideal_c)
        else:
            verify = rc_highpass_cutoff(r=ideal_r, c=ideal_c)
    else:
        # R is the fixed input; solve for fc.
        if topology == "rc_lowpass":
            verify = rc_lowpass_cutoff(r=fixed_resistor, c=c)
        else:
            verify = rc_highpass_cutoff(r=fixed_resistor, c=c)
        if not verify.ok or verify.result is None:
            return CalculationReport(
                topology=topology,
                success=False,
                code=CODE_FORMULA_FAILED,
                detail=verify.detail,
            )
        ideal_r = fixed_resistor
        ideal_c = c
        selected_c = c
        formula_name = (
            "rc_lowpass_cutoff" if topology == "rc_lowpass"
            else "rc_highpass_cutoff"
        )
        formula_expression = "fc = 1 / (2 * pi * R * C)"

    # Standard-value selection for R.
    try:
        selection = _select_ohm(ideal_r, series)
    except ValueError as exc:
        return CalculationReport(
            topology=topology,
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=str(exc),
        )
    selected_r = selection.selected

    # Recompute the predicted cutoff using the *selected* values so
    # the report can compare it to the user's target.
    if topology == "rc_lowpass":
        final_cutoff = rc_lowpass_cutoff(r=selected_r, c=selected_c)
    else:
        final_cutoff = rc_highpass_cutoff(r=selected_r, c=selected_c)

    predicted_fc = (
        final_cutoff.result if final_cutoff.ok and final_cutoff.result is not None
        else None
    )
    error_percent = None if predicted_fc is None else calculate_error_percent(fc, predicted_fc)

    report = CalculationReport(
        topology=topology,
        description=(
            "First-order passive RC low-pass filter"
            if topology == "rc_lowpass"
            else "First-order passive RC high-pass filter"
        ),
        formulas=[FormulaEntry(name=formula_name, expression=formula_expression)],
        ideal_values={
            "R": _make_named_quantity_ohm(ideal_r),
            "C": _make_named_quantity_f(ideal_c),
        },
        selected_values={
            "R": NamedQuantity(
                value=selected_r,
                unit="ohm",
                display=format_value(selected_r, "ohm"),
            ),
            "C": NamedQuantity(
                value=selected_c,
                unit="F",
                display=format_value(selected_c, "F"),
            ),
        },
        predicted={
            "fc": _make_named_quantity_hz(predicted_fc)
            if predicted_fc is not None
            else _make_named_quantity_hz(0.0),
        },
        error_percent=error_percent,
        assumptions=[
            "ideal capacitor unless tolerance analysis is enabled",
            "no parasitic ESR / ESL modeled in MVP",
            "source impedance is assumed ideal",
        ],
        warnings=[],
        success=True,
    )
    if predicted_fc is not None and error_percent is not None and abs(error_percent) > 5.0:
        report.warnings.append(
            f"predicted cutoff is {error_percent:+.2f}% from the target; "
            "consider a finer E-series or a different fixed value"
        )
    return report


def build_rc_report(
    fc: float, c: float, series: str = "E24", topology: str = "rc_lowpass"
) -> CalculationReport:
    """Build a :class:`CalculationReport` for an RC filter design.

    Args:
        fc: Target cutoff frequency (Hz). Must be positive.
        c: Fixed capacitor value (F). Must be positive.
        series: Preferred-number series to pick R from.
        topology: Either ``"rc_lowpass"`` or ``"rc_highpass"``.

    Returns:
        A :class:`CalculationReport` whose ``success`` flag reflects
        whether every formula and lookup succeeded.
    """
    return _build_rc_report(
        fc=fc, c=c, series=series, topology=topology, fixed_resistor=None,
    )


# ---------------------------------------------------------------------------
# Op-amp gain reports
# ---------------------------------------------------------------------------


def _build_opamp_report(
    gain: float, rg: float, series: str, topology: str
) -> CalculationReport:
    """Build a report for an op-amp gain-stage design."""
    from .formulas import (
        inverting_opamp_feedback,
        noninverting_opamp_feedback,
    )

    if topology == "noninv_opamp":
        rf = noninverting_opamp_feedback(gain=gain, rg=rg)
    elif topology == "inverting_opamp":
        # The op-amp-side variable is "rg" in the caller, but the
        # inverting formula's input is named "rin" (the resistor at
        # the inverting input). Translate here so the caller can use a
        # uniform keyword.
        rf = inverting_opamp_feedback(gain=gain, rin=rg)
    else:
        return CalculationReport(
            topology=topology,
            success=False,
            code=CODE_UNKNOWN_TOPOLOGY,
            detail=(
                f"unknown topology {topology!r}; expected "
                "noninv_opamp or inverting_opamp"
            ),
        )
    if not rf.ok or rf.result is None:
        return CalculationReport(
            topology=topology,
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=rf.detail,
        )
    ideal_rf = rf.result
    try:
        selection_rf = _select_ohm(ideal_rf, series)
        selection_rg = _select_ohm(rg, series)
    except ValueError as exc:
        return CalculationReport(
            topology=topology,
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=str(exc),
        )

    selected_rf = selection_rf.selected
    selected_rg = selection_rg.selected
    if topology == "noninv_opamp":
        actual_gain = 1.0 + selected_rf / selected_rg
        expression = "Av = 1 + Rf / Rg"
    else:
        actual_gain = -(selected_rf / selected_rg)
        expression = "Av = -Rf / Rin"

    error_percent = calculate_error_percent(gain, actual_gain)

    return CalculationReport(
        topology=topology,
        description=(
            "Non-inverting operational amplifier"
            if topology == "noninv_opamp"
            else "Inverting operational amplifier"
        ),
        formulas=[FormulaEntry(name=rf.name, expression=rf.expression)],
        ideal_values={
            "Rf": _make_named_quantity_ohm(ideal_rf),
            "Rg": _make_named_quantity_ohm(rg),
        },
        selected_values={
            "Rf": NamedQuantity(
                value=selected_rf,
                unit="ohm",
                display=format_value(selected_rf, "ohm"),
            ),
            "Rg": NamedQuantity(
                value=selected_rg,
                unit="ohm",
                display=format_value(selected_rg, "ohm"),
            ),
        },
        predicted={
            "gain": _make_named_quantity(actual_gain, "dimensionless", ""),
        },
        error_percent=error_percent,
        assumptions=[
            "ideal op-amp with infinite open-loop gain and zero offset",
            "no parasitic capacitances included in the model",
            "feedback network alone sets the gain",
        ],
        warnings=[],
        success=True,
        extra={"expression": expression, "ideal_gain": gain, "actual_gain": actual_gain},
    )


def build_noninverting_opamp_report(
    gain: float, rg: float, series: str = "E24"
) -> CalculationReport:
    """Build a report for a non-inverting op-amp stage."""
    return _build_opamp_report(
        gain=gain, rg=rg, series=series, topology="noninv_opamp",
    )


def build_inverting_opamp_report(
    gain: float, rin: float, series: str = "E24"
) -> CalculationReport:
    """Build a report for an inverting op-amp stage."""
    return _build_opamp_report(
        gain=gain, rg=rin, series=series, topology="inverting_opamp",
    )


# ---------------------------------------------------------------------------
# LED resistor report
# ---------------------------------------------------------------------------


def build_led_resistor_report(
    v_supply: float, v_forward: float, i_led: float, series: str = "E24"
) -> CalculationReport:
    """Build a report for a current-limiting resistor around an LED."""
    from .formulas import led_resistor

    calc = led_resistor(
        v_supply=v_supply, v_forward=v_forward, i_led=i_led,
    )
    if not calc.ok or calc.result is None:
        return CalculationReport(
            topology="led_resistor",
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=calc.detail,
        )
    ideal_r = calc.result
    try:
        selection = _select_ohm(ideal_r, series)
    except ValueError as exc:
        return CalculationReport(
            topology="led_resistor",
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=str(exc),
        )

    report = CalculationReport(
        topology="led_resistor",
        description="Current-limiting resistor for an LED",
        formulas=[FormulaEntry(name=calc.name, expression=calc.expression)],
        ideal_values={
            "R": _make_named_quantity_ohm(ideal_r),
            "Vs": _make_named_quantity_v(v_supply),
            "Vf": _make_named_quantity_v(v_forward),
            "I": _make_named_quantity_a(i_led),
        },
        selected_values={
            "R": NamedQuantity(
                value=selection.selected,
                unit="ohm",
                display=format_value(selection.selected, "ohm"),
            ),
        },
        predicted={
            "p_dissipated": _make_named_quantity(
                calc.extra.get("p_dissipated", 0.0),
                "dimensionless",
                "W",
            ),
        },
        error_percent=selection.error_percent,
        assumptions=[
            "ideal supply with no internal resistance",
            "LED forward voltage is constant over the operating current",
        ],
        success=True,
    )
    return report


# ---------------------------------------------------------------------------
# Switched-mode reports
# ---------------------------------------------------------------------------


def build_buck_report(
    vin: float, vout: float, iout: float
) -> CalculationReport:
    """Build a report for an ideal buck converter."""
    from .formulas import buck_ideal

    res = buck_ideal(vin=vin, vout=vout, iout=iout)
    if not isinstance(res, BuckResult):
        return CalculationReport(
            topology="buck_ideal",
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=res.detail if isinstance(res, FormulaResult) else str(res),
        )
    return CalculationReport(
        topology="buck_ideal",
        description="Ideal buck (step-down) converter",
        formulas=[FormulaEntry(name="buck_ideal", expression="D = Vout / Vin, R = Vout / Iout")],
        ideal_values={
            "Vin": _make_named_quantity_v(vin),
            "Vout": _make_named_quantity_v(vout),
            "Iout": _make_named_quantity_a(iout),
        },
        selected_values={
            "R_load": NamedQuantity(
                value=res.r_load,
                unit="ohm",
                display=format_value(res.r_load, "ohm"),
            ),
        },
        predicted={
            "duty": _make_named_quantity(res.duty, "dimensionless", ""),
            "P_out": _make_named_quantity(res.p_out, "dimensionless", "W"),
        },
        assumptions=[
            "lossless switch and inductor",
            "continuous conduction mode",
        ],
        success=True,
    )


def build_boost_report(
    vin: float, vout: float, iout: float
) -> CalculationReport:
    """Build a report for an ideal boost converter."""
    from .formulas import boost_ideal

    res = boost_ideal(vin=vin, vout=vout, iout=iout)
    if not isinstance(res, BuckResult):
        return CalculationReport(
            topology="boost_ideal",
            success=False,
            code=CODE_FORMULA_FAILED,
            detail=res.detail if isinstance(res, FormulaResult) else str(res),
        )
    return CalculationReport(
        topology="boost_ideal",
        description="Ideal boost (step-up) converter",
        formulas=[FormulaEntry(name="boost_ideal", expression="D = 1 - Vin / Vout, R = Vout / Iout")],
        ideal_values={
            "Vin": _make_named_quantity_v(vin),
            "Vout": _make_named_quantity_v(vout),
            "Iout": _make_named_quantity_a(iout),
        },
        selected_values={
            "R_load": NamedQuantity(
                value=res.r_load,
                unit="ohm",
                display=format_value(res.r_load, "ohm"),
            ),
        },
        predicted={
            "duty": _make_named_quantity(res.duty, "dimensionless", ""),
            "P_out": _make_named_quantity(res.p_out, "dimensionless", "W"),
        },
        assumptions=[
            "lossless switch and inductor",
            "continuous conduction mode",
        ],
        success=True,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _md_format_named(named: NamedQuantity) -> str:
    """Render a NamedQuantity as ``"1.6kΩ (1600 ohm)"`` for the markdown."""
    return f"`{named.display}` ({named.value:g} {named.unit})"


def render_markdown(report: CalculationReport) -> str:
    """Render ``report`` as a Markdown document.

    The output is intended to be written to ``calculation.md`` next to
    the IR. The headings and bullet structure are part of the contract
    — they are what the verification reader greps for.
    """
    lines: list[str] = []
    lines.append(f"# Calculation Report: {report.topology}")
    if report.description:
        lines.append("")
        lines.append(f"_{report.description}_")
    lines.append("")
    if not report.success:
        lines.append(f"**Status:** FAILED ({report.code})")
        if report.detail:
            lines.append("")
            lines.append(f"> {report.detail}")
        return "\n".join(lines) + "\n"

    lines.append("**Status:** OK")
    lines.append("")

    # --- Formulas ---------------------------------------------------------
    if report.formulas:
        lines.append("## Formulas")
        lines.append("")
        for f in report.formulas:
            lines.append(f"- `{f.name}`: `{f.expression}`")
        lines.append("")

    # --- Ideal values -----------------------------------------------------
    if report.ideal_values:
        lines.append("## Ideal Values")
        lines.append("")
        for name, q in report.ideal_values.items():
            lines.append(f"- {name} = {_md_format_named(q)}")
        lines.append("")

    # --- Standard-value selection -----------------------------------------
    if report.selected_values:
        lines.append("## Standard Value Selection")
        lines.append("")
        for name, q in report.selected_values.items():
            lines.append(f"- {name} = {_md_format_named(q)}")
        lines.append("")

    # --- Predicted result -------------------------------------------------
    if report.predicted:
        lines.append("## Predicted Result")
        lines.append("")
        for name, q in report.predicted.items():
            lines.append(f"- {name} = {_md_format_named(q)}")
        if report.error_percent is not None:
            lines.append(f"- error = {report.error_percent:+.3f}%")
        lines.append("")

    # --- Warnings ---------------------------------------------------------
    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # --- Assumptions ------------------------------------------------------
    if report.assumptions:
        lines.append("## Assumptions")
        lines.append("")
        for a in report.assumptions:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "CALCULATION_SCHEMA_VERSION",
    "CODE_FORMULA_FAILED",
    "CODE_OK",
    "CODE_UNKNOWN_TOPOLOGY",
    "CalculationReport",
    "FormulaEntry",
    "NamedQuantity",
    "build_boost_report",
    "build_buck_report",
    "build_inverting_opamp_report",
    "build_led_resistor_report",
    "build_noninverting_opamp_report",
    "build_rc_report",
    "render_markdown",
]
