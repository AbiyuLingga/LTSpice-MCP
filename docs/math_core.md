# Math Core

> **Scope:** the calculation engine that powers the File-Based Live
> Editing workflow. Source of record:
> [`ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md)
> sections 13-19. The math core is implemented in
> `src/ltagent/math_core/` (Agent 4's surface) and is called by the
> live editing layer, the CLI, and the MCP tools.

## 1. Why the LLM is not the calculator

A language model is good at *naming* a topology, *describing* a
target, and *interpreting* a result. It is not reliable at the parts
in between:

* **Unit disambiguation.** "10k" alone is ambiguous (resistance?
  voltage? frequency?). The LLM has to guess from context, and the
  guess can drift mid-conversation.
* **Floating-point hygiene.** The kind of small arithmetic that
  happens in RC filters (1 / (2π × 1591.55 × 1e-7)) is exactly the
  regime where hand-arithmetic silently loses a digit.
* **Series tables.** E24, E96, common-capacitor decades. A model can
  hallucinate a value that does not exist in any series.
* **Optimisation loops.** Searching a value space for a target is a
  search, not a generation task.
* **Auditability.** When the user asks "why 994.7 Hz and not 1000
  Hz?", the answer must come from a saved, replayable calculation —
  not from re-derivation in prose.

So the plan (§13.2) sets out the division of labour:

```text
AI               = planner + explainer
Math Core        = calculator
SPICE / LTspice  = numerical verifier
Optimizer        = search / refinement engine
Verification     = pass / fail authority
```

The agent names the topology, supplies the targets, and reads the
result. The math core does the rest.

## 2. Module map (plan §14)

```text
src/ltagent/math_core/
  __init__.py                public surface (Agent 0)
  units.py                   SI prefix parser + formatter (Agent 4)
  formulas.py                closed-form formulas + registry (Agent 4)
  formula_registry.py        optional JSON-driven formula loader (Agent 4)
  standard_values.py         E-series lookup (Agent 4)
  specs.py                   typed input specs per formula (Agent 4)
  symbolic.py                optional SymPy helpers (Agent 4)
  mna.py                     optional MNA solver (Agent 4)
  optimizer.py               deterministic value-space search (Agent 4)
  tolerance.py               E-series tolerance / worst-case analysis (Agent 4)
  calculation_report.py      JSON + Markdown report builder (Agent 4)
```

The math core has one **hard rule** (AGENT_LOCKS §5): every public
function must remain pure-Python at the default extras level.
`numpy`, `sympy`, and `scipy` are only allowed behind optional
extras. When an extra is not installed, the math core either
degrades to a pure-Python implementation or raises a structured
`MathCoreOptionalDependencyMissing` error — never a bare
`ImportError`.

The math core never writes files, never spawns subprocesses, never
calls into the LLM. Its inputs are plain Python floats and small
dataclasses; its outputs are structured dataclasses with `to_dict()`
serialisers. The caller decides what to do with them.

## 3. The unit parser

### 3.1 Why a dedicated parser

The pre-Phase-12 `ltagent.units.parse_spice_value` returns a float or
`None`. That is enough to render a netlist, but it is not enough to
build a calculation report: the caller cannot tell from the return
value whether "10k" was meant as resistance, voltage, or frequency,
and cannot format the result back into a SPICE-style literal.

`ltagent.math_core.units` (plan §14.2) replaces it with a parser
that returns a structured `ParsedValue`:

```python
from ltagent.math_core.units import parse_value

parsed = parse_value("100nF")
#   ParsedValue(raw="100nF", si_value=1e-7, si_unit="F",
#               quantity="capacitance", prefix=1e-9, mantissa=100.0)
```

Fields:

* `raw` — the original string, unchanged.
* `si_value` — the numeric value in SI base units (ohms, farads,
  volts, amps, hertz, seconds).
* `si_unit` — the SI base unit symbol (e.g. `"F"`).
* `quantity` — the inferred physical quantity
  (`"resistance"`, `"capacitance"`, `"voltage"`, `"current"`,
  `"frequency"`, `"time"`, `"inductance"`, `"dimensionless"`).
* `prefix` — the SI multiplier that was applied to the mantissa.
* `mantissa` — the numeric portion of the input.

### 3.2 The SI prefix table

From `ltagent.math_core.units.SI_PREFIXES`:

```text
f    femto  1e-15
p    pico   1e-12
n    nano   1e-9
u    micro  1e-6     (Unicode µ is normalised to u)
m    milli  1e-3
(ε)  unity  1
k    kilo   1e3
K    kilo   1e3
M    mega   1e6
meg  mega   1e6
G    giga   1e9
T    tera   1e12
```

The M / m disambiguation is **case-sensitive** because SPICE uses
`M` for mega and `m` for milli. SPICE's `Meg` spelling is the only
non-ambiguous way to request 10⁶ in mixed-case text and is mapped
to the same prefix internally.

### 3.3 Quantity hint from the unit letter

A trailing unit letter labels the quantity and lets the parser flag
contradictions:

```text
10k        -> dimensionless (caller decides the domain)
10kohm     -> resistance,    10000 ohm
10kR       -> resistance,    10000 ohm
100nF      -> capacitance,   1e-7 F
3.3V       -> voltage,       3.3 V
1mA        -> current,       0.001 A
1kHz       -> frequency,     1000 Hz
10ms       -> time,          0.01 s
```

Bare prefixes (no unit letter) are rejected when the caller specifies
an `expected_quantity`. This is the "quantity mismatch" guard that
prevents "10kHz parsed as resistance" type bugs from sneaking into
the rest of the pipeline.

### 3.4 Error envelope

The parser never raises for input-driven failures. It returns a
`UnitError`:

```python
@dataclass(frozen=True)
class UnitError:
    code: str        # UNIT_EMPTY, UNIT_NOT_NUMERIC, UNIT_UNKNOWN,
                     # UNIT_NOT_STRING, UNIT_QUANTITY_MISMATCH
    message: str
    raw: str = ""

    def to_dict(self) -> dict[str, str]: ...
```

The caller is expected to switch on `code` (a stable string) and
embed `to_dict()` in the standard envelope.

### 3.5 The `format_value` companion

`format_value(1.6e3, "ohm") -> "1.6kohm"` and similar. The function
chooses the largest SI prefix that keeps the mantissa in `[1, 1000)`,
so the round-trip `parse_value(format_value(x))` recovers the same
SI value within float epsilon.

## 4. The formula engine

### 4.1 The contract

Every formula function in `ltagent.math_core.formulas` follows the
same shape:

```python
def rc_lowpass_resistor(fc: float, c: float) -> FormulaResult:
    """Return the ideal series resistance for an RC low-pass filter."""
```

* **Inputs are plain Python floats** in SI base units. The caller
  is expected to have already normalised units through `units.parse_to_si`.
* **Returns a `FormulaResult` dataclass** that carries the ideal
  value, the formula expression (verbatim, for the report), the
  input mapping, and a `code` field for structured errors.
* **Never raises** for input-driven failures. Negative resistance or
  zero capacitance produce a `FormulaResult` with `ok=False` and a
  stable error code such as `FORMULA_INPUT_NON_POSITIVE`.

### 4.2 The MVP catalog

From plan §14.3 and §15.2:

```text
voltage_divider
rc_lowpass
rc_highpass
rl_lowpass
rl_highpass
rlc_resonance          (planned)
inverting_opamp
noninv_opamp
led_resistor
bjt_switch_basic      (planned)
mosfet_switch_basic   (planned)
buck_ideal
boost_ideal
```

The canonical formula text (verbatim — agents do not re-derive
these):

| Topology | Ideal-quantity formula | Solve-for |
|---|---|---|
| Voltage divider | `Vout = Vin · R2 / (R1 + R2)` | `R1`, `R2` |
| RC low-pass | `fc = 1 / (2π R C)` | `R`, `C`, `fc` |
| RC high-pass | `fc = 1 / (2π R C)` | `R`, `C`, `fc` |
| Non-inverting op-amp | `Av = 1 + Rf / Rg` | `Rf`, `Rg` |
| Inverting op-amp | `Av = −Rf / Rin` | `Rf`, `Rin` |
| LED resistor | `R = (Vsupply − Vf) / Iled` | `R` |
| Ideal buck | `D = Vout / Vin`; `Rload = Vout / Iout` | `D`, `Rload` |
| Ideal boost | `D = 1 − Vin / Vout`; `Rload = Vout / Iout` | `D`, `Rload` |

The formula registry (`FORMULA_REGISTRY` in `formulas.py`) maps each
topology name to a `TopologyFormula` description so the report
builder can render the math without a hard-coded `if/elif` tree.

### 4.3 Worked example

A user asks for "RC low-pass 1 kHz dengan C 100 nF":

```python
from ltagent.math_core.units import parse_to_si
from ltagent.math_core.formulas import rc_lowpass_resistor

fc = parse_to_si("1kHz", expected_quantity="frequency")
c  = parse_to_si("100nF", expected_quantity="capacitance")
# fc == 1000.0, c == 1e-7

result = rc_lowpass_resistor(fc=fc, c=c)
# FormulaResult(
#   name="rc_lowpass_resistor",
#   expression="R = 1 / (2*pi*fc*C)",
#   inputs={"fc": 1000.0, "C": 1e-7},
#   result=1591.5494309189535,
#   ok=True,
#   code="OK",
# )
```

The `expression` field is what `calculation_report` writes verbatim
into the `calculation.md` derivation; the agent never has to
re-format the formula.

### 4.4 Stable error codes (formula layer)

```text
OK
FORMULA_INPUT_INVALID         bad type or shape
FORMULA_INPUT_NON_FINITE      NaN, +inf, -inf
FORMULA_INPUT_NON_POSITIVE    physically meaningless (e.g. -1 ohm)
FORMULA_DIVISION_BY_ZERO      denominator collapsed
FORMULA_INVERTING_GAIN_SIGN   asked for |Av| but Av was negative
```

The caller switches on `code`, never on `message`.

## 5. Standard value selection

### 5.1 Series tables

From `ltagent.math_core.standard_values`:

```text
E6    6 values per decade (≈20 % tolerance)
E12  12 values per decade (≈10 % tolerance)
E24  24 values per decade (≈5 %  tolerance)
E48  48 values per decade (≈2 %  tolerance)
E96  96 values per decade (≈1 %  tolerance)
CAP  common capacitor mantissas {1.0, 1.5, 2.2, 3.3, 4.7, 6.8, 10.0}
```

`series_values(series)` expands the mantissa list across all decades
from `1e-15` to `1e15`, deduplicates, and sorts ascending. The
result is the "ladder" the selector searches.

### 5.2 Selection algorithm

`nearest_standard_value(value, series)` returns a
`StandardValueSelection` with the ideal value, the series name, the
selected standard value, and the signed percent error:

```python
@dataclass(frozen=True)
class StandardValueSelection:
    ideal: float
    series: str
    selected: float
    error_percent: float  # (selected - ideal) / ideal * 100
```

The selector minimises `|error_percent|` and breaks ties in favour
of the **larger** standard value (over-spec'ing a filter cutoff is
safer than under-spec'ing it).

### 5.3 Worked example

```python
from ltagent.math_core.standard_values import nearest_standard_value

nearest_standard_value(1591.55, "E24")
# StandardValueSelection(
#   ideal=1591.55, series="E24", selected=1600.0,
#   error_percent=0.5317...,
# )
```

The error is +0.53 %, which is the answer the verification engine
will see when the simulation's measured cutoff is 994.7 Hz against a
1 kHz target.

## 6. The calculation report

### 6.1 `calculation.json`

The structured payload written next to every project:

```json
{
  "schemaVersion": "0.1",
  "success": true,
  "topology": "rc_lowpass",
  "formulas": [
    {"name": "rc_lowpass_resistor", "expression": "R = 1 / (2*pi*fc*C)"}
  ],
  "idealValues": {
    "R": {"value": 1591.55, "unit": "ohm"},
    "C": {"value": 1e-7,    "unit": "F"}
  },
  "selectedValues": {
    "R": {"value": 1600, "unit": "ohm", "display": "1.6k"},
    "C": {"value": 1e-7, "unit": "F",    "display": "100n"}
  },
  "predicted": {"fc": {"value": 994.718, "unit": "Hz"}, "errorPercent": 0.528},
  "assumptions": ["ideal capacitor", "no parasitic ESR/ESL"],
  "warnings": []
}
```

Schema version lives in `ltagent.math_core.calculation_report.CALCULATION_SCHEMA_VERSION`.
Downstream tooling (the verification engine, the MCP `explain`
tool) greps on the field names; do not rename them.

### 6.2 `calculation.md`

The human-readable companion:

```md
# Calculation Report

## User Target
- Circuit: RC low-pass filter
- Target cutoff: 1 kHz
- Fixed capacitor: 100 nF
- Component series: E24

## Formula
fc = 1 / (2π R C)

## Solve for R
R = 1 / (2π fc C)

## Substitution
R = 1 / (2π × 1000 × 100nF)
R = 1591.55 Ω

## Standard Value Selection
Selected R = 1.6 kΩ
Selected C = 100 nF

## Predicted Result
fc = 994.7 Hz
error = 0.53 %

## LTspice Verification
Measured gain at 1 kHz = ...
Passed: true

## Assumptions
- Ideal capacitor unless tolerance analysis is enabled.
- No ESR / ESL modelled in MVP.
- Source impedance is assumed ideal.
```

The headings are stable so downstream tooling can grep them.

## 7. Verification gate

The verification engine is the **pass / fail authority** for the
workstream (plan §17). It is implemented in
`ltagent.live.verification` (Agent 5's surface) and is fed by the
math core's predictions plus the simulator's measurements.

### 7.1 Check shape

```python
@dataclass(frozen=True)
class VerificationCheck:
    name: str
    kind: CheckKind          # NEAR_TARGET, MAX, MIN
    target: float | None
    actual: float | None
    tolerance_percent: float | None
    passed: bool
    code: str                # OK, TARGET_MISSING, TOLERANCE_INVALID,
                             # BOUND_INVALID, ACTUAL_MISSING,
                             # ACTUAL_INVALID
    detail: str
```

### 7.2 `overall_passed`

A `VerificationResult` rolls the checks up:

```python
@dataclass
class VerificationResult:
    checks: list[VerificationCheck]
    overall_passed: bool      # AND of every check's `passed` field
    confidence: float         # fixed-step ladder [0.0, 0.25, 0.5, 0.75, 1.0]
    reason_codes: list[str]
```

`overall_passed = True` only if **every** check has `passed = True`.
A `None` or non-finite `actual` is never silently passed; it carries
`code = ACTUAL_MISSING` or `ACTUAL_INVALID` and `passed = False`.
The low-level aggregator is mathematically vacuous for an empty list,
but the project-level runner rejects empty checks with
`VERIFY_TARGETS_MISSING`. A successful process exit alone is never a
verification pass.

### 7.3 `verification.json`

The on-disk artefact:

```json
{
  "checks": [
    {
      "name": "cutoff_frequency",
      "target": 1000,
      "actual": 994.7,
      "unit": "Hz",
      "tolerancePercent": 2,
      "errorPercent": 0.53,
      "passed": true
    }
  ],
  "overallPassed": true,
  "confidence": 0.94
}
```

### 7.4 Verification levels (plan §17.2)

```text
Level 0  graph validation only
Level 1  formula calculation only
Level 2  formula + generated netlist
Level 3  LTspice simulation pass
Level 4  simulation + target measurement pass
Level 5  simulation + tolerance / worst-case pass
```

The MVP gate is **Level 4**: a project is "verified" iff Level 4 is
green. Level 5 is opt-in and only required when the user explicitly
asks for tolerance analysis.

## 8. Confidence scoring (plan §17.3)

A fixed-point ladder. Positive contributions stack; negative
subtract.

```text
+25  formula available and validated
+20  unit / dimension check passed
+25  LTspice simulation passed
+10  target error below tolerance
+10  standard component selection available
+10  tolerance analysis passed

-30  simulation failed
-20  formula unavailable
-20  unsupported topology
-15  ideal model only
-10  tolerance not tested
```

The score is clamped to `[0.0, 1.0]`. It is **not** a probability;
it is a "how much evidence do we have" signal.

## 9. Tolerance and worst-case analysis (plan §14.8, planned)

The tolerance module ships behind the Phase 13+
`ltspice-ai-agent[math]` extra (it uses `scipy` for the Monte-Carlo
mode). The MVP surface is `E-series` derived tolerance:

```text
E6   -> ±20 % 1-sigma equivalent
E12  -> ±10 %
E24  -> ±5 %
E96  -> ±1 %
```

Output shape:

```json
{
  "nominal":   {"fc": 994.7},
  "worstCase": {"min": 890.0, "max": 1110.0},
  "monteCarlo": {"samples": 200, "mean": 998.0, "std": 42.0}
}
```

The Monte-Carlo mode is opt-in. The worst-case mode is the default
when the user asks for "berapa batas toleransi cutoff-nya?". The
report writer never runs Monte-Carlo unless `monteCarloSamples > 0`
is set explicitly.

## 10. Optimizer (plan §14.7)

### 10.1 When to invoke the optimizer

Use the optimizer when:

* the formula is unavailable,
* the formula exists but the simulation fails the target,
* the user asks for a multi-variable trade-off,
* the topology has non-ideal components in the model,
* the user asks for high accuracy,
* tolerance analysis fails.

Do **not** invoke the optimizer when:

* the design is unsafe / high-voltage and the model is insufficient,
* the target is under-specified,
* no valid simulation model exists for the topology,
* the topology is outside the supported domain.

### 10.2 Optimizer progression

```text
formula -> standard value search -> local sweep
        -> LTspice .step sweep
        -> SciPy differential evolution
        -> Bayesian optimisation  (later)
```

The MVP priority is the first row. The next two are wired in once
the math core + verification engine integration is green.

### 10.3 Objective function

```text
score = w1 * target_error
      + w2 * ripple_penalty
      + w3 * clipping_penalty
      + w4 * power_penalty
      + w5 * instability_penalty
      + w6 * component_cost_penalty
```

The weights are chosen per topology and live in
`ltagent.math_core.optimizer` constants. They are exposed via the
CLI's `--json` output so a user can audit them.

## 11. Why this layer is safe for the MCP server

* **Pure functions.** Every entry point takes plain Python values
  and returns dataclasses. No filesystem, no subprocess, no clock,
  no LLM.
* **Deterministic.** Same inputs → same outputs, byte-for-byte.
  The verification gate can replay any past run.
* **Structured errors.** No `ValueError` or `TypeError` propagates
  to the caller. Every failure path returns a dataclass with a
  stable `code` field.
* **JSON-friendly.** Every dataclass has a `to_dict()` serialiser
  that matches the schemas used by `calculation.json` and
  `verification.json`.
* **No LLM call.** The math core is the opposite of the LLM: it
  is the part that does not have a temperature knob.

## 12. Cross-references

* [`live_editing.md`](live_editing.md) — how the math core is
  invoked from the live editing surface.
* [`agent_workflow.md`](agent_workflow.md) — Agent 4 (Math Core) and
  Agent 5 (Simulation + Verification) hand-offs.
* [`../ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md)
  — plan of record, sections 13-19.
* [`examples/noninv_opamp_calculation.md`](examples/noninv_opamp_calculation.md)
  — worked transcript of an op-amp gain calculation.
* [`AGENT_LOCKS.md`](AGENT_LOCKS.md) — Agent 4 owns everything
  under `src/ltagent/math_core/` except `__init__.py`.
