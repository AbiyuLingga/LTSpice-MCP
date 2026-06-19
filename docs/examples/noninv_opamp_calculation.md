# Example — Non-Inverting Op-Amp Calculation

> Worked calculation for a non-inverting op-amp gain stage, traced
> through the math core only (no live editing, no simulation). The
> shape of every Python call and every dataclass is the contract
> `ltagent.math_core` exposes; the numeric values are illustrative
> and assume an ideal op-amp.

## 1. The target

```text
"non-inverting amplifier, gain 10, supply ±12 V, input 100 mV peak
1 kHz"
```

Translated into the math core's vocabulary:

* `topology` = `"noninv_opamp"`
* `target_gain` = `10`
* `supply_voltage` = `±12 V`
* `input_amplitude` = `100 mV`
* `input_frequency` = `1 kHz`

The agent is allowed to *name* those four parameters; everything
else comes from the math core.

## 2. The math

The textbook formula (verbatim from the formula registry):

```text
Av = 1 + Rf / Rg
```

Solved for `Rf`:

```text
Rf = (Av - 1) · Rg
```

The math core's `noninverting_opamp_feedback(Av, Rg)` returns a
`FormulaResult` whose `expression` field carries this string
verbatim. The agent never re-derives the formula in prose; the
report writes it as-is.

## 3. Worked Python transcript

```python
from ltagent.math_core.units import parse_to_si, format_value
from ltagent.math_core.formulas import (
    noninverting_opamp_gain,
    noninverting_opamp_feedback,
)
from ltagent.math_core.standard_values import nearest_standard_value

# 1. Parse the inputs. Each is a string from the prompt; the parser
#    labels the quantity so a "12V" parsed as resistance is rejected.
av = parse_to_si("10", expected_quantity="dimensionless")
rg = parse_to_si("10k", expected_quantity="resistance")  # Rg = 10 kΩ
vin = parse_to_si("100mV", expected_quantity="voltage")
vsup = parse_to_si("12V", expected_quantity="voltage")
fin = parse_to_si("1kHz", expected_quantity="frequency")

# 2. Solve for Rf.
rf_ideal = noninverting_opamp_feedback(Av=av, Rg=rg)
#   FormulaResult(name="noninverting_opamp_feedback",
#                expression="Rf = (Av - 1) * Rg",
#                inputs={"Av": 10.0, "Rg": 10000.0},
#                result=90000.0,
#                ok=True, code="OK")

# 3. Snap to E24.
rf_selection = nearest_standard_value(rf_ideal.result, "E24")
#   StandardValueSelection(ideal=90000.0, series="E24",
#                          selected=91000.0, error_percent=+1.111...)

# 4. Predict the actual gain with the E24 value.
gain_actual = noninverting_opamp_gain(Rf=rf_selection.selected, Rg=rg)
#   FormulaResult(name="noninverting_opamp_gain",
#                expression="Av = 1 + Rf / Rg",
#                inputs={"Rf": 91000.0, "Rg": 10000.0},
#                result=10.1,
#                ok=True, code="OK")

# 5. Predict the output amplitude. Output = Av * input.
vout_peak = gain_actual.result * vin   # 10.1 * 0.1 = 1.01 V peak

# 6. Check the supply headroom. Output must stay below ±12 V.
headroom_ok = abs(vout_peak) < vsup    # True — 1.01 V << 12 V

print(format_value(rf_selection.selected, "ohm"))  # "91kohm"
print(format_value(gain_actual.result, "ohm/ohm"))  # "10.1"
print(format_value(vout_peak, "V"))    # "1.01V"
```

Numbers:

| Quantity | Ideal | E24 selected | Error |
|---|---|---|---|
| `Rf` | 90 kΩ | 91 kΩ | +1.11 % |
| `Av` | 10.0 | 10.1 | +1.0 % |
| `Vout peak` | 1.000 V | 1.010 V | +1.0 % |

The +1.0 % gain error is the cost of snapping `Rf` to the nearest
E24 value. The math core reports it in the calculation report; the
verification engine carries the same number into
`verification.json`.

## 4. The calculation report

### 4.1 `calculation.json`

```json
{
  "schemaVersion": "0.1",
  "success": true,
  "topology": "noninv_opamp",
  "formulas": [
    {
      "name": "noninverting_opamp_feedback",
      "expression": "Rf = (Av - 1) * Rg"
    },
    {
      "name": "noninverting_opamp_gain",
      "expression": "Av = 1 + Rf / Rg"
    }
  ],
  "idealValues": {
    "Av": {"value": 10.0,    "unit": ""},
    "Rg": {"value": 10000.0, "unit": "ohm"},
    "Rf": {"value": 90000.0, "unit": "ohm"}
  },
  "selectedValues": {
    "Rf": {"value": 91000.0, "unit": "ohm", "display": "91k"}
  },
  "predicted": {
    "gain_actual": {"value": 10.1, "unit": ""},
    "vout_peak":   {"value": 1.01, "unit": "V"},
    "errorPercent": 1.0
  },
  "assumptions": [
    "ideal op-amp (infinite input impedance, zero output impedance)",
    "no GBW limit at 1 kHz (verify with a real op-amp model)",
    "supply rails ±12 V (headroom: 1.01 V peak << 12 V)"
  ],
  "warnings": []
}
```

### 4.2 `calculation.md`

```md
# Calculation Report

## User Target
- Circuit: Non-inverting op-amp gain stage
- Target gain: 10
- Supply: ±12 V
- Input: 100 mV peak, 1 kHz

## Formula
Av = 1 + Rf / Rg

## Solve for Rf
Rf = (Av − 1) · Rg

## Substitution
Rf = (10 − 1) × 10kΩ
Rf = 9 × 10kΩ
Rf = 90 kΩ

## Standard Value Selection
Series: E24
Selected Rf = 91 kΩ
Error: +1.11 %

## Recomputed Actual Gain
Av_actual = 1 + 91k / 10k = 10.1

## Predicted Output
Vout_peak = Av_actual × Vin_peak = 10.1 × 100 mV = 1.01 V

## Headroom Check
|Vout_peak| < |Vsupply|      1.01 V < 12 V      OK

## LTspice Verification
[Run the simulation; expected Vout_peak ≈ 1.01 V at 1 kHz]
[Expected gain ≈ 10.1 from .meas expression FIND mag(V(out)/V(in)) AT=1k]

## Assumptions
- Ideal op-amp (infinite Zin, zero Zout).
- Real op-amp GBW ≫ 1 kHz (LT1012 / OPA2227 / similar).
- Single-pole compensation; closed-loop gain ≥ 10 ensures stability.
```

## 5. Verification

The verification gate runs three checks:

```json
{
  "checks": [
    {
      "name": "gain_at_1k",
      "kind": "NEAR_TARGET",
      "target": 10,
      "actual": 10.05,
      "tolerancePercent": 5,
      "errorPercent": 0.5,
      "passed": true
    },
    {
      "name": "output_amplitude",
      "kind": "NEAR_TARGET",
      "target": 1.01,
      "actual": 1.005,
      "tolerancePercent": 5,
      "errorPercent": 0.5,
      "passed": true
    },
    {
      "name": "no_clipping",
      "kind": "MAX",
      "max_value": 11.5,
      "actual": 1.005,
      "passed": true
    }
  ],
  "overallPassed": true,
  "confidence": 0.94
}
```

The actual measured gain (10.05) differs slightly from the math
prediction (10.1) because the LTspice netlist uses a real op-amp
subcircuit model (e.g. `UniversalOpamp`) instead of an ideal
gain-of-1 VCVS. The 0.5 % discrepancy is the cost of using a real
model; the verification gate still passes because the tolerance was
5 %.

## 6. Failure modes the math core rejects

The formula layer is total — bad inputs do not raise. They return a
`FormulaResult` with `ok=False` and a stable `code`:

```python
noninverting_opamp_feedback(Av=10, Rg=0)
#   FormulaResult(ok=False, code="FORMULA_DIVISION_BY_ZERO",
#                detail="Rg must be > 0")

noninverting_opamp_feedback(Av=-5, Rg=10_000)
#   FormulaResult(ok=False, code="FORMULA_INVERTING_GAIN_SIGN",
#                detail="Av=-5 is a negative gain; "
#                       "use noninverting_opamp_feedback only for Av>0")

noninverting_opamp_feedback(Av=float("nan"), Rg=10_000)
#   FormulaResult(ok=False, code="FORMULA_INPUT_NON_FINITE",
#                detail="Av is NaN")
```

The verification layer reports the same `code` field in its
`errors` list, so the agent can switch on it.

## 7. Wiring into the live edit surface

In the live edit pipeline, this calculation drives a `set_component_value`
operation rather than a calculation report stand-alone:

```json
{
  "op": "set_component_value",
  "args": {"componentId": "Rf", "value": "91k"},
  "reason": "Gain target 10 with Rg=10k; Rf ideal=90k, E24=91k, +1.11%"
}
```

The apply pipeline (live_editing.md §5.4) records:

1. Snapshot before the change (with the previous `Rf` value).
2. Apply the value.
3. Lower the graph to IR.
4. Regenerate `circuit.cir`, `circuit.asc`.
5. Append `{"op":"set_component_value", "old":"...", "new":"91k"}`
   to `edit_history.jsonl`.

The math core output (the `FormulaResult` and the E24 selection)
is what populates `calculation.json` and `calculation.md` next to
the project.

## 8. Cross-references

* [`../math_core.md`](../math_core.md) §4 (formula engine), §5
  (standard-value selection), §7 (verification gate).
* [`../live_editing.md`](../live_editing.md) §5 (edit operations).
* `examples/noninv_opamp.ir.json` — the canonical Circuit IR seed
  for this topology (Phase 11 handcrafted).
* [`../ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md)
  — plan §15.2 (formula examples), §16 (calculation report
  format), §17 (verification).