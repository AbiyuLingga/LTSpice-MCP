"""Public deterministic calculation facade used by CLI and MCP adapters."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from .calculation_report import (
    build_boost_report,
    build_buck_report,
    build_inverting_opamp_report,
    build_led_resistor_report,
    build_noninverting_opamp_report,
    build_rc_report,
)
from .formulas import led_resistor, voltage_divider_vout
from .units import ParsedValue, UnitError, format_value, parse_value

_FORMULA_METADATA: Final[dict[str, dict[str, Any]]] = {
    "voltage_divider": {
        "description": "Two-resistor voltage divider",
        "expression": "Vout = Vin * R2 / (R1 + R2)",
        "assumptions": ["no load current on Vout", "ideal resistors"],
    },
    "rc_lowpass": {
        "description": "First-order passive RC low-pass filter",
        "expression": "fc = 1 / (2*pi*R*C)",
        "assumptions": ["ideal capacitor", "ideal source and load impedance"],
    },
    "rc_highpass": {
        "description": "First-order passive RC high-pass filter",
        "expression": "fc = 1 / (2*pi*R*C)",
        "assumptions": ["ideal capacitor", "ideal source and load impedance"],
    },
    "noninv_opamp": {
        "description": "Non-inverting operational amplifier",
        "expression": "Av = 1 + Rf / Rg",
        "assumptions": ["ideal op-amp", "ideal feedback resistors"],
    },
    "inverting_opamp": {
        "description": "Inverting operational amplifier",
        "expression": "Av = -Rf / Rin",
        "assumptions": ["ideal op-amp", "ideal feedback resistors"],
    },
    "led_resistor": {
        "description": "LED current-limiting resistor",
        "expression": "R = (Vs - Vf) / I",
        "assumptions": ["constant LED forward voltage", "ideal supply"],
    },
    "buck_ideal": {
        "description": "Ideal buck converter",
        "expression": "D = Vout / Vin, Rload = Vout / Iout",
        "assumptions": ["lossless components", "continuous conduction mode"],
    },
    "boost_ideal": {
        "description": "Ideal boost converter",
        "expression": "D = 1 - Vin / Vout, Rload = Vout / Iout",
        "assumptions": ["lossless components", "continuous conduction mode"],
    },
}


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        parsed = parse_value(value)
        if isinstance(parsed, UnitError):
            raise ValueError(parsed.message)
        assert isinstance(parsed, ParsedValue)
        result = parsed.si_value
    else:
        raise ValueError(f"{name} must be numeric or an engineering value")
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _quantity(value: float, unit: str) -> dict[str, float | str]:
    return {"value": value, "unit": unit, "display": format_value(value, unit)}


def _base(topology: str) -> dict[str, Any]:
    meta = _FORMULA_METADATA[topology]
    return {
        "success": True,
        "topology": topology,
        "description": meta["description"],
        "formulas": [{"name": topology, "expression": meta["expression"]}],
        "idealValues": {},
        "selectedValues": {},
        "predicted": {},
        "assumptions": list(meta["assumptions"]),
        "warnings": [],
        "source": "math_core",
    }


def _require(params: Mapping[str, Any], *names: str) -> list[Any]:
    missing = [name for name in names if params.get(name) is None]
    if missing:
        raise ValueError(f"missing required parameters: {', '.join(missing)}")
    return [params[name] for name in names]


def _calculate_rc(topology: str, params: Mapping[str, Any]) -> dict[str, Any]:
    fc_raw, r_raw, c_raw = params.get("fc"), params.get("R"), params.get("C")
    if sum(value is not None for value in (fc_raw, r_raw, c_raw)) < 2:
        raise ValueError("RC calculation needs at least two of fc, R, and C")
    series = str(params.get("series", "E24"))
    if fc_raw is not None and c_raw is not None and r_raw is None:
        report = build_rc_report(
            _number(fc_raw, name="fc"),
            _number(c_raw, name="C"),
            series=series,
            topology=topology,
        )
        if not report.success:
            raise ValueError(report.detail)
        payload = report.to_dict()
        payload["source"] = "math_core"
        return payload

    fc = _number(fc_raw, name="fc") if fc_raw is not None else None
    r = _number(r_raw, name="R") if r_raw is not None else None
    c = _number(c_raw, name="C") if c_raw is not None else None
    for name, value in (("fc", fc), ("R", r), ("C", c)):
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be > 0")
    payload = _base(topology)
    if fc is None:
        assert r is not None and c is not None
        fc = 1.0 / (2.0 * math.pi * r * c)
        payload["idealValues"]["fc"] = _quantity(fc, "Hz")
    elif r is None:
        assert c is not None
        r = 1.0 / (2.0 * math.pi * fc * c)
        payload["idealValues"]["R"] = _quantity(r, "ohm")
    elif c is None:
        c = 1.0 / (2.0 * math.pi * fc * r)
        payload["idealValues"]["C"] = _quantity(c, "F")
    else:
        predicted = 1.0 / (2.0 * math.pi * r * c)
        payload["predicted"]["fc"] = _quantity(predicted, "Hz")
    return payload


def calculate(topology: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate a supported topology using deterministic Math Core functions."""
    if topology not in _FORMULA_METADATA:
        raise ValueError(f"unsupported topology {topology!r}")
    params = dict(parameters)
    if topology in {"rc_lowpass", "rc_highpass"}:
        return _calculate_rc(topology, params)
    if topology == "voltage_divider":
        vin_raw, vout_raw, r1_raw, r2_raw = (
            params.get("vin", params.get("Vin")),
            params.get("vout", params.get("Vout")),
            params.get("r1", params.get("R1")),
            params.get("r2", params.get("R2")),
        )
        if sum(value is not None for value in (vin_raw, vout_raw, r1_raw, r2_raw)) < 3:
            raise ValueError("voltage divider needs three of vin, vout, r1, and r2")
        vin = _number(vin_raw, name="vin") if vin_raw is not None else None
        vout = _number(vout_raw, name="vout") if vout_raw is not None else None
        r1 = _number(r1_raw, name="r1") if r1_raw is not None else None
        r2 = _number(r2_raw, name="r2") if r2_raw is not None else None
        if vout is None:
            assert vin is not None and r1 is not None and r2 is not None
            result = voltage_divider_vout(vin, r1, r2)
            if not result.ok or result.result is None:
                raise ValueError(result.detail)
            vout = result.result
        elif r1 is None:
            assert vin is not None and r2 is not None
            if not 0 < vout < vin:
                raise ValueError("voltage_divider requires 0 < vout < vin")
            r1 = r2 * (vin - vout) / vout
        elif r2 is None:
            assert vin is not None
            if not 0 < vout < vin:
                raise ValueError("voltage_divider requires 0 < vout < vin")
            r2 = r1 * vout / (vin - vout)
        payload = _base(topology)
        for key, value, unit in (
            ("vin", vin, "V"),
            ("vout", vout, "V"),
            ("r1", r1, "ohm"),
            ("r2", r2, "ohm"),
        ):
            if value is not None:
                payload["idealValues"][key] = _quantity(value, unit)
        return payload
    if topology in {"noninv_opamp", "inverting_opamp"}:
        gain_raw = params.get("gain")
        resistor_name = "rg" if topology == "noninv_opamp" else "rin"
        resistor_raw = params.get(resistor_name)
        gain, resistor = _require(params, "gain", resistor_name)
        gain_value = _number(gain, name="gain")
        resistor_value = _number(resistor, name=resistor_name)
        report = (
            build_noninverting_opamp_report(gain_value, resistor_value)
            if topology == "noninv_opamp"
            else build_inverting_opamp_report(gain_value, resistor_value)
        )
        if not report.success:
            raise ValueError(report.detail)
        payload = report.to_dict()
        payload["idealValues"]["rf"] = payload["idealValues"]["Rf"]
        payload["idealValues"][resistor_name] = payload["idealValues"]["Rg"]
        payload["source"] = "math_core"
        _ = gain_raw, resistor_raw
        return payload
    if topology == "led_resistor":
        vs, vf, current = _require(params, "vsupply", "vf", "iled")
        values = (
            _number(vs, name="vsupply"),
            _number(vf, name="vf"),
            _number(current, name="iled"),
        )
        calc = led_resistor(*values)
        if not calc.ok or calc.result is None:
            raise ValueError(calc.detail)
        report = build_led_resistor_report(*values)
        payload = report.to_dict()
        payload["idealValues"]["P_R"] = _quantity(calc.extra["p_dissipated"], "W")
        payload["source"] = "math_core"
        return payload
    vin, vout, iout = _require(params, "vin", "vout", "iout")
    builder = build_buck_report if topology == "buck_ideal" else build_boost_report
    report = builder(
        _number(vin, name="vin"),
        _number(vout, name="vout"),
        _number(iout, name="iout"),
    )
    if not report.success:
        raise ValueError(report.detail)
    payload = report.to_dict()
    payload["source"] = "math_core"
    return payload


def explain(topology: str, parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return formula metadata, optionally enriched by a calculation."""
    if topology not in _FORMULA_METADATA:
        raise ValueError(f"unsupported topology {topology!r}")
    if parameters:
        return calculate(topology, parameters)
    return _base(topology)


def supported_topologies() -> tuple[str, ...]:
    return tuple(sorted(_FORMULA_METADATA))


__all__ = ["calculate", "explain", "supported_topologies"]
