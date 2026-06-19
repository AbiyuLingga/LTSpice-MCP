"""Run-and-verify orchestrator for the file-based live editing workflow.

This module glues three pieces together:

1. A *runner adapter* (anything that satisfies the
   :class:`RunnerAdapter` protocol) that can produce a
   :class:`RunPayload` from a project handle.
2. A *log parser* (anything that satisfies the :class:`LogParser`
   protocol) that turns the runner output into a flat
   ``{name: value}`` dict plus a list of findings.
3. A *list of pre-built :class:`VerificationCheck` values* that the
   caller wants to run against the parsed measurements.

The orchestrator never assumes a real LTspice install. The
:func:`run_and_verify` entry point takes both adapters as arguments
so unit tests can substitute a :func:`fake_runner` that returns a
canned :class:`RunPayload`. This is the same approach used by
:mod:`ltagent.runner` and :mod:`ltagent.doctor`.

Public surface
--------------

* :class:`SimLoopError` -- raised when the orchestrator itself
  cannot proceed (e.g. the runner raised a non-timeout exception).
* :class:`RunPayload` -- a tiny, self-contained carrier for the
  bits of :class:`ltagent.runner.RunResult` that the verifier
  needs.
* :class:`LogParseOutcome` -- the carrier for the parser's output
  (measurements + findings + the simulation_finished flag).
* :class:`RunnerAdapter`, :class:`LogParser` -- structural types
  used to type the injected dependencies.
* :func:`run_and_verify` -- the orchestrator.
* :func:`fake_runner` -- a test helper that returns a canned
  :class:`RunPayload` from a closure or dict.

The orchestrator is total: every failure path is encoded in the
returned :class:`VerificationResult` plus a structured
:class:`SimLoopReport`; it never raises for runner / parser
problems. The only exception is :class:`SimLoopError`, raised when
the caller's input itself is malformed (no checks supplied, runner
adapter returning a non-payload, etc.) -- these are programming
errors and should surface as 500-style internal failures in the
MCP layer.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final, Protocol, cast

from ltagent import log_parser

from .verification import (
    CODE_ACTUAL_MISSING,
    CheckKind,
    VerificationCheck,
    VerificationResult,
    aggregate_verification,
    check_max,
    check_min,
    check_near_target,
)

# ---------------------------------------------------------------------------
# Stable codes
# ---------------------------------------------------------------------------

CODE_OK: Final = "OK"
CODE_RUNNER_RAISED: Final = "SIM_LOOP_RUNNER_RAISED"
CODE_RUN_NOT_ATTEMPTED: Final = "SIM_LOOP_RUN_NOT_ATTEMPTED"
CODE_RUN_FAILED: Final = "SIM_LOOP_RUN_FAILED"
CODE_LOG_MISSING: Final = "SIM_LOOP_LOG_MISSING"
CODE_LOG_FATAL: Final = "SIM_LOOP_LOG_FATAL"
CODE_CHECK_NAME_MISSING: Final = "SIM_LOOP_CHECK_NAME_MISSING"
CODE_DUPLICATE_CHECK_NAME: Final = "SIM_LOOP_DUPLICATE_CHECK_NAME"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class RunnerAdapter(Protocol):
    """Structural type for an injected runner.

    The adapter receives a project handle and returns a
    :class:`RunPayload`. Real implementations wrap
    :func:`ltagent.runner.run_simulation`; test implementations
    return canned data without touching the filesystem.
    """

    def __call__(self, project: Mapping[str, Any]) -> RunPayload: ...


class LogParser(Protocol):
    """Structural type for an injected log parser.

    The default implementation is :func:`log_parser.parse_log` (the
    synchronous file-reading form), but the orchestrator accepts
    anything with the same signature so tests can substitute a
    pure-Python shim.
    """

    def __call__(self, log_path: str) -> LogParseOutcome: ...


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunPayload:
    """The subset of :class:`ltagent.runner.RunResult` the verifier reads.

    The dataclass is intentionally narrow. The orchestrator never
    touches the rest of the runner output; if the rest becomes
    useful later, fields can be added without breaking callers
    that construct their own :class:`RunPayload` (e.g. tests).

    Attributes:
        success: ``True`` iff the runner reported a successful run.
        log_path: Absolute or relative path to the ``.log`` file.
            ``None`` if the run produced no log.
        log_text: Optional inline log content. When set, takes
            priority over ``log_path``; useful for tests and for
            callers that already have the log in memory.
        duration_ms: Wall-clock duration of the run. ``None`` when
            the runner never started (e.g. pre-flight failure).
        exit_code: Process exit code. ``None`` when not available.
        error: Optional structured error from the runner. Should
            be a ``{"code": str, "detail": str, "data": dict}``
            dict following the project's JSON error contract.
    """

    success: bool
    log_path: str | None = None
    log_text: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogParseOutcome:
    """The orchestrator's view of a parsed ``.log`` file.

    Attributes:
        measurements: Flat mapping of measurement name -> value.
        has_fatal: ``True`` if the parser detected a fatal finding.
        simulation_finished: ``True`` if the parser saw the standard
            "Elapsed time" trailer.
        findings: The raw parser findings, in case the caller
            wants to surface them. The dataclass is otherwise
            trimmed to what the verification pipeline needs.
    """

    measurements: dict[str, float]
    has_fatal: bool
    simulation_finished: bool
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimLoopReport:
    """Structured summary of a run-and-verify cycle.

    Attributes:
        run_payload: The :class:`RunPayload` returned by the runner
            adapter (or a synthetic one if the adapter raised).
        parse_outcome: The :class:`LogParseOutcome`. ``None`` when
            no log was parsed.
        result: The :class:`VerificationResult` produced by
            aggregating the checks.
        reason_codes: Top-level codes (e.g.
            :data:`CODE_RUN_NOT_ATTEMPTED`,
            :data:`CODE_LOG_FATAL`) that the orchestrator added
            to the report in addition to the per-check codes.
    """

    run_payload: RunPayload
    parse_outcome: LogParseOutcome | None
    result: VerificationResult
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run_payload.to_dict(),
            "parse": self.parse_outcome.to_dict() if self.parse_outcome else None,
            "verification": self.result.to_dict(),
            "reasonCodes": list(self.reason_codes),
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_and_verify(
    project: Mapping[str, Any],
    runner_adapter: RunnerAdapter,
    checks: list[VerificationCheck],
    *,
    log_parser_func: LogParser | None = None,
) -> SimLoopReport:
    """Run the simulation and aggregate the verification checks.

    Parameters:
        project: A mapping describing the project. The contract is
            intentionally loose -- the runner adapter interprets it
            (e.g. ``{"cirPath": ..., "workdir": ...}`` for the
            real runner; tests can pass whatever they like).
        runner_adapter: Anything satisfying :class:`RunnerAdapter`.
            Called exactly once with ``project``.
        checks: Pre-built :class:`VerificationCheck` values. Each
            check is matched to a measurement by ``check.name``;
            checks whose name is missing from the parsed
            measurements are rewritten to ``ACTUAL_MISSING`` and
            fail. An empty list raises :class:`SimLoopError` -- a
            run with no checks is a programming error.
        log_parser_func: Optional injected parser. When ``None``,
            :func:`ltagent.log_parser.parse_log` is used.

    Returns:
        A :class:`SimLoopReport` carrying the run payload, the
        parse outcome, the verification result, and any
        orchestrator-level reason codes.

    Raises:
        SimLoopError: when the inputs themselves are malformed
            (no checks, runner returned a non-``RunPayload``, etc.).
        TypeError: when the runner returned a non-payload object.
            The orchestrator cannot know how to coerce an arbitrary
            return value into a :class:`RunPayload`, so it surfaces
            a clear type error.
    """
    if not checks:
        raise SimLoopError(
            "run_and_verify requires at least one VerificationCheck; "
            "an empty list would silently produce confidence=1.0"
        )
    _assert_unique_check_names(checks)

    parser = log_parser_func or _default_log_parser

    # ---- run -----------------------------------------------------------
    try:
        payload = runner_adapter(project)
    except Exception as exc:
        # The runner is responsible for its own structured error
        # contract. When the runner itself raises (which the
        # production runner does *not*, by design -- it returns a
        # RunResult with success=False) we wrap the exception as a
        # failed payload so the verifier can still produce a
        # well-formed report.
        payload = RunPayload(
            success=False,
            log_path=None,
            log_text=None,
            duration_ms=None,
            exit_code=None,
            error={
                "code": CODE_RUNNER_RAISED,
                "detail": f"{type(exc).__name__}: {exc}",
                "data": {"exceptionType": type(exc).__name__},
            },
        )
        return _build_report(
            payload,
            parser=parser,
            checks=checks,
            reason_codes=[CODE_RUNNER_RAISED],
        )

    if not isinstance(payload, RunPayload):
        raise TypeError(
            "runner_adapter must return a RunPayload instance; "
            f"got {type(payload).__name__}"
        )

    # ---- short-circuit when the run itself never produced a log ----
    if not payload.success:
        reason = (
            payload.error.get("code", CODE_RUN_FAILED)
            if payload.error
            else CODE_RUN_FAILED
        )
        return _build_report(
            payload,
            parser=parser,
            checks=checks,
            reason_codes=[reason or CODE_RUN_FAILED],
        )
    if payload.log_text is None and not payload.log_path:
        return _build_report(
            payload,
            parser=parser,
            checks=checks,
            reason_codes=[CODE_LOG_MISSING],
        )

    # ---- parse --------------------------------------------------------
    try:
        outcome = _parse_payload_log(payload, parser)
    except Exception as exc:
        # A broken parser should not crash the orchestrator. We
        # surface the failure as a no-measurement outcome so the
        # rest of the pipeline still runs.
        outcome = LogParseOutcome(
            measurements={},
            has_fatal=True,
            simulation_finished=False,
            findings=[
                {
                    "code": "SIM_LOOP_PARSER_RAISED",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "data": {"exceptionType": type(exc).__name__},
                }
            ],
        )

    # ---- bind checks to measurements ---------------------------------
    updated = _bind_checks_to_measurements(checks, outcome.measurements)
    result = aggregate_verification(updated)

    reasons: list[str] = []
    if outcome.has_fatal:
        reasons.append(CODE_LOG_FATAL)
    result.reason_codes = reasons + result.reason_codes

    return SimLoopReport(
        run_payload=payload,
        parse_outcome=outcome,
        result=result,
        reason_codes=reasons,
    )


def run_project_and_verify(
    project_dir: Path | str,
    config: Any,
    check_specs: list[Mapping[str, Any]],
    *,
    projects_root: Path | str,
    run_func: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded live project and persist ``verification.json``.

    Verification targets are explicit inputs. The function never
    infers a target from prose or silently treats a successful process
    exit as a successful engineering verification.
    """
    from ltagent.runner import RunRequest, RunResult, run_simulation

    from .project import open_live_project

    paths = open_live_project(project_dir, projects_root=projects_root)
    if not check_specs:
        return {
            "success": False,
            "simulationAttempted": False,
            "errors": [
                {
                    "code": "VERIFY_TARGETS_MISSING",
                    "detail": "at least one explicit verification check is required",
                    "data": {},
                }
            ],
            "warnings": [],
        }

    checks: list[VerificationCheck] = []
    for spec in check_specs:
        name = spec.get("name")
        kind = spec.get("kind", "near_target")
        if not isinstance(name, str) or not name:
            return {
                "success": False,
                "simulationAttempted": False,
                "errors": [{"code": "VERIFY_CHECK_INVALID", "detail": "check name is required", "data": {}}],
                "warnings": [],
            }
        if kind == "near_target":
            checks.append(
                check_near_target(
                    None,
                    spec.get("target"),
                    spec.get("tolerancePercent", 0.0),
                    name=name,
                )
            )
        elif kind == "max":
            checks.append(
                check_max(None, cast(float | int | str, spec.get("bound")), name=name)
            )
        elif kind == "min":
            checks.append(
                check_min(None, cast(float | int | str, spec.get("bound")), name=name)
            )
        else:
            return {
                "success": False,
                "simulationAttempted": False,
                "errors": [{"code": "VERIFY_CHECK_INVALID", "detail": f"unknown check kind {kind!r}", "data": {"name": name}}],
                "warnings": [],
            }

    request = RunRequest(
        cir_path=paths.cir,
        workdir=paths.project_dir,
        timeout_seconds=config.runner.timeout_seconds,
        mode=config.ltspice.mode,
        executable=config.ltspice.executable,
        wine_command=config.ltspice.wine_command,
        expected_log_name="circuit.log",
    )
    actual_run = run_func or run_simulation

    def adapter(_project: Mapping[str, Any]) -> RunPayload:
        result = actual_run(request)
        if not isinstance(result, RunResult):
            raise TypeError(f"runner must return RunResult, got {type(result).__name__}")
        first_error = result.errors[0] if result.errors else None
        return RunPayload(
            success=result.success,
            log_path=result.data.get("logPath"),
            duration_ms=result.data.get("durationMs"),
            exit_code=result.data.get("exitCode"),
            error=first_error,
        )

    report = run_and_verify(
        {"projectDir": str(paths.project_dir), "cirPath": str(paths.cir)},
        cast(RunnerAdapter, adapter),
        checks,
    )
    payload = report.to_dict()
    payload["success"] = report.result.overall_passed
    payload["simulationAttempted"] = True
    payload["errors"] = [] if report.result.overall_passed else [
        {
            "code": code,
            "detail": "simulation or verification did not pass",
            "data": {},
        }
        for code in report.reason_codes or report.result.reason_codes
    ]
    payload["warnings"] = []
    tmp_path = paths.verification.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(paths.verification)
    return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake_runner(
    log_text: str,
    *,
    success: bool = True,
    log_path: str = "/tmp/fake.log",
    duration_ms: int = 12,
    exit_code: int = 0,
    error: dict[str, Any] | None = None,
) -> RunnerAdapter:
    """Return a :class:`RunnerAdapter` that yields a canned :class:`RunPayload`.

    The returned closure captures the log text and the success flag
    so a single ``fake_runner(...)`` call produces an adapter
    suitable for handing directly to :func:`run_and_verify`.

    Example::

        adapter = fake_runner(
            "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\\n",
        )
        report = run_and_verify({}, adapter, [check_near_target(...)])
    """
    payload = RunPayload(
        success=success,
        log_path=log_path,
        log_text=log_text,
        duration_ms=duration_ms,
        exit_code=exit_code,
        error=error,
    )

    def _adapter(project: Mapping[str, Any]) -> RunPayload:
        return payload

    return _adapter


def fake_runner_raising(exc: BaseException) -> RunnerAdapter:
    """Return a :class:`RunnerAdapter` that raises ``exc`` immediately.

    Used to test the ``SIM_LOOP_RUNNER_RAISED`` branch without
    needing to monkey-patch ``subprocess.run`` or similar.
    """
    def _adapter(project: Mapping[str, Any]) -> RunPayload:
        raise exc

    return _adapter


def _default_log_parser(log_path: str) -> LogParseOutcome:
    """Default implementation of :class:`LogParser`.

    Wraps :func:`ltagent.log_parser.parse_log` and trims the
    :class:`ParseReport` down to the fields the orchestrator uses.
    """
    report = log_parser.parse_log(log_path)
    return LogParseOutcome(
        measurements={k: v.value for k, v in report.measurements.items()},
        has_fatal=report.has_fatal,
        simulation_finished=report.simulation_finished,
        findings=[f.to_dict() for f in report.findings],
    )


def _parse_payload_log(
    payload: RunPayload, parser: LogParser
) -> LogParseOutcome:
    """Resolve a payload to a :class:`LogParseOutcome`.

    Inline ``log_text`` takes priority over ``log_path`` so tests
    can run without writing to disk. When the parser needs a file
    we use :mod:`tempfile` to materialise the inline text.
    """
    if payload.log_text is not None:
        with NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write(payload.log_text)
            tmp_path = f.name
        try:
            return parser(tmp_path)
        finally:
            # Best-effort cleanup. The temp dir is a /tmp location;
            # the OS reclaims it on reboot.
            with suppress(OSError):
                Path(tmp_path).unlink(missing_ok=True)
    if payload.log_path is not None:
        return parser(payload.log_path)
    raise SimLoopError(
        "RunPayload has neither log_text nor log_path; cannot parse"
    )


def _bind_checks_to_measurements(
    checks: list[VerificationCheck],
    measurements: Mapping[str, float],
) -> list[VerificationCheck]:
    """Rewrite each check's ``actual`` from the parsed measurements.

    Each pre-built :class:`VerificationCheck` is treated as a *spec*
    (kind + target / bound / tolerance) plus a possibly-stale
    ``actual`` value. The orchestrator replaces ``actual`` with the
    parsed measurement and re-evaluates the check from scratch so
    ``passed``, ``code`` and ``detail`` always describe the
    *bound* state. This is the only way the final report can be
    internally consistent -- a check that says
    ``actual=0.7071, passed=False, detail="actual=0"`` would be
    impossible to read.

    If the measurement is missing, the check is replaced with a new
    check of the same kind / target / bound / tolerance but with
    ``actual = None`` and ``code = ACTUAL_MISSING``. This is the
    contract :mod:`ltagent.live.verification` already understands;
    the orchestrator just adapts the parsed dict into that shape.
    """
    updated: list[VerificationCheck] = []
    for c in checks:
        if c.name in measurements:
            value = measurements[c.name]
            actual = _coerce_finite_float(value)
            if actual is None:
                updated.append(
                    VerificationCheck(
                        name=c.name,
                        kind=c.kind,
                        actual=None,
                        target=c.target,
                        bound=c.bound,
                        tolerance_percent=c.tolerance_percent,
                        passed=False,
                        code=CODE_ACTUAL_MISSING,
                        detail=(
                            f"measurement {c.name!r} was present but could not "
                            "be coerced to a finite number"
                        ),
                    )
                )
                continue
            # Re-evaluate from the spec so passed/code/detail match
            # the freshly bound actual.
            updated.append(
                _evaluate_check(
                    name=c.name,
                    kind=c.kind,
                    target=c.target,
                    bound=c.bound,
                    tolerance_percent=c.tolerance_percent,
                    actual=actual,
                )
            )
            continue
        # Measurement missing -> rewrite as a structured failure.
        updated.append(
            VerificationCheck(
                name=c.name,
                kind=c.kind,
                actual=None,
                target=c.target,
                bound=c.bound,
                tolerance_percent=c.tolerance_percent,
                passed=False,
                code=CODE_ACTUAL_MISSING,
                detail=(
                    f"measurement {c.name!r} is not present in the parsed log; "
                    "check cannot be evaluated"
                ),
            )
        )
    return updated


def _evaluate_check(
    *,
    name: str,
    kind: CheckKind,
    target: float | None,
    bound: float | None,
    tolerance_percent: float | None,
    actual: float,
) -> VerificationCheck:
    """Recompute ``passed`` / ``code`` / ``detail`` for a bound check.

    Mirrors the three public helpers in :mod:`ltagent.live.verification`
    but operates on already-typed arguments so we don't have to
    pass through the messy ``float | int | str | None`` coercion
    twice. The behaviour is identical; the helper exists so the
    orchestrator and the public checks can share the same logic
    without circular imports.
    """
    from .verification import (
        CODE_OK,
        check_max,
        check_min,
        check_near_target,
    )

    if kind is CheckKind.NEAR_TARGET:
        if target is None or tolerance_percent is None:
            return VerificationCheck(
                name=name,
                kind=kind,
                actual=actual,
                target=target,
                bound=bound,
                tolerance_percent=tolerance_percent,
                passed=False,
                code="TARGET_MISSING",
                detail=(
                    "near_target check requires both target and tolerance_percent; "
                    f"got target={target!r}, tolerance_percent={tolerance_percent!r}"
                ),
            )
        return check_near_target(actual, target, tolerance_percent, name=name)
    if kind is CheckKind.MAX:
        if bound is None:
            return VerificationCheck(
                name=name,
                kind=kind,
                actual=actual,
                target=target,
                bound=bound,
                tolerance_percent=tolerance_percent,
                passed=False,
                code="BOUND_INVALID",
                detail="max check requires a bound value",
            )
        return check_max(actual, bound, name=name)
    if kind is CheckKind.MIN:
        if bound is None:
            return VerificationCheck(
                name=name,
                kind=kind,
                actual=actual,
                target=target,
                bound=bound,
                tolerance_percent=tolerance_percent,
                passed=False,
                code="BOUND_INVALID",
                detail="min check requires a bound value",
            )
        return check_min(actual, bound, name=name)
    # Unknown kind -- treat as a structural error so the caller sees
    # a structured failure rather than a silent pass.
    return VerificationCheck(
        name=name,
        kind=kind,
        actual=actual,
        target=target,
        bound=bound,
        tolerance_percent=tolerance_percent,
        passed=False,
        code=CODE_OK,  # use OK as a fallback; aggregation will surface
        # the mismatch via the unknown kind.
        detail=f"unknown check kind: {kind!r}",
    )


def _coerce_finite_float(value: Any) -> float | None:
    """Return ``value`` as a finite ``float`` or ``None``.

    Bool is rejected because ``True``/``False`` are not meaningful
    numeric measurements and would silently coerce to 1.0/0.0.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _assert_unique_check_names(checks: list[VerificationCheck]) -> None:
    """Raise :class:`SimLoopError` when two checks share a name."""
    seen: set[str] = set()
    for c in checks:
        if c.name in seen:
            raise SimLoopError(
                f"duplicate check name {c.name!r}; each VerificationCheck "
                "must have a unique name within a run_and_verify call"
            )
        seen.add(c.name)


def _build_report(
    payload: RunPayload,
    *,
    parser: LogParser,
    checks: list[VerificationCheck],
    reason_codes: list[str],
) -> SimLoopReport:
    """Build a :class:`SimLoopReport` for a failure that prevented parsing.

    All checks are rewritten to ``ACTUAL_MISSING`` so the caller
    still gets a well-formed :class:`VerificationResult` (the
    orchestrator is total).
    """
    missing_checks: list[VerificationCheck] = []
    for c in checks:
        if c.name:
            missing_checks.append(
                VerificationCheck(
                    name=c.name,
                    kind=c.kind,
                    actual=None,
                    target=c.target,
                    bound=c.bound,
                    tolerance_percent=c.tolerance_percent,
                    passed=False,
                    code=CODE_ACTUAL_MISSING,
                    detail=(
                        f"measurement {c.name!r} not available because the "
                        "simulation did not produce a usable log"
                    ),
                )
            )
    result = aggregate_verification(missing_checks)
    result.reason_codes = list(reason_codes) + result.reason_codes
    return SimLoopReport(
        run_payload=payload,
        parse_outcome=None,
        result=result,
        reason_codes=list(reason_codes),
    )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SimLoopError(RuntimeError):
    """Raised when the run-and-verify inputs themselves are malformed.

    The orchestrator catches runner / parser exceptions and encodes
    them in the :class:`SimLoopReport`. :class:`SimLoopError` is for
    the *caller* -- a missing or duplicate check name, a runner
    adapter returning a non-payload -- that the orchestrator
    cannot fix on its own.
    """


__all__ = [
    "CODE_CHECK_NAME_MISSING",
    "CODE_DUPLICATE_CHECK_NAME",
    "CODE_LOG_FATAL",
    "CODE_LOG_MISSING",
    "CODE_OK",
    "CODE_RUNNER_RAISED",
    "CODE_RUN_FAILED",
    "CODE_RUN_NOT_ATTEMPTED",
    "CheckKind",
    "LogParseOutcome",
    "LogParser",
    "RunPayload",
    "RunnerAdapter",
    "SimLoopError",
    "SimLoopReport",
    "VerificationCheck",
    "VerificationResult",
    "fake_runner",
    "fake_runner_raising",
    "run_and_verify",
    "run_project_and_verify",
]
