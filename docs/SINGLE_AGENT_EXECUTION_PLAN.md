# Single-Agent Roadmap Execution Plan

> Workbench audit note, 2026-06-21: this document remains the long-term engine
> roadmap. It is not evidence that Workbench v1 Phases 0-11 are complete. The
> current implementation status and missing end-to-end gates are recorded in
> `docs/REPO_AUDIT.md`.

Date: 2026-06-19
Canonical roadmap: `docs/AI_HARDWARE_AGENT_ROADMAP.md`
Execution owner: one main AI agent

## Milestone Status

- Milestone 0: complete and validated on 2026-06-19.
- Milestone 1: next; acceptance-gap analysis in progress.
- Milestones 2-12: gated on their prerequisites.

## 1. Understanding

Evolve the existing LTspice-oriented CLI/MCP project into a deterministic
hardware-design pipeline covering analog, digital, Tiny8/system, mixed-signal,
verification, optimization, repair, knowledge, and release-quality workflows.
All edits and decisions are serialized through one main agent. Parallelism is
allowed only for deterministic jobs such as test shards or simulation sweeps.

## 2. Current Context

The repository is not an empty starting point. It already has mature analog
Phases 0-11, a Tiny8-oriented Phase 12 implementation, and a Phase 13
live-editing/Math Core prototype. The current architecture centers on:

```text
validated IR
-> deterministic generator
-> bounded runner/parser
-> structured result/report
-> CLI
-> curated MCP adapter
```

Relevant current modules include `ir.py`, `netlist.py`, `asc.py`, `runner.py`,
`templates.py`, `digital_ir.py`, `digital_generator.py`, `digital_runner.py`,
`digital_project.py`, `live/`, `math_core/`, `cli.py`, and `mcp_server.py`.

## 3. Critical Review

- The roadmap's folder tree is a target model, not a migration requirement.
  Repackaging working modules before acceptance gaps are closed would create
  churn without user value.
- Existing Tiny8 code does not prove the generic Digital HDL MVP. Generic
  module generation, VCD parsing, and Verilator remain missing.
- Wrapper tests do not prove real EDA tools ran. Reports must distinguish
  unit/fake evidence, optional-tool skip, and real simulator evidence.
- Games and mixed-signal work must not begin before an emulator, LED renderer,
  generic HDL path, and formal/synthesis gates exist.
- The final roadmap is multi-month product scope. Progress is tracked by
  milestone acceptance, not by creating placeholder directories.

## 4. Options

### Option A: Restructure first

Move current modules into the roadmap's proposed package tree before adding
features.

- Pros: tree resembles the final diagram early.
- Cons: broad import churn, regression risk, no new validated capability.
- Complexity: high.
- Risk: high.
- Decision: rejected.

### Option B: Acceptance-gap closure

Keep stable modules, add missing contracts and vertical slices, and refactor
only when a real boundary needs it.

- Pros: preserves green behavior, produces measurable capability, easy rollback.
- Cons: temporary flat modules remain.
- Complexity: incremental.
- Risk: moderate to low per slice.
- Decision: selected.

## 5. Recommended Approach

Use contract-first, risk-first vertical slices. Each slice follows:

```text
test/spec
-> smallest implementation
-> targeted checks
-> full regression checkpoint
-> documentation/evidence
-> commit
```

MCP changes come last in a slice. No capability is described as supported
until its core API, CLI path, tests, and required real-tool evidence exist.

## 6. Implementation Plan

### Milestone 0: Repository alignment

1. Adopt the single-agent roadmap under `docs/`.
2. Publish a current repository audit and this execution plan.
3. Mark the old multi-agent supervisor plan as superseded.
4. Reconcile phase/status wording in README, AGENTS, SPEC, and MCP docs.
5. Add contribution and architecture boundary documentation.
6. Prove clean install, CLI/MCP help, tests, lint, types, and build.

Rollback: documentation-only commits can be reverted independently.

### Milestone 1: Analog MVP acceptance closure

1. Define a 20-case deterministic analog regression matrix.
2. Close the four MVP topology paths, including `led_resistor`.
3. Ensure each case produces IR, netlist, calculation, verification, and result.
4. Separate fake-runner tests from real LTspice integration evidence.
5. Record structured timeout/skip when LTspice cannot complete on this host.

### Milestone 2: ASC acceptance closure

1. Extend deterministic layout only for any missing Milestone 1 topology.
2. Add symbol/layout contract tests and deterministic golden checks.
3. Record manual LTspice-open evidence when the host permits it.

### Milestone 3: Templates and deterministic optimization

1. Preserve current official/candidate/rejected promotion gates.
2. Add a typed objective and bounded grid-sweep contract.
3. Rank candidates deterministically with stable tie-breaking.
4. Integrate one RC filter optimization path before generalization.

### Milestone 4: Generic Digital HDL MVP

Implement one module at a time: `and_gate`, `mux2`, adder, counter,
shift-register, FSM, then PWM. Each slice includes typed spec/IR, deterministic
Verilog, deterministic testbench, compile/sim result, and report. Add VCD
summary and Verilator only after the first Icarus-backed slices are stable.

### Milestone 5: Synthesis and formal

1. Generalize the existing Yosys wrapper for official digital templates.
2. Add structured SymbiYosys availability and bounded execution.
3. Add mux/counter/FSM properties and counterexample summaries.

### Milestone 6: Tiny8 and LED matrix

1. Implement a deterministic ISA emulator.
2. Define typed memory-map and LED-frame contracts.
3. Add blink, move-pixel, draw-square, and scroll-pattern programs.
4. Compare emulator traces with RTL simulation before promotion.

### Milestone 7: Games

Implement Snake-lite, Pong-lite, then Tetris-lite. Every game uses scripted
inputs, bounded execution, memory checks, deterministic frames, and invariants.

### Milestone 8: Mixed signal

Add a `SystemIR` contract and one PWM-plus-RC-filter example. Keep analog and
digital simulations separate initially, then produce one integrated report.

### Milestone 9: Advanced analog/power

Add simulation-only safety classification, then one topology per slice. Start
with a low-energy open-loop buck model; do not claim build readiness.

### Milestone 10: Planning and repair loop

Add `RequirementSpec`, failure taxonomy, bounded repair attempts, immutable
repair history, and final structured reports. The loop may edit only through
allowlisted operations.

### Milestone 11: Component knowledge

Add a sourced metadata registry for symbols, pinouts, models, formulas, and
known errors. Store citations/links and reject unknown pinouts by default.

### Milestone 12: Release quality

Add a fresh-user test matrix, examples gallery, template contribution guide,
documentation build, reproducibility checks, and release checklist.

## 7. Validation Plan

Every behavioral slice requires RED/GREEN tests, deterministic output checks,
path-safety regression, structured error assertions, Ruff, mypy, and relevant
pytest scopes. Milestone checkpoints additionally run the full suite and build.

External tool gates must capture:

- executable/version,
- exact bounded command category (not arbitrary shell),
- exit/timeout state,
- generated artifacts,
- parsed metrics,
- explicit skip reason when unavailable.

## 8. Approval Gate

The user's instruction to execute the named single-agent roadmap is treated as
approval for this milestone sequence. Architectural replacement, destructive
migration, new runtime dependencies, or unsafe physical-design scope still
requires a fresh explicit approval.

## Evidence Reviewed

- Local roadmap and repository files: primary implementation evidence.
- Current code graph: confirms real module boundaries and missing contracts.
- Current CI and test configuration: confirms existing quality gates.
- Roadmap source map: identifies official EDA documentation and research to
  verify immediately before each relevant later milestone.

External research was not repeated for Milestone 0 because its decisions are
repository-local documentation and status alignment. Later simulator, formal,
mixed-signal, optimization, and knowledge milestones require current official
documentation research before implementation.

## Evidence-Based Decisions

- Preserve CLI core/MCP adapter boundary: supported by current code and roadmap.
- Avoid repackaging-first migration: current tests and code graph show mature,
  interconnected modules whose behavior already matches the desired boundary.
- Defer games: roadmap acceptance depends on missing emulator/renderer/formal
  evidence.
- Use real-tool evidence labels: local doctor output shows optional tool absence.

## Remaining Assumptions

- LTspice batch execution may continue timing out on this host.
- External digital/formal tools may need installation before real acceptance.
- Some current Phase 13 prototype contracts may need migration after
  `RequirementSpec` and `SystemIR` are introduced.

## Research Gaps

- Exact current SymbiYosys installation/runtime behavior.
- Best constrained VCD parsing dependency versus a small internal parser.
- Mixed-signal synchronization approach for the first integrated example.
- License/provenance format for component model metadata.
