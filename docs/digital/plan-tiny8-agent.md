# Tiny8 CPU — Phase 12 Plan

**Status:** Phase A (spec). Code begins in Phase B.
**Companion docs:** [`../adr/0004-hybrid-hdl-spice.md`](../adr/0004-hybrid-hdl-spice.md),
[`./toolchain.md`](./toolchain.md).
**Branch:** `phase-12-tiny8`.

## 1. Summary

`ltspice-ai-agent` becomes a **multi-domain engineering agent**:
LTspice stays for analog/power/peripheral, HDL is added for
digital, both under one Python core and one MCP surface. The
LLM/agent proposes intent and parameters; the Python core
validates the IR, selects templates, writes files, runs the
toolchain, and refuses anything that does not fit a vetted
shape.

v1 target: **Tiny8 CPU** — a verified 8-bit accumulator CPU
with a fixed ISA, a demo program that adds two numbers and
halts, and a Yosys-clean synthesis. Roadmap (not v1):
Tiny8 SoC, RV32I subset, full mini PC.

## 2. Evidence base

- [Analog Devices LTspice](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html) — confirms LTspice is analog/mixed-signal, not the right tool for processor logic.
- [Verilator User Guide](https://verilator.org/guide/latest/) — verilating, linting, simulation of Verilog models.
- [Icarus Verilog](https://steveicarus.github.io/iverilog/) — `iverilog` + `vvp` as a portable v1 simulator.
- [Yosys](https://yosyshq.readthedocs.io/projects/yosys/en/latest/) — synthesis sanity check (`hierarchy`, `proc`, `opt`, `stat`).
- [lowRISC Verilog Style Guide](https://github.com/lowRISC/style-guides/blob/master/VerilogCodingStyle.md) — naming and style baseline.
- [FuseSoC CAPI2](https://fusesoc.readthedocs.io/en/stable/ref/capi2.html) — packaging roadmap (not v1).
- [cocotb](https://docs.cocotb.org/) — Python co-sim roadmap (not v1).
- [RISC-V RV32I](https://docs.riscv.org/reference/isa/unpriv/rv32.html) — full ISA roadmap, not v1.
- [OWASP Top 10 for LLM Apps](https://genai.owasp.org/llm-top-10/) — LLM01/LLM05/LLM06 apply; the design surfaces in §6.3/§6.4 are the mitigations.

## 3. Core decisions

- **Hybrid HDL + SPICE.** Digital core = Verilog-2001 from a
  `DesignIR`. Analog companion = LTspice for clock/reset/
  power/IO drivers. Same Python core, same MCP contract.
- **Tiny8 only in v1.** v1 is not RISC-V, not a full mini PC,
  not Linux-capable. Anything larger is a structured
  `RoadmapSuggestion`, not a refusal and not a build.
- **No LLM in v1.** The digital planner
  (`ltagent.digital_planner`) is rule-based like the analog
  one. HDL only ever comes from a `DesignIR` + a template.
  Free-form LLM-emitted Verilog is forbidden.
- **MCP boundary preserved.** New tools wrap the Python core
  only. No `run_shell`, no `execute_python`, no generic
  `read_file`/`write_file`, no caller-supplied simulator
  command.

## 4. Tiny8 architecture (v1)

- Data: 8-bit. Address: 8-bit. Instruction: 16-bit.
- Program memory: 256 words. Data memory: 256 bytes.
- Instruction layout: `opcode[15:12] | mode[11:8] | operand[7:0]`.
  `mode` is reserved at 0 in v1.
- State: `pc` (8-bit), `acc` (8-bit), `zero_flag` (1-bit),
  `halted` (1-bit).
- Reset: synchronous `rst`; `pc=0`, `acc=0`, `zero_flag=1`,
  `halted=0`.

## 5. Tiny8 ISA (v1)

Accumulator-based. No register file.

| Opcode | Mnemonic | Semantics |
|---|---|---|
| `0x0` | `NOP` | no state change, `pc++` |
| `0x1` | `LDI imm` | `acc = imm` |
| `0x2` | `LDA addr` | `acc = ram[addr]` |
| `0x3` | `STA addr` | `ram[addr] = acc` |
| `0x4` | `ADD addr` | `acc = acc + ram[addr]` (mod 256) |
| `0x5` | `SUB addr` | `acc = acc - ram[addr]` (mod 256) |
| `0x6` | `AND addr` | `acc = acc & ram[addr]` |
| `0x7` | `OR addr`  | `acc = acc | ram[addr]` |
| `0x8` | `XOR addr` | `acc = acc ^ ram[addr]` |
| `0x9` | `JMP addr` | `pc = addr` |
| `0xA` | `JZ addr`  | if `zero_flag` then `pc = addr` else `pc++` |
| `0xB` | `JNZ addr` | if not `zero_flag` then `pc = addr` else `pc++` |
| `0xC` | `OUT port` | `out_valid=1`, `out_port=port`, `out_data=acc` |
| `0xD` | `IN port`  | `acc = in_data` when `in_valid` |
| `0xE` | reserved  | `illegal_instruction` |
| `0xF` | `HALT`    | `halted = 1` |

Rules: arithmetic wraps mod 256; `zero_flag` updates on any
instruction that writes `acc`; `HALT` stops `pc` advancement;
unsupported prompt requests (pipeline, cache, MMU, OS, USB,
GPU, full mini PC) return structured refusal or roadmap
guidance.

## 6. Public interfaces

### 6.1 CLI

```bash
ltagent digital doctor --json
ltagent digital plan "<prompt>" --json
ltagent digital create "<prompt-or-design-ir>" --json
ltagent digital assemble programs/demo.asm --out programs/demo.mem --json
ltagent digital simulate <project-dir> --json
ltagent digital synth-check <project-dir> --json
ltagent digital inspect <project-dir> --json
```

Behaviour:

- `digital doctor` reports `iverilog`, `vvp`, `verilator`,
  `yosys`, `gtkwave` status; missing tools are warnings.
- `digital plan` returns `DesignIR` or a structured
  `PlannerRefusal` / `ClarificationRequest` /
  `RoadmapSuggestion`.
- `digital create` writes the full artefact set and runs
  static validation; simulation is opt-in via flag.
- `digital simulate` prefers `iverilog+vvp`. Missing tools
  are a structured `skipped` unless `--strict`.
- `digital synth-check` uses Yosys if installed; missing is
  `skipped` unless `--strict`.
- All commands honour the existing JSON envelope: `success`,
  `command`, `message`, `data`, `warnings`, `errors`.

### 6.2 Python modules

```text
src/ltagent/digital_ir.py        # Pydantic DesignIR + JSON Schema
src/ltagent/digital_planner.py   # prompt -> DesignIR / refusal / roadmap
src/ltagent/digital_templates.py # Verilog module + testbench templates
src/ltagent/digital_generator.py # DesignIR + template -> files
src/ltagent/digital_asm.py       # tiny8 assembler
src/ltagent/digital_runner.py    # iverilog / verilator / yosys runner
src/ltagent/digital_project.py   # workspace orchestrator
src/ltagent/digital_reports.py   # sim.json / synth.json / lint.json
```

Boundaries:

- `digital_ir.py` — no filesystem, no subprocess.
- `digital_planner.py` — no file writes.
- `digital_generator.py` — deterministic, templates only.
- `digital_runner.py` — list args only, no shell.
- `digital_project.py` — orchestrates writes under workspace.
- `mcp_server.py` — thin wrappers.

### 6.3 `DesignIR` v0.1 (sketch)

```json
{
  "schemaVersion": "0.1",
  "domain": "digital",
  "kind": "tiny8_cpu",
  "name": "tiny8_cpu_demo",
  "description": "8-bit accumulator CPU demo",
  "clock": { "name": "clk", "frequencyHz": 1000000 },
  "reset": { "name": "rst", "activeHigh": true, "synchronous": true },
  "cpu": {
    "dataWidth": 8,
    "addressWidth": 8,
    "instructionWidth": 16,
    "architecture": "accumulator",
    "isa": "tiny8_v0"
  },
  "memory": { "romWords": 256, "ramBytes": 256 },
  "io": { "ports": [{ "name": "out0", "direction": "output", "width": 8 }] },
  "program": {
    "source": "demo.asm",
    "entry": 0,
    "expectedHaltCyclesMax": 200
  },
  "verification": {
    "expected": { "halted": true, "acc": 42, "memory": { "16": 42 } }
  },
  "artifacts": { "rtl": [], "testbenches": [], "reports": [] },
  "metadata": { "createdBy": "ltagent", "source": "digital_planner" }
}
```

Validation rules: `domain` must be `digital`; v1 `kind` must
be `tiny8_cpu` or `tiny8_soc`; widths must be `8/8/16`; ROM
and RAM sizes in `1..256`; `name` matches the existing safe
slug; no arbitrary HDL body in the IR; no path may escape the
workspace; `program.source` is relative and ends with `.asm`;
unknown fields are rejected.

### 6.4 MCP surface (new)

Tools:

```text
plan_digital_system
create_digital_project
assemble_tiny8_program
simulate_hdl_project
synth_check_hdl_project
inspect_digital_project
```

Resources:

```text
ltagent://digital/capabilities
ltagent://digital/tiny8/spec
ltagent://digital/templates
ltagent://projects/{project_id}/digital-manifest
ltagent://projects/{project_id}/rtl
ltagent://projects/{project_id}/verification-report
```

Constraints (carried forward from Phase 10): no generic file
tools, no shell, no caller-supplied simulator command, output
JSON-serialisable, descriptions include the v1 scope so
callers know what is and is not a v1 build.

## 7. Planner

`plan_digital_prompt(text)` returns one of:

- `DesignIR` — vetted prompt, schema-valid.
- `PlannerRefusal` — `code` like `UNSUPPORTED_PROMPT`,
  `MISSING_PARAM`, `INVALID_VALUE`, `AMBIGUOUS_PROMPT`,
  `PROMPT_INJECTION`.
- `ClarificationRequest` — supported prompt with a missing
  program goal; offers a default (e.g. "add 20 + 22 and halt").
- `RoadmapSuggestion` — supported direction but not v1
  (RV32I, full mini PC, USB, HDMI, Linux, GPU, ...).

Categories the planner recognises:

- `analog_circuit` — route to the existing analog planner.
- `tiny8_cpu` — Tiny8 CPU.
- `tiny8_soc` — Tiny8 + simple memory-mapped IO wrapper.
- `riscv_request` / `mini_pc_full` — `RoadmapSuggestion`.
- `ambiguous` — `ClarificationRequest`.
- `unsafe` (prompt injection, filesystem or shell request)
  — `PlannerRefusal` with `code=PROMPT_INJECTION`.

## 8. HDL generation rules

- Verilog-2001 only (no SystemVerilog classes/packages).
- Style follows the lowRISC guide: descriptive
  `snake_case`, `clk`/`rst` first, nonblocking in
  sequential `always`, blocking in `always @*`, no inferred
  latches, no unsized constants in datapath.
- Every generated file has a header: generated by
  `ltspice-ai-agent`, schema version, do-not-edit notice.
- Modules: `tiny8_alu`, `tiny8_control`, `tiny8_rom`,
  `tiny8_ram`, `tiny8_cpu`, `tiny8_top`, and
  `tb_tiny8_top`.
- Testbench: clock generator, reset sequence, cycle
  timeout, pass/fail via `$display` markers the runner
  parses.

## 9. Assembler

v1 supports:

- Mnemonics for the 15 valid opcodes + 1 reserved.
- Labels (for `JMP`, `JZ`, `JNZ`).
- Comments: `;` or `#`.
- Numbers: decimal, `0x` hex, `0b` binary.
- Stable error codes: `ASM_UNKNOWN_OPCODE`,
  `ASM_BAD_OPERAND`, `ASM_LABEL_DUPLICATE`,
  `ASM_LABEL_UNKNOWN`.

Output: 16-bit hex words in `.mem`, one per line, no
address prefix.

## 10. Toolchain runner

- `digital doctor` reports `iverilog`, `vvp`, `verilator`,
  `yosys`, `gtkwave` and emits the install hint.
- `digital simulate` runs `iverilog` + `vvp` on the
  generated testbench; captures stdout/stderr (capped),
  parses pass/fail, writes `reports/sim.json`.
- `digital synth-check` runs Yosys `hierarchy`/`proc`/`opt`/
  `stat`; writes `reports/synth.json`. Missing Yosys is
  `skipped` unless `--strict`.
- Optional: `verilator --lint-only` as a separate
  `reports/lint.json` step. Missing Verilator is a warning.
- All subprocess calls use list args. No `shell=True`.
  Tool names come from a config allowlist. Output is
  capped; tails included in `result.json`.

## 11. Manifest and result contracts

`manifest.json`:

```json
{
  "schemaVersion": "0.1",
  "projectKind": "digital",
  "designKind": "tiny8_cpu",
  "topModule": "tiny8_top",
  "sourceIr": "design.ir.json",
  "rtlFiles": [
    "rtl/tiny8_alu.v", "rtl/tiny8_control.v", "rtl/tiny8_cpu.v",
    "rtl/tiny8_ram.v", "rtl/tiny8_rom.v", "rtl/tiny8_top.v"
  ],
  "testbenches": ["tb/tb_tiny8_top.v"],
  "programs": ["programs/demo.asm", "programs/demo.mem"],
  "reports": ["reports/sim.json", "reports/synth.json"],
  "createdBy": "ltagent"
}
```

`result.json`:

```json
{
  "schemaVersion": "0.1",
  "projectKind": "digital",
  "status": "pass|fail|skipped|partial",
  "lint":      { "status": "pass|fail|skipped", "tool": "verilator" },
  "simulation":{
    "status": "pass|fail|skipped",
    "tool":   "iverilog+vvp",
    "cycles": 12,
    "halted": true,
    "observed": { "acc": 42, "memory": { "17": 42 } }
  },
  "synthesis":{ "status": "pass|fail|skipped", "tool": "yosys" },
  "warnings": [], "errors": []
}
```

## 12. Implementation phases

| Phase | Scope | Acceptance |
|---|---|---|
| **A — Spec & ADR** | This doc, ADR 0004, AGENTS.md update, toolchain doc | All four documents present; AGENTS.md declares Phase 12 current. **In progress.** |
| **B — DesignIR + planner** | `digital_ir.py`, `digital_planner.py`, `ltagent digital plan`, three example IRs | Valid examples load and round-trip; invalid IR rejects bad width / bad path / unknown fields / unsupported kind; supported prompts create valid IR; mini PC prompt returns clarification; RISC-V prompt returns roadmap. |
| **C — Generator + assembler** | `digital_templates.py`, `digital_generator.py`, `digital_asm.py`, `ltagent digital create`, `ltagent digital assemble` | Generated project has the full file set; generated HDL is deterministic (snapshot test); assembler resolves labels and emits `.mem`; no generated path escapes the workspace. |
| **D — Runner + reports** | `digital_runner.py`, `digital_reports.py`, `digital doctor`, `simulate`, `synth-check` | Missing tools return structured skip / fail; with tools installed, demo program halts and matches expected `acc` and `memory`; reports are JSON and stable; argv list is shell-free. |
| **E — MCP** | 6 new tools, 4 new resources, MCP tests for `tiny8_cpu` only | Tool count updated; resource count updated; `tool_create_digital_project` matches `ltagent digital create` artefacts; MCP refuses unsupported mini PC / RISC-V with roadmap guidance; no dangerous tool names. |
| **F — Roadmap docs** | Only docs; no code | Roadmap clearly marked as future, not implemented. |

Out-of-scope for v1: RV32I, Tiny8 SoC, full mini PC, USB,
HDMI, GPU, Linux-capable, FuseSoC packaging, cocotb advanced
verification, LTspice co-simulation of the digital core.

## 13. Testing strategy

Unit (always run):

- `tests/test_digital_ir.py` — valid/invalid width, bad
  path, unsupported kind, unknown field rejection.
- `tests/test_digital_planner.py` — EN/ID Tiny8 prompts,
  mini PC clarification, RISC-V roadmap, prompt injection
  refusal.
- `tests/test_digital_asm.py` — opcode encoding, labels,
  hex/decimal/binary operands, error codes.
- `tests/test_digital_generator.py` — file list, snapshot,
  module names, top module.
- `tests/test_digital_project.py` — full artefact set,
  path traversal rejected, `result.json` written.
- `tests/test_digital_runner.py` — missing tools return
  structured skip/fail; argv list; stdout/stderr cap.
- `tests/test_mcp_digital.py` — tool/resource counts,
  no dangerous names, CLI parity for `create`.

Integration (gated by tool availability):

- `@pytest.mark.hdl_sim` — runs `digital simulate` if
  Icarus is installed.
- `@pytest.mark.hdl_synth` — runs `digital synth-check` if
  Yosys is installed.

Commands:

```bash
pytest
pytest -m hdl_sim
pytest -m hdl_synth
ruff check .
mypy src
python -m build
```

Manual acceptance (per the Phase 11 pattern):

```bash
ltagent digital doctor --json
ltagent digital plan "buat mini processor 8-bit sederhana" --json
ltagent digital create "buat mini processor 8-bit sederhana yang menjumlahkan 20 dan 22" --json
ltagent digital simulate projects/<id> --json
ltagent digital synth-check projects/<id> --json
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

## 14. Risk register

- **"I asked for a mini PC, I got Tiny8."** — `RoadmapSuggestion` is loud, not silent. `ltagent digital plan` and the MCP `inspect_digital_project` resource both surface the roadmap.
- **LLM invents broken HDL.** — impossible by construction; HDL only comes from templates, never from prompt text or LLM output.
- **Digital toolchain missing locally.** — `doctor` reports; `simulate`/`synth-check` are `skipped` in non-strict mode.
- **Scope creep into RISC-V / Linux.** — explicit `RoadmapSuggestion`; the planner refuses the prompt as not-v1.
- **MCP regression.** — same curated-tool rule as Phase 10; no new generic tools; tests assert tool and resource counts and forbid dangerous names.
- **"Toy" perception.** — docs and CLI must say "verified 8-bit accumulator CPU" and "Tiny8 is not a mini PC". The roadmap is the answer for users who want more.

## 15. Definition of done

Phase 12 is done when:

- `ltagent digital create "buat mini processor 8-bit sederhana" --json` creates the full project.
- The generated project contains `design.ir.json`, `manifest.json`, `result.json`, `rtl/`, `tb/`, `programs/`, `reports/`, and `spice/`.
- `digital simulate` passes when Icarus is installed, or returns a structured `skipped` when not.
- `digital synth-check` passes when Yosys is installed, or returns a structured `skipped` when not.
- MCP can plan / create / inspect digital projects through the new curated tools.
- Existing analog commands still work; the Phase 11 acceptance suite still passes.
- `pytest`, `ruff check .`, `mypy src`, and `python -m build` all pass.
- Docs make Tiny8 scope, out-of-scope mini PC / RISC-V, and the hybrid approach unambiguous.

## 16. Assumptions

- Phase 11 wrap-up is committed at HEAD (`phase-12-tiny8`).
- Toolchain install is optional; missing tools must not
  crash normal tests.
- Generated HDL is Verilog-2001 for compatibility.
- LTspice stays in scope as the analog companion, not the
  primary backend for processor logic.
