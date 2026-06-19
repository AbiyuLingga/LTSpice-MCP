# Supervisor Execution Plan

Created: 2026-06-19
Role: Codex Supervisor
Repository: `ltspice-ai-agent`
Current branch observed: `agent-6-mcp-tools`

This document is the supervisor plan requested by the LTSpice-MCP AI
Hardware Agent prompt pack. It is intentionally plan-first: no large
source rewrite should begin until this file is reviewed and the current
dirty worktree is either accepted, split, or reset by an explicit human
decision.

## Initial Supervisor Report

### Current Repository Status

- The repository already implements a local Python CLI plus optional
  stdio MCP server for LTspice-oriented analog workflows.
- The active checkout is not clean. `git status --short --branch`
  reported many modified and untracked files on `agent-6-mcp-tools`,
  including README, MCP docs, Phase 13 live-editing code, Math Core
  code, MCP wiring, and many new tests.
- The current runtime surface, when invoked through `main()` with
  `PYTHONPATH=src .venv/bin/python`, reports:
  - 24 curated MCP tools.
  - 14 curated MCP resources.
- `docs/AI_HARDWARE_AGENT_ROADMAP.md`, referenced by the prompt pack,
  does not currently exist. The closest local roadmap sources are:
  - `docs/PROJECT_PLAN.md`
  - `docs/digital/plan-tiny8-agent.md`
  - `docs/adr/0004-hybrid-hdl-spice.md`
  - `ltspice_file_based_live_editing_math_plan.md`
- Test discovery currently covers 1332 test outcomes. After repairing
  the local editable install and removing stale absolute paths in three
  template tests, the full suite reports 1331 passed and 1 skipped.
- The local `.venv` was repaired with `pip install -e ".[dev,mcp]"`
  during this planning pass. Normal entrypoints now work for
  `ltagent` and `ltagent-mcp`. `mypy` is validated through
  `.venv/bin/python -m mypy src`, which avoids stale script-wrapper
  issues.
- The system Python is also not a project runtime because it lacks
  `pydantic`.
- `.venv/bin/ltagent digital doctor --json` reports missing digital
  tools as structured warnings: `iverilog`, `vvp`, `verilator`,
  `yosys`, and `gtkwave`.

### Existing Capabilities

- Analog core:
  - `CircuitIR` validation.
  - SPICE value parsing.
  - Deterministic `.cir` netlist rendering.
  - Deterministic `.asc` schematic rendering for supported topologies.
  - Layout scoring.
  - LTspice runner with Wine/native command construction and structured
    timeout/error reporting.
  - `.log` / `.meas` parser and `result.json` builder.
- Template memory:
  - Official, candidate, and rejected states.
  - Seeded official template library.
  - Template matching, audit, evaluation, and promotion.
- Planner:
  - Rule-based analog prompt planner.
  - Rule-based digital planner for Tiny8 / roadmap / refusal outputs.
- Digital Phase 12 surface:
  - `DesignIR`.
  - Tiny8 assembler.
  - Deterministic Tiny8 project generator.
  - Icarus and Yosys runner wrappers with missing-tool skip behavior.
  - Digital CLI subcommands and MCP tools.
- Phase 13 prototype surface:
  - Circuit graph model.
  - Safe live edit operations.
  - Live project/snapshot/history support.
  - Math Core formulas, unit parsing, and standard-value helpers.
  - Live/math MCP tools.
- Security:
  - Central path/URI/slug validation in `ltagent.security`.
  - Curated MCP tools only.
  - No generic `run_shell`, `execute_python`, `read_file`, or
    `write_file` tool in the current MCP list.
  - MCP resources use `ltagent://` URIs and `.raw` exposure is blocked.
- CI:
  - `.github/workflows/ci.yml` runs install, ruff, mypy, pytest, and
    build on Python 3.11 and 3.12.

### Inconsistencies / Risks

- `AGENTS.md` still contains Phase 10 and Phase 12 wording that
  conflicts with README and current MCP runtime counts.
- `docs/SPEC.md` says Phase 11 is the current shipping phase and lists
  future Phase 12/13 meanings that no longer match the current branch.
- `docs/mcp_setup.md` has updated 24/14 summary text but stale section
  headings and troubleshooting text that still say 10 tools, 8
  resources, and 8 URIs.
- `docs/AGENT_LOCKS.md` points to
  `docs/ltspice_file_based_live_editing_math_plan.md`, but the file
  exists at repo root as `ltspice_file_based_live_editing_math_plan.md`.
- The prompt pack expects `docs/AI_HARDWARE_AGENT_ROADMAP.md`, but the
  repo has not adopted that canonical filename yet.
- The active branch contains broad uncommitted integration work. Any
  new agent must treat this as a shared integration branch and avoid
  source edits until file ownership is explicit.
- The digital toolchain is optional and currently absent locally. Do
  not claim HDL simulation or synthesis has been proven on this host.
- `docs/agent_reports/edit_ops.md` still contains one absolute
  `/home/abiyulinx/ltspice-ai-agent` command example. The failing
  hard-coded test paths were fixed in `tests/test_templates.py`.

### Recommended First Milestone

Milestone 0 should be **documentation and environment alignment only**.

Do not start new analog, digital, Tiny8, game, optimizer, or MCP tool
implementation until the repository has one current source of truth for:

- current phase/status,
- MCP tool/resource counts,
- roadmap filename and ownership,
- active branch/worktree integration state,
- validation commands and the required interpreter setup.

### Agent Assignment Table

| Agent | Scope | Allowed Files | Forbidden Files | Current Task | Status |
|---|---|---|---|---|---|
| Repo Auditor Agent | Audit stale docs, counts, package/runtime setup, dirty tree | `docs/REPO_AUDIT.md`, `docs/SUPERVISOR_EXECUTION_PLAN.md` | `src/`, `tests/`, `pyproject.toml`, `README.md` | Produce repo audit and cleanup checklist | Ready |
| Architecture Agent | Target architecture and boundary docs | `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`, `docs/MCP_TOOLS.md`, `docs/ANALOG_ENGINE.md`, `docs/DIGITAL_ENGINE.md` | `src/`, `tests/`, `README.md`, `pyproject.toml` | Reconcile AI Hardware Agent architecture with existing Phase 12/13 code | Blocked until audit |
| Analog Engine Agent | Existing analog IR/netlist/result/template path | `src/ltagent/ir.py`, `src/ltagent/units.py`, `src/ltagent/netlist.py`, `src/ltagent/result.py`, `src/ltagent/templates.py`, related tests | `src/ltagent/mcp_server.py`, digital modules, docs except assigned docs | No new task until Milestone 0 closes | Waiting |
| Schematic/Layout Agent | Deterministic ASC and layout checker | `src/ltagent/asc.py`, `src/ltagent/layout.py`, `src/ltagent/layout_checker.py`, related tests | MCP entry point, digital modules, README, pyproject | No new task until Milestone 0 closes | Waiting |
| Digital Engine Agent | Tiny8 `DesignIR`, generator, assembler, runners | `src/ltagent/digital_*.py`, `examples/digital/`, related tests | analog modules, MCP entry point, broad docs | Verify current Phase 12 scope after audit | Waiting |
| Verification Agent | Analog/digital verification reports and regression harness | `src/ltagent/live/verification.py`, `src/ltagent/live/measurements.py`, future `src/ltagent/verification/`, related tests | CLI/MCP entry points unless assigned | No new task until current prototype is audited | Waiting |
| System/Tiny8 Agent | Tiny8 system evolution, LED matrix, games roadmap | future `src/ltagent/systems/`, examples/games, related tests | current analog/MCP core, README, pyproject | Roadmap only; no game implementation yet | Waiting |
| MCP/CLI Agent | Curated CLI/MCP surface | `src/ltagent/cli.py`, `src/ltagent/mcp_server.py`, `src/ltagent/mcp_live_tools.py`, MCP/CLI tests, `docs/MCP_TOOLS.md` | analog/digital internals unless explicitly paired | Fix counts/docs only after audit approval | Waiting |
| Test/CI Agent | Test, lint, typecheck, CI, local env health | `tests/`, `.github/workflows/`, `pyproject.toml` | `src/`, README, broad docs | Audit remaining hard-coded absolute paths and script-wrapper assumptions | Waiting |
| Documentation Writer | User docs and roadmap | `README.md`, `docs/`, `examples/` | `src/`, `tests/`, `pyproject.toml` | Align docs with runtime surface after audit | Waiting |

### Files To Modify First

1. `docs/SUPERVISOR_EXECUTION_PLAN.md` - this file.
2. `docs/REPO_AUDIT.md` - next audit artifact.
3. Either create `docs/AI_HARDWARE_AGENT_ROADMAP.md` or explicitly
   document that `docs/PROJECT_PLAN.md` remains canonical.
4. `docs/mcp_setup.md` - stale 10/8 headings and troubleshooting text.
5. `docs/SPEC.md` - stale phase status.
6. `AGENTS.md` - stale Phase 10/12 language versus current branch.
7. `docs/AGENT_LOCKS.md` - stale path to the live-editing plan file.

Do not edit `src/` until the above documentation alignment work is
accepted or explicitly postponed.

### Tests To Run

Minimum after documentation-only edits:

```bash
git diff --check
.venv/bin/python -m pytest --collect-only -q
```

Runtime surface checks:

```bash
.venv/bin/ltagent-mcp --list-tools
.venv/bin/ltagent-mcp --list-resources
.venv/bin/ltagent digital doctor --json
```

Full gates before merge:

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m build
```

Use `.venv/bin/python -m mypy src` for typecheck validation if the
standalone `mypy` wrapper is ever stale.

## Validation Performed

Commands run after creating this plan and repairing the local editable
install:

```bash
git diff --check
.venv/bin/python -m pytest tests/test_templates.py -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m build
.venv/bin/ltagent-mcp --list-tools
.venv/bin/ltagent-mcp --list-resources
.venv/bin/ltagent digital doctor --json
```

Results:

- `git diff --check`: clean.
- `tests/test_templates.py`: 56 passed.
- Full pytest: 1331 passed, 1 skipped, 28 warnings.
- Ruff: clean.
- Mypy: clean, 51 source files.
- Build: sdist and wheel built successfully.
- MCP: 24 tools and 14 resources.
- Digital doctor: success with warnings for missing external digital
  tools (`iverilog`, `vvp`, `verilator`, `yosys`, `gtkwave`).

### Next 5 Concrete Tasks

1. Create `docs/REPO_AUDIT.md` with a line-item audit of stale docs,
   dirty worktree files, missing roadmap file, MCP counts, and local
   environment notes.
2. Decide the canonical roadmap filename:
   `docs/AI_HARDWARE_AGENT_ROADMAP.md` versus `docs/PROJECT_PLAN.md`.
   If the prompt-pack name is adopted, create it as a concise index
   that links to the existing project, digital, ADR, and live-editing
   plans instead of duplicating all content.
3. Fix the obvious docs drift in `docs/mcp_setup.md`: update section
   headings and troubleshooting from 10/8 to 24/14 and list the digital
   plus live/math tools separately.
4. Update `docs/SPEC.md` and `AGENTS.md` so phase status matches the
   integrated branch: Phase 12 complete and Phase 13 prototype present,
   while future optimizer/UI/game work remains planned.
5. Audit remaining absolute paths and stale local-machine references,
   especially in agent reports and workflow snippets.

## Current Codebase Context

### Relevant Files Inspected

- `README.md`
- `pyproject.toml`
- `AGENTS.md`
- `MCP.md`
- `docs/PROJECT_PLAN.md`
- `docs/SPEC.md`
- `docs/security.md`
- `docs/mcp_setup.md`
- `docs/digital/plan-tiny8-agent.md`
- `docs/digital/toolchain.md`
- `docs/adr/0004-hybrid-hdl-spice.md`
- `docs/agent_reports/integration_final.md`
- `docs/AGENT_LOCKS.md`
- `docs/live_editing.md`
- `docs/math_core.md`
- `tests/test_templates.py`
- `src/ltagent/mcp_server.py` through the code graph
- Code graph architecture and search results for CLI/MCP/digital paths
- `tests/` collection output

### Current Data Flow

Analog flow:

```text
prompt or IR
-> rule-based planner / `CircuitIR`
-> validation
-> template match
-> netlist writer
-> ASC writer and layout checker
-> optional LTspice runner
-> log/result parser
-> result.json
-> optional template evaluator/promoter
```

Digital flow:

```text
prompt or DesignIR
-> rule-based digital planner
-> `DesignIR` validation
-> deterministic Tiny8 RTL/testbench/program generation
-> optional Icarus simulation
-> optional Yosys synthesis check
-> result/report JSON
-> MCP/CLI inspection
```

Live-editing prototype flow:

```text
live project
-> Circuit Graph
-> validated edit op
-> snapshot/history
-> graph-to-IR
-> existing analog generators
-> optional run-and-verify
-> Math Core calculation/explanation
```

### Existing Dependencies And Frameworks

- Python 3.11+
- Pydantic v2
- pytest, ruff, mypy, build as dev dependencies
- Optional `mcp[cli]` extra for `ltagent-mcp`
- Optional external tools:
  - LTspice/Wine for analog simulation
  - Icarus Verilog (`iverilog` + `vvp`) for HDL simulation
  - Verilator for lint
  - Yosys for synthesis checks
  - GTKWave for manual waveform viewing

## Critical Review

The prompt pack is directionally right, but it assumes an earlier repo
state than this branch actually has. Treating it literally would cause
duplicated roadmap docs and likely overwrite the existing Phase 12/13
integration work. The safer interpretation is:

- adopt the supervisor discipline,
- write the execution plan,
- audit the current branch,
- align documentation,
- then select the next smallest task.

Weak assumptions to challenge:

- "Milestone 0 comes before digital HDL" is historically true, but in
  this checkout digital and live-editing prototypes already exist.
  Milestone 0 should now mean alignment and stabilization, not starting
  over.
- "AI_HARDWARE_AGENT_ROADMAP.md is the roadmap" is not true locally
  yet. Creating a new duplicate roadmap without reconciling
  `PROJECT_PLAN.md` and ADR 0004 would increase drift.
- "MCP has 10 tools and 8 resources" is stale. Runtime reports 24/14.
- "Tests can be run normally from the venv" was initially false because
  entrypoint wrappers pointed at an old checkout path. That was repaired
  locally, but the issue should be captured in the audit so future
  worktrees do not inherit stale wrappers.
- "Digital simulation/synthesis is done" should mean "wrappers and skip
  behavior exist"; the actual external tools are missing on this host.

Risks:

- Multi-agent collision in `README.md`, `pyproject.toml`,
  `src/ltagent/mcp_server.py`, and `src/ltagent/cli.py`.
- Stale docs causing future agents to undo completed Phase 12/13 work.
- Optional tool absence being misreported as a product failure.
- Local venv state hiding packaging errors.
- Scope creep into Tiny8 games or optimizer before the integration
  branch is committed and validated.

## Options

### Option A - Plan Plus Local Validation Cleanup

Description: Write `docs/SUPERVISOR_EXECUTION_PLAN.md`, repair only
validation blockers that are clearly local/test-environment issues, and
avoid source feature work.

Pros:

- Lowest conflict risk in the dirty integration branch.
- Satisfies the prompt pack's first hard gate.
- Preserves user and agent changes.

Cons:

- Does not fix stale docs yet.

Complexity: Low

Risk: Low

Choose when: the branch contains active work from other agents, as it
does now.

### Option B - Plan Plus Immediate Docs Cleanup

Description: Create this plan and also patch stale docs in
`AGENTS.md`, `docs/SPEC.md`, and `docs/mcp_setup.md`.

Pros:

- Removes obvious contradiction immediately.
- Helps future agents follow current reality.

Cons:

- Touches shared files already modified in this branch.
- Could conflict with an active integrator's intended edits.

Complexity: Medium

Risk: Medium

Choose when: the supervisor has explicit approval to own the docs pass.

### Option C - Start Implementing New AI Hardware Milestones

Description: Begin new analog/digital/system features from the prompt
pack immediately.

Pros:

- Moves toward long-term product features.

Cons:

- Violates the plan-first gate.
- High conflict risk.
- Likely duplicates or disrupts existing Phase 12/13 work.

Complexity: High

Risk: High

Choose when: not recommended until Milestone 0 alignment is complete.

## Recommended Approach

Choose Option A now.

This branch is already carrying large uncommitted changes. The safe
supervisor action was to add a single planning artifact, repair local
validation blockers, and prevent future agents from starting from stale
prompt-pack assumptions.

After this file is reviewed, proceed to Option B as the next small
milestone: a documentation-only cleanup pass.

## Evidence Reviewed

| Source | Type | What it supports | Impact on plan |
|---|---|---|---|
| Local code graph for `home-abiyulinx-computing-ltspice-ai-agent` | Local codebase | 3331 nodes, 13530 edges; clusters cover CLI, MCP, digital, live, runner, templates, tests | Use code graph as source for module boundaries instead of guessing |
| `README.md` | Local docs | Current public status says Phase 12 complete and Phase 13 prototype integrated, 24 tools and 14 resources | Treat Phase 13 as existing prototype, not future empty work |
| `pyproject.toml` | Local config | Python package, dependencies, optional MCP extra, CI/lint/test config | Keep dependency changes out of Milestone 0 unless required |
| `docs/digital/plan-tiny8-agent.md` and ADR 0004 | Local architecture docs | Hybrid HDL + SPICE, Tiny8 v1, no LLM-generated HDL, optional external tools | Preserve current digital boundary |
| `docs/agent_reports/integration_final.md` | Local integration report | Prior final validation claimed 1331 pass, ruff, mypy, build, 24/14 MCP surface | Use as historical context, but re-check current branch before claiming current pass |
| Runtime MCP list checks | Local runtime | `ltagent-mcp --list-tools` and `ltagent-mcp --list-resources` report 24/14 | Fix stale docs to 24/14 in next milestone |
| `ltagent digital doctor --json` | Local runtime | Digital tools missing are structured warnings | Do not require Icarus/Yosys locally for docs alignment |
| Full quality gates | Local runtime | `pytest`, `ruff`, `mypy`, `build`, `git diff --check`, and MCP list checks are green after the small test-path fix | This plan can be used as a stable supervisor baseline |
| MCP Tools spec, 2025-11-25 | Official docs | MCP tools are named, schema-described model-controlled functions, with trust/safety guidance | Keep MCP surface curated and human-visible |
| MCP Resources spec, 2025-06-18 | Official docs | Resources are URI-identified context exposed by servers; resource templates are parameterized | Keep `ltagent://` allowlisted resources and reject traversal |
| Icarus Verilog docs | Official docs | `iverilog` compiles and `vvp` executes default compiled output | Keep Icarus runner as compile+runtime pair |
| Verilator User Guide | Official docs | Verilator has user-guide sections for verilating, lint/errors, and simulation runtime | Keep Verilator optional and primarily lint/sim support |
| Yosys documentation/about pages | Official docs | Yosys handles synthesizable Verilog and synthesis scripts, with formal-method ecosystem links | Keep Yosys as synthesis sanity gate, not as proof of product completeness |
| SymbiYosys docs | Official docs | `sby` is a front-end for Yosys-based formal verification flows | Treat formal as later milestone unless implemented and tested |
| Analog Devices LTspice page | Official docs | LTspice is an analog/mixed-signal simulator, schematic capture, and waveform viewer | Preserve LTspice as analog/mixed-signal backend, not primary CPU logic backend |
| lowRISC Verilog style guide | Professional engineering source | Recommends lower_snake_case and descriptive signal naming | Keep generated HDL naming deterministic and style-guided |
| OWASP Top 10 for LLM Applications 2025 | Standard/security source | Prompt injection, improper output handling, excessive agency, and unbounded consumption are current LLM app risks | Preserve no generic shell/file tools and structured validation |

## Evidence-Based Decisions

- MCP must remain curated because MCP tools are model-invoked
  functions and the project handles local filesystem/simulation side
  effects.
- Resources should remain under the custom `ltagent://` scheme with
  controlled templates because MCP resources are URI-addressed context.
- Digital simulation should continue to model Icarus as `iverilog`
  plus `vvp`, not one command.
- Yosys is a synthesis sanity check, not a full correctness proof.
- SymbiYosys/formal work should be a later milestone unless the repo
  adds actual `.sby` generation, solver handling, and tests.
- LTspice should remain the analog/mixed-signal backend, while HDL
  owns CPU logic.
- Generated HDL should keep deterministic, descriptive naming.
- The next implementation step should be docs/environment alignment,
  not new feature code.

## Milestone Breakdown

### Milestone 0 - Alignment And Stabilization

Goal: make repository status truthful and make future agent work safe.

Deliverables:

- `docs/REPO_AUDIT.md`
- canonical roadmap decision
- updated `docs/mcp_setup.md`
- updated `docs/SPEC.md`
- updated `AGENTS.md`
- local validation environment notes

Acceptance:

- Docs agree on current phase/status.
- Runtime MCP count in docs matches 24 tools and 14 resources.
- No dangerous MCP surface is documented or exposed.
- `git diff --check` passes.
- Test collection passes.
- Full gates are green or blocked by a documented environment issue
  with exact command/output.

### Milestone 1 - Existing Analog And Math Contract Audit

Goal: verify that Phase 13 Math Core and old analog unit parsing are
consistent enough for future optimizer work.

Deliverables:

- map of `ltagent.units` vs `ltagent.math_core.units`
- formula coverage matrix
- gap list for LED resistor, E-series, tolerance, and simulation
  comparison

Acceptance:

- no duplicated conflicting formulas,
- formula tests pass,
- public docs mark planned vs implemented features clearly.

### Milestone 2 - Live Editing Hardening

Goal: make file-based live editing reliable before expanding it.

Deliverables:

- snapshot/restore edge-case audit,
- regeneration failure audit,
- MCP live tool safety tests,
- docs for `LIVE_GENERATION_NOT_RUN`.

Acceptance:

- live edit tests pass,
- path traversal tests pass,
- failed generation never destroys the pre-edit project state.

### Milestone 3 - Digital Toolchain Verification

Goal: prove the Tiny8 digital path with real tools when installed, and
keep graceful skips otherwise.

Deliverables:

- optional local install instructions,
- real-tool smoke scripts or tests,
- simulation/synthesis report examples.

Acceptance:

- if tools are absent: structured skip and tests still pass,
- if tools are present: Tiny8 demo simulates and Yosys checks cleanly.

### Milestone 4 - Formal Verification Hook

Goal: add formal only after digital smoke is stable.

Deliverables:

- narrow `.sby` generation for one counter/FSM property,
- optional solver detection,
- structured formal report.

Acceptance:

- formal tests skip gracefully without tools,
- one example proves/fails deterministically when tools exist.

### Milestone 5 - Tiny8 SoC And LED Matrix Foundation

Goal: add a software-visible IO path before games.

Deliverables:

- memory-mapped IO spec,
- LED matrix renderer,
- assembler/emulator tests for blink and move-pixel.

Acceptance:

- emulator frames deterministic,
- no ROM/RAM out-of-bounds,
- no RTL CPU expansion unless v1 remains green.

### Milestone 6 - Games

Goal: implement games only after emulator, renderer, and IO invariants
are stable.

Order:

1. blink,
2. move pixel,
3. Snake-lite,
4. Pong-lite,
5. Tetris-lite.

Acceptance:

- programs assemble,
- deterministic input sequences render deterministic frames,
- board and memory invariants pass.

## File Ownership Rules

- Only the Supervisor/Integrator may edit broad shared files in a
  milestone:
  - `README.md`
  - `pyproject.toml`
  - `AGENTS.md`
  - `MCP.md`
  - `src/ltagent/cli.py`
  - `src/ltagent/mcp_server.py`
  - `docs/SPEC.md`
  - `docs/mcp_setup.md`
- Subagents must declare allowed files before editing.
- No two agents may edit the same file in parallel.
- Source agents do not edit documentation except their assigned
  report/doc file.
- Documentation agents do not edit source or tests.
- Test/CI agents do not edit source unless the Supervisor approves a
  narrow testability fix.
- Every subagent must report:
  - files changed,
  - tests run,
  - skipped checks and why,
  - remaining risk.

## Testing Strategy

- Documentation-only tasks:
  - `git diff --check`
  - Markdown link spot checks for changed docs
  - MCP count commands if MCP docs changed
- Source tasks:
  - focused unit tests for touched modules
  - relevant CLI/MCP contract tests
  - path traversal/security tests if any filesystem/MCP logic changed
  - `ruff check` on touched files at minimum
  - full `pytest`, `ruff check .`, `mypy src`, and build before merge
- Optional external tools:
  - missing LTspice, Icarus, Verilator, Yosys, SymbiYosys, or GTKWave
    must be `skipped` or `warning` unless a command explicitly runs in
    strict mode.
  - real-tool success may be an additional host-specific signal, but
    absence of tools must not break CI.

## Rollback Strategy

- For documentation-only changes: revert the changed docs file(s).
- For source changes: keep each agent's work on a separate branch or
  commit; revert the single commit if validation fails.
- For live project changes: require a snapshot before mutation and
  document how to restore it.
- For MCP surface changes: preserve the previous tool/resource list in
  the review report and restore registration if clients break.

## Remaining Assumptions

- The uncommitted branch is intended to remain the active integration
  target. This must be confirmed before broad doc cleanup.
- The 24/14 MCP surface is the desired current truth, not a temporary
  local experiment.
- `docs/PROJECT_PLAN.md` remains historically important even if a new
  `docs/AI_HARDWARE_AGENT_ROADMAP.md` index is added.
- The uncommitted dirty branch is acceptable as the active integration
  state. This still needs human confirmation.

## Research Gaps

- No wheel-installed smoke test was executed after building the wheel.
- No real LTspice, Icarus, Verilator, Yosys, or SymbiYosys execution
  was performed.
- No license review was performed for future optional HDL/formal/math
  dependencies beyond the sources already cited in existing docs.
- No graphify persistent graph was generated because no `graphify-out/`
  existed and the codebase-memory MCP graph already provided local code
  orientation for this task.

## Approval Gate

This plan authorizes only documentation-audit work as the next step.
Before editing source code, adding dependencies, changing CLI/MCP
contracts, or implementing new features, the Supervisor should present a
specific milestone plan and get explicit approval.
