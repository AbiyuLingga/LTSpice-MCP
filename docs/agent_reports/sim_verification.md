# Agent 5 — Simulation + Verification Report

**Agent:** 5 (Simulation + Verification)
**Branch:** `agent-5-sim-verification`
**Status:** Complete — verification + measurement + sim-loop API landed,
88 unit tests passing, ruff and mypy clean.

---

## 1. Scope

This agent owns the **verification pipeline** for the file-based live
editing workflow described in
[`ltspice_file_based_live_editing_math_plan.md`](../../ltspice_file_based_live_editing_math_plan.md)
(plan sections 17 and 18.1). The goal: given a closed-form formula
prediction and a simulation measurement, decide whether the design
*passed* the target, attach a confidence score, and surface a
structured error / warning if the inputs are missing or invalid.

The pipeline is the consumer-facing contract for the eventual MCP
`live_run_and_verify` tool (plan §11.7, Phase 7). The math core
sibling agent owns the closed-form formulas; this agent consumes
their numeric output and reconciles it against LTspice's numeric
output.

In scope:

* :class:`VerificationCheck` and :class:`VerificationResult` —
  the per-check and aggregate result types.
* :func:`check_near_target`, :func:`check_max`, :func:`check_min` —
  the three pure-function comparison kinds.
* :func:`aggregate_verification` — rolls a list of checks up into
  an :class:`VerificationResult` with a fixed-step confidence
  score in ``{0.0, 0.25, 0.5, 0.75, 1.0}``.
* :class:`MeasurementRequest`, :func:`generate_meas_directives`,
  :func:`parse_measurement_lines`, :func:`ripple_from_max_min` —
  the ``.meas`` request side of the contract (DC voltage, AC gain
  at frequency, transient MAX / MIN / PP, log-line parsing).
* :class:`FormulaVsSimResult` and
  :func:`ltagent.math_core.verification_math.compare_formula_vs_simulation`
  — the math-core helper that compares a formula prediction to a
  measurement and decides pass/fail with the same percentage
  tolerance the verification pipeline uses.
* :func:`run_and_verify` and :class:`SimLoopReport` — the
  orchestrator that runs an injected runner adapter, parses the
  log, binds the resulting measurements to pre-built checks, and
  produces a structured report. Includes a
  :func:`fake_runner` helper so the whole pipeline is testable
  without a real LTspice install.

Out of scope (deferred to other agents or later phases):

* The optimizer loop (plan §18) — Agent 5 provides the per-check
  objective, not the search driver.
* Tolerance / worst-case / Monte-Carlo analysis (plan §11.11) —
  the ``math_core.tolerance`` module is owned by Agent 4.
* The live editing wire format (graph / IR / edit operations) —
  Agent 1 / Agent 2 own those.
* MCP tool wrappers (plan §11.7) — Agent 6 owns the FastMCP glue.

---

## 2. Files

| File | Purpose |
| --- | --- |
| `src/ltagent/live/__init__.py` | Minimal package marker. |
| `src/ltagent/live/verification.py` | `VerificationCheck` / `VerificationResult` and the three pure-function checks. |
| `src/ltagent/live/measurements.py` | `MeasurementRequest`, the four convenience constructors (`dc_voltage`, `ac_gain_at_frequency`, `transient_max`, `transient_min`, `transient_ripple`), `generate_meas_directives`, `parse_measurement_lines`, `ripple_from_max_min`. |
| `src/ltagent/live/sim_loop.py` | `run_and_verify`, `RunPayload`, `LogParseOutcome`, `SimLoopReport`, `RunnerAdapter` / `LogParser` protocols, `fake_runner` / `fake_runner_raising` test helpers, `SimLoopError`. |
| `src/ltagent/math_core/__init__.py` | Minimal package marker (no eager submodule imports — see "Integration Requests" below). |
| `src/ltagent/math_core/verification_math.py` | `compare_formula_vs_simulation` + `FormulaVsSimResult`. |
| `tests/test_live_verification.py` | 50 tests covering `verification.py` + `sim_loop.py`. |
| `tests/test_measurements.py` | 38 tests covering `measurements.py` + `verification_math.py`. |
| `docs/agent_reports/sim_verification.md` | This report. |

88 tests, 0 skipped, ~0.5 s wall clock on a single CI worker.

---

## 3. Public API

### 3.1 Verification checks (`ltagent.live.verification`)

```python
from ltagent.live.verification import (
    check_near_target, check_max, check_min, aggregate_verification,
    VerificationCheck, VerificationResult, CheckKind,
)

c1 = check_near_target(actual=994.7, target=1000.0, tolerance_percent=2.0,
                       name="cutoff_frequency")
c2 = check_max(actual=0.045, max_value=0.05, name="ripple")
result = aggregate_verification([c1, c2])
assert isinstance(result, VerificationResult)
assert result.overall_passed is True
assert result.confidence == pytest.approx(1.0)
```

* `check_near_target` accepts ``None`` / non-finite inputs and
  produces a structured ``ACTUAL_MISSING`` / ``ACTUAL_INVALID`` /
  ``TARGET_MISSING`` / ``TOLERANCE_INVALID`` code rather than
  raising.
* `check_max` / `check_min` mirror the same pattern with one-sided
  bounds and ``BOUND_INVALID`` for a missing / non-finite bound.
* `aggregate_verification` rolls a list of checks up; the
  ``confidence`` is a fixed-step score (0.0, 0.25, 0.5, 0.75, 1.0)
  with a ``MISSING_ACTUAL_PENALTY`` of 0.25 per missing actual.
  Empty input returns ``confidence == 1.0`` (no checks → nothing
  to fail).
* All outputs are JSON-serialisable via ``to_dict()``; downstream
  ``verification.json`` consumers do not need
  :func:`ltagent.serialization.to_jsonable`.

### 3.2 Measurement requests (`ltagent.live.measurements`)

```python
from ltagent.live.measurements import (
    generate_meas_directives,
    dc_voltage, ac_gain_at_frequency,
    transient_max, transient_min, transient_ripple,
)

reqs = [
    dc_voltage("VOUT", "out"),
    ac_gain_at_frequency("AV", "out", "in", "1k"),
    transient_max("VMAX", "v(out)"),
    transient_min("VMIN", "v(out)"),
    transient_ripple("VRIP", "v(out)", from_value="10m", to_value="20m"),
]
print(*generate_meas_directives(reqs), sep="")
# .meas op VOUT FIND v(out)
# .meas ac AV FIND v(out)/v(in) AT 1k
# .meas tran VMAX MAX v(out)
# .meas tran VMIN MIN v(out)
# .meas tran VRIP PP v(out) FROM 10m TO 20m
```

* :class:`MeasurementRequest` is a frozen dataclass; ``__post_init__``
  enforces the SPICE identifier pattern on ``name`` and a probe
  pattern (``v(<ident>)`` / ``i(<ident>)`` plus arithmetic) on
  ``expression``. ``AT`` is only allowed on ``FIND``.
* :func:`parse_measurement_lines` is a thin wrapper over
  :func:`ltagent.log_parser.parse_log_text`; returns the full
  mapping (name → :class:`ltagent.log_parser.MeasurementResult`).
* :func:`ripple_from_max_min` derives a peak-to-peak ripple from a
  pair of ``MAX`` / ``MIN`` measurements; the function never
  raises and tags swapped inputs as
  ``MEAS_RIPPLE_INPUT_SWAPPED`` so callers can decide whether
  to flag or correct.

### 3.3 Math core helper (`ltagent.math_core.verification_math`)

```python
from ltagent.math_core.verification_math import (
    compare_formula_vs_simulation,
    FormulaVsSimResult, CODE_OK, CODE_ZERO_PREDICTION,
    CODE_FORMULA_INPUT_INVALID,
)

r = compare_formula_vs_simulation(
    formula_prediction=994.7,  # 1 / (2*pi*R*C) for R=1.6k, C=100nF
    simulation_measurement=1000.0,
    tolerance_percent=2.0,
)
assert r.code == "OK"
assert r.passed is True
assert r.percent_error == pytest.approx(0.53, rel=0.01)
```

* The function is total — ``None`` / ``nan`` / ``inf`` produce a
  structured ``FORMULA_INPUT_INVALID`` rather than raising.
* A zero formula prediction produces a structured
  ``ZERO_PREDICTION`` code and falls back to absolute-error
  comparison; ``percent_error`` is ``None``.
* Negative tolerances are clamped to zero (bit-exact
  comparison) so a negative caller input is never silently
  treated as "always pass".

### 3.4 Run-and-verify orchestrator (`ltagent.live.sim_loop`)

```python
from ltagent.live.sim_loop import run_and_verify, fake_runner
from ltagent.live.verification import check_near_target

adapter = fake_runner(
    "vout_max: MAX(v(out))=0.70710678 FROM 0 TO 0.005\n"
    "Elapsed time: 0.1 seconds\n"
)
report = run_and_verify(
    project={},
    runner_adapter=adapter,
    checks=[check_near_target(0.0, 0.7071, 1.0, name="vout_max")],
)
assert report.result.overall_passed is True
assert report.result.confidence == pytest.approx(1.0)
```

* :func:`run_and_verify` is total: every failure path (runner
  raised, run failed, log missing, log has fatal finding) is
  encoded in :class:`SimLoopReport.reason_codes` and
  :class:`VerificationResult.reason_codes` rather than as an
  exception. The only exception is :class:`SimLoopError`, raised
  when the *caller's* input is malformed (empty check list,
  duplicate check names, runner returned a non-payload).
* The orchestrator is dependency-injected: any callable
  matching :class:`RunnerAdapter` and :class:`LogParser` works.
  The default parser is :func:`ltagent.log_parser.parse_log`.
  Tests substitute :func:`fake_runner` and a custom parser
  closure.
* Each pre-built :class:`VerificationCheck` is treated as a
  *spec* (kind + target / bound / tolerance) plus a possibly-
  stale ``actual`` value. The orchestrator replaces ``actual``
  with the parsed measurement and re-evaluates the check from
  scratch so ``passed`` / ``code`` / ``detail`` always describe
  the *bound* state.

---

## 4. How to test

```bash
# Unit tests for Agent 5 (88 tests, no LTspice / Wine required)
.venv/bin/python -m pytest tests/test_live_verification.py tests/test_measurements.py -v

# Lint
.venv/bin/ruff check src/ltagent/live/ src/ltagent/math_core/ tests/test_live_verification.py tests/test_measurements.py

# Type check
.venv/bin/python -m mypy src/ltagent/live/verification.py src/ltagent/live/measurements.py src/ltagent/live/sim_loop.py src/ltagent/math_core/verification_math.py
```

Expected: 88 passed in <1 s; ruff clean; mypy clean.

If the project-wide pytest is run with the rest of the test files
present, the only test files that touch this agent's code are the
two above; the rest of the test suite is unaffected.

---

## 5. Risks and limitations

1. **No real LTspice test in CI.** The verification pipeline can
   be exercised end-to-end via :func:`fake_runner` and an inline
   log text, but the actual `LTSPICE → parse_log → verify` path
   is covered by the Phase 0 / Phase 3 integration tests, not by
   this module's unit tests. That is intentional: Agent 5 owns
   the verification math, not the runner. If the LTspice parser
   changes shape, the worst-case symptom is ``ACTUAL_MISSING``
   falling out of the orchestrator (which is the right answer).

2. **The math core is the source of truth for formula
   predictions.** ``compare_formula_vs_simulation`` does not
   evaluate formulas itself; it only reconciles a pre-computed
   prediction against a pre-computed measurement. The
   ``formulas.py`` / ``standard_values.py`` modules (Agent 4)
   own the actual evaluation. Agent 5's helper is the *bridge*,
   not the engine.

3. **Confidence score is a fixed-step ladder, not a
   probability.** The 0.25 step granularity is documented in
   :data:`ltagent.live.verification.CONFIDENCE_STEP`; downstream
   consumers that want a probability should not use the raw
   ``confidence`` field. A future phase can add a
   Bayesian-style confidence alongside it.

4. **No integration with the live project module yet.** The
   orchestrator is intentionally runner-agnostic; it does not
   call :mod:`ltagent.live.live_project` or
   :mod:`ltagent.live.snapshot`. Wiring those is Agent 6's job
   (MCP tools) and Agent 3's job (project glue).

5. **The ``(str, Enum)`` pattern for serialisable enums is
   project policy** (see `pyproject.toml` per-file ignores for
   `ir.py` / `digital_ir.py` / `templates.py` / `evaluator.py`).
   This module follows the same convention; the ``UP042`` ruff
   rule is silenced per-line with ``# noqa: UP042``. If
   ``pyproject.toml`` is updated to whitelist my files instead,
   the noqa comments can be removed.

---

## 6. Integration Requests

These are the cross-agent touch points Agent 5 needs from other
agents (or expects the integrator to wire up):

* **`src/ltagent/math_core/__init__.py` integration.** Agent 5
  created a *minimal* ``math_core/__init__.py`` because the file
  did not exist on the branch when Agent 5's work started. The
  Agent-4 integrator may want to replace it with a richer
  re-export (e.g. `from . import formulas, standard_values, units,
  verification_math`) once Agent 4 lands their modules. Agent
  5's modules do *not* require this re-export — they are
  importable as `ltagent.math_core.verification_math` directly.

* **`run_and_verify` ↔ `ltagent.runner.run_simulation`.** Agent
  5's :class:`RunPayload` is intentionally a narrow carrier of
  the fields the verification pipeline reads. The integrator
  should write a thin adapter that wraps
  :func:`ltagent.runner.run_simulation` and returns a
  :class:`RunPayload`. The mapping is roughly:

  | `RunResult` field | `RunPayload` field |
  | --- | --- |
  | `success` | `success` |
  | `data["logPath"]` | `log_path` |
  | `data["durationMs"]` | `duration_ms` |
  | `data["exitCode"]` | `exit_code` |
  | `errors[0]` | `error` (when present) |

  The adapter should *not* try to inline the log; the
  orchestrator handles `log_path → log_text` materialisation
  itself for tests but for real runs the file path is sufficient.

* **`MeasurementRequest` ↔ Circuit IR `measurements` block.**
  Agent 2's Circuit IR already has a `measurements` list (see
  `tests/test_digital_ir.py::test_digital_ir_*` fixtures).
  Agent 6's MCP tool (`live_run_and_verify`) is the natural place
  to convert: for each IR measurement, build a
  :class:`MeasurementRequest` and append to the netlist via
  :func:`generate_meas_directives`. Agent 5 does not need to
  depend on the IR module directly.

* **Snapshot rollback ↔ measurement cleanup.** If Agent 3's
  snapshot module restores a previous `.cir` and re-runs the
  simulation, the resulting log may include measurements from
  the *previous* run that no longer apply. Agent 5 treats the
  log as authoritative and binds by name; stale measurements
  that no longer match a check are silently ignored (which is
  the right behaviour — a check that does not bind becomes
  ``ACTUAL_MISSING``). No cross-agent change needed.

* **`ltagent.live.edit_ops` ↔ measurement editing.** When the
  user adds a new measurement through Agent 2's `add_measurement`
  operation, the resulting `MeasurementRequest` should be
  forwarded through the same code path that produces
  `generate_meas_directives`. Agent 2 owns `add_measurement` and
  can call into `ltagent.live.measurements` directly.

---

## 7. Open questions for the integrator

* Should :class:`RunPayload.to_dict` use camelCase like
  :class:`ltagent.result.RunInfo`? Currently it uses snake_case
  (raw ``asdict()``). The test suite asserts snake_case; the
  verification block inside the same dict is camelCase. Either
  way works; the choice should match the project-wide policy
  (which today is "snake_case for dataclasses, camelCase for the
  ``result.json`` contract"). I left it snake_case for now to
  match the dataclass field names verbatim.

* Should the orchestrator re-evaluate checks when binding, or
  carry the prior verdict through? I chose re-evaluate
  (the pre-built check is a *spec*; the orchestrator fills in
  the actual). This produces internally consistent reports at
  the cost of one extra evaluation per check. The trade-off
  favours consistency — a check that says
  ``actual=0.7071, passed=False, detail="actual=0"`` would be
  impossible to read.

* Should :func:`compare_formula_vs_simulation` accept tolerance
  as a ``float`` only, or also as a percentage string like
  ``"2%"``? I chose float only; if Agent 4 wants to pass
  percentage strings through, the parser lives in
  :mod:`ltagent.math_core.units` and Agent 5's helper can wrap
  it.
