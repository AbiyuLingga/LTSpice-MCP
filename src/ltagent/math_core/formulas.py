"""Closed-form circuit formulas for the math core.

The math core is the only place in the project that is allowed to
evaluate the closed-form equations that drive simple analog circuits.
The LLM layer is restricted to *naming* the topology and supplying the
inputs; the math core returns the ideal component value, the standard
E-series value, and the predicted result so the agent has a fully
deterministic, reproducible plan to compare against the eventual
LTspice simulation.

Every formula function in this module follows the same contract:

* Inputs are *plain Python floats* (not stringly typed). The caller is
  expected to have already normalised units through
  :func:`ltagent.math_core.units.parse_to_si` (or
  :func:`ltagent.math_core.units.parse_value`). Stringly-typed inputs
  were rejected early in the design phase: they would have made the
  verification flow dependent on string parsing, and they would have
  hidden unit bugs (e.g. an RC cutoff computed from a capacitor in
  farads but a resistance in kilohms).
* The function returns a :class:`FormulaResult` dataclass that carries
  the ideal value, the formula expression, and a structured description
  of the inputs. Callers can then ask
  :mod:`ltagent.math_core.standard_values` for a preferred-value
  substitute. Keeping the lookup decoupled means the same formula can
  be used with no series ("ideal"), with E24, or with a custom set of
  user-supplied values.
* The functions never raise. If the inputs are nonsensical (negative
  resistance, zero capacitance, …) the function returns a
  :class:`FormulaResult` with ``ok=False`` and a stable ``code`` field.

Formula catalog
---------------

The MVP catalog covers the topologies promised by the live-editing
plan section 14.3 and 15.2:

* :func:`voltage_divider_vout` / :func:`voltage_divider_ratio`
* :func:`rc_lowpass_cutoff` / :func:`rc_lowpass_resistor`
* :func:`rc_highpass_cutoff` / :func:`rc_highpass_resistor`
* :func:`inverting_opamp_gain` / :func:`inverting_opamp_feedback`
* :func:`noninverting_opamp_gain` / :func:`noninverting_opamp_feedback`
* :func:`led_resistor`
* :func:`buck_ideal` / :func:`boost_ideal`

A registry (:data:`FORMULA_REGISTRY`) maps a topology name to a
:class:`TopologyFormula` description so the calculation-report layer
can describe the math without a hard-coded ``if/elif`` tree.

Note on units
-------------

All values are SI base units. ``R`` is in ohms, ``C`` in farads,
``f`` / ``fc`` in hertz, ``V`` in volts, ``I`` in amps. The caller is
responsible for converting to / from the engineering literals shown in
the schematic.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

CODE_OK: Final = "OK"
CODE_INVALID_INPUT: Final = "FORMULA_INPUT_INVALID"
CODE_NON_FINITE: Final = "FORMULA_INPUT_NON_FINITE"
CODE_NON_POSITIVE: Final = "FORMULA_INPUT_NON_POSITIVE"
CODE_DIVISION_BY_ZERO: Final = "FORMULA_DIVISION_BY_ZERO"
CODE_INVERTING_GAIN_SIGN: Final = "FORMULA_INVERTING_GAIN_SIGN"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormulaResult:
    """Generic envelope for a single-formula calculation.

    Attributes:
        name: Short identifier (e.g. ``"rc_lowpass_cutoff"``).
        expression: The symbolic expression that was evaluated. Used
            verbatim in the calculation report so the LLM never has to
            *interpret* a formula.
        inputs: Mapping of the input names to numeric values. SI base
            units, no engineering prefix.
        result: The computed value, or ``None`` if the formula failed.
        ok: ``True`` iff the result is meaningful.
        code: Stable error code. :data:`CODE_OK` on success.
        detail: Human-readable explanation for the report.
        extra: Optional structured extra information, e.g. ``{"gain":
            -10.0}`` for the inverting op-amp. Used by the report
            builder to enrich the JSON output.
    """

    name: str
    expression: str
    inputs: dict[str, float]
    result: float | None
    ok: bool
    code: str
    detail: str
    extra: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "expression": self.expression,
            "inputs": dict(self.inputs),
            "result": self.result,
            "ok": self.ok,
            "code": self.code,
            "detail": self.detail,
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class TopologyFormula:
    """A named formula plus a human description, used by the report layer.

    Attributes:
        name: Topology identifier (e.g. ``"rc_lowpass"``).
        description: One-line description.
        expression: Symbolic expression (the same string used in
            :class:`FormulaResult`).
        variables: Mapping from variable name to ``(quantity, unit)``.
        compute: A zero-arg callable that returns a
            :class:`FormulaResult` when called with no arguments —
            callers must supply the inputs through closure. This
            indirection lets the registry carry the function reference
            alongside its metadata.
    """

    name: str
    description: str
    expression: str
    variables: dict[str, tuple[str, str]]
    compute: Callable[[], FormulaResult]


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------


def _ensure_finite(name: str, value: float) -> str | None:
    """Return an error code if ``value`` is not a finite ``float``."""
    if value is None:
        return f"{name}: missing"
    if not isinstance(value, (int, float)):
        return f"{name}: not numeric ({type(value).__name__})"
    if isinstance(value, float) and not math.isfinite(value):
        return f"{name}: not finite ({value!r})"
    return None


def _ensure_positive(name: str, value: float) -> str | None:
    err = _ensure_finite(name, value)
    if err is not None:
        return err
    if value <= 0:
        return f"{name}: must be > 0 (got {value!r})"
    return None


# ---------------------------------------------------------------------------
# Voltage divider
# ---------------------------------------------------------------------------


def voltage_divider_vout(
    vin: float, r1: float, r2: float
) -> FormulaResult:
    """``Vout = Vin * R2 / (R1 + R2)`` for a passive resistive divider.

    Args:
        vin: Input voltage (V). Any finite value; a negative ``vin``
            is allowed and the sign is preserved.
        r1: Top resistor (Ω). Must be positive.
        r2: Bottom resistor (Ω). Must be positive.

    Returns:
        A :class:`FormulaResult` whose ``result`` is the predicted
        output voltage in volts. If either resistor is non-positive
        the function returns ``ok=False`` with a structured
        :data:`CODE_INVALID_INPUT` code.
    """
    inputs = {"vin": float(vin), "r1": float(r1), "r2": float(r2)}
    for name in ("r1", "r2"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="voltage_divider_vout",
                expression="Vout = Vin * R2 / (R1 + R2)",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    if not math.isfinite(inputs["vin"]):
        return FormulaResult(
            name="voltage_divider_vout",
            expression="Vout = Vin * R2 / (R1 + R2)",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_NON_FINITE,
            detail=f"vin: not finite ({vin!r})",
        )
    total = inputs["r1"] + inputs["r2"]
    if total == 0:
        return FormulaResult(
            name="voltage_divider_vout",
            expression="Vout = Vin * R2 / (R1 + R2)",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_DIVISION_BY_ZERO,
            detail="r1 + r2 == 0",
        )
    result = inputs["vin"] * inputs["r2"] / total
    return FormulaResult(
        name="voltage_divider_vout",
        expression="Vout = Vin * R2 / (R1 + R2)",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=(
            f"Vout = {inputs['vin']} * {inputs['r2']} / "
            f"({inputs['r1']} + {inputs['r2']})"
        ),
    )


def voltage_divider_ratio(r1: float, r2: float) -> float:
    """``R2 / (R1 + R2)`` — the divider's attenuation factor (no units)."""
    total = r1 + r2
    if total == 0:
        raise ValueError("voltage_divider_ratio requires r1 + r2 > 0")
    return r2 / total


# ---------------------------------------------------------------------------
# RC low-pass
# ---------------------------------------------------------------------------


def rc_lowpass_cutoff(r: float, c: float) -> FormulaResult:
    """``fc = 1 / (2 * pi * R * C)`` for a first-order RC low-pass."""
    inputs = {"r": float(r), "c": float(c)}
    for name in ("r", "c"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="rc_lowpass_cutoff",
                expression="fc = 1 / (2 * pi * R * C)",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    rc = inputs["r"] * inputs["c"]
    if rc == 0:
        return FormulaResult(
            name="rc_lowpass_cutoff",
            expression="fc = 1 / (2 * pi * R * C)",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_DIVISION_BY_ZERO,
            detail="R * C == 0",
        )
    result = 1.0 / (2.0 * math.pi * rc)
    return FormulaResult(
        name="rc_lowpass_cutoff",
        expression="fc = 1 / (2 * pi * R * C)",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=(
            f"fc = 1 / (2 * pi * {inputs['r']} * {inputs['c']})"
        ),
    )


def rc_lowpass_resistor(fc: float, c: float) -> FormulaResult:
    """``R = 1 / (2 * pi * fc * C)`` — solve the low-pass equation for R."""
    inputs = {"fc": float(fc), "c": float(c)}
    for name in ("fc", "c"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="rc_lowpass_resistor",
                expression="R = 1 / (2 * pi * fc * C)",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    denom = 2.0 * math.pi * inputs["fc"] * inputs["c"]
    if denom == 0:
        return FormulaResult(
            name="rc_lowpass_resistor",
            expression="R = 1 / (2 * pi * fc * C)",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_DIVISION_BY_ZERO,
            detail="2 * pi * fc * C == 0",
        )
    result = 1.0 / denom
    return FormulaResult(
        name="rc_lowpass_resistor",
        expression="R = 1 / (2 * pi * fc * C)",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=(
            f"R = 1 / (2 * pi * {inputs['fc']} * {inputs['c']})"
        ),
    )


# ---------------------------------------------------------------------------
# RC high-pass
# ---------------------------------------------------------------------------


def rc_highpass_cutoff(r: float, c: float) -> FormulaResult:
    """``fc = 1 / (2 * pi * R * C)`` for a first-order RC high-pass.

    Mathematically identical to the low-pass cutoff; the equation is
    kept as a separate entry so the topology-specific report can label
    it correctly.
    """
    inputs = {"r": float(r), "c": float(c)}
    for name in ("r", "c"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="rc_highpass_cutoff",
                expression="fc = 1 / (2 * pi * R * C)",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    rc = inputs["r"] * inputs["c"]
    if rc == 0:
        return FormulaResult(
            name="rc_highpass_cutoff",
            expression="fc = 1 / (2 * pi * R * C)",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_DIVISION_BY_ZERO,
            detail="R * C == 0",
        )
    result = 1.0 / (2.0 * math.pi * rc)
    return FormulaResult(
        name="rc_highpass_cutoff",
        expression="fc = 1 / (2 * pi * R * C)",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=(
            f"fc = 1 / (2 * pi * {inputs['r']} * {inputs['c']})"
        ),
    )


def rc_highpass_resistor(fc: float, c: float) -> FormulaResult:
    """``R = 1 / (2 * pi * fc * C)`` — solve the high-pass equation for R."""
    inputs = {"fc": float(fc), "c": float(c)}
    for name in ("fc", "c"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="rc_highpass_resistor",
                expression="R = 1 / (2 * pi * fc * C)",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    denom = 2.0 * math.pi * inputs["fc"] * inputs["c"]
    if denom == 0:
        return FormulaResult(
            name="rc_highpass_resistor",
            expression="R = 1 / (2 * pi * fc * C)",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_DIVISION_BY_ZERO,
            detail="2 * pi * fc * C == 0",
        )
    result = 1.0 / denom
    return FormulaResult(
        name="rc_highpass_resistor",
        expression="R = 1 / (2 * pi * fc * C)",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=(
            f"R = 1 / (2 * pi * {inputs['fc']} * {inputs['c']})"
        ),
    )


# ---------------------------------------------------------------------------
# Inverting op-amp
# ---------------------------------------------------------------------------


def inverting_opamp_gain(rf: float, rin: float) -> FormulaResult:
    """``Av = -Rf / Rin`` for the inverting op-amp topology."""
    inputs = {"rf": float(rf), "rin": float(rin)}
    for name in ("rf", "rin"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="inverting_opamp_gain",
                expression="Av = -Rf / Rin",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    result = -inputs["rf"] / inputs["rin"]
    return FormulaResult(
        name="inverting_opamp_gain",
        expression="Av = -Rf / Rin",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=f"Av = -({inputs['rf']}) / ({inputs['rin']})",
        extra={"gain": result, "abs_gain": abs(result)},
    )


def inverting_opamp_feedback(gain: float, rin: float) -> FormulaResult:
    """``Rf = |Av| * Rin`` — solve the inverting equation for Rf.

    The gain ``Av`` is allowed to be negative (inverting amplifiers
    have negative gain by construction). The result is a positive
    resistor value.
    """
    inputs = {"gain": float(gain), "rin": float(rin)}
    err = _ensure_positive("rin", inputs["rin"])
    if err is not None:
        return FormulaResult(
            name="inverting_opamp_feedback",
            expression="Rf = |Av| * Rin",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=err,
        )
    if not math.isfinite(inputs["gain"]):
        return FormulaResult(
            name="inverting_opamp_feedback",
            expression="Rf = |Av| * Rin",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_NON_FINITE,
            detail=f"gain: not finite ({gain!r})",
        )
    if inputs["gain"] >= 0:
        return FormulaResult(
            name="inverting_opamp_feedback",
            expression="Rf = |Av| * Rin",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVERTING_GAIN_SIGN,
            detail=(
                "inverting_opamp_feedback expects a negative gain; got "
                f"{gain!r}. For positive gain use noninverting_opamp_feedback."
            ),
        )
    result = abs(inputs["gain"]) * inputs["rin"]
    return FormulaResult(
        name="inverting_opamp_feedback",
        expression="Rf = |Av| * Rin",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=f"Rf = |{inputs['gain']}| * {inputs['rin']}",
    )


# ---------------------------------------------------------------------------
# Non-inverting op-amp
# ---------------------------------------------------------------------------


def noninverting_opamp_gain(rf: float, rg: float) -> FormulaResult:
    """``Av = 1 + Rf / Rg`` for the non-inverting op-amp topology."""
    inputs = {"rf": float(rf), "rg": float(rg)}
    for name in ("rf", "rg"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="noninverting_opamp_gain",
                expression="Av = 1 + Rf / Rg",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    result = 1.0 + inputs["rf"] / inputs["rg"]
    return FormulaResult(
        name="noninverting_opamp_gain",
        expression="Av = 1 + Rf / Rg",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=f"Av = 1 + ({inputs['rf']}) / ({inputs['rg']})",
        extra={"gain": result},
    )


def noninverting_opamp_feedback(gain: float, rg: float) -> FormulaResult:
    """``Rf = (Av - 1) * Rg`` — solve the non-inverting equation for Rf.

    The required gain must be strictly greater than one (any op-amp
    has a baseline gain of 1, the feedback network amplifies on top).
    """
    inputs = {"gain": float(gain), "rg": float(rg)}
    err = _ensure_positive("rg", inputs["rg"])
    if err is not None:
        return FormulaResult(
            name="noninverting_opamp_feedback",
            expression="Rf = (Av - 1) * Rg",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=err,
        )
    if not math.isfinite(inputs["gain"]):
        return FormulaResult(
            name="noninverting_opamp_feedback",
            expression="Rf = (Av - 1) * Rg",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_NON_FINITE,
            detail=f"gain: not finite ({gain!r})",
        )
    if inputs["gain"] <= 1.0:
        return FormulaResult(
            name="noninverting_opamp_feedback",
            expression="Rf = (Av - 1) * Rg",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=(
                "noninverting_opamp_feedback requires gain > 1; got "
                f"{gain!r}. A unity-gain buffer is Rf=0; use a different topology."
            ),
        )
    result = (inputs["gain"] - 1.0) * inputs["rg"]
    return FormulaResult(
        name="noninverting_opamp_feedback",
        expression="Rf = (Av - 1) * Rg",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=f"Rf = ({inputs['gain']} - 1) * {inputs['rg']}",
    )


# ---------------------------------------------------------------------------
# LED current-limiting resistor
# ---------------------------------------------------------------------------


def led_resistor(
    v_supply: float, v_forward: float, i_led: float
) -> FormulaResult:
    """``R = (Vs - Vf) / I`` for a simple LED current limiter.

    Args:
        v_supply: Supply voltage (V). Must be positive and strictly
            greater than ``v_forward`` (otherwise the math goes
            negative and the LED would not conduct at all).
        v_forward: LED forward voltage (V). Must be positive.
        i_led: LED forward current (A). Must be positive.

    Returns:
        A :class:`FormulaResult` whose ``result`` is the resistor
        value in ohms. Power dissipation in the resistor is also
        reported in the ``extra`` map as ``p_dissipated``.
    """
    inputs = {
        "v_supply": float(v_supply),
        "v_forward": float(v_forward),
        "i_led": float(i_led),
    }
    for name in ("v_supply", "v_forward", "i_led"):
        err = _ensure_positive(name, inputs[name])
        if err is not None:
            return FormulaResult(
                name="led_resistor",
                expression="R = (Vs - Vf) / I",
                inputs=inputs,
                result=None,
                ok=False,
                code=CODE_INVALID_INPUT,
                detail=err,
            )
    headroom = inputs["v_supply"] - inputs["v_forward"]
    if headroom <= 0:
        return FormulaResult(
            name="led_resistor",
            expression="R = (Vs - Vf) / I",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=(
                f"v_supply ({inputs['v_supply']}) must exceed "
                f"v_forward ({inputs['v_forward']})"
            ),
        )
    result = headroom / inputs["i_led"]
    p_dissipated = inputs["i_led"] * inputs["i_led"] * result
    return FormulaResult(
        name="led_resistor",
        expression="R = (Vs - Vf) / I",
        inputs=inputs,
        result=result,
        ok=True,
        code=CODE_OK,
        detail=(
            f"R = ({inputs['v_supply']} - {inputs['v_forward']}) / "
            f"{inputs['i_led']}"
        ),
        extra={"p_dissipated": p_dissipated, "headroom": headroom},
    )


# ---------------------------------------------------------------------------
# Switched-mode power supplies (ideal, lossless)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuckResult:
    """Multi-output result for the ideal buck converter.

    A buck converter's design typically depends on *two* independent
    choices: the desired output voltage (which sets the duty cycle
    through ``D = Vout / Vin``) and the desired output current (which
    sets the load resistance through ``R = Vout / Iout``). Returning a
    dataclass instead of a single :class:`FormulaResult` keeps both
    numbers together and lets the report layer show them side by side.
    """

    duty: float
    r_load: float
    p_out: float
    inputs: dict[str, float]

    def to_dict(self) -> dict[str, float]:
        return {
            "duty": self.duty,
            "rLoad": self.r_load,
            "pOut": self.p_out,
            **self.inputs,
        }


def buck_ideal(vin: float, vout: float, iout: float) -> BuckResult | FormulaResult:
    """Ideal buck converter: ``D = Vout / Vin``, ``R = Vout / Iout``.

    Returns a :class:`BuckResult` on success or a :class:`FormulaResult`
    with ``ok=False`` on failure. The function rejects ``vin <= 0`` and
    ``vout > vin`` because a buck converter cannot step up.
    """
    inputs = {
        "vin": float(vin),
        "vout": float(vout),
        "iout": float(iout),
    }
    if not math.isfinite(inputs["vin"]) or inputs["vin"] <= 0:
        return FormulaResult(
            name="buck_ideal",
            expression="D = Vout / Vin, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=f"vin must be > 0 (got {vin!r})",
        )
    if not math.isfinite(inputs["vout"]) or inputs["vout"] <= 0:
        return FormulaResult(
            name="buck_ideal",
            expression="D = Vout / Vin, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=f"vout must be > 0 (got {vout!r})",
        )
    if inputs["vout"] > inputs["vin"]:
        return FormulaResult(
            name="buck_ideal",
            expression="D = Vout / Vin, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=(
                f"buck converter cannot step up: vout ({vout!r}) > vin ({vin!r})"
            ),
        )
    if not math.isfinite(inputs["iout"]) or inputs["iout"] <= 0:
        return FormulaResult(
            name="buck_ideal",
            expression="D = Vout / Vin, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=f"iout must be > 0 (got {iout!r})",
        )
    duty = inputs["vout"] / inputs["vin"]
    r_load = inputs["vout"] / inputs["iout"]
    p_out = inputs["vout"] * inputs["iout"]
    return BuckResult(
        duty=duty,
        r_load=r_load,
        p_out=p_out,
        inputs=inputs,
    )


def boost_ideal(
    vin: float, vout: float, iout: float
) -> BuckResult | FormulaResult:
    """Ideal boost converter: ``D = 1 - Vin / Vout``, ``R = Vout / Iout``.

    The result is a :class:`BuckResult` because the report shape is
    identical (duty, R_load, P_out) — only the duty-cycle formula
    differs. Returns a :class:`FormulaResult` with ``ok=False`` on
    invalid input (``vout <= vin`` is rejected because a boost cannot
    step down).
    """
    inputs = {
        "vin": float(vin),
        "vout": float(vout),
        "iout": float(iout),
    }
    if not math.isfinite(inputs["vin"]) or inputs["vin"] <= 0:
        return FormulaResult(
            name="boost_ideal",
            expression="D = 1 - Vin / Vout, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=f"vin must be > 0 (got {vin!r})",
        )
    if not math.isfinite(inputs["vout"]) or inputs["vout"] <= 0:
        return FormulaResult(
            name="boost_ideal",
            expression="D = 1 - Vin / Vout, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=f"vout must be > 0 (got {vout!r})",
        )
    if inputs["vout"] <= inputs["vin"]:
        return FormulaResult(
            name="boost_ideal",
            expression="D = 1 - Vin / Vout, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=(
                f"boost converter cannot step down: vout ({vout!r}) <= vin ({vin!r})"
            ),
        )
    if not math.isfinite(inputs["iout"]) or inputs["iout"] <= 0:
        return FormulaResult(
            name="boost_ideal",
            expression="D = 1 - Vin / Vout, R = Vout / Iout",
            inputs=inputs,
            result=None,
            ok=False,
            code=CODE_INVALID_INPUT,
            detail=f"iout must be > 0 (got {iout!r})",
        )
    duty = 1.0 - inputs["vin"] / inputs["vout"]
    r_load = inputs["vout"] / inputs["iout"]
    p_out = inputs["vout"] * inputs["iout"]
    return BuckResult(
        duty=duty,
        r_load=r_load,
        p_out=p_out,
        inputs=inputs,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


#: Catalog of every formula the report layer can describe. The ``compute``
#: callables carry closures over the inputs so the report can render
#: without re-passing the same arguments.
FORMULA_REGISTRY: dict[str, TopologyFormula] = {
    "voltage_divider": TopologyFormula(
        name="voltage_divider",
        description="Passive resistive voltage divider",
        expression="Vout = Vin * R2 / (R1 + R2)",
        variables={
            "vin": ("voltage", "V"),
            "r1": ("resistance", "ohm"),
            "r2": ("resistance", "ohm"),
            "vout": ("voltage", "V"),
        },
        # The actual computation lives in voltage_divider_vout; the
        # registry entry just describes the equation so the report
        # builder can render the formula even if the inputs are not
        # yet known.
        compute=lambda: FormulaResult(
            name="voltage_divider",
            expression="Vout = Vin * R2 / (R1 + R2)",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "rc_lowpass": TopologyFormula(
        name="rc_lowpass",
        description="First-order passive RC low-pass filter",
        expression="fc = 1 / (2 * pi * R * C)",
        variables={
            "r": ("resistance", "ohm"),
            "c": ("capacitance", "F"),
            "fc": ("frequency", "Hz"),
        },
        compute=lambda: FormulaResult(
            name="rc_lowpass",
            expression="fc = 1 / (2 * pi * R * C)",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "rc_highpass": TopologyFormula(
        name="rc_highpass",
        description="First-order passive RC high-pass filter",
        expression="fc = 1 / (2 * pi * R * C)",
        variables={
            "r": ("resistance", "ohm"),
            "c": ("capacitance", "F"),
            "fc": ("frequency", "Hz"),
        },
        compute=lambda: FormulaResult(
            name="rc_highpass",
            expression="fc = 1 / (2 * pi * R * C)",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "inverting_opamp": TopologyFormula(
        name="inverting_opamp",
        description="Inverting operational amplifier",
        expression="Av = -Rf / Rin",
        variables={
            "rf": ("resistance", "ohm"),
            "rin": ("resistance", "ohm"),
            "gain": ("dimensionless", ""),
        },
        compute=lambda: FormulaResult(
            name="inverting_opamp",
            expression="Av = -Rf / Rin",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "noninv_opamp": TopologyFormula(
        name="noninv_opamp",
        description="Non-inverting operational amplifier",
        expression="Av = 1 + Rf / Rg",
        variables={
            "rf": ("resistance", "ohm"),
            "rg": ("resistance", "ohm"),
            "gain": ("dimensionless", ""),
        },
        compute=lambda: FormulaResult(
            name="noninv_opamp",
            expression="Av = 1 + Rf / Rg",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "led_resistor": TopologyFormula(
        name="led_resistor",
        description="LED current-limiting resistor",
        expression="R = (Vs - Vf) / I",
        variables={
            "v_supply": ("voltage", "V"),
            "v_forward": ("voltage", "V"),
            "i_led": ("current", "A"),
            "r": ("resistance", "ohm"),
        },
        compute=lambda: FormulaResult(
            name="led_resistor",
            expression="R = (Vs - Vf) / I",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "buck_ideal": TopologyFormula(
        name="buck_ideal",
        description="Ideal buck (step-down) converter",
        expression="D = Vout / Vin, R = Vout / Iout",
        variables={
            "vin": ("voltage", "V"),
            "vout": ("voltage", "V"),
            "iout": ("current", "A"),
            "duty": ("dimensionless", ""),
            "r_load": ("resistance", "ohm"),
        },
        compute=lambda: FormulaResult(
            name="buck_ideal",
            expression="D = Vout / Vin, R = Vout / Iout",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
    "boost_ideal": TopologyFormula(
        name="boost_ideal",
        description="Ideal boost (step-up) converter",
        expression="D = 1 - Vin / Vout, R = Vout / Iout",
        variables={
            "vin": ("voltage", "V"),
            "vout": ("voltage", "V"),
            "iout": ("current", "A"),
            "duty": ("dimensionless", ""),
            "r_load": ("resistance", "ohm"),
        },
        compute=lambda: FormulaResult(
            name="boost_ideal",
            expression="D = 1 - Vin / Vout, R = Vout / Iout",
            inputs={},
            result=None,
            ok=False,
            code=CODE_OK,
            detail="metadata only",
        ),
    ),
}


__all__ = [
    "CODE_DIVISION_BY_ZERO",
    "CODE_INVALID_INPUT",
    "CODE_INVERTING_GAIN_SIGN",
    "CODE_NON_FINITE",
    "CODE_NON_POSITIVE",
    "CODE_OK",
    "FORMULA_REGISTRY",
    "BuckResult",
    "FormulaResult",
    "TopologyFormula",
    "boost_ideal",
    "buck_ideal",
    "inverting_opamp_feedback",
    "inverting_opamp_gain",
    "led_resistor",
    "noninverting_opamp_feedback",
    "noninverting_opamp_gain",
    "rc_highpass_cutoff",
    "rc_highpass_resistor",
    "rc_lowpass_cutoff",
    "rc_lowpass_resistor",
    "voltage_divider_ratio",
    "voltage_divider_vout",
]
