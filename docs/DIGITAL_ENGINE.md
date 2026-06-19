# Digital Engine

## Current Experimental Foundation

- Tiny8-oriented validated `DesignIR`.
- Deterministic Verilog-2001 and testbench templates.
- Deterministic Tiny8 assembler and project artifacts.
- Bounded Icarus/VVP and Yosys wrappers with structured unavailable states.
- CLI and curated MCP adapters for plan/create/assemble/simulate/synthesize.

## Data Flow

```text
prompt or DesignIR
-> deterministic digital planner
-> validated Tiny8 template generation
-> HDL/testbench
-> optional simulation and synthesis
-> structured reports
```

## Missing Roadmap Gates

- Generic and/mux/adder/counter/shift-register/FSM/PWM vertical slices.
- Verilator integration and VCD parsing.
- SymbiYosys runner, property templates, and counterexample summaries.
- Tiny8 emulator, LED MMIO renderer, and emulator-versus-RTL comparison.
- Snake/Pong/Tetris-lite demos and game invariants.

LTspice is not the processor-logic backend. Digital correctness comes from HDL
simulation, synthesis, formal checks, and golden-model comparison.
