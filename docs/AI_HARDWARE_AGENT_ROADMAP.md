# AI Hardware Design Agent Roadmap — Single-Agent Edition

**Revision note:** versi ini membatalkan workflow lintas-agent/subagent. Arsitektur sekarang memakai **satu main AI agent** yang memanggil modul/tool deterministik secara berurutan dan terkontrol.

**Target project:** evolution of `LTSpice-MCP` into a complete AI-assisted hardware design system.
**Goal:** allow one main AI agent to generate, validate, simulate, optimize, document, and reuse hardware designs ranging from simple analog circuits to complex digital/mixed-signal systems such as a small CPU or mini game console driving an LED matrix.

---

## 0. Executive Summary

The project should evolve from **AI + LTspice automation** into a broader **AI Hardware Design Agent**.

The key idea is:

```text
User prompt
→ requirement extraction
→ structured design specification
→ circuit/system IR
→ deterministic generators
→ simulation and formal verification
→ optimization loop
→ report and template memory
```

The AI should **not** directly write arbitrary LTspice `.asc`, arbitrary Verilog, or arbitrary shell commands and then assume correctness. The AI should act as a **single planner, reviewer, debugger, and orchestrator**, while correctness must come from:

- deterministic schemas,
- formula/math engines,
- SPICE simulation,
- HDL simulation,
- linting,
- formal verification,
- synthesis reports,
- regression tests,
- template promotion rules.

The project should be split into engines:

```text
AI Hardware Agent
├── Analog Engine          → LTspice/ngspice/SPICE, .cir, .asc, .raw, .log
├── Digital Engine         → Verilog/SystemVerilog, Verilator/Icarus, Yosys, SBY
├── Embedded/System Engine → firmware, CPU ISA, memory map, peripherals
├── Visualization Engine   → schematic, waveform, LED matrix/game renderer
├── Optimization Engine    → component sizing, topology search, design-space exploration
├── Verification Engine    → tests, assertions, formal checks, golden models
├── Template Memory        → reusable designs, candidates, rejected designs
└── MCP/CLI Interface      → safe tool access for Codex/Claude/OpenCode/Cursor/etc.
```

The safest development strategy is **gradual capability expansion**, not “AI can create any circuit immediately.”

---

## 1. Final Vision

The final system should support user requests such as:

```text
"Buat voltage divider 12V ke 5V."
"Buat RC low-pass 1 kHz dan simulasikan Bode plot-nya."
"Buat inverting op-amp gain -10 dengan bandwidth minimal 20 kHz."
"Buat buck converter 12V ke 5V 1A dan optimasi ripple-nya."
"Buat LED matrix driver 8x8 dengan transistor array."
"Buat CPU 8-bit sederhana yang menjalankan Snake di LED matrix."
"Buat mini console yang menjalankan Tetris sederhana di simulator."
```

But the system should classify each request into one of these levels:

| Level | Meaning | Action |
|---|---|---|
| Supported | Known topology/system, validated template available | Generate automatically, simulate, report |
| Partially supported | Similar to supported topology | Generate approximated design with warnings |
| Experimental | Possible, but needs more tests | Generate in sandbox only, not promoted as official |
| Unsupported | Too complex/unsafe/unvalidated | Produce plan/spec only, ask for manual review |
| Unsafe physical design | High-voltage, mains, high power, safety-critical | Simulate only or refuse implementation details for real hardware |

The user-facing promise should be:

> “AI can help design many circuits, but every generated design must pass structured validation and simulation before being trusted.”

---

## 2. Core Design Principles

### 2.1 AI is not the source of truth

The LLM may generate plans, explanations, HDL drafts, and debugging hypotheses. But it should not be the final authority.

Final authority must be:

- SPICE simulation for analog behavior,
- HDL simulation for digital behavior,
- formal verification for invariants,
- synthesis for hardware feasibility,
- math checks for known formulas,
- regression tests for known templates.

### 2.2 Use structured IR everywhere

Avoid direct free-form generation of `.asc` and large HDL files.

Instead:

```text
Natural language
→ RequirementSpec
→ CircuitIR / DigitalIR / SystemIR
→ Generator
→ Tool output
→ Analyzer
→ Report
```

### 2.3 Prefer deterministic generation

File generation should be deterministic:

- same input spec → same output file,
- stable component naming,
- stable node naming,
- stable layout coordinates,
- stable report format.

This makes debugging, testing, and version control much easier.

### 2.4 Tool-in-the-loop over one-shot generation

Every generated artifact must go through a tool loop:

```text
generate → parse/lint → simulate → analyze → repair → retest → promote
```

### 2.5 Safe reverse engineering only

Do not disassemble or bypass proprietary EDA tools. The safe approach is:

- study public file formats,
- study official CLI behavior,
- write parsers for your own generated files,
- use public documentation,
- use open-source tools as references,
- use black-box tests on allowed inputs/outputs,
- respect licenses.

### 2.6 Keep CLI as the foundation, MCP as adapter

The CLI should be the core product. MCP is only an interface layer for AI agents.

```text
Python Core
├── CLI
└── MCP server
```

This prevents your project from becoming locked into one AI interface.

### 2.7 Cancel cross-agent work

The project should **not** depend on multiple independent AI agents working in parallel.

Avoid this model:

```text
Planner Agent
+ Analog Agent
+ Digital Agent
+ Verification Agent
+ Optimizer Agent
+ Memory Agent
→ all independently editing or deciding
```

Use this model instead:

```text
One Main Agent
→ calls deterministic modules/tools
→ receives structured results
→ makes one serialized decision at a time
```

Reason:

- hardware projects need consistent state,
- schematic, netlist, HDL, tests, and reports must stay synchronized,
- parallel AI agents can overwrite each other,
- debugging becomes harder when many agents change different files,
- verification should be deterministic, not negotiated between agents.

Parallelism is allowed only at the **tool execution level**, not at the AI decision level.

Safe examples:

```text
Allowed:
- run several simulations in parallel for parameter sweep
- run HDL tests in parallel
- run lint and unit tests in parallel
- generate multiple candidate parameter sets deterministically

Not allowed:
- multiple AI agents editing the same project
- multiple AI agents deciding which topology is correct
- multiple AI agents promoting templates independently
- one agent changing tests while another changes implementation
```

---

## 3. Scope and Non-Scope

### 3.1 In scope

Analog:

- voltage divider,
- RC/RL/RLC filters,
- diode circuits,
- rectifiers,
- transistor switches,
- op-amp amplifiers,
- comparators,
- simple oscillators,
- LED drivers,
- basic power converters,
- SPICE netlist generation,
- LTspice `.asc` generation,
- waveform/log parsing,
- component sizing,
- Monte Carlo/tolerance simulation later.

Digital:

- Verilog/SystemVerilog module generation,
- testbench generation,
- Icarus/Verilator simulation,
- Yosys synthesis,
- SymbiYosys formal verification,
- simple CPUs,
- FSMs,
- ALUs,
- register files,
- memory-mapped I/O,
- LED matrix controllers,
- small games like blink, Snake, Pong, Tetris-lite.

Mixed system:

- digital control + analog driver,
- CPU/FPGA logic + LED driver circuit,
- embedded firmware + peripheral map,
- simulation reports from multiple tools.

Project infrastructure:

- project folders,
- template library,
- sandboxed execution,
- JSON schemas,
- CI tests,
- documentation,
- MCP safe tool server.

### 3.2 Out of scope for early phases

Do not target these too early:

- full commercial-grade PCB autorouting,
- transistor-level CPU simulation in LTspice,
- complete PC architecture,
- high-speed signal integrity,
- RF design automation,
- mains/high-voltage build-ready designs,
- safety-critical medical/automotive control,
- fully autonomous fabrication/tapeout.

---

## 4. Recommended Repository Structure

Suggested structure:

```text
ltspice-mcp/
├── pyproject.toml
├── README.md
├── docs/
│   ├── AI_HARDWARE_AGENT_ROADMAP.md
│   ├── ARCHITECTURE.md
│   ├── MCP_TOOLS.md
│   ├── ANALOG_ENGINE.md
│   ├── DIGITAL_ENGINE.md
│   ├── REVERSE_ENGINEERING_NOTES.md
│   ├── SECURITY_MODEL.md
│   ├── TEMPLATE_SYSTEM.md
│   ├── OPTIMIZATION.md
│   └── SOURCES.md
├── src/ltagent/
│   ├── cli.py
│   ├── mcp_server.py
│   ├── config.py
│   ├── security.py
│   ├── workspace.py
│   ├── schemas/
│   │   ├── requirements.py
│   │   ├── analog_ir.py
│   │   ├── digital_ir.py
│   │   ├── system_ir.py
│   │   ├── results.py
│   │   └── templates.py
│   ├── units/
│   │   ├── parser.py
│   │   ├── formats.py
│   │   └── standard_values.py
│   ├── math_core/
│   │   ├── formulas.py
│   │   ├── rc.py
│   │   ├── rlc.py
│   │   ├── opamp.py
│   │   ├── power.py
│   │   ├── mna.py
│   │   └── tolerances.py
│   ├── analog/
│   │   ├── netlist_writer.py
│   │   ├── asc_writer.py
│   │   ├── asc_parser.py
│   │   ├── symbol_parser.py
│   │   ├── layout_engine.py
│   │   ├── ltspice_runner.py
│   │   ├── ngspice_runner.py
│   │   ├── raw_parser.py
│   │   ├── log_parser.py
│   │   ├── measurement_parser.py
│   │   └── analog_validator.py
│   ├── digital/
│   │   ├── hdl_ir.py
│   │   ├── verilog_writer.py
│   │   ├── sv_parser.py
│   │   ├── testbench_writer.py
│   │   ├── iverilog_runner.py
│   │   ├── verilator_runner.py
│   │   ├── yosys_runner.py
│   │   ├── sby_runner.py
│   │   ├── vcd_parser.py
│   │   ├── waveform_analyzer.py
│   │   └── digital_validator.py
│   ├── systems/
│   │   ├── tiny8/
│   │   │   ├── isa.py
│   │   │   ├── assembler.py
│   │   │   ├── emulator.py
│   │   │   ├── cpu_templates.py
│   │   │   ├── memory_map.py
│   │   │   └── games/
│   │   │       ├── blink.asm
│   │   │       ├── pixel.asm
│   │   │       ├── snake.asm
│   │   │       └── tetris_lite.asm
│   │   ├── led_matrix.py
│   │   └── firmware.py
│   ├── optimization/
│   │   ├── objective.py
│   │   ├── sweeps.py
│   │   ├── scipy_opt.py
│   │   ├── bayes_opt.py
│   │   ├── rl_interface.py
│   │   └── ranking.py
│   ├── verification/
│   │   ├── analog_checks.py
│   │   ├── digital_checks.py
│   │   ├── golden_models.py
│   │   ├── formal_properties.py
│   │   ├── report.py
│   │   └── regression.py
│   ├── templates/
│   │   ├── loader.py
│   │   ├── matcher.py
│   │   ├── evaluator.py
│   │   ├── promoter.py
│   │   └── registry.py
│   ├── ai/
│   │   ├── prompt_contracts.py
│   │   ├── task_planner.py
│   │   ├── repair_loop.py
│   │   ├── critique.py
│   │   └── tool_policy.py
│   └── render/
│       ├── schematic_svg.py
│       ├── led_matrix_renderer.py
│       ├── waveform_summary.py
│       └── html_report.py
├── templates/
│   ├── official/
│   ├── candidates/
│   └── rejected/
├── examples/
│   ├── analog/
│   ├── digital/
│   ├── mixed/
│   └── games/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── fixtures/
└── .github/workflows/
    ├── ci.yml
    └── toolchain-smoke.yml
```

---

## 5. Data Contracts and Schemas

### 5.1 RequirementSpec

Purpose: convert natural language into validated structured requirements.

Example:

```json
{
  "request_id": "req_001",
  "domain": "analog",
  "intent": "rc_lowpass_filter",
  "constraints": {
    "cutoff_frequency_hz": 1000,
    "input_voltage_v": 5,
    "load_resistance_ohm": 100000,
    "preferred_capacitance_f": 1e-7
  },
  "outputs": ["ltspice_asc", "netlist", "bode_report"],
  "safety_level": "simulation_only"
}
```

### 5.2 CircuitIR

Purpose: canonical representation for analog circuits.

Example:

```json
{
  "schema_version": "analog-ir-v1",
  "topology": "rc_lowpass",
  "nodes": ["vin", "vout", "0"],
  "components": [
    {"id": "V1", "type": "voltage_source", "pins": {"p": "vin", "n": "0"}, "value": {"dc": 0, "ac": 1}},
    {"id": "R1", "type": "resistor", "pins": {"a": "vin", "b": "vout"}, "value": "1.59k"},
    {"id": "C1", "type": "capacitor", "pins": {"a": "vout", "b": "0"}, "value": "100n"}
  ],
  "analyses": [
    {"type": "ac", "sweep": "dec", "points": 100, "start_hz": 10, "stop_hz": 1000000}
  ],
  "measurements": [
    {"name": "gain_1khz", "type": "gain_db", "node": "vout", "frequency_hz": 1000}
  ]
}
```

### 5.3 DigitalIR

Purpose: canonical representation for digital modules.

Example:

```json
{
  "schema_version": "digital-ir-v1",
  "top_module": "led_counter_top",
  "clock": {"name": "clk", "period_ns": 10},
  "reset": {"name": "rst_n", "active": "low"},
  "modules": [
    {
      "name": "counter8",
      "ports": [
        {"name": "clk", "dir": "input", "width": 1},
        {"name": "rst_n", "dir": "input", "width": 1},
        {"name": "q", "dir": "output", "width": 8}
      ],
      "behavior": "increment q on every rising clock edge"
    }
  ],
  "test_goals": ["reset clears q", "q increments after reset"]
}
```

### 5.4 SystemIR

Purpose: describe systems that combine CPU, memory, display, and peripherals.

Example:

```json
{
  "schema_version": "system-ir-v1",
  "system_type": "tiny_game_console",
  "cpu": "tiny8",
  "rom_words": 256,
  "ram_bytes": 256,
  "display": {"type": "led_matrix", "width": 8, "height": 8},
  "input": ["left", "right", "rotate", "drop"],
  "memory_map": {
    "0xF0": "LED_X",
    "0xF1": "LED_Y",
    "0xF2": "LED_VALUE",
    "0xF3": "INPUT_BUTTONS"
  },
  "program": "snake.asm",
  "simulation_goal": "render at least 20 valid LED frames"
}
```

### 5.5 Result schema

Every tool should output machine-readable results:

```json
{
  "status": "pass",
  "artifacts": {
    "netlist": "projects/rc_lowpass/circuit.cir",
    "schematic": "projects/rc_lowpass/circuit.asc",
    "log": "projects/rc_lowpass/run.log",
    "report": "projects/rc_lowpass/report.md"
  },
  "metrics": {
    "cutoff_frequency_hz": 1003.2,
    "gain_at_fc_db": -3.02,
    "simulation_time_s": 0.82
  },
  "warnings": [],
  "errors": []
}
```

---

## 6. Safe Reverse Engineering Plan

### 6.1 LTspice reverse engineering

Goal: understand enough of LTspice to generate, inspect, and validate files without relying on undocumented binary internals.

Focus on:

```text
.asc  schematic format
.asy  symbol format
.cir  SPICE netlist
.log  simulation output and .meas results
.raw  waveform data
.plt  plot settings
.lib/.sub/.model model libraries
```

What to build:

1. `.cir` writer
2. `.cir` parser
3. `.asc` writer for supported subset
4. `.asc` parser for your generated files
5. `.asy` symbol parser for supported symbols
6. `.log` parser
7. `.raw` parser or PyLTSpice adapter
8. layout checker
9. round-trip tests

Recommended policy:

```text
Generate .cir first → run simulation → if valid, generate .asc.
```

Why: `.cir` is easier to validate than `.asc`. `.asc` has visual/layout complexity.

Important tests:

```text
- generated .asc opens in LTspice
- generated .asc exports expected netlist
- generated .cir simulates successfully
- .log parser extracts .meas values
- .raw parser extracts named traces
- parser rejects path traversal and unsupported directives
```

### 6.2 Verilog/SystemVerilog reverse engineering

For HDL, do not write your own full SystemVerilog parser at first. Use existing parsers/tools.

Recommended layers:

```text
Basic parse/lint:
- tree-sitter-verilog for code structure
- slang or Surelog/UHDM later for serious SystemVerilog

Simulation:
- Icarus Verilog for simple Verilog
- Verilator for fast cycle simulation

Synthesis:
- Yosys

Formal:
- SymbiYosys
```

Build your own wrappers around these tools instead of reimplementing them.

### 6.3 KiCad/SKiDL reverse engineering

Use KiCad/SKiDL mainly later, when you want PCB-ready output.

Early project should focus on simulation. PCB generation should be delayed until analog/digital simulation is stable.

Potential future flow:

```text
CircuitIR
→ SKiDL/KiCad netlist
→ KiCad schematic
→ ERC
→ PCB footprint assignment
→ PCB layout assist
```

---

## 7. Analog Engine Detailed Plan

### Phase A1 — Analog core foundation

Goal: reliable analog project generation.

Deliverables:

- `CircuitIR` schema
- unit parser: `1k`, `10u`, `3.3V`, `100nF`
- standard value selector: E12/E24/E48/E96
- component validators
- node validators
- `.cir` writer
- LTspice runner
- `.log` parser
- `.meas` parser
- `result.json`

Commands:

```bash
ltagent create analog rc_lowpass --fc 1k --c 100n
ltagent generate-netlist projects/rc_lowpass
ltagent run projects/rc_lowpass
ltagent result projects/rc_lowpass
```

Supported topologies:

```text
voltage_divider
rc_lowpass
rc_highpass
rl_lowpass
rl_highpass
series_rlc
parallel_rlc
led_resistor
```

Acceptance criteria:

- generated netlist passes LTspice simulation,
- result parser extracts all measurements,
- formula and simulation agree within tolerance,
- all invalid component values are rejected,
- tests cover at least 20 generated circuits.

### Phase A2 — Formula/math core

Goal: make AI calculations accurate and explainable.

Modules:

```text
math_core/rc.py
math_core/rl.py
math_core/rlc.py
math_core/opamp.py
math_core/power.py
math_core/tolerances.py
```

Features:

- cutoff frequency calculation,
- time constant calculation,
- damping factor,
- Q factor,
- op-amp gain formulas,
- LED resistor calculation,
- power dissipation,
- basic thermal margin,
- tolerance worst-case estimate,
- standard component snapping.

Output:

```text
calculation.json
calculation.md
```

Example `calculation.md`:

```md
# RC Low-Pass Calculation

Target cutoff: 1 kHz
Chosen C: 100 nF
Ideal R: 1591.55 ohm
Selected E24 R: 1.6 kOhm
Expected cutoff: 994.7 Hz
Simulation cutoff: 1001.2 Hz
Error: 0.65%
```

### Phase A3 — LTspice `.asc` generation

Goal: readable schematic output.

Rules:

- do not let LLM directly place coordinates,
- use grid-based deterministic layout,
- every topology has a layout template,
- wires must be orthogonal,
- component labels must not overlap,
- ground must be consistent,
- node labels must be generated from IR.

Deliverables:

```text
asc_writer.py
layout_engine.py
symbol_parser.py
layout_checker.py
```

Supported layout primitives:

```text
Place component at grid coordinate
Connect two pins with orthogonal wire
Place ground symbol
Place node label
Place simulation directive
Place text annotation
```

Acceptance criteria:

- `.asc` generated for all Phase A1 circuits,
- layout checker catches overlaps,
- LTspice can open generated files,
- human-readable schematic screenshot/manual check for official templates.

### Phase A4 — Analog template memory

Goal: reuse and improve known good designs.

Template states:

```text
templates/official/    → verified and stable
templates/candidates/  → generated but not fully trusted
templates/rejected/    → failed or unsafe designs
```

Each template must include:

```yaml
id: rc_lowpass_v1
domain: analog
topology: rc_lowpass
status: official
inputs:
  fc_hz: number
  c_f: optional number
outputs:
  cutoff_frequency_hz: number
artifacts:
  ir: circuit.ir.json
  netlist: circuit.cir
  schematic: circuit.asc
tests:
  - formula_check
  - ltspice_ac_sim
  - layout_check
metrics:
  max_formula_error_percent: 2
promotion:
  approved_by: human_or_regression
  date: 2026-06-19
```

Promotion rules:

```text
candidate → official only if:
- schema valid
- simulation pass
- measurements extracted
- formula agreement pass
- no safety violations
- layout check pass
- regression added
```

### Phase A5 — Advanced analog

After stable basics, add:

```text
inverting_opamp
non_inverting_opamp
comparator
schmitt_trigger
diode_clipper
half_wave_rectifier
bridge_rectifier
transistor_switch
common_emitter_amp
mosfet_switch
buck_converter_open_loop
boost_converter_open_loop
photodiode_tia_basic
```

For each topology, define:

- formula model,
- SPICE template,
- layout template,
- expected simulation metrics,
- failure modes,
- parameter ranges.

---

## 8. Digital Engine Detailed Plan

### Phase D1 — Digital HDL foundation

Goal: generate and simulate simple Verilog modules safely.

Deliverables:

```text
digital_ir.py
verilog_writer.py
testbench_writer.py
iverilog_runner.py
verilator_runner.py
vcd_parser.py
digital_validator.py
```

Initial supported designs:

```text
and_gate
mux2
adder
counter
shift_register
pwm
fsm_simple
led_blinker
uart_tx_basic
```

Workflow:

```text
DigitalIR
→ Verilog writer
→ testbench writer
→ lint/compile
→ simulate
→ parse output/VCD
→ result.json
```

Acceptance criteria:

- all generated designs compile,
- simulation output matches expected truth table or sequence,
- VCD generated and parsed,
- failure logs are converted to actionable repair hints.

### Phase D2 — Yosys synthesis

Goal: ensure generated HDL is synthesizable.

Add:

```text
yosys_runner.py
yosys_script_writer.py
synthesis_report_parser.py
```

Output metrics:

```json
{
  "cells": 34,
  "wires": 120,
  "flops": 8,
  "luts_estimated": 12,
  "synth_status": "pass"
}
```

Acceptance criteria:

- every official digital template passes Yosys,
- unsupported SV constructs are detected early,
- combinational loops and inferred latches are flagged.

### Phase D3 — Formal verification

Goal: verify invariants, not just simulate examples.

Add:

```text
sby_runner.py
formal_property_writer.py
formal_report_parser.py
```

Examples:

Counter property:

```systemverilog
assert property (@(posedge clk) disable iff (!rst_n) q_next == q + 1);
```

FIFO properties:

```text
- never read when empty
- never write when full
- order is preserved
- count never exceeds depth
```

Acceptance criteria:

- counters, FIFOs, FSMs have formal checks,
- failed proof returns counterexample trace,
- AI repair loop can inspect counterexample.

### Phase D4 — Tiny8 CPU platform

Goal: small programmable system that can run programs.

Components:

```text
Tiny8 CPU
├── program counter
├── instruction register
├── accumulator
├── flags
├── ALU
├── control FSM
├── ROM interface
├── RAM interface
└── memory-mapped I/O
```

Suggested ISA:

```text
NOP
LDI imm
LDA addr
STA addr
ADD addr
SUB addr
AND addr
OR addr
XOR addr
JMP addr
JZ addr
JNZ addr
IN port
OUT port
HALT
```

Memory map:

```text
0x00-0x7F RAM
0x80-0xBF framebuffer
0xF0 LED_X
0xF1 LED_Y
0xF2 LED_VALUE
0xF3 INPUT_BUTTONS
0xF4 FRAME_COMMIT
0xFF DEBUG_HALT
```

Deliverables:

```text
isa.py
assembler.py
emulator.py
rtl/tiny8_cpu.sv
rtl/tiny8_rom.sv
rtl/tiny8_ram.sv
rtl/led_matrix_mmio.sv
tb/tb_tiny8.sv
```

Test programs:

```text
blink.asm
move_pixel.asm
draw_square.asm
scroll_pattern.asm
```

Acceptance criteria:

- assembler produces ROM image,
- emulator and RTL simulation agree,
- CPU passes instruction-level tests,
- LED matrix renderer shows frames.

### Phase D5 — Game console demos

Goal: demonstrate complex system generation.

Game milestones:

```text
1. Blink LED
2. Moving pixel
3. Bouncing pixel
4. Snake-lite
5. Pong-lite
6. Tetris-lite
```

Tetris-lite constraints:

```text
Display: 8x16 or 10x20 later
Pieces: start with 2-3 pieces, then all 7 tetrominoes
Rotation: simple lookup table
Random: deterministic sequence first
Scoring: optional
Line clear: basic
Input: left, right, rotate, drop
```

Do not start with full Tetris. Start with falling block + collision.

Acceptance criteria for Tetris-lite:

- generated program assembles,
- RTL simulation runs for N cycles,
- LED frames are rendered,
- game state remains valid,
- collision does not write outside board memory,
- formal/simulation assertions detect invalid board access.

---

## 9. Mixed-Signal/System-Level Plan

Mixed systems should be modeled as separate domains connected by clear interfaces.

Example: LED matrix mini console.

```text
Digital simulation:
- CPU
- RAM
- ROM
- LED matrix controller
- input buttons

Analog simulation:
- LED current limiting
- transistor row/column driver
- power rail behavior
- button pull-up/pull-down filter
```

Do not simulate CPU transistor-level in LTspice for early versions. Use HDL for logic and SPICE for analog interfaces.

Mixed report:

```text
Digital result:
- frames rendered correctly
- CPU instruction tests pass
- synthesis pass

Analog result:
- LED current within safe range
- transistor saturation margin OK
- power dissipation OK
- button debounce RC response OK
```

---

## 10. MCP Tool Architecture

Expose only safe, curated tools.

MCP should be treated as a **tool adapter**, not as a cross-agent coordination layer.

```text
Good:
Main agent → MCP tool → deterministic result

Bad:
Main agent → MCP server → spawns other autonomous agents → uncontrolled edits
```


### 10.1 Analog tools

```text
create_analog_project
inspect_analog_project
generate_netlist
generate_schematic
run_spice_simulation
read_spice_measurements
read_waveform_summary
check_layout
find_analog_template
evaluate_analog_template
promote_analog_template
```

### 10.2 Digital tools

```text
plan_digital_system
create_digital_project
generate_hdl
assemble_program
run_hdl_simulation
run_hdl_lint
run_yosys_synthesis
run_formal_check
read_waveform_summary
render_led_matrix
inspect_digital_project
promote_digital_template
```

### 10.3 Mixed/system tools

```text
plan_hardware_system
create_system_project
split_analog_digital_blocks
run_system_simulation
compare_emulator_and_rtl
render_system_report
```

### 10.4 Forbidden MCP tools

Do not expose:

```text
run_shell
execute_python
read_any_file
write_any_file
raw_path_access
delete_project_without_snapshot
network_access
unrestricted_process_spawn
```

Instead, expose constrained actions with schema validation.

---

## 11. AI Agent Workflow

### 11.1 Single-agent supervisor loop

```text
1. Read user request
2. Classify domain: analog, digital, mixed, embedded, unknown
3. Extract requirements
4. Check supported capabilities
5. Generate structured plan
6. Call deterministic tools/modules one at a time
7. Inspect errors/results
8. Repair if needed
9. Generate report
10. Store reusable template only if approved
```

### 11.2 Single-agent module workflow

The project should **not** run multiple AI subagents that independently edit files, execute tools, or make competing decisions.

Instead, use **one main agent** with clearly separated deterministic modules:

```text
Main AI Agent
├── Requirement Module
│   └── Converts prompt into structured requirement JSON
├── Topology Module
│   └── Chooses topology/template or marks unsupported
├── Math Module
│   └── Computes values and sanity checks
├── Analog Generator Module
│   └── Produces CircuitIR, then deterministic netlist/schematic writers generate files
├── Digital Generator Module
│   └── Produces DigitalIR/module spec, then deterministic HDL writer generates files
├── Simulation Module
│   └── Runs SPICE/HDL tools and summarizes result
├── Verification Module
│   └── Runs assertions/formal/regression checks
├── Optimizer Module
│   └── Changes values or architecture to meet objectives
├── Reviewer Module
│   └── Checks safety, scope, and correctness
└── Memory Curator Module
    └── Decides whether to save candidate template
```

Important rule:

```text
Only the main agent may decide the next action.
Modules may return data, reports, errors, and suggestions,
but modules must not independently modify project direction.
```

This prevents the common multi-agent failure mode:

```text
Agent A edits schematic
Agent B edits netlist
Agent C changes tests
Agent D reverts previous work
→ inconsistent project state
```

The desired model is:

```text
one agent brain
+ many deterministic tools/modules
+ strict schemas
+ explicit checkpoints
```

### 11.3 Error repair loop

```text
Generate artifact
→ tool fails
→ parse error log
→ classify failure
→ apply minimal patch
→ rerun
→ stop after max attempts
→ save failure report
```

Failure classes:

```text
schema_error
unit_parse_error
unsupported_component
spice_convergence_error
ltspice_missing_model
hdl_syntax_error
hdl_width_mismatch
simulation_mismatch
formal_counterexample
synthesis_unsupported_construct
layout_overlap
unsafe_parameter
```

---

## 12. Optimization Strategy

### 12.1 Start simple

Use deterministic sweeps before advanced ML.

```text
1. Formula-based initial sizing
2. Standard value snapping
3. Grid sweep
4. LTspice .step sweep
5. SciPy optimization
6. Bayesian optimization
7. Reinforcement learning later
```

### 12.2 Objective function

Example for RC filter:

```json
{
  "target": {"cutoff_frequency_hz": 1000},
  "minimize": ["frequency_error", "component_count", "cost"],
  "constraints": [
    "gain_at_fc_db between -3.5 and -2.5",
    "component_values in E24",
    "load_effect_error_percent < 5"
  ]
}
```

Example for buck converter:

```json
{
  "target": {"vout_v": 5, "iout_a": 1},
  "minimize": ["output_ripple_mv", "inductor_current_ripple", "power_loss"],
  "constraints": [
    "duty_cycle between 0.05 and 0.95",
    "switch_current_margin_percent > 30",
    "diode_reverse_voltage_margin_percent > 30"
  ]
}
```

### 12.3 Candidate ranking

Rank candidates by:

```text
simulation_pass
spec_error
safety_margin
component_count
cost_proxy
layout_score
stability_margin
runtime
reuse_score
```

---

## 13. Verification Strategy

### 13.1 Analog verification

For each analog design:

```text
Schema check
→ electrical sanity check
→ formula check
→ SPICE simulation
→ measurement extraction
→ tolerance check
→ layout check
→ report
```

Analog checks:

```text
- required ground exists
- no floating critical nodes
- no duplicate component IDs
- no invalid values
- voltage/current/power within expected range
- AC/transient/DC simulation matches intended analysis
- no LTspice fatal errors
- no missing models
```

### 13.2 Digital verification

For each digital design:

```text
Schema check
→ HDL generation
→ lint/compile
→ simulation
→ golden model comparison
→ formal checks
→ synthesis
→ report
```

Digital checks:

```text
- no implicit latch unless intentional
- no width mismatch
- no multiple driver
- no combinational loop
- reset behavior verified
- testbench passes
- formal assertions pass where available
- Yosys synthesis pass
```

### 13.3 System verification

For CPU/game systems:

```text
Assembler output
→ emulator run
→ RTL simulation run
→ compare emulator vs RTL
→ render LED frames
→ check game invariants
→ synthesize top module
```

Game invariants:

```text
- board memory access in bounds
- no invalid piece coordinates
- input state is valid
- frame buffer dimensions correct
- no CPU execution past ROM bounds
- HALT/debug condition handled
```

---

## 14. Test Plan

### 14.1 Unit tests

```text
units parser
standard value selector
CircuitIR validation
DigitalIR validation
netlist writer
asc writer
log parser
raw parser
Verilog writer
assembler
emulator
memory map
```

### 14.2 Integration tests

```text
RC low-pass end-to-end
op-amp inverting end-to-end
rectifier end-to-end
counter HDL end-to-end
FSM HDL end-to-end
Tiny8 blink end-to-end
Tiny8 LED matrix end-to-end
```

### 14.3 Regression tests

Every official template must have:

```text
input spec
expected artifacts
expected metrics tolerance
simulation output sample
report snapshot
```

### 14.4 CI

Minimum GitHub Actions:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e '.[dev,mcp]'
      - run: ruff check .
      - run: mypy src
      - run: pytest
```

Toolchain smoke test later:

```text
- install ngspice
- install iverilog
- install verilator
- install yosys
- install symbiyosys if available
- run small generated designs
```

---

## 15. Roadmap by Milestone

## Milestone 0 — Repo cleanup

Goal: make repo ready for serious development.

Tasks:

- fix project naming consistency,
- update README to current tool/resource counts,
- separate analog/digital docs,
- add `docs/AI_HARDWARE_AGENT_ROADMAP.md`,
- add CI,
- add contribution rules,
- add security model.

Acceptance criteria:

- clean install from README works,
- `pytest` passes,
- `ltagent --help` works,
- `ltagent-mcp --help` works,
- docs match current code.

---

## Milestone 1 — Analog MVP

Goal: reliable simple analog generation.

Topologies:

```text
voltage_divider
rc_lowpass
rc_highpass
led_resistor
```

Deliverables:

- `CircuitIR v1`,
- `.cir` writer,
- LTspice runner,
- log/meas parser,
- calculation report,
- result JSON,
- regression tests.

Acceptance:

- 20+ generated circuits pass simulation,
- formula vs simulation report generated,
- all artifacts stored in project folder.

---

## Milestone 2 — `.asc` schematic generation

Goal: produce readable LTspice schematics.

Deliverables:

- layout engine,
- `.asc` writer,
- layout checker,
- symbol registry,
- official schematic templates.

Acceptance:

- all Milestone 1 circuits produce valid `.asc`,
- layout check passes,
- examples open in LTspice.

---

## Milestone 3 — Analog templates and optimization

Goal: reusable and optimized analog circuits.

Add:

```text
op-amp amplifier
comparator
diode clipper
rectifier
transistor switch
```

Optimization:

```text
formula sizing
standard value snapping
parameter sweep
```

Acceptance:

- template promotion pipeline works,
- candidates are not automatically trusted,
- topologies have docs and tests.

---

## Milestone 4 — Digital HDL MVP

Goal: generate and simulate simple Verilog.

Modules:

```text
and_gate
mux2
adder
counter
shift_register
fsm
pwm
```

Deliverables:

- DigitalIR,
- Verilog writer,
- testbench writer,
- Icarus/Verilator runner,
- VCD parser,
- report.

Acceptance:

- generated designs compile and simulate,
- expected outputs are checked,
- synthesis not required yet.

---

## Milestone 5 — Digital synthesis and formal

Goal: ensure generated HDL is synthesizable and verifiable.

Deliverables:

- Yosys runner,
- synthesis report parser,
- SymbiYosys runner,
- formal property templates.

Acceptance:

- official digital templates pass synthesis,
- counters/FIFOs/FSMs have formal checks,
- formal counterexamples are summarized.

---

## Milestone 6 — Tiny8 CPU and LED matrix

Goal: create a programmable mini computer.

Deliverables:

- Tiny8 ISA,
- assembler,
- emulator,
- CPU RTL,
- RAM/ROM RTL,
- LED matrix MMIO,
- testbench,
- LED frame renderer.

Demo programs:

```text
blink
move_pixel
draw_square
scroll_pattern
```

Acceptance:

- emulator and RTL outputs match,
- LED frames render,
- CPU passes instruction tests,
- Yosys synthesis passes.

---

## Milestone 7 — Game demos

Goal: show complex end-to-end design.

Games:

```text
Snake-lite
Pong-lite
Tetris-lite
```

Acceptance:

- game program assembles,
- simulator renders frames,
- game invariants pass,
- generated report includes animation/frame output.

---

## Milestone 8 — Mixed analog-digital

Goal: combine HDL logic with LTspice interface circuits.

Examples:

```text
LED matrix controller + transistor LED driver
button input + RC debounce + digital input
PWM generator + analog RC filter
DAC ladder + digital counter
```

Acceptance:

- digital part passes HDL sim,
- analog part passes SPICE sim,
- integrated report explains both.

---

## Milestone 9 — Advanced analog and power electronics

Goal: support more complex analog/power designs.

Add:

```text
buck converter open loop
boost converter open loop
buck feedback compensation assistant
TIA photodiode amplifier
active filter design
oscillator basics
```

Acceptance:

- only simulation-oriented output,
- safety warnings for power/high-voltage,
- optimization loop with sweeps,
- no build-ready claim without human review.

---

## Milestone 10 — AI planning and repair loop

Goal: AI can orchestrate tools robustly.

Deliverables:

- prompt contracts,
- JSON output contracts,
- repair classifier,
- tool feedback summarizer,
- max-attempt loop,
- structured final report.

Acceptance:

- AI can repair simple SPICE/HDL errors,
- failures are not hidden,
- every repair is logged.

---

## Milestone 11 — RAG and component knowledge base

Goal: ground AI in reliable component data.

Knowledge base:

```text
component symbols
pinouts
SPICE model metadata
common topologies
formula registry
tool docs snippets
known error fixes
```

Rules:

- do not scrape copyrighted datasheets into repo wholesale,
- store metadata and links,
- allow user-provided local datasheets if needed,
- keep source attribution.

Acceptance:

- AI can retrieve correct pinout for known parts,
- unknown components are rejected or require explicit model/pinout,
- RAG results are cited in reports.

---

## Milestone 12 — Public release quality

Goal: make project usable by others.

Deliverables:

- docs site,
- install guide,
- examples gallery,
- test matrix,
- security docs,
- MCP setup docs,
- demo videos/gifs,
- template contribution guide.

Acceptance:

- new user can install and run examples,
- CI passes,
- docs match current code,
- templates are reproducible.

---

## 16. Source and Reference Map

### 16.1 Official software/tool references

[S1] LTspice official page — Analog Devices
https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html
Use for: LTspice capability, schematic capture, waveform viewer, ADI tool export to LTspice.

[S2] PyLTSpice documentation
https://pyltspice.readthedocs.io/en/latest/
Use for: reading/manipulating netlists, running simulations, reading raw/log files, AscEditor references.

[S3] ngspice documentation
https://ngspice.sourceforge.io/docs.html
Use for: SPICE internals, netlist processing, matrix setup, simulation run stages, open-source SPICE behavior.

[S4] Yosys documentation
https://yosyshq.readthedocs.io/projects/yosys/en/latest/
Use for: RTL synthesis, optimization passes, FSM/memory handling, formal verification, equivalence checking.

[S5] Verilator documentation
https://verilator.org/guide/latest/
Use for: Verilog/SystemVerilog simulation, C++/SystemC model generation, profiling and coverage.

[S6] Icarus Verilog documentation
https://steveicarus.github.io/iverilog/
Use for: simple Verilog simulation, VVP, waveform viewing, command-line compilation.

[S7] SymbiYosys documentation
https://symbiyosys.readthedocs.io/en/latest/
Use for: formal verification, `.sby` files, engines, assertions.

[S8] OpenROAD documentation
https://openroad.readthedocs.io/en/latest/
Use for: future RTL-to-GDS flow, place-and-route, OpenROAD-flow-scripts.

[S9] KiCad documentation
https://docs.kicad.org/
Use for: PCB/schematic workflows, command-line interface, later PCB integration.

[S10] SKiDL documentation
https://devbisme.github.io/skidl/
Use for: Python-based circuit description, ERC, netlist generation, parametric reusable circuit modules.

[S11] Amaranth HDL documentation
https://amaranth-lang.org/docs/amaranth/latest/
Use for: optional Python-based hardware generator approach.

[S12] Chisel documentation
https://www.chisel-lang.org/docs
Use for: optional Scala-based hardware generator approach.

### 16.2 Papers and research references

[S13] AutoCkt: Deep Reinforcement Learning of Analog Circuit Designs
https://arxiv.org/abs/2001.01808
Use for: reinforcement learning + analog circuit sizing + layout-aware optimization.

[S14] Domain Knowledge-Based Automated Analog Circuit Design with Deep Reinforcement Learning
https://arxiv.org/abs/2202.13185
Use for: domain-knowledge-aware RL for analog design.

[S15] AUTOCIRCUIT-RL: Reinforcement Learning-Driven LLM for Automated Circuit Topology Generation
https://arxiv.org/abs/2506.03122
Use for: LLM + RL topology generation and validity/efficiency rewards.

[S16] Schemato: An LLM for Netlist-to-Schematic Conversion
https://arxiv.org/abs/2411.13899
Use for: netlist-to-schematic conversion and LTspice `.asc` generation research direction.

[S17] CircuitLM: LLM-Aided Circuit Schematic Generation from Natural Language
https://arxiv.org/abs/2601.04505
Use for: NL-to-schematic pipeline ideas, pinout retrieval, structured JSON circuit generation. Do not copy the multi-agent architecture; adapt only the structured pipeline concepts.

[S18] VerilogEval: Evaluating LLMs for Verilog Code Generation
https://arxiv.org/abs/2309.07544
Use for: HDL generation benchmark and automatic simulation-based correctness evaluation.

[S19] VerilogCoder: Autonomous Verilog Coding Agents with Graph Planning and AST-Based Waveform Tracing
https://arxiv.org/abs/2408.08927
Use for: autonomous HDL agent, repair loop, waveform tracing.

[S20] VerilogReader: LLM-Aided Hardware Test Generation
https://arxiv.org/abs/2406.04373
Use for: LLM-based test generation and coverage-directed verification.

[S21] ChipNeMo: Domain-Adapted LLMs for Chip Design
https://arxiv.org/abs/2311.00176
Use for: domain adaptation, EDA script generation, bug summarization.

[S22] MCP4EDA: LLM-Powered MCP RTL-to-GDSII Automation
https://arxiv.org/abs/2507.19570
Use for: MCP architecture for EDA tools, closed-loop optimization across Yosys/Icarus/OpenLane/GTKWave/KLayout.

[S23] ORAssistant: RAG-based Conversational Assistant for OpenROAD
https://arxiv.org/abs/2410.03845
Use for: RAG assistant over EDA documentation and open-source flow setup.

[S24] FormalRTL: Verified RTL Synthesis at Scale
https://arxiv.org/abs/2603.08738
Use for: RTL generation guided by executable formal specs and equivalence checking. Use as verification inspiration, not as a multi-agent workflow.

[S25] Yosys+nextpnr: Open Source Framework from Verilog to Bitstream
https://arxiv.org/abs/1903.10407
Use for: open-source FPGA flow from Verilog through place-and-route.

[S26] Arch: AI-Native HDL for Register-Transfer Clocked Hardware Design
https://arxiv.org/abs/2604.05983
Use for: future idea—designing an AI-friendly intermediate HDL with stronger constraints.

---

## 17. Implementation Priority

If you want the most realistic path, do this order:

```text
1. Repo cleanup + docs sync
2. Analog .cir + LTspice simulation + result parser
3. Formula/math core + calculation report
4. .asc deterministic layout generator
5. Template memory and promotion system
6. DigitalIR + simple Verilog + testbench + Icarus/Verilator
7. Yosys synthesis + SymbiYosys formal
8. Tiny8 CPU + assembler + emulator
9. LED matrix renderer
10. Snake/Pong/Tetris-lite demos
11. Mixed analog-digital demos
12. Optimization loop
13. RAG/component knowledge base
14. Public release polish
```

Do not jump directly to “Tetris on mini PC” before the lower layers exist. The right first impressive demo is:

```text
AI prompt:
"Buat sistem digital yang menyalakan LED matrix 8x8 membentuk animasi kotak bergerak."

Pipeline:
RequirementSpec
→ SystemIR
→ Verilog modules
→ testbench
→ simulation
→ LED frames
→ rendered report
```

After that, move to Snake, then Tetris-lite.

---

## 17.5 Single-Agent Operating Rules

These rules replace the earlier cross-agent/subagent idea.

### 17.5.1 Decision ownership

```text
Only one main AI agent owns:
- requirement interpretation,
- topology selection,
- repair decisions,
- template promotion decisions,
- final user-facing report.
```

### 17.5.2 Module ownership

Modules own only deterministic work:

```text
Requirement Module     → returns RequirementSpec
Math Module            → returns calculations and warnings
Analog Module          → returns CircuitIR/netlist/schematic artifacts
Digital Module         → returns DigitalIR/HDL/testbench artifacts
Simulation Module      → returns logs, measurements, traces
Verification Module    → returns pass/fail and counterexamples
Optimization Module    → returns ranked candidate parameter sets
Memory Module          → returns candidate/official/rejected recommendation
```

### 17.5.3 Serialized edits

All project edits should pass through one serialized queue:

```text
planned_change
→ validate target files
→ apply patch
→ run checks
→ commit/snapshot
→ next change
```

No two AI processes should edit the same project at the same time.

### 17.5.4 Parallelism boundary

Parallelism is allowed for deterministic jobs only:

```text
OK:
- simulation sweep jobs
- test shards
- waveform parsing
- report rendering
- synthesis experiments

Not OK:
- parallel AI reasoning agents
- parallel file-editing agents
- parallel template promotion agents
```


## 18. Definition of Done

A generated design is considered done only if:

```text
- requirements are captured in JSON,
- IR schema validation passes,
- generated files are reproducible,
- simulation passes,
- measurements are extracted,
- report is generated,
- limitations are stated,
- tests are added,
- template status is assigned correctly.
```

A generated design is **not done** if:

```text
- AI only produced code/text without running tools,
- no simulation was run,
- tool errors were ignored,
- measurements were guessed,
- schematic layout is unreadable,
- HDL is not synthesizable but claimed as hardware-ready,
- unsafe real-world build claims are made.
```

---

## 19. Final Recommendation

Build this project as a **hardware design operating system for one main AI agent with deterministic tool modules**.

The main product is not merely:

```text
AI writes LTspice file
```

The real product is:

```text
One main AI agent controls a safe, deterministic, validated hardware design pipeline.
```

That pipeline should eventually cover:

```text
Natural language
→ analog circuit
→ digital HDL
→ embedded program
→ CPU/system design
→ simulation
→ verification
→ optimization
→ reusable template
```

The most important engineering rule:

> Every generated design must be machine-checkable before it becomes trusted.
