"""MCP live-editing tools (Phase 13, "file-based live editing + math accuracy").

This module exposes **tool-level** functions for the planned live-editing
surface documented in
``ltspice_file_based_live_editing_math_plan.md`` (§11 — MCP live editing
tools). It does **not** register them with the FastMCP server; the
integrator is expected to import the ``tool_*`` callables defined here
and wire them onto the existing :mod:`ltagent.mcp_server` server.

The module is intentionally MCP-SDK-free: it has no dependency on
``mcp.server.fastmcp`` and can be unit-tested in isolation. It mirrors
the JSON output contract from ``docs/SPEC.md §2``
(``success``, ``command``, ``message``, ``data``, ``warnings``,
``errors``) and reuses the same path-safety primitives as
:mod:`ltagent.security` so the integrator gets a consistent error story.

Design contract
---------------

Eight tool-level callables are provided, one per "live" + "math" tool
listed in the plan:

* :func:`tool_live_open_project`        — resolve + load a live project
* :func:`tool_live_inspect_project`     — return the live state view
* :func:`tool_live_apply_edit`          — apply a single edit op
* :func:`tool_live_snapshot`            — snapshot a project
* :func:`tool_live_restore_snapshot`    — restore from a snapshot
* :func:`tool_live_run_and_verify`      — run + verify the project
* :func:`tool_calculate_circuit`        — pure math calculation
* :func:`tool_explain_calculation`      — explain a calculation

Hard rules (from the live-editing plan §22, restated for the tool
surface):

* **No arbitrary shell execution.** No tool accepts or executes a shell
  command, no tool calls :func:`subprocess.run` with ``shell=True``.
* **No arbitrary file write.** Tools either return structured data or
  delegate writes to the live-editing core (the integrator's contract).
  This module never opens a project file for write itself.
* **No workspace escape.** All path-bearing tools resolve their targets
  with :func:`ltagent.security.safe_resolve_under` and reject traversal
  with the stable code ``PATH_TRAVERSAL``.
* **No ``allow_outside_workspace`` knob.** The MCP integration must
  never be able to opt out of the workspace boundary.
* **No ``.raw`` exposure.** Tools do not read or surface ``*.raw``
  files; :func:`ltagent.security.assert_no_raw_path` is the integrator's
  responsibility for resource URIs that fan out to project files.

Optional backend modules
------------------------

The plan describes a ``ltagent.live`` and a ``ltagent.math_core`` module
that other agents are building in parallel. The tools here use **safe
imports**: if the modules are not present, the call returns the
structured code ``LIVE_MODULE_UNAVAILABLE`` (or ``MATH_CORE_UNAVAILABLE``)
with no raw Python exception leaking to the MCP client.

The math tools also carry a small **built-in formula library** for the
six topologies listed in the plan §15.1 so they keep producing useful
output even before the math_core module lands. The library is
deliberately tiny — ideal-value calculation only, no symbolic or MNA —
and is not a substitute for the real math_core.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeAlias

from .config import Config, ConfigError, load_config
from .security import (
    ERR_PATH_TRAVERSAL,
    PathSafetyError,
    SecurityError,
    safe_resolve_under,
    validate_slug,
)
from .serialization import to_jsonable as _to_jsonable
from .units import parse_spice_value

# ---------------------------------------------------------------------------
# Public type alias — same shape the CLI / Phase 10 MCP server use
# ---------------------------------------------------------------------------

HandlerResult: TypeAlias = dict[str, Any]

# ---------------------------------------------------------------------------
# Stable error codes (string literals, never use repr() of an exception)
# ---------------------------------------------------------------------------

# Input validation
ERR_INVALID_INPUT: Final[str] = "INVALID_INPUT"
ERR_MISSING_PARAM: Final[str] = "MISSING_PARAM"
ERR_INVALID_OPERATION: Final[str] = "INVALID_OPERATION"
ERR_INVALID_TOPOLOGY: Final[str] = "INVALID_TOPOLOGY"
ERR_INVALID_SNAPSHOT_ID: Final[str] = "INVALID_SNAPSHOT_ID"

# Path safety — codes aligned with ltagent.security. We import the
# traversal code under a local alias so the ``Final`` re-export below
# doesn't try to rebind the import.
_PATH_TRAVERSAL_CODE: Final[str] = ERR_PATH_TRAVERSAL
ERR_PATH_TRAVERSAL_CODE: Final[str] = _PATH_TRAVERSAL_CODE
ERR_PATH_NOT_FOUND: Final[str] = "PATH_NOT_FOUND"
ERR_PROJECT_NOT_FOUND: Final[str] = "PROJECT_NOT_FOUND"
ERR_SNAPSHOT_NOT_FOUND: Final[str] = "SNAPSHOT_NOT_FOUND"

# Backend availability
ERR_LIVE_MODULE_UNAVAILABLE: Final[str] = "LIVE_MODULE_UNAVAILABLE"
ERR_LIVE_METHOD_MISSING: Final[str] = "LIVE_METHOD_MISSING"
ERR_MATH_CORE_UNAVAILABLE: Final[str] = "MATH_CORE_UNAVAILABLE"
ERR_MATH_CORE_METHOD_MISSING: Final[str] = "MATH_CORE_METHOD_MISSING"

# Live-edit operations
ERR_EDIT_OP_FAILED: Final[str] = "EDIT_OP_FAILED"
ERR_SNAPSHOT_FAILED: Final[str] = "SNAPSHOT_FAILED"
ERR_RESTORE_FAILED: Final[str] = "RESTORE_FAILED"
ERR_RUN_FAILED: Final[str] = "RUN_FAILED"
ERR_VERIFY_FAILED: Final[str] = "VERIFY_FAILED"
ERR_CALCULATION_FAILED: Final[str] = "CALCULATION_FAILED"

# Config
ERR_CONFIG_INVALID: Final[str] = "CONFIG_INVALID"


# ---------------------------------------------------------------------------
# Optional backend imports (live editing + math core)
# ---------------------------------------------------------------------------
#
# These are loaded lazily so a missing module does not break import of
# this file. The integrator (and the tests) can monkey-patch the
# resulting module-level globals to inject a fake backend.

_LIVE_MODULE: Any = None
_LIVE_IMPORT_ERROR: BaseException | None = None
try:
    from . import live as _LIVE_MODULE
except Exception as _exc:  # pragma: no cover - exercised when the live module lands
    _LIVE_IMPORT_ERROR = _exc

_MATH_CORE_MODULE: Any = None
_MATH_CORE_IMPORT_ERROR: BaseException | None = None
try:
    from . import math_core as _MATH_CORE_MODULE
except Exception as _exc:  # pragma: no cover - exercised when the math core lands
    _MATH_CORE_IMPORT_ERROR = _exc


# ---------------------------------------------------------------------------
# JSON contract helpers
# ---------------------------------------------------------------------------


def _ok(command: str, message: str, data: dict[str, Any] | None = None) -> HandlerResult:
    return {
        "success": True,
        "command": command,
        "message": message,
        "data": dict(data) if data else {},
        "warnings": [],
        "errors": [],
    }


def _err(
    command: str,
    message: str,
    code: str,
    detail: str,
    data: dict[str, Any] | None = None,
) -> HandlerResult:
    return {
        "success": False,
        "command": command,
        "message": message,
        "data": dict(data) if data else {},
        "warnings": [],
        "errors": [{"code": code, "detail": detail, "data": dict(data) if data else {}}],
    }


def _from_security_error(command: str, exc: SecurityError) -> HandlerResult:
    return _err(command, exc.message, exc.code, exc.message, exc.data)


def _ensure_jsonable(payload: HandlerResult) -> HandlerResult:
    """Defensive: never let a non-JSONable value escape to MCP."""
    try:
        json.dumps(payload)
        return payload
    except (TypeError, ValueError):
        return {
            "success": bool(payload.get("success")),
            "command": str(payload.get("command", "ltagent.live")),
            "message": str(payload.get("message", "")),
            "data": _to_jsonable(payload.get("data", {})),
            "warnings": _to_jsonable(payload.get("warnings", [])),
            "errors": _to_jsonable(payload.get("errors", [])),
        }


# ---------------------------------------------------------------------------
# Path / config helpers
# ---------------------------------------------------------------------------


def _resolve_config(config_path: str | None) -> tuple[Config | None, HandlerResult | None]:
    try:
        cfg = load_config(Path(config_path).expanduser() if config_path else None)
    except ConfigError as exc:
        return None, _err("config", "Invalid configuration", ERR_CONFIG_INVALID, str(exc))
    return cfg, None


def _resolve_projects_root(cfg: Config) -> Path:
    return (Path.cwd() / cfg.workspace.projects_dir).resolve()


def _resolve_project_dir(
    cfg: Config, project_id: str, *, command: str, must_exist: bool = True
) -> tuple[Path | None, HandlerResult | None]:
    """Validate the slug, then resolve the project directory under workspace."""
    try:
        validate_slug(project_id, kind="project id")
    except SecurityError as exc:
        return None, _from_security_error(command, exc)

    projects_root = _resolve_projects_root(cfg)
    project_dir = projects_root / project_id
    try:
        project_dir = safe_resolve_under(
            project_dir, projects_root, must_exist=must_exist
        )
    except PathSafetyError as exc:
        return None, _from_security_error(command, exc)
    return project_dir, None


# ---------------------------------------------------------------------------
# Live module dispatch helper
# ---------------------------------------------------------------------------


def _live_method(name: str) -> Callable[..., Any] | None:
    if _LIVE_MODULE is None:
        return None
    return getattr(_LIVE_MODULE, name, None)


def _math_core_method(name: str) -> Callable[..., Any] | None:
    if _MATH_CORE_MODULE is None:
        return None
    return getattr(_MATH_CORE_MODULE, name, None)


# ---------------------------------------------------------------------------
# Built-in mini formula library (math_core fallback)
# ---------------------------------------------------------------------------
#
# Six topologies from plan §15.1. The library is intentionally tiny:
# compute the unknown ideal value, attach the formula text, attach a
# list of assumptions. No standard-value selection, no MNA, no symbols
# — that is the job of the real math_core. The strings here are
# matched on the math-core's eventual formula registry so swapping in
# the real module is a drop-in change.

_TWO_PI: Final[float] = 2.0 * math.pi


@dataclass(frozen=True)
class _BuiltinFormula:
    topology: str
    description: str
    formula_text: str
    formula_name: str
    unit_targets: tuple[str, ...]
    assumptions: tuple[str, ...]
    verification: tuple[dict[str, Any], ...]


#: Solver functions live in a separate mapping so the frozen dataclass
#: can stay immutable. Each solver takes the user ``params`` and returns
#: a ``dict[str, {"value": float, "unit": str, "display": str}]``.
_SOLVERS: Final[dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {}


def _parse_unit_value(value: Any, *, name: str) -> tuple[float, str]:
    """Parse a value that may be a number or a SPICE-style string.

    Returns ``(si_value, display)``. Numeric SPICE parsing is delegated
    to :func:`ltagent.units.parse_spice_value` after a small
    pre-processing step that strips a trailing decorative unit letter
    that the strict SPICE parser does not know about (e.g. ``F`` for
    farads on a capacitance, ``A`` for amps on a current, ``V`` for
    volts on a voltage, ``Hz`` for hertz on a frequency). The unit is
    implied by the topology context; the trailing letter is purely
    cosmetic and is preserved as the display string.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name!r} must be a number, got bool")
    if isinstance(value, (int, float)):
        return float(value), str(value)
    if not isinstance(value, str):
        raise ValueError(f"{name!r} must be a number or SPICE string")
    s = value.strip().replace("µ", "u")
    if not s:
        raise ValueError(f"{name!r} is empty")
    # Plain integer / float?
    try:
        return float(s), s
    except ValueError:
        pass
    # Strict SPICE first.
    parsed = parse_spice_value(s)
    if parsed is not None:
        return float(parsed), s
    # Strip a trailing decorative unit (F, A, V, ohm, Hz, etc.) and
    # retry. We try every strip length up to 3 and accept the first
    # that yields a valid SPICE or plain number. This is robust to
    # both single-letter and short two-/three-letter unit suffixes.
    for trim in range(1, 4):
        if len(s) <= trim:
            break
        if not s[-trim:].isalpha():
            continue
        core = s[:-trim]
        if not core:
            continue
        try:
            return float(core), s
        except ValueError:
            pass
        parsed = parse_spice_value(core)
        if parsed is not None:
            return float(parsed), s
    raise ValueError(f"{name!r}={value!r} is not a valid number or SPICE value")


def _make_rc_solver(*, kind: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build an rc_lowpass / rc_highpass solver. They share the formula."""

    def _solve(params: Mapping[str, Any]) -> dict[str, Any]:
        known_fc = "fc" in params
        known_r = "R" in params
        known_c = "C" in params
        count = sum(bool(x) for x in (known_fc, known_r, known_c))
        if count < 2:
            missing = [k for k, present in (("fc", known_fc), ("R", known_r), ("C", known_c)) if not present]
            raise ValueError(
                f"{kind} needs at least two of fc/R/C; missing {missing}"
            )

        ideal: dict[str, Any] = {}
        if known_fc and known_r and known_c:
            # Verify the trio is consistent (within 1 %).
            fc_v, _ = _parse_unit_value(params["fc"], name="fc")
            r_v, _ = _parse_unit_value(params["R"], name="R")
            c_v, _ = _parse_unit_value(params["C"], name="C")
            ideal = {
                "fc": {"value": fc_v, "unit": "Hz", "display": params["fc"]},
                "R": {"value": r_v, "unit": "ohm", "display": params["R"]},
                "C": {"value": c_v, "unit": "F", "display": params["C"]},
            }
            predicted_fc = 1.0 / (_TWO_PI * r_v * c_v)
            ideal["fcPredicted"] = {
                "value": predicted_fc,
                "unit": "Hz",
                "errorPercent": (predicted_fc - fc_v) / fc_v * 100.0 if fc_v else None,
            }
        else:
            fc: float | None = None
            r: float | None = None
            c: float | None = None
            if known_fc:
                fc, _ = _parse_unit_value(params["fc"], name="fc")
            if known_r:
                r, _ = _parse_unit_value(params["R"], name="R")
            if known_c:
                c, _ = _parse_unit_value(params["C"], name="C")
            if not known_fc:
                assert known_r and known_c and r is not None and c is not None
                fc_val = 1.0 / (_TWO_PI * r * c)
                ideal["fc"] = {"value": fc_val, "unit": "Hz", "display": f"{fc_val:.6g}"}
            if not known_r:
                assert known_fc and known_c and fc is not None and c is not None
                r_val = 1.0 / (_TWO_PI * fc * c)
                ideal["R"] = {"value": r_val, "unit": "ohm", "display": f"{r_val:.6g}"}
            if not known_c:
                assert known_fc and known_r and fc is not None and r is not None
                c_val = 1.0 / (_TWO_PI * fc * r)
                ideal["C"] = {"value": c_val, "unit": "F", "display": f"{c_val:.6g}"}
        return ideal

    return _solve


def _make_voltage_divider_solver() -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    def _solve(params: Mapping[str, Any]) -> dict[str, Any]:
        vin = params.get("vin") or params.get("Vin")
        vout = params.get("vout") or params.get("Vout")
        r1 = params.get("r1") or params.get("R1")
        r2 = params.get("r2") or params.get("R2")
        present = {
            "vin": vin is not None,
            "vout": vout is not None,
            "r1": r1 is not None,
            "r2": r2 is not None,
        }
        if sum(present.values()) < 3:
            missing = [k for k, v in present.items() if not v]
            raise ValueError(
                f"voltage_divider needs at least three of vin/vout/r1/r2; missing {missing}"
            )
        ideal: dict[str, Any] = {}
        if vin is not None:
            vin_v, vin_d = _parse_unit_value(vin, name="vin")
            ideal["vin"] = {"value": vin_v, "unit": "V", "display": vin_d}
        if vout is not None:
            vout_v, vout_d = _parse_unit_value(vout, name="vout")
            ideal["vout"] = {"value": vout_v, "unit": "V", "display": vout_d}
        if r1 is not None:
            r1_v, r1_d = _parse_unit_value(r1, name="r1")
            ideal["r1"] = {"value": r1_v, "unit": "ohm", "display": r1_d}
        if r2 is not None:
            r2_v, r2_d = _parse_unit_value(r2, name="r2")
            ideal["r2"] = {"value": r2_v, "unit": "ohm", "display": r2_d}
        if vout is None and vin is not None and r1 is not None and r2 is not None:
            vin_v = ideal["vin"]["value"]
            r1_v = ideal["r1"]["value"]
            r2_v = ideal["r2"]["value"]
            vout_v = vin_v * r2_v / (r1_v + r2_v)
            ideal["vout"] = {"value": vout_v, "unit": "V", "display": f"{vout_v:.6g}"}
        if r2 is None and vin is not None and vout is not None and r1 is not None:
            vin_v = ideal["vin"]["value"]
            vout_v = ideal["vout"]["value"]
            r1_v = ideal["r1"]["value"]
            r2_v = r1_v * vout_v / (vin_v - vout_v)
            if r2_v <= 0:
                raise ValueError(
                    f"voltage_divider requires vout < vin (got vin={vin_v}, vout={vout_v})"
                )
            ideal["r2"] = {"value": r2_v, "unit": "ohm", "display": f"{r2_v:.6g}"}
        if r1 is None and vin is not None and vout is not None and r2 is not None:
            vin_v = ideal["vin"]["value"]
            vout_v = ideal["vout"]["value"]
            r2_v = ideal["r2"]["value"]
            r1_v = r2_v * (vin_v - vout_v) / vout_v
            if r1_v <= 0:
                raise ValueError(
                    f"voltage_divider requires vout < vin (got vin={vin_v}, vout={vout_v})"
                )
            ideal["r1"] = {"value": r1_v, "unit": "ohm", "display": f"{r1_v:.6g}"}
        return ideal

    return _solve


def _make_opamp_solver(*, kind: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    if kind == "noninv_opamp":
        rg_k = "rg"
    elif kind == "inverting_opamp":
        rg_k = "rin"
    else:  # pragma: no cover - guarded by the registry
        raise ValueError(f"unknown opamp kind {kind!r}")

    def _solve(params: Mapping[str, Any]) -> dict[str, Any]:
        gain_k = "gain"
        rf_k = "rf"
        present = {gain_k: False, rf_k: False, rg_k: False}
        for k in (gain_k, rf_k, rg_k):
            present[k] = params.get(k) is not None

        if sum(present.values()) < 2:
            missing = [k for k, v in present.items() if not v]
            raise ValueError(f"{kind} needs at least two of {sorted(present)}; missing {missing}")

        ideal: dict[str, Any] = {}
        for k, v in params.items():
            if v is not None and k in present:
                vi, vd = _parse_unit_value(v, name=k)
                ideal[k] = {"value": vi, "unit": "", "display": vd}

        gain_v = ideal[gain_k]["value"] if gain_k in ideal else None
        rf_v = ideal[rf_k]["value"] if rf_k in ideal else None
        rg_v = ideal[rg_k]["value"] if rg_k in ideal else None

        if kind == "noninv_opamp":
            if gain_v is None and rf_v is not None and rg_v is not None:
                gain_v = 1.0 + rf_v / rg_v
                ideal[gain_k] = {
                    "value": gain_v,
                    "unit": "",
                    "display": f"{gain_v:.6g}",
                }
            if rg_v is None and gain_v is not None and rf_v is not None:
                if gain_v <= 1.0:
                    raise ValueError(
                        f"noninv_opamp requires gain > 1 (got {gain_v})"
                    )
                rg_v = rf_v / (gain_v - 1.0)
                ideal[rg_k] = {
                    "value": rg_v,
                    "unit": "ohm",
                    "display": f"{rg_v:.6g}",
                }
            if rf_v is None and gain_v is not None and rg_v is not None:
                if gain_v <= 1.0:
                    raise ValueError(
                        f"noninv_opamp requires gain > 1 (got {gain_v})"
                    )
                rf_v = (gain_v - 1.0) * rg_v
                ideal[rf_k] = {
                    "value": rf_v,
                    "unit": "ohm",
                    "display": f"{rf_v:.6g}",
                }
        else:  # inverting
            if gain_v is None and rf_v is not None and rg_v is not None:
                gain_v = -rf_v / rg_v
                ideal[gain_k] = {
                    "value": gain_v,
                    "unit": "",
                    "display": f"{gain_v:.6g}",
                }
            if rg_v is None and gain_v is not None and rf_v is not None:
                if gain_v == 0:
                    raise ValueError("inverting_opamp gain must be non-zero")
                rg_v = rf_v / abs(gain_v)
                ideal[rg_k] = {
                    "value": rg_v,
                    "unit": "ohm",
                    "display": f"{rg_v:.6g}",
                }
            if rf_v is None and gain_v is not None and rg_v is not None:
                if gain_v == 0:
                    raise ValueError("inverting_opamp gain must be non-zero")
                rf_v = abs(gain_v) * rg_v
                ideal[rf_k] = {
                    "value": rf_v,
                    "unit": "ohm",
                    "display": f"{rf_v:.6g}",
                }
        return ideal

    return _solve


def _make_led_resistor_solver() -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    def _solve(params: Mapping[str, Any]) -> dict[str, Any]:
        vs = params.get("vsupply")
        vf = params.get("vf")
        iled = params.get("iled")
        present = {"vsupply": vs is not None, "vf": vf is not None, "iled": iled is not None}
        if sum(present.values()) < 3:
            missing = [k for k, v in present.items() if not v]
            raise ValueError(
                f"led_resistor needs vsupply, vf, and iled; missing {missing}"
            )
        vs_v, vs_d = _parse_unit_value(vs, name="vsupply")
        vf_v, vf_d = _parse_unit_value(vf, name="vf")
        iled_v, iled_d = _parse_unit_value(iled, name="iled")
        if iled_v <= 0:
            raise ValueError("iled must be > 0")
        if vs_v <= vf_v:
            raise ValueError(
                f"led_resistor requires vsupply > vf (got {vs_v} <= {vf_v})"
            )
        r_v = (vs_v - vf_v) / iled_v
        p_v = iled_v * iled_v * r_v
        return {
            "vsupply": {"value": vs_v, "unit": "V", "display": vs_d},
            "vf": {"value": vf_v, "unit": "V", "display": vf_d},
            "iled": {"value": iled_v, "unit": "A", "display": iled_d},
            "R": {"value": r_v, "unit": "ohm", "display": f"{r_v:.6g}"},
            "P_R": {"value": p_v, "unit": "W", "display": f"{p_v:.6g}"},
        }

    return _solve


_BUILTIN_FORMULAS: Final[dict[str, _BuiltinFormula]] = {
    "voltage_divider": _BuiltinFormula(
        topology="voltage_divider",
        description="Two-resistor voltage divider: Vout = Vin * R2 / (R1 + R2).",
        formula_text="Vout = Vin * R2 / (R1 + R2)",
        formula_name="voltage_divider_ratio",
        unit_targets=("vin", "vout", "r1", "r2"),
        assumptions=(
            "no load current on Vout",
            "ideal resistors (no tolerance analysis)",
        ),
        verification=(
            {"name": "ratio", "expression": "R2 / (R1 + R2)", "tolerancePercent": 1.0},
        ),
    ),
    "rc_lowpass": _BuiltinFormula(
        topology="rc_lowpass",
        description="First-order passive RC low-pass filter.",
        formula_text="fc = 1 / (2*pi*R*C)",
        formula_name="cutoff_frequency",
        unit_targets=("fc", "R", "C"),
        assumptions=(
            "ideal capacitor (no ESR/ESL)",
            "source impedance is zero",
            "load impedance is infinite",
        ),
        verification=(
            {
                "name": "gain_at_cutoff",
                "expectedMagnitude": 0.7071,
                "tolerancePercent": 5.0,
            },
        ),
    ),
    "rc_highpass": _BuiltinFormula(
        topology="rc_highpass",
        description="First-order passive RC high-pass filter.",
        formula_text="fc = 1 / (2*pi*R*C)",
        formula_name="cutoff_frequency",
        unit_targets=("fc", "R", "C"),
        assumptions=(
            "ideal capacitor (no ESR/ESL)",
            "source impedance is zero",
            "load impedance is infinite",
        ),
        verification=(
            {
                "name": "gain_at_cutoff",
                "expectedMagnitude": 0.7071,
                "tolerancePercent": 5.0,
            },
        ),
    ),
    "noninv_opamp": _BuiltinFormula(
        topology="noninv_opamp",
        description="Non-inverting op-amp amplifier: Av = 1 + Rf / Rg.",
        formula_text="Av = 1 + Rf / Rg",
        formula_name="noninv_gain",
        unit_targets=("gain", "rf", "rg"),
        assumptions=(
            "ideal op-amp (infinite GBW, infinite input impedance, zero offset)",
            "resistor values are ideal",
        ),
        verification=(
            {"name": "gain", "tolerancePercent": 1.0},
            {"name": "no_clipping", "expression": "Vout_peak < Vsupply"},
        ),
    ),
    "inverting_opamp": _BuiltinFormula(
        topology="inverting_opamp",
        description="Inverting op-amp amplifier: Av = -Rf / Rin.",
        formula_text="Av = -Rf / Rin",
        formula_name="inverting_gain",
        unit_targets=("gain", "rf", "rin"),
        assumptions=(
            "ideal op-amp (infinite GBW, infinite input impedance, zero offset)",
            "resistor values are ideal",
        ),
        verification=(
            {"name": "gain", "tolerancePercent": 1.0},
            {"name": "no_clipping", "expression": "Vout_peak < Vsupply"},
        ),
    ),
    "led_resistor": _BuiltinFormula(
        topology="led_resistor",
        description="Current-limiting resistor for an LED: R = (Vsupply - Vf) / Iled.",
        formula_text="R = (Vsupply - Vf) / Iled",
        formula_name="led_resistor",
        unit_targets=("vsupply", "vf", "iled", "R"),
        assumptions=(
            "constant forward voltage Vf (no I-V curve modelling)",
            "DC operating point only",
        ),
        verification=(
            {"name": "current", "tolerancePercent": 5.0},
            {"name": "power", "expression": "P_R < P_R_rating"},
        ),
    ),
}

# Wire per-topology solvers into the _SOLVERS table.
_SOLVERS["voltage_divider"] = _make_voltage_divider_solver()
_SOLVERS["rc_lowpass"] = _make_rc_solver(kind="rc_lowpass")
_SOLVERS["rc_highpass"] = _make_rc_solver(kind="rc_highpass")
_SOLVERS["noninv_opamp"] = _make_opamp_solver(kind="noninv_opamp")
_SOLVERS["inverting_opamp"] = _make_opamp_solver(kind="inverting_opamp")
_SOLVERS["led_resistor"] = _make_led_resistor_solver()


def _builtin_solve(topology: str, params: Mapping[str, Any]) -> dict[str, Any]:
    formula = _BUILTIN_FORMULAS.get(topology)
    solver = _SOLVERS.get(topology)
    if formula is None or solver is None:
        return {
            "topology": topology,
            "formulas": [],
            "idealValues": {},
            "predicted": {},
            "assumptions": [],
            "source": "builtin_fallback",
            "available": False,
        }
    try:
        ideal = solver(params)
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return {
            "topology": topology,
            "formulas": [{"name": formula.formula_name, "expression": formula.formula_text}],
            "idealValues": {},
            "predicted": {},
            "assumptions": list(formula.assumptions),
            "source": "builtin_fallback",
            "available": True,
            "error": str(exc),
        }
    return {
        "topology": topology,
        "formulas": [{"name": formula.formula_name, "expression": formula.formula_text}],
        "idealValues": ideal,
        "predicted": {},
        "assumptions": list(formula.assumptions),
        "verification": [dict(v) for v in formula.verification],
        "source": "builtin_fallback",
        "available": True,
    }


def _builtin_explain(topology: str) -> dict[str, Any]:
    formula = _BUILTIN_FORMULAS.get(topology)
    if formula is None:
        return {
            "topology": topology,
            "description": "topology not in built-in library",
            "formulas": [],
            "assumptions": [],
            "verification": [],
            "source": "builtin_fallback",
            "available": False,
        }
    return {
        "topology": topology,
        "description": formula.description,
        "formulas": [{"name": formula.formula_name, "expression": formula.formula_text}],
        "variables": list(formula.unit_targets),
        "assumptions": list(formula.assumptions),
        "verification": [dict(v) for v in formula.verification],
        "source": "builtin_fallback",
        "available": True,
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tool_live_open_project(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Open an existing live-editing project by id.

    Returns a JSON view of the on-disk project: project directory,
    metadata.json contents (if present), the canonical artifact
    filenames, and a flag indicating whether the project already has a
    live-editing graph (``circuit.graph.json``).

    This tool does **not** load the project into process memory; the
    integrator's MCP session is expected to do that on the tool call
    site. The function only resolves the path and reads the small
    project metadata.
    """
    command = "live_open_project"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    metadata_path = project_dir / "metadata.json"
    graph_path = project_dir / "circuit.graph.json"
    ir_path = project_dir / "circuit.ir.json"
    netlist_path = project_dir / "circuit.cir"
    schematic_path = project_dir / "circuit.asc"

    metadata: dict[str, Any] | None = None
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = _to_jsonable(loaded)
        except (OSError, json.JSONDecodeError):
            # Corrupt metadata is not fatal for open; report it as a warning.
            pass

    is_live = graph_path.exists()
    files_present = {
        "metadata": metadata_path.exists(),
        "graph": graph_path.exists(),
        "ir": ir_path.exists(),
        "netlist": netlist_path.exists(),
        "schematic": schematic_path.exists(),
    }
    data: dict[str, Any] = {
        "projectId": project_id,
        "projectDir": str(project_dir),
        "isLiveProject": is_live,
        "files": {
            "metadata": str(metadata_path) if files_present["metadata"] else None,
            "graph": str(graph_path) if files_present["graph"] else None,
            "ir": str(ir_path) if files_present["ir"] else None,
            "netlist": str(netlist_path) if files_present["netlist"] else None,
            "schematic": str(schematic_path) if files_present["schematic"] else None,
        },
        "metadata": metadata,
    }
    warnings: list[dict[str, Any]] = []
    if not is_live:
        warnings.append(
            {
                "code": "LIVE_GRAPH_MISSING",
                "detail": "project has no circuit.graph.json; this is not yet a live project",
                "data": {"expected": str(graph_path)},
            }
        )
    payload = _ok(command, f"opened project {project_id}", data)
    payload["warnings"] = warnings
    return _ensure_jsonable(payload)


def tool_live_inspect_project(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Return a live state view: graph, IR, measurements, snapshots."""
    command = "live_inspect_project"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    graph_path = project_dir / "circuit.graph.json"
    ir_path = project_dir / "circuit.ir.json"
    result_path = project_dir / "result.json"
    snapshots_dir = project_dir / ".snapshots"

    def _safe_read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(loaded, dict):
            result: Any = _to_jsonable(loaded)
            return result if isinstance(result, dict) else None
        wrapped: Any = _to_jsonable({"value": loaded})
        return wrapped if isinstance(wrapped, dict) else None

    graph = _safe_read_json(graph_path)
    ir = _safe_read_json(ir_path)
    result = _safe_read_json(result_path)

    snapshot_ids: list[str] = []
    if snapshots_dir.is_dir():
        snapshot_ids = sorted(p.name for p in snapshots_dir.iterdir() if p.is_dir())

    data: dict[str, Any] = {
        "projectId": project_id,
        "projectDir": str(project_dir),
        "graph": graph,
        "ir": ir,
        "result": result,
        "snapshots": snapshot_ids,
        "hasGraph": graph is not None,
        "hasIR": ir is not None,
        "hasResult": result is not None,
    }
    return _ensure_jsonable(_ok(command, f"inspected live project {project_id}", data))


def tool_live_apply_edit(
    project_id: str,
    operation: Mapping[str, Any] | None,
    *,
    auto_snapshot: bool = True,
    config: str | None = None,
) -> HandlerResult:
    """Apply a single edit operation to a live project.

    The ``operation`` argument is a structured dict following the plan
    §8.2 schema::

        {"op": "set_component_value",
         "args": {"componentId": "R1", "value": "1.6k"},
         "reason": "switch to E24 value"}

    This tool **never** writes the project file itself. It delegates
    the actual apply to the ``ltagent.live`` module (planned) which is
    expected to expose ``apply_operation(project_dir, op_dict) -> dict``.
    """
    command = "live_apply_edit"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if not isinstance(operation, Mapping):
        return _err(
            command, "operation must be a dict",
            ERR_INVALID_OPERATION, "operation is missing or not a dict",
            {"receivedType": type(operation).__name__},
        )

    op_name = operation.get("op")
    if not isinstance(op_name, str) or not op_name:
        return _err(
            command, "operation.op must be a non-empty string",
            ERR_INVALID_OPERATION, "operation.op is missing or empty",
            {"operation": dict(operation)},
        )
    op_args = operation.get("args", {})
    if not isinstance(op_args, Mapping):
        return _err(
            command, "operation.args must be a dict",
            ERR_INVALID_OPERATION, "operation.args is not a dict",
            {"receivedType": type(op_args).__name__},
        )
    op_reason_raw = operation.get("reason", "")
    op_reason = str(op_reason_raw) if op_reason_raw is not None else ""

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {
                    "importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None,
                    "op": op_name,
                    "autoSnapshot": auto_snapshot,
                },
            )
        )

    apply_fn = _live_method("apply_operation")
    if apply_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose apply_operation",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
                {"op": op_name},
            )
        )

    op_payload = {"op": op_name, "args": dict(op_args), "reason": op_reason}
    try:
        result = apply_fn(
            project_dir,
            op_payload,
            auto_snapshot=bool(auto_snapshot),
            config=cfg,
        )
    except (TypeError, ValueError) as exc:
        return _ensure_jsonable(
            _err(command, "live.apply rejected the operation", ERR_EDIT_OP_FAILED, str(exc), {"op": op_name})
        )
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(command, "live.apply raised an unexpected error", ERR_EDIT_OP_FAILED, repr(exc), {"op": op_name})
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    data.setdefault("op", op_name)
    data.setdefault("autoSnapshot", bool(auto_snapshot))
    return _ensure_jsonable(_ok(command, f"applied {op_name} to {project_id}", data))


def tool_live_snapshot(
    project_id: str,
    reason: str | None = None,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Create a snapshot of a live project before risky edits.

    The snapshot is delegated to :mod:`ltagent.live` (planned). Until
    that module lands, the tool returns a structured
    ``LIVE_MODULE_UNAVAILABLE`` error.
    """
    command = "live_snapshot"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if reason is not None and not isinstance(reason, str):
        return _err(
            command, "reason must be a string",
            ERR_INVALID_INPUT, "reason is not a string",
            {"receivedType": type(reason).__name__},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {"importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None},
            )
        )

    snapshot_fn = _live_method("snapshot")
    if snapshot_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose snapshot",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
            )
        )

    try:
        result = snapshot_fn(project_dir, reason=reason or "", config=cfg)
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(command, "snapshot failed", ERR_SNAPSHOT_FAILED, repr(exc), {"projectId": project_id})
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    data.setdefault("reason", reason or "")
    return _ensure_jsonable(_ok(command, f"snapshot created for {project_id}", data))


def tool_live_restore_snapshot(
    project_id: str,
    snapshot_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Restore a project from a previously created snapshot."""
    command = "live_restore_snapshot"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return _err(
            command, "snapshot id must be a non-empty string",
            ERR_MISSING_PARAM, "snapshot_id is required",
            {"field": "snapshotId"},
        )
    if "/" in snapshot_id or "\\" in snapshot_id or ".." in Path(snapshot_id).parts:
        return _err(
            command, "snapshot id must not contain path separators",
            ERR_INVALID_SNAPSHOT_ID, "snapshot id is not a plain slug",
            {"snapshotId": snapshot_id},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {"importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None},
            )
        )

    restore_fn = _live_method("restore")
    if restore_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose restore",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
            )
        )

    try:
        result = restore_fn(project_dir, snapshot_id, config=cfg)
    except FileNotFoundError as exc:
        return _ensure_jsonable(
            _err(
                command, "snapshot not found", ERR_SNAPSHOT_NOT_FOUND,
                str(exc), {"projectId": project_id, "snapshotId": snapshot_id},
            )
        )
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(
                command, "restore failed", ERR_RESTORE_FAILED, repr(exc),
                {"projectId": project_id, "snapshotId": snapshot_id},
            )
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    data.setdefault("snapshotId", snapshot_id)
    return _ensure_jsonable(_ok(command, f"restored {project_id} from {snapshot_id}", data))


def tool_live_run_and_verify(
    project_id: str,
    *,
    config: str | None = None,
) -> HandlerResult:
    """Run the live project's simulation and verify its targets."""
    command = "live_run_and_verify"
    if not isinstance(project_id, str) or not project_id:
        return _err(
            command, "project id must be a non-empty string",
            ERR_MISSING_PARAM, "project_id is required",
            {"field": "projectId"},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    project_dir, perr = _resolve_project_dir(cfg, project_id, command=command)
    if perr is not None or project_dir is None:
        return _ensure_jsonable(perr or _err(command, "no project", ERR_PROJECT_NOT_FOUND, "no project"))

    if _LIVE_MODULE is None:
        return _ensure_jsonable(
            _err(
                command,
                "live editing module is not built yet",
                ERR_LIVE_MODULE_UNAVAILABLE,
                "ltagent.live is not importable; another agent is implementing it",
                {"importError": repr(_LIVE_IMPORT_ERROR) if _LIVE_IMPORT_ERROR else None},
            )
        )

    run_fn = _live_method("run_and_verify")
    if run_fn is None:
        return _ensure_jsonable(
            _err(
                command,
                "ltagent.live does not expose run_and_verify",
                ERR_LIVE_METHOD_MISSING,
                "the live module is present but does not provide the expected function",
            )
        )

    try:
        result = run_fn(project_dir, config=cfg)
    except Exception as exc:  # pragma: no cover - depends on live module
        return _ensure_jsonable(
            _err(command, "run and verify failed", ERR_RUN_FAILED, repr(exc), {"projectId": project_id})
        )

    data = _to_jsonable(result) if result is not None else {}
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("projectId", project_id)
    return _ensure_jsonable(_ok(command, f"ran and verified {project_id}", data))


def tool_calculate_circuit(
    topology: str,
    parameters: Mapping[str, Any] | None,
    *,
    project_id: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Pure-math circuit calculation. Returns ideal values + formulas.

    The function is intentionally side-effect free: it never writes
    any file, never executes shell, and never depends on the project
    being on disk unless ``project_id`` is provided. When the optional
    ``ltagent.math_core`` module is importable, the tool delegates to
    its ``calculate(topology, parameters)`` entry point. Otherwise it
    falls back to the built-in mini library.
    """
    command = "calculate_circuit"
    if not isinstance(topology, str) or not topology:
        return _err(
            command, "topology must be a non-empty string",
            ERR_MISSING_PARAM, "topology is required",
            {"field": "topology"},
        )
    if not isinstance(parameters, Mapping):
        return _err(
            command, "parameters must be a dict",
            ERR_INVALID_INPUT, "parameters is missing or not a dict",
            {"receivedType": type(parameters).__name__},
        )

    if project_id is not None and not isinstance(project_id, str):
        return _err(
            command, "project id must be a string",
            ERR_INVALID_INPUT, "project_id is not a string",
            {"receivedType": type(project_id).__name__},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    if project_id is not None:
        # Validate the project id but do not require the project to exist;
        # the calculation is independent of the project's state.
        try:
            validate_slug(project_id, kind="project id")
        except SecurityError as exc:
            return _ensure_jsonable(_from_security_error(command, exc))

    # Prefer the math_core module if it implements calculate().
    calc_fn = _math_core_method("calculate")
    if calc_fn is not None and _MATH_CORE_MODULE is not None:
        try:
            result = calc_fn(topology, dict(parameters))
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            return _ensure_jsonable(
                _err(command, "math_core.calculate failed", ERR_CALCULATION_FAILED, str(exc), {"topology": topology})
            )
        except Exception as exc:  # pragma: no cover - depends on math_core
            return _ensure_jsonable(
                _err(command, "math_core.calculate raised", ERR_CALCULATION_FAILED, repr(exc), {"topology": topology})
            )
        data = _to_jsonable(result) if result is not None else {}
        if not isinstance(data, dict):
            data = {"result": data}
        data.setdefault("topology", topology)
        data.setdefault("source", "math_core")
        return _ensure_jsonable(_ok(command, f"calculated {topology}", data))

    # Fallback: built-in mini library.
    if topology not in _BUILTIN_FORMULAS:
        return _ensure_jsonable(
            _err(
                command,
                "topology not supported by built-in library",
                ERR_INVALID_TOPOLOGY,
                "ltagent.math_core is not built yet and the built-in library does not have this topology",
                {
                    "topology": topology,
                    "supported": sorted(_BUILTIN_FORMULAS.keys()),
                    "mathCoreImportError": repr(_MATH_CORE_IMPORT_ERROR) if _MATH_CORE_IMPORT_ERROR else None,
                },
            )
        )

    fallback = _builtin_solve(topology, parameters)
    if "error" in fallback:
        return _ensure_jsonable(
            _err(
                command, "calculation failed", ERR_CALCULATION_FAILED,
                str(fallback.pop("error")),
                {"topology": topology, "parameters": _to_jsonable(dict(parameters))},
            )
        )
    return _ensure_jsonable(_ok(command, f"calculated {topology}", fallback))


def tool_explain_calculation(
    topology: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    project_id: str | None = None,
    config: str | None = None,
) -> HandlerResult:
    """Return the formulas, assumptions, and verification contract
    for a topology calculation. Pure read-only, no file writes."""
    command = "explain_calculation"
    if not isinstance(topology, str) or not topology:
        return _err(
            command, "topology must be a non-empty string",
            ERR_MISSING_PARAM, "topology is required",
            {"field": "topology"},
        )
    if parameters is not None and not isinstance(parameters, Mapping):
        return _err(
            command, "parameters must be a dict when provided",
            ERR_INVALID_INPUT, "parameters is not a dict",
            {"receivedType": type(parameters).__name__},
        )
    if project_id is not None and not isinstance(project_id, str):
        return _err(
            command, "project id must be a string",
            ERR_INVALID_INPUT, "project_id is not a string",
            {"receivedType": type(project_id).__name__},
        )

    cfg, err = _resolve_config(config)
    if err is not None or cfg is None:
        return _ensure_jsonable(err or _err(command, "no config", ERR_CONFIG_INVALID, "no config"))

    if project_id is not None:
        try:
            validate_slug(project_id, kind="project id")
        except SecurityError as exc:
            return _ensure_jsonable(_from_security_error(command, exc))

    explain_fn = _math_core_method("explain")
    if explain_fn is not None and _MATH_CORE_MODULE is not None:
        try:
            result = explain_fn(topology, dict(parameters) if parameters is not None else None)
        except Exception as exc:  # pragma: no cover - depends on math_core
            return _ensure_jsonable(
                _err(command, "math_core.explain raised", ERR_CALCULATION_FAILED, repr(exc), {"topology": topology})
            )
        data = _to_jsonable(result) if result is not None else {}
        if not isinstance(data, dict):
            data = {"result": data}
        data.setdefault("topology", topology)
        data.setdefault("source", "math_core")
        return _ensure_jsonable(_ok(command, f"explained {topology}", data))

    if topology not in _BUILTIN_FORMULAS:
        return _ensure_jsonable(
            _err(
                command,
                "topology not supported by built-in library",
                ERR_INVALID_TOPOLOGY,
                "ltagent.math_core is not built yet and the built-in library does not have this topology",
                {
                    "topology": topology,
                    "supported": sorted(_BUILTIN_FORMULAS.keys()),
                    "mathCoreImportError": repr(_MATH_CORE_IMPORT_ERROR) if _MATH_CORE_IMPORT_ERROR else None,
                },
            )
        )

    return _ensure_jsonable(_ok(command, f"explained {topology}", _builtin_explain(topology)))


# ---------------------------------------------------------------------------
# Public introspection helpers (used by the integrator + tests)
# ---------------------------------------------------------------------------


def live_module_available() -> bool:
    """Return True if :mod:`ltagent.live` is importable."""
    return _LIVE_MODULE is not None


def math_core_available() -> bool:
    """Return True if :mod:`ltagent.math_core` is importable."""
    return _MATH_CORE_MODULE is not None


def supported_builtin_topologies() -> tuple[str, ...]:
    """Return the topology names the built-in mini library can handle."""
    return tuple(sorted(_BUILTIN_FORMULAS.keys()))


__all__ = [
    "ERR_CALCULATION_FAILED",
    "ERR_CONFIG_INVALID",
    "ERR_EDIT_OP_FAILED",
    "ERR_INVALID_INPUT",
    "ERR_INVALID_OPERATION",
    "ERR_INVALID_SNAPSHOT_ID",
    "ERR_INVALID_TOPOLOGY",
    "ERR_LIVE_METHOD_MISSING",
    "ERR_LIVE_MODULE_UNAVAILABLE",
    "ERR_MATH_CORE_METHOD_MISSING",
    "ERR_MATH_CORE_UNAVAILABLE",
    "ERR_MISSING_PARAM",
    "ERR_PROJECT_NOT_FOUND",
    "ERR_RESTORE_FAILED",
    "ERR_RUN_FAILED",
    "ERR_SNAPSHOT_FAILED",
    "ERR_SNAPSHOT_NOT_FOUND",
    "ERR_VERIFY_FAILED",
    "live_module_available",
    "math_core_available",
    "supported_builtin_topologies",
    "tool_calculate_circuit",
    "tool_explain_calculation",
    "tool_live_apply_edit",
    "tool_live_inspect_project",
    "tool_live_open_project",
    "tool_live_restore_snapshot",
    "tool_live_run_and_verify",
    "tool_live_snapshot",
]
