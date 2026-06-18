# ADR 0004 — Hybrid HDL + SPICE, Tiny8 CPU as v1 digital target

- Status: Accepted (Phase 12 current)
- Date: 2026-06-19
- Deciders: ltagent maintainers
- Phase: 12 (Tiny8 CPU)

## Context

`ltspice-ai-agent` ships an analog-first product: `CircuitIR`,
`netlist.py`, `runner.py`, `.asc` writer, and the curated MCP
surface all assume the artefact is a SPICE netlist plus a
schematic. Phase 11 closed the analog library (10 official
templates, 3 passive + 7 analog).

The next natural pressure is "design a small CPU / mini PC".
The naive answer — "let the LLM write Verilog and ship it" —
breaks three of the project's invariants:

1. **No free-form LLM artefacts.** Phase 8/9 explicitly state
   the planner is rule-based; the same rule must apply to HDL.
2. **MCP tools are curated, not generic.** `run_shell`,
   `execute_python`, generic `read_file`/`write_file` are
   forbidden. Free-form HDL invites all three.
3. **The Python core owns validation and generation.** Letting
   the agent dump Verilog into a project file inverts this.

The naive analog answer — "use LTspice for the CPU" — is
worse. LTspice is a SPICE-class simulator: combinational
gates, sequential logic, and FSMs are out of scope, and a
multi-instruction CPU is not what the tool is for. The
[LTspice product page](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html)
is explicit that the tool targets analog/mixed-signal.

The naive full-stack answer — "build an RV32I Linux-capable
SoC v1" — is the request, not a v1. It is multi-year
engineering; the [RISC-V RV32I spec](https://docs.riscv.org/reference/isa/unpriv/rv32.html)
alone is enough to know the compiler toolchain is its own
project.

## Decision

Adopt a **hybrid HDL + SPICE** pipeline and ship **Tiny8 CPU**
as the v1 digital target. The boundaries are explicit:

- **Digital flow.** Verilog-2001 generated from a `DesignIR`
  schema (`ltagent.digital_ir.DesignIR`), template-backed
  (`ltagent.digital_templates.py`), simulated with
  Icarus (`iverilog` + `vvp`), lint-checked optionally with
  Verilator, and synthesis-sanity-checked with Yosys.
  See [Verilator](https://verilator.org/guide/latest/),
  [Icarus](https://steveicarus.github.io/iverilog/),
  [Yosys](https://yosyshq.readthedocs.io/projects/yosys/en/latest/).
- **Analog companion.** LTspice (via the existing `runner.py`
  + Wine) handles the clock source, reset RC, regulator, and
  IO driver models that surround the digital core. This is
  the only reason LTspice stays in the digital story.
- **MCP surface.** New curated tools wrap the Python core;
  no `run_shell`, no generic `read_file`/`write_file`, no
  simulator command coming from the caller.
- **No LLM in v1.** The digital planner
  (`ltagent.digital_planner`) is rule-based like the analog
  one. Tiny8 ISA + memory map + IO map are fixed in
  `docs/digital/plan-tiny8-agent.md`. The planner maps a
  small, vetted prompt set to that fixed shape and returns
  structured refusals for everything else.

The Tiny8 v1 product is a verified 8-bit accumulator CPU:
`pc`, `acc`, `zero_flag`, `halted`, 256-word ROM, 256-byte
RAM, 16-bit instruction word, IN/OUT ports, an
accumulator-based ISA, and a fixed "add two numbers and
halt" demo. The acceptance for v1 is that the demo
assembles, the testbench passes under Icarus, and Yosys
synthesises it without errors when installed. Full mini PC
and RV32I are explicitly roadmap, not v1.

## Consequences

**Gained.**

- A v1 product that runs end-to-end on this host (no
  `iverilog`/`yosys` needed for tests, present-and-warn for
  sim/synth). The acceptance suite passes without
  external tooling, matching the Phase 11 precedent.
- The Phase 8 invariant ("no LLM-backed planner") is
  preserved. The new `digital_planner` follows the same
  rule-based pattern.
- The MCP security boundary is preserved. No new generic
  tools, no `run_shell`. The new digital tools wrap
  deterministic Python functions and emit the standard
  JSON contract.
- A clean, reviewable design surface: every generated
  file is a `DesignIR` field combined with a template
  constant. The generator never reads from the LLM.

**Lost / constrained.**

- Free-form CPU requests ("like Intel NUC", "with USB",
  "running Linux", "RISC-V") become structured
  `RoadmapSuggestion` responses, not refusals and not
  implementations. The CLI must say so explicitly so
  callers understand what v1 is and is not.
- Generated HDL is Verilog-2001 only (no SystemVerilog
  classes/packages). This trades modern conveniences for
  maximum toolchain compatibility (Icarus, Verilator,
  Yosys all support it well).
- The assembler is intentionally tiny (no macros, no
  include, no `.data`/`.bss` split). When v1 graduates to
  Tiny8 SoC and beyond, the assembler is the part that
  grows first.
- Tiny8 is an educational CPU, not a "real" mini PC.
  Anyone who runs `ltagent digital create "mini PC"` and
  gets a Tiny8 back will be disappointed if the docs do
  not make this loud.

**Operational.**

- Icarus, Verilator, and Yosys are optional. Their
  absence is a structured warning, not a failure, except
  in `--strict` mode on `simulate` / `synth-check`.
- `ltagent digital doctor` reports tool status and the
  install hint, matching the analog doctor's style.
- The Phase 12 work lives on the `phase-12-tiny8` branch
  and follows the same one-commit-per-change cadence as
  prior phases.

## Alternatives considered

1. **RV32I v1.** The spec is mature and the tooling is
   excellent, but the v1 is "build the compiler and
   the testbench" before "demonstrate the CPU does
   anything". Tiny8 is one order of magnitude smaller
   and demonstrates the same pipeline (IR -> template
   -> generate -> simulate -> synth).
2. **Pure LLM-emitted HDL.** "Ask the LLM, write the
   file." Fastest to demo, but breaks invariants 1-3
   above and invites OWASP LLM01 (prompt injection) and
   LLM05 (improper output handling) right where the
   security boundary is the thinnest. Rejected.
3. **LTspice-only.** Run the whole CPU in LTspice by
   using `B` sources and `A` devices. Possible for
   toy demos, but the layout scales poorly, the test
   framework is absent, and the schematic becomes
   unreadable beyond ~10 gates. Rejected for the same
   reason we do not write Python in netlist comments.
4. **No analog companion.** Pure HDL, no LTspice
   involvement. Cleaner separation, but loses the
   ability to model the reset RC, regulator, and IO
   driver in the same project. The analog companion is
   cheap and stays in scope.
