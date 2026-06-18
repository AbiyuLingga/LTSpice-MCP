# Phase 12 Roadmap — Beyond Tiny8 CPU

This document is **roadmap only**. No code in the current branch
implements any of it. Every entry is gated on the Phase 12 v1
acceptance criteria being met first (see
[`./plan-tiny8-agent.md`](./plan-tiny8-agent.md) §15 and the repo
AGENTS.md).

The order below is the order they would land if v1 lands cleanly.
Earlier phases block later ones.

## R1. Tiny8 SoC (memory-mapped IO)

**Scope.** Add an `io` block to the Tiny8 SoC wrapper: address
decode that maps a 256-byte IO region onto the bottom of the
data memory address space, plus a small bank of memory-mapped
registers (timer, GPIO, UART TX). The generated HDL adds a new
`tiny8_soc_top.v` module and a `tiny8_bus` module; the existing
CPU is unchanged.

**Why this is the first step.** v1 is a CPU on a bus-less
memory. The SoC gives the CPU a bus, which is the smallest
plausible "computer" shape and the cleanest test of the IR
schema for memory-mapped devices.

**Acceptance gate.** Tiny8 v1 acceptance is met; `tiny8_soc` is
already a reserved ``DesignIR.kind`` (it validates but no
template emits it); the existing ``tiny8_soc_top.v`` template
is a no-op placeholder.

## R2. cocotb advanced verification

**Scope.** Replace the hand-written Verilog testbench with a
[cocotb](https://docs.cocotb.org/) Python coroutine driver. The
cocotb test asserts the same Tiny8 demo contract (halt within
N cycles, expected ACC, expected RAM), but can also exercise
edge cases (interrupts once R1 is in, dual-port memory, reset
behaviour) without hand-coding more Verilog.

**Why.** Phase 12 v1 has a single testbench for the demo. The
cocotb layer adds parameterised tests and Python-level
assertion matching that scales to the SoC.

**Acceptance gate.** R1 is in; cocotb is in `[dev]`.

## R3. RV32I subset

**Scope.** A new `kind: rv32i` (reserved in v0.1 of the IR) with
a 32-bit datapath, 32 general-purpose registers, the official
[RV32I base integer ISA](https://docs.riscv.org/reference/isa/unpriv/rv32.html).
The generator would emit a riscv-compatible `rv32i_top.v`
instead of `tiny8_top.v`, with the same `simulate` / `synth-check`
/ `inspect` flow.

**Why.** Tiny8 is intentionally tiny. RV32I is the next rung
on the "real CPU" ladder: spec-driven, compiler-targetable,
testable against the official RISC-V test suite. Picking the
ISA rather than inventing one keeps the design auditable.

**Acceptance gate.** R1 + R2 are in; we have a real
benchmark target (compile C with riscv32-unknown-elf-gcc,
run on the simulator, diff against QEMU's `-cpu rv32`).

## R4. FuseSoC packaging

**Scope.** Wrap each generated Tiny8 project in a
[FuseSoC](https://fusesoc.readthedocs.io/en/stable/) `.core`
file (CAPI2) so the same artefacts can drive Icarus, Verilator,
Yosys, and (later) commercial simulators without bespoke glue.

**Acceptance gate.** R1–R3 are in; FuseSoC is in `[dev]`.

## R5. Full mini PC reference design

**Scope.** A multi-IP SoC: RV32I CPU + memory-mapped IO bus +
timer + UART + a tiny VGA-style text framebuffer + a simple
interrupt controller + a boot ROM. The CPU still runs the
existing ISA; the bus is the new piece.

This is **not** "Linux-capable" — that requires an MMU, a
memory hierarchy, and a BIOS the size of which dwarfs the
rest of the design. It is "a CPU that can run a Forth or a
small RTOS, with a screen and a keyboard, on an FPGA". The
FMC (FPGA Mezzanine Card) is the right form factor; the
plan does not include board files.

**Why last.** Each prior step tightens the IR schema, the
templates, the testbench, and the toolchain integration. The
mini PC is the integration test of all of them.

**Acceptance gate.** R1–R4 are in; we have a board file
contributor and a clear RFP.

## Out of scope for the foreseeable future

- **Linux-capable SoC.** Requires MMU, supervisor mode, page
  tables, a full toolchain. Multi-year project. A real PR.
- **Multi-core.** Same order of magnitude as the Linux path.
- **GPU / DSP / NPU.** Different problem space; the agent's
  templates are not the right surface.
- **Networking (Ethernet / WiFi / Bluetooth).** Hardware IP
  and PHY selection; not a templating problem.
- **USB / HDMI.** Same: not a templating problem.
- **External CPU ISAs beyond RV32I.** The plan explicitly
  avoids inventing ISAs. RISC-V is the only spec-driven path
  we are willing to take.

## How a request for one of these surfaces today

* A prompt mentioning any of the above keywords (RISC-V, mini
  PC, USB, HDMI, GPU, Linux, …) returns a ``RoadmapSuggestion``
  from the planner. The response enumerates the relevant
  ``proposedPhases`` and points the caller to this document.
* The MCP tools do not introduce new magic. They return the
  same roadmap.
* The CLI's `ltagent digital plan` and `ltagent digital create`
  surface the same roadmap in the JSON contract.
