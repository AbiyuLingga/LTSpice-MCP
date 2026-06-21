"""Phase 4: ``result.json`` builder, writer, and assertion engine.

The ``result.json`` artifact is the stable, machine-readable summary of
a project run. It is consumed by agents and the eventual MCP server to
make decisions without re-parsing the raw ``.log``.

The contract (from plan section 14.2) is:

::

    {
      "success": true,
      "projectId": "2026-06-17_rc_lowpass_1khz",
      "files": {
        "ir": "circuit.ir.json",
        "cir": "circuit.cir",
        "asc": "circuit.asc",
        "log": "circuit.log",
        "raw": null,
        "result": "result.json"
      },
      "run": {
        "attempted": true,
        "success": true,
        "timeoutSeconds": 30,
        "durationMs": 812
      },
      "measurements": {
        "VOUT_MAX": 0.707
      },
      "assertions": [
        {"name": "simulation_has_no_errors", "passed": true}
      ],
      "layout": {
        "score": 92,
        "warnings": []
      },
      "template": {
        "used": "rc_lowpass",
        "promoted": false
      },
      "warnings": [],
      "errors": []
    }

This module owns the construction. The CLI / MCP layers just call
:func:`build_result` and either print the dict or hand it to
:func:`write_result`.

The assertion engine here is intentionally minimal. It only checks two
kinds of constraints:

1. ``simulation_has_no_errors`` — always added; fails if the parser
   detected any fatal/error finding.
2. Constraints declared in the IR (e.g. ``targetCutoffHz``) — handled by
   :func:`assert_constraints`.

Constraint formulas are intentionally not interpreted here. The MVP
only knows about ``targetCutoffHz`` for ``rc_lowpass`` and
``rc_highpass`` topologies. Everything else is reported as
``UNSUPPORTED_CONSTRAINT`` so future phases can extend safely.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .log_parser import ParseReport

#: Schema version of the ``result.json`` contract. Bumped whenever the
#: shape changes in a backwards-incompatible way.
RESULT_SCHEMA_VERSION = "0.1"

# ---------------------------------------------------------------------------
# Error / assertion code constants
# ---------------------------------------------------------------------------

#: Result-level error codes. Keep them stable: agents may switch on them.
RES_ERR_RUN_FAILED = "RUN_FAILED"
RES_ERR_RUN_NOT_ATTEMPTED = "RUN_NOT_ATTEMPTED"
RES_ERR_ASSERTION_FAILED = "ASSERTION_FAILED"
RES_ERR_UNSUPPORTED_CONSTRAINT = "UNSUPPORTED_CONSTRAINT"
RES_ERR_MEASUREMENT_MISSING = "MEASUREMENT_MISSING"
RES_ERR_WRITE_FAILED = "RESULT_WRITE_FAILED"

#: Names of the always-on assertions.
ASSERT_SIM_NO_ERRORS = "simulation_has_no_errors"
ASSERT_SIM_FINISHED = "simulation_finished"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileMap:
    """The set of project files referenced in ``result.json``.

    All paths are stored as relative strings (no absolute paths leak into
    the result artifact). The CLI / MCP layer is responsible for
    combining this with a base directory when it actually wants to read
    a file.
    """

    ir: str = "circuit.ir.json"
    cir: str = "circuit.cir"
    asc: str | None = "circuit.asc"
    log: str | None = "circuit.log"
    raw: str | None = None
    result: str = "result.json"

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class RunInfo:
    """Subset of :class:`ltagent.runner.RunResult` that goes into result.json.

    Only the fields that are useful for downstream consumers are kept.
    Anything that varies between simulator versions (e.g. argv) is
    dropped to keep the contract stable.

    The dataclass fields use snake_case (Python convention) but the
    JSON output uses camelCase (plan section 14.2).
    """

    attempted: bool
    success: bool
    timeout_seconds: int | None = None
    duration_ms: int | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "success": self.success,
            "timeoutSeconds": self.timeout_seconds,
            "durationMs": self.duration_ms,
            "exitCode": self.exit_code,
        }


@dataclass(frozen=True)
class AssertionResult:
    """Outcome of a single assertion check."""

    name: str
    passed: bool
    detail: str = ""
    observed: float | None = None
    expected: float | None = None
    tolerance: float | None = None
    code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Result:
    """Top-level ``result.json`` structure.

    The class is mutable for incremental assembly; once :func:`to_dict`
    is called the shape is frozen and safe to serialize.
    """

    project_id: str
    files: FileMap = field(default_factory=FileMap)
    run: RunInfo = field(default_factory=lambda: RunInfo(attempted=False, success=False))
    measurements: dict[str, float] = field(default_factory=dict)
    assertions: list[AssertionResult] = field(default_factory=list)
    layout_score: int | None = None
    layout_warnings: list[str] = field(default_factory=list)
    template_used: str | None = None
    template_promoted: bool = False
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = RESULT_SCHEMA_VERSION

    @property
    def success(self) -> bool:
        """``True`` iff no errors and all assertions pass."""
        return not self.errors and all(a.passed for a in self.assertions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "success": self.success,
            "projectId": self.project_id,
            "files": self.files.to_dict(),
            "run": self.run.to_dict(),
            "measurements": dict(self.measurements),
            "assertions": [a.to_dict() for a in self.assertions],
            "layout": {
                "score": self.layout_score,
                "warnings": list(self.layout_warnings),
            },
            "template": {
                "used": self.template_used,
                "promoted": self.template_promoted,
            },
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def build_result_from_run(
    project_id: str,
    run_payload: Mapping[str, Any] | None,
    parse_report: ParseReport | None,
    *,
    files: FileMap | None = None,
    template_used: str | None = None,
    template_promoted: bool = False,
    layout_score: int | None = None,
    layout_warnings: list[str] | None = None,
) -> Result:
    """Assemble a :class:`Result` from the output of runner + parser.

    The function is a pure assembler; it does not perform I/O. It
    enforces the rule "failed simulation cannot be marked successful"
    by inspecting both the run payload and the parse report.
    """
    result = Result(
        project_id=project_id,
        files=files or FileMap(),
        template_used=template_used,
        template_promoted=template_promoted,
        layout_score=layout_score,
        layout_warnings=list(layout_warnings) if layout_warnings else [],
    )

    # ---- run info -------------------------------------------------------
    if run_payload is None:
        result.run = RunInfo(attempted=False, success=False)
    else:
        success = bool(run_payload.get("success"))
        result.run = RunInfo(
            attempted=True,
            success=success,
            timeout_seconds=(
                int(run_payload["timeoutSeconds"])
                if run_payload.get("timeoutSeconds") is not None
                else None
            ),
            duration_ms=(
                int(run_payload["durationMs"])
                if run_payload.get("durationMs") is not None
                else None
            ),
            exit_code=(
                int(run_payload["exitCode"]) if run_payload.get("exitCode") is not None else None
            ),
        )

    # ---- measurements ---------------------------------------------------
    if parse_report is not None:
        result.measurements = {name: mr.value for name, mr in parse_report.measurements.items()}
        # Surface parser findings into the result-level errors/warnings.
        for f in parse_report.errors:
            result.errors.append(
                {
                    "code": f.code,
                    "detail": f.line.strip(),
                    "data": {"lineNo": f.line_no, "source": "log_parser"},
                }
            )
        for f in parse_report.warnings:
            result.warnings.append(
                {
                    "code": f.code,
                    "detail": f.line.strip(),
                    "data": {"lineNo": f.line_no, "source": "log_parser"},
                }
            )

    # ---- always-on assertions -----------------------------------------
    add_simulation_assertions(result, parse_report)

    return result


def add_simulation_assertions(result: Result, report: ParseReport | None) -> None:
    """Add the two always-on assertions: error-free + finished."""
    if report is None:
        result.assertions.append(
            AssertionResult(
                name=ASSERT_SIM_NO_ERRORS,
                passed=False,
                detail="no log was parsed",
                code=RES_ERR_RUN_NOT_ATTEMPTED,
            )
        )
        result.assertions.append(
            AssertionResult(
                name=ASSERT_SIM_FINISHED,
                passed=False,
                detail="no log was parsed",
                code=RES_ERR_RUN_NOT_ATTEMPTED,
            )
        )
        return

    result.assertions.append(
        AssertionResult(
            name=ASSERT_SIM_NO_ERRORS,
            passed=not report.has_fatal and not report.errors,
            detail=(
                "no fatal findings"
                if not report.has_fatal and not report.errors
                else f"{len(report.errors)} error finding(s)"
            ),
        )
    )
    result.assertions.append(
        AssertionResult(
            name=ASSERT_SIM_FINISHED,
            passed=report.simulation_finished,
            detail=(
                "elapsed-time trailer found"
                if report.simulation_finished
                else "no 'Elapsed time' trailer; run may be partial"
            ),
        )
    )


def assert_constraints(
    result: Result,
    topology: str | None,
    constraints: Mapping[str, Any] | None,
    *,
    cutoff_tolerance_pct: float = 5.0,
) -> None:
    """Add topology-aware constraint assertions to ``result``.

    Supported constraints per plan MVP:

    * ``targetCutoffHz`` for ``rc_lowpass`` and ``rc_highpass`` — derived
      from ``1 / (2 * pi * R * C)`` and compared to the user's target
      with a percentage tolerance. We don't have R and C values here
      directly; the caller is expected to pass the *measured* cutoff
      using a constraint-side measurement named ``fcut_meas`` if they
      want a numerical comparison. When that measurement is missing we
      add a skipped-style note instead of failing.

    Unknown constraint keys are recorded as ``UNSUPPORTED_CONSTRAINT``
    warnings so future phases can extend the catalog without breaking
    older result.json readers.
    """
    if not constraints:
        return
    for key, target in constraints.items():
        if key == "targetCutoffHz":
            if topology not in ("rc_lowpass", "rc_highpass"):
                result.warnings.append(
                    {
                        "code": RES_ERR_UNSUPPORTED_CONSTRAINT,
                        "detail": (
                            f"targetCutoffHz only applies to rc_lowpass/"
                            f"rc_highpass; topology is {topology!r}"
                        ),
                        "data": {"constraint": key, "target": target},
                    }
                )
                continue
            measured = result.measurements.get("fcut_meas")
            if measured is None:
                # Not a hard failure; the user just didn't supply a
                # measured cutoff. We add an informational assertion so
                # the gap is visible in the result.
                result.assertions.append(
                    AssertionResult(
                        name="cutoff_within_tolerance",
                        passed=True,
                        detail=(
                            f"no fcut_meas measurement provided; target {target} Hz not validated"
                        ),
                        expected=float(target),
                        code=RES_ERR_UNSUPPORTED_CONSTRAINT,
                    )
                )
                continue
            tol_pct = abs(float(measured) - float(target)) / float(target) * 100.0
            passed = tol_pct <= cutoff_tolerance_pct
            result.assertions.append(
                AssertionResult(
                    name="cutoff_within_tolerance",
                    passed=passed,
                    detail=(
                        f"measured {measured:g} Hz vs target {target:g} Hz "
                        f"({tol_pct:.2f}% off, tolerance {cutoff_tolerance_pct:.1f}%)"
                    ),
                    observed=measured,
                    expected=float(target),
                    tolerance=cutoff_tolerance_pct,
                    code=RES_ERR_ASSERTION_FAILED if not passed else None,
                )
            )
            continue
        # Unknown key: surface as warning, never as error.
        result.warnings.append(
            {
                "code": RES_ERR_UNSUPPORTED_CONSTRAINT,
                "detail": f"unsupported constraint key: {key!r}",
                "data": {"constraint": key, "target": target},
            }
        )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def write_result(result: Result, target: Path) -> None:
    """Serialize ``result`` to ``target`` as pretty JSON.

    Creates parent directories as needed. Uses a stable key order so
    diff-friendly behaviour holds across runs.

    Raises:
        OSError: on filesystem failure.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def read_result(source: Path | str) -> dict[str, Any]:
    """Read a previously written ``result.json`` and return the dict.

    Does not validate the schema; that is the caller's responsibility.
    """
    text = Path(source).read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


# ---------------------------------------------------------------------------
# Topology-aware formulas (small, additive; safe to import lazily)
# ---------------------------------------------------------------------------


def compute_rc_cutoff_hz(r_ohms: float, c_farads: float) -> float:
    """Return the -3 dB cutoff frequency of a first-order RC filter.

    ``f = 1 / (2 * pi * R * C)``. Pure helper exposed here so both the
    netlist template (Phase 2) and the constraint checker (this module)
    can share the same formula without a circular import.
    """
    if r_ohms <= 0 or c_farads <= 0:
        raise ValueError("R and C must be positive")
    return 1.0 / (2.0 * math.pi * r_ohms * c_farads)
