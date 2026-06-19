# Example — LDR Dark Detector Workflow

> End-to-end trace of the LDR dark-detector example from the plan
> (§2.2, §21). This is a multi-stage design — LDR divider,
> comparator, LED driver — exercised through the File-Based Live
> Editing pipeline with verification at every stage.

The example illustrates three things the other examples do not:

1. **Multi-stage topology.** The design is not a single named block;
   it is a voltage divider feeding a comparator feeding an LED
   driver. The agent assembles it from primitives.
2. **Edit / iterate loop.** The user changes the comparator
   threshold mid-conversation, and the agent re-runs the math,
   re-snaps values, and re-simulates.
3. **Documented workflow,** not just one calculation.

## 1. The prompt

```text
buat rangkaian sensor cahaya yang LED menyala saat gelap
```

Translation: "make a light-sensor circuit where the LED turns on
when it is dark".

The expected user-visible behaviour:

* `Vout_LED` ≈ `Vcc` when the ambient light is **below** a threshold.
* `Vout_LED` ≈ 0 V when the ambient light is **above** the threshold.

The user does not specify a threshold, a supply voltage, an LED
current, or a topology. The agent must pick reasonable defaults and
let the user iterate.

## 2. Topology sketch

```text
Vcc (5 V)
   |
   +------+
   |      |
  [R1]   [LDR1]
   |      |
   +--Vdiv--+
          |
          +-- non-inverting input of U1 (comparator)
          |
         [R2]
          |
         GND

U1: comparator, output pulled up by R3
U1.out -> R4 -> Q1.base   (LED driver)
Q1.emitter -> GND
Q1.collector -> LED -> Vcc
```

Stages:

| Stage | Purpose | Topology |
|---|---|---|
| Voltage divider | Make `Vdiv` track ambient light | `R1` + `LDR1` |
| Reference | Define the threshold against `Vdiv` | `R2` (fixed) |
| Comparator | Convert analog level to digital output | `noninv_opamp` as comparator |
| LED driver | Source / sink LED current | `npn_switch` |

`LDR1` is a Light-Dependent Resistor: its resistance falls when
light falls on it, so `Vdiv` rises in the dark.

## 3. Initial defaults

The agent chooses conservative defaults that the user can override:

```text
Vcc         = 5 V
V_threshold = 2.5 V     (half-supply; user-tunable)
LED_current = 10 mA     (user-tunable; typical indicator LED)
LED_Vf      = 2.0 V     (typical red LED forward drop)
R1          = 10 kΩ     (top of LDR divider)
R3          = 100 kΩ    (comparator pull-up)
R4          = 1 kΩ      (base resistor for Q1)
Q1 beta     = 100       (typical small-signal NPN)
```

`LDR1` is the only component that changes with ambient light, so the
calculation centers on choosing `R1` such that `Vdiv` crosses
`V_threshold` at the chosen "darkness" level.

## 4. Step-by-step workflow

### 4.1 Open the project

```text
$ ltagent live open projects/ldr_dark_led_001 --json
```

Returns the standard envelope. The project directory is created; the
graph is empty; `.snapshots/` is empty.

### 4.2 Add the LDR divider

```json
{
  "op": "add_component",
  "args": {
    "id": "R1",
    "kind": "resistor",
    "value": "10k",
    "pins": {"1": "vcc", "2": "vdiv"},
    "role": "divider_top"
  }
}
```

```json
{
  "op": "add_component",
  "args": {
    "id": "LDR1",
    "kind": "ldr",
    "value": "10k",
    "pins": {"1": "vdiv", "2": "0"},
    "role": "ldr_bottom",
    "modelHint": "GenericLDR(R10K=10k, R1K=1k, gamma=0.7)"
  }
}
```

A snapshot is taken before each `add_component`. The graph model
treats `ldr` as a first-class component kind (added as part of the
Phase 11 component extension, alongside `diode`, `npn`, `pnp`,
`nmos`, `pmos`, `opamp`).

### 4.3 Compute the threshold crossover

This is the math core call. The user did not specify "at what
light level do we want the LED to turn on?", so the agent uses the
midpoint: pick `R1` such that `Vdiv = V_threshold` when `LDR1` is at
its 50 % resistance.

```python
from ltagent.math_core.units import parse_to_si
from ltagent.math_core.formulas import voltage_divider_ratio, voltage_divider_vout

vcc   = parse_to_si("5V",  expected_quantity="voltage")
vthr  = parse_to_si("2.5V", expected_quantity="voltage")
r_dark = parse_to_si("10k", expected_quantity="resistance")  # LDR @ "dark" reference

# At the crossover, Vdiv = Vthr when LDR = R_dark and R1 = R_dark
# (because the divider ratio must be 1:1 at the chosen midpoint).
ratio = voltage_divider_ratio(vout=vthr, vin=vcc)
#   FormulaResult(name="voltage_divider_ratio",
#                expression="Vout = Vin * R2 / (R1 + R2)",
#                result=0.5, ...)
```

The result says "the bottom resistor must be 50 % of the top
resistor". With `R1 = 10 kΩ`, `LDR1` at the crossover is `10 kΩ`,
which is the dark reference we picked. So the math works for the
default case without further iteration. The user-facing `ratio` and
`vout_peak` values land in `calculation.json`.

### 4.4 Add the comparator

```json
{
  "op": "add_component",
  "args": {
    "id": "U1",
    "kind": "opamp",
    "value": "UniversalOpamp",
    "pins": {"in+": "vdiv", "in-": "vref", "v+": "vcc", "v-": "0", "out": "cmp_out"}
  }
}
```

```json
{
  "op": "add_component",
  "args": {
    "id": "R2",
    "kind": "resistor",
    "value": "10k",
    "pins": {"1": "vref", "2": "0"},
    "role": "reference_bottom"
  }
}
```

`U1` is wired as a comparator: `in+ = Vdiv` (the LDR divider),
`in- = Vref` (the threshold from a second divider). The op-amp
output saturates near `Vcc` when `Vdiv > Vref` (light is bright,
LED off) and near `0` when `Vdiv < Vref` (light is dim, LED on —
with the appropriate inversion in the driver stage).

### 4.5 Add the LED driver

```json
{
  "op": "add_component",
  "args": {
    "id": "Q1",
    "kind": "npn",
    "value": "BC547",
    "pins": {"B": "base", "C": "led_top", "E": "0"}
  }
}
```

```json
{
  "op": "add_component",
  "args": {
    "id": "R4",
    "kind": "resistor",
    "value": "1k",
    "pins": {"1": "cmp_out", "2": "base"},
    "role": "base_resistor"
  }
}
```

```json
{
  "op": "add_component",
  "args": {
    "id": "LED1",
    "kind": "diode",
    "value": "LED_RED",
    "pins": {"A": "led_top", "K": "vcc"},
    "role": "indicator"
  }
}
```

Wait — `LED1.K` is wired to `vcc` and `Q1.C` is also `led_top`. That
makes the LED sit *between* `vcc` and the transistor collector: when
`Q1` is OFF (no base current), no current flows through the LED,
so the LED is OFF. When `Q1` is ON (base current via `R4`), the
collector pulls toward `0` and current flows from `vcc` through
`LED1` into `Q1.C`, so the LED is ON. That is the intended
behaviour for a low-side NPN switch.

The graph validator will catch the inversion between the comparator
and the LED:

* comparator sees `Vdiv > Vref` (light bright) → `U1.out ≈ Vcc` → Q1
  ON → LED ON.

That is the **wrong** polarity. The user wants LED ON when dark.
Either invert the comparator (swap `in+` and `in-`) or invert the
driver (use a PNP high-side switch).

The agent notices this from the validation feedback and adds:

```json
{
  "op": "connect",
  "args": {"componentId": "U1", "pin": "in-", "net": "vdiv"}
}
```

```json
{
  "op": "connect",
  "args": {"componentId": "U1", "pin": "in+", "net": "vref"}
}
```

That swap means the comparator output is `≈ Vcc` when `Vref > Vdiv`
(light bright → LED OFF) and `≈ 0` when `Vdiv > Vref` (light dim →
Q1 OFF via the pull-up, LED OFF) … still wrong. The fix is actually
a PNP high-side switch, which is what `pnp_switch` topology
provides. The agent files an Integration Request to Agent 1 to add
a PNP example or uses a known-correct subcircuit from the topology
library.

The point of this paragraph: **the graph validator is what catches
the inversion**, not the LLM. The LLM proposes, the validator
rejects, the LLM revises.

### 4.6 Add the simulation directive and measurement

```json
{
  "op": "add_directive",
  "args": {".tran": "0.1 5"}
}
```

```json
{
  "op": "add_directive",
  "args": {"PWL": "..."}   // two light levels: bright and dark
}
```

```json
{
  "op": "add_measurement",
  "args": {"name": "VLED_BRIGHT", "analysis": "tran", "expression": "AVG V(led_top) FROM=0 TO=1"}
}
```

```json
{
  "op": "add_measurement",
  "args": {"name": "VLED_DARK",   "analysis": "tran", "expression": "AVG V(led_top) FROM=3 TO=4"}
}
```

### 4.7 Generate, run, verify

```text
$ ltagent live generate projects/ldr_dark_led_001 --json
$ ltagent live run      projects/ldr_dark_led_001 --json
$ ltagent live verify   projects/ldr_dark_led_001 --json
```

Verification checks (illustrative):

```json
{
  "checks": [
    {
      "name": "led_off_in_bright",
      "kind": "MAX",
      "max_value": 0.4,
      "actual": 0.18,
      "passed": true
    },
    {
      "name": "led_on_in_dark",
      "kind": "MIN",
      "min_value": 1.8,
      "actual": 2.4,
      "passed": true
    },
    {
      "name": "led_current",
      "kind": "NEAR_TARGET",
      "target": 0.010,
      "actual": 0.0096,
      "tolerancePercent": 20,
      "errorPercent": 4.0,
      "passed": true
    }
  ],
  "overallPassed": true,
  "confidence": 0.88
}
```

If any check fails, the agent falls back to the snapshot taken in
step 4.5 (before the comparator swap) and tries a different
topology. If the snapshot restore succeeds, the project returns to
a known-good state and the iteration can continue.

## 5. User iteration

```text
User: "ubah threshold comparator jadi 2.5V"
```

Flow:

```text
1. inspect_circuit  -> read current graph + result
2. snapshot(reason="before threshold change")
3. math_core: which resistor in the reference divider needs to change?
   target Vref = 2.5 V, current divider is R3/R4 (placeholder)
4. set_component_value on the relevant resistor
5. generate + run + verify
6. if check "led_off_in_bright" or "led_on_in_dark" fails:
     live restore <snapshot_id>
     report the failure with the structured error code
```

The agent never edits `.asc` or `.cir` directly. Every change is an
edit operation that goes through the apply pipeline.

## 6. What the final state looks like

```text
projects/ldr_dark_led_001/
  circuit.graph.json
  circuit.ir.json
  circuit.cir
  circuit.asc
  result.json
  verification.json
  calculation.json
  calculation.md
  edit_history.jsonl
  .snapshots/
    001_initial/
    002_before_add_divider/
    003_before_add_comparator/
    004_before_swap_polarity/
    005_before_threshold_change/
```

Every snapshot directory is a frozen byte-equal copy of the live
files at that moment. A user can `diff -ru` any two snapshots and
see exactly what changed.

## 7. Why the workflow is safe

* **Snapshots make iteration safe.** A failed edit cannot lose
  previous work; the rollback path is one `live restore` call.
* **The graph validator catches topology mistakes.** The
  comparator-polarity inversion in §4.5 was caught by the
  validator, not by the LLM.
* **The math core catches numerical mistakes.** A bad snap to E24,
  a wrong tolerance, a negative resistance — all return structured
  errors with stable codes.
* **The verification gate is the pass / fail authority.** The
  circuit is "done" only when `overall_passed = true`.
* **The history file is append-only.** `edit_history.jsonl` is a
  permanent audit trail.

## 8. Cross-references

* [`../live_editing.md`](../live_editing.md) §6 (worked workflow),
  §7 (editing an existing project).
* [`../math_core.md`](../math_core.md) §4 (formula engine),
  §7 (verification gate).
* [`ltspice_file_based_live_editing_math_plan.md`](../../ltspice_file_based_live_editing_math_plan.md)
  — plan §2.2 (user-experience target), §15.2 (voltage divider
  formula), §20.1 (LDR topology metadata), §22 (security model).
* `examples/ldr_dark_detector.json` (planned; Agent 0) — the
  canonical topology metadata for this design.
