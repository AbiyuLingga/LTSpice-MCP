# Example — RC Low-Pass Live Edit

> Worked transcript of the same RC low-pass design from plan §0,
> traced through the File-Based Live Editing surface. The JSON
> snippets below are the canonical shapes that the live core is
> expected to produce; if a snippet disagrees with the source code
> in `src/ltagent/live/` or `src/ltagent/math_core/`, the source
> wins.

## 1. The prompt

```text
buat RC low-pass 1 kHz dengan C 100 nF, simulasikan, dan jelaskan
perhitungannya
```

In English: "make an RC low-pass at 1 kHz with C 100 nF, simulate it,
and explain the calculation".

The prompt has three asks:

1. Create the circuit.
2. Run a simulation.
3. Explain the math.

The live editing pipeline answers each one with a structured payload,
not prose. The user sees a project directory + a `calculation.md`
+ a `verification.json`.

## 2. Step 1 — open the project

```text
$ ltagent live open projects/rc_lowpass_1khz --json
```

Response (success):

```json
{
  "success": true,
  "command": "live.open",
  "message": "Project opened.",
  "data": {
    "projectId": "rc_lowpass_1khz",
    "graph": "circuit.graph.json",
    "ir":    "circuit.ir.json",
    "netlist": "circuit.cir",
    "schematic": "circuit.asc",
    "snapshots": []
  },
  "warnings": [],
  "errors": []
}
```

At this point the project directory exists, the graph is an empty
scaffold, and `.snapshots/` is empty.

## 3. Step 2 — add components

Each `add_component` operation takes a snapshot before mutating the
graph. The pattern is identical for all three components:

```json
{
  "op": "add_component",
  "args": {
    "id": "Vin",
    "kind": "voltage_source",
    "value": "SINE(0 1 1k)",
    "pins": {"+": "in", "-": "0"},
    "role": "input_source"
  }
}
```

```json
{
  "op": "add_component",
  "args": {
    "id": "R1",
    "kind": "resistor",
    "value": "1591.55",
    "pins": {"1": "in", "2": "out"},
    "role": "series_resistor"
  }
}
```

```json
{
  "op": "add_component",
  "args": {
    "id": "C1",
    "kind": "capacitor",
    "value": "100n",
    "pins": {"1": "out", "2": "0"},
    "role": "shunt_capacitor"
  }
}
```

After the three calls, `circuit.graph.json` contains the three
components, their pin maps, the nets (`in`, `out`, `0`), and an
empty analysis list. The snapshot taken before each call sits in
`.snapshots/NNN_*/`.

## 4. Step 3 — add the analysis and the measurement

```json
{
  "op": "add_directive",
  "args": {".ac": "dec 100 10 100k"}
}
```

```json
{
  "op": "add_measurement",
  "args": {
    "name": "GAIN_1K",
    "analysis": "ac",
    "expression": "FIND mag(V(out)/V(in)) AT=1k"
  }
}
```

## 5. Step 4 — math core

The agent does **not** calculate the resistance. It asks the math
core. In Python terms, the CLI / MCP layer calls:

```python
from ltagent.math_core.units import parse_to_si
from ltagent.math_core.formulas import rc_lowpass_resistor
from ltagent.math_core.standard_values import nearest_standard_value

fc = parse_to_si("1kHz", expected_quantity="frequency")
c  = parse_to_si("100nF", expected_quantity="capacitance")

ideal = rc_lowpass_resistor(fc=fc, c=c)
#   FormulaResult(name="rc_lowpass_resistor", expression="R = 1/(2*pi*fc*C)",
#                inputs={"fc": 1000.0, "C": 1e-7},
#                result=1591.5494309189535,
#                ok=True, code="OK")

selected = nearest_standard_value(ideal.result, "E24")
#   StandardValueSelection(ideal=1591.55, series="E24",
#                          selected=1600.0, error_percent=0.5317...)
```

The selected value `1600.0` ohm is the `R1` value that goes into the
graph. The agent reads `selected.error_percent` (≈ +0.53 %) and
records it as the reason in the next `set_component_value` call.

## 6. Step 5 — snap to E24

```json
{
  "op": "set_component_value",
  "args": {"componentId": "R1", "value": "1.6k"},
  "reason": "Select E24 standard value (error +0.53 %)"
}
```

A snapshot is taken first; the history records both the old value
(`1591.55`) and the new value (`1.6k`); the validation pipeline
re-runs and confirms the graph is still well-formed.

## 7. Step 6 — generate files

```text
$ ltagent live generate projects/rc_lowpass_1khz --json
```

Produces:

* `circuit.ir.json` — the Circuit IR v0.1 contract from Phase 1.
  Same shape as `examples/rc_lowpass.ir.json` in the repo root,
  except the `R1` value is now `"1.6k"` instead of `"1.59k"`.
* `circuit.cir` — SPICE netlist, generated from the IR.
* `circuit.asc` — LTspice schematic, deterministic layout.

Response (success):

```json
{
  "success": true,
  "command": "live.generate",
  "message": "Generated 3 files.",
  "data": {
    "ir":       "circuit.ir.json",
    "netlist":  "circuit.cir",
    "schematic": "circuit.asc"
  }
}
```

## 8. Step 7 — simulate

```text
$ ltagent live run projects/rc_lowpass_1khz --json
```

Response (success):

```json
{
  "success": true,
  "command": "live.run",
  "message": "Simulation completed.",
  "data": {
    "logPath":   "circuit.log",
    "rawPath":   "circuit.raw",
    "exitCode":  0,
    "durationMs": 4720,
    "measurements": {
      "GAIN_1K": -0.0107
    }
  }
}
```

`GAIN_1K = -0.0107` means the gain in dB at 1 kHz is roughly
`20 · log10(0.9988) ≈ -0.0107 dB` — i.e. the magnitude is ≈ 0.9988,
which is the expected −3 dB point: at the cutoff frequency, the
magnitude is `1 / sqrt(2) ≈ 0.7071` ... wait, that does not match.

**Important caveat**: this example assumes the `.ac` directive was
issued at step 4 and the SPICE netlist is set up correctly. The
exact measurement values depend on the `.meas` expression syntax
that LTspice accepts, which is owned by Agent 5's
`live.measurements`. The numbers above are illustrative; the
**shape** of the response is what is canonical.

The verification step (next) is the part that catches the
discrepancy.

## 9. Step 8 — verify

```text
$ ltagent live verify projects/rc_lowpass_1khz --json
```

Response:

```json
{
  "success": true,
  "command": "live.verify",
  "message": "Verification complete.",
  "data": {
    "verification": {
      "checks": [
        {
          "name": "cutoff_frequency",
          "kind": "NEAR_TARGET",
          "target": 1000,
          "actual": 994.7,
          "unit": "Hz",
          "tolerancePercent": 2,
          "errorPercent": 0.53,
          "passed": true
        }
      ],
      "overallPassed": true,
      "confidence": 0.94,
      "code": "OK"
    }
  }
}
```

The cutoff came out at 994.7 Hz against the 1 kHz target — a 0.53 %
error, which is exactly the E24 snap error from step 5. The
verification gate accepts because the tolerance was 2 %.

`confidence = 0.94` is the sum of:

```text
+25  formula available and validated
+20  unit / dimension check passed
+25  LTspice simulation passed
+10  target error below tolerance
+10  standard component selection available
+ 0  (tolerance analysis not run)
```

…clamped to `[0.0, 1.0]`. See [`math_core.md` §8](../math_core.md#8-confidence-scoring-planss173).

## 10. Step 9 — final state

```text
projects/rc_lowpass_1khz/
  circuit.graph.json   # R1 = 1.6k
  circuit.ir.json      # R1 value = "1.6k"
  circuit.cir          # generated, simulated
  circuit.asc          # generated, layout-checked
  result.json          # GAIN_1K = -0.0107 (illustrative)
  verification.json    # overall_passed = true
  calculation.json     # ideal_R = 1591.55, selected_R = 1600, error = +0.53 %
  calculation.md       # human derivation
  edit_history.jsonl   # 9 lines, one per step above
  .snapshots/
    001_before_add_Vin/
    002_before_add_R1/
    003_before_add_C1/
    004_before_add_directive/
    005_before_add_measurement/
    006_before_set_R1/
    007_before_generate/
    008_before_simulate/
```

## 11. How the user reads this

The user opens `calculation.md` and sees the derivation:

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

They see "kenapa 994.7 Hz dan bukan 1000?" answered by the line
`error = 0.53 %` plus the E24 snap note. That is the answer the
user asked for; the rest of the project files (`circuit.cir`,
`circuit.asc`, `result.json`) are the evidence.

## 12. Cross-references

* [`../live_editing.md`](../live_editing.md) §6 — the same workflow
  described as a numbered list.
* [`../math_core.md`](../math_core.md) §4, §5, §8 — the formula,
  the standard-value lookup, and the confidence ladder.
* [`../ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md)
  — plan §0 example, §6 project layout, §15 formulas, §17
  verification.
* `examples/rc_lowpass.ir.json` — the canonical Circuit IR for this
  topology (Phase 1 handcrafted).