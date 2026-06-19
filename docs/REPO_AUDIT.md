# Repository Audit Against the Single-Agent Roadmap

Date: 2026-06-19
Branch: `main`
Roadmap: `docs/AI_HARDWARE_AGENT_ROADMAP.md`

## Audit Scope

This audit compares the current checkout with Milestones 0-12 in the
single-agent roadmap. A milestone is marked complete only when its stated
acceptance criteria are supported by current code, tests, and runnable
tooling. Existing code is not rewritten merely to resemble the roadmap's
suggested folder structure.

## Current Baseline

- Python package: `ltspice-ai-agent` / import package `ltagent`.
- Core interfaces: local CLI and stdio MCP adapter.
- Runtime MCP surface: 24 curated tools and 14 curated resources.
- Analog foundations: `CircuitIR`, unit parsing, deterministic netlist and
  ASC generation, LTspice runner, log/measurement parsing, layout checks,
  templates, and project orchestration.
- Digital foundations: Tiny8-oriented `DesignIR`, deterministic Verilog and
  testbench generation, assembler, Icarus wrapper, Yosys wrapper, reports,
  CLI, and MCP tools.
- Live/Math prototype: circuit graph, edit operations, snapshots/history,
  formulas, standard values, verification, and eight MCP tools.
- CI: Python 3.11/3.12, Ruff, mypy, pytest, and package build.
- Security: workspace path guards, URI allowlists, structured errors, no
  generic shell/file MCP tools, and no MCP `.raw` exposure.

## Roadmap Completion Matrix

| Milestone | Status | Evidence | Missing acceptance evidence |
|---|---|---|---|
| 0 Repo cleanup | Complete | Canonical roadmap, architecture/security/engine docs, contribution guide, CI, fresh install and full gates | None for Milestone 0 |
| 1 Analog MVP | Partial | `ir.py`, `netlist.py`, `runner.py`, Math Core, result/log tests, deterministic `led_resistor` IR/netlist/ASC slice | 20 real simulation cases, `led_resistor` planner/project report closure, formula-vs-real-sim artifact proof |
| 2 ASC generation | Partial | deterministic `asc.py`, layout checker, ten official analog templates | Milestone 1 topology closure, host LTspice-open evidence, explicit symbol-registry contract |
| 3 Templates/optimization | Partial | official/candidate/rejected states, evaluator/promoter, E-series selection | deterministic parameter sweep and ranking pipeline |
| 4 Digital HDL MVP | Incomplete | Tiny8 generator, testbench, Icarus runner | generic and/mux/adder/counter/shift/FSM/PWM specs, Verilator, VCD parser |
| 5 Synthesis/formal | Partial | Yosys runner and report | SymbiYosys runner, properties, counterexample summaries |
| 6 Tiny8/LED matrix | Partial | Tiny8 ISA/spec, assembler, CPU RTL generation | emulator, LED MMIO renderer, emulator-vs-RTL comparison, demo frames |
| 7 Games | Not started | roadmap only | Snake/Pong/Tetris-lite programs, invariant tests, rendered frames |
| 8 Mixed signal | Not started | architecture/ADR discussion only | typed mixed-system contract and verified analog/digital example |
| 9 Advanced analog/power | Partial | op-amp/diode/transistor templates and ideal buck/boost formulas | power topologies, sweeps, safety classification, simulation reports |
| 10 AI repair loop | Incomplete | deterministic analog/digital planners and structured errors | RequirementSpec, repair classifier, bounded attempts, repair history |
| 11 Knowledge base | Not started | template metadata only | sourced component/pin/model registry and citation contract |
| 12 Release quality | Partial | install docs, examples, CI, MCP setup | fresh-user test matrix, docs site/gallery, contribution guides, release audit |

## Confirmed Documentation Drift

1. `AGENTS.md` opens by calling Phase 11 current, then later calls Phase 12
   current, while README reports Phase 12 complete and Phase 13 prototype.
2. `docs/SPEC.md` still calls Phase 11 the current shipping phase.
3. `docs/SUPERVISOR_EXECUTION_PLAN.md` records an old branch and a
   multi-agent operating model that the new roadmap explicitly cancels.
4. `docs/mcp_setup.md` has a correct 24/14 summary but must remain checked
   against the runtime list whenever tools change.
5. The local environment does not currently provide Icarus, Verilator,
   Yosys, GTKWave, or SymbiYosys; wrapper tests are not proof of real runs.

## Architecture Findings

- The current flat modules are mature and tested. Moving them into the
  roadmap's suggested package tree now would be a high-risk cosmetic rewrite.
- `CircuitIR` and Tiny8 `DesignIR` are real contracts. `RequirementSpec` and
  `SystemIR` are still missing and should be added as new contracts rather
  than inferred from prose.
- MCP correctly delegates to the Python core. New roadmap capabilities must
  land in core/CLI before MCP exposure.
- The next behavioral work should close vertical acceptance paths, not add
  empty packages named after future engines.

## Security Findings

- No dangerous MCP tool name is exported.
- Path and URI validation are centralized in `ltagent.security`.
- Simulator wrappers use bounded argv lists rather than `shell=True`.
- Real external EDA execution remains host-dependent and is not sandboxed in
  containers. It must remain local, timeout-bounded, and simulation-only.

## Prioritized Work

1. Finish Milestone 0 documentation alignment and prove a fresh install.
2. Close Milestone 1 with a deterministic analog regression matrix and
   honest separation of mocked versus real simulator evidence.
3. Add deterministic parameter sweep/ranking before advanced optimization.
4. Build generic DigitalIR vertical slices before extending Tiny8/games.
5. Add formal tooling only after generic digital simulation is green.
6. Add Tiny8 emulator and LED renderer before any game implementation.

## Validation Commands

```bash
git diff --check
python -m pytest -q
python -m ruff check .
python -m mypy src
python -m build
ltagent --help
ltagent-mcp --help
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

Real toolchain acceptance must additionally record versions and actual runs
for LTspice/Wine, Icarus, Verilator, Yosys, and SymbiYosys when available.

## Milestone 0 Validation Evidence

Validated on 2026-06-19:

- Fresh environment created at `/tmp/ltagent-m0-venv`.
- `pip install -e '.[dev,mcp]'`: passed.
- Fresh `ltagent --help`: passed and reports the current CLI/MCP wording.
- Fresh `ltagent-mcp --help`: passed.
- Fresh MCP inventory: 24 tools and 14 resources.
- Markdown local-link audit: passed.
- `git diff --check`: passed.
- `pytest`: 1331 passed, 1 expected LTspice-configuration skip.
- `ruff check .`: passed.
- `mypy src`: passed for 51 source files.
- `python -m build`: sdist and wheel built successfully.

The 28 Pydantic `ArbitraryTypeWarning` messages emitted while FastMCP schemas
are built remain non-blocking maintenance debt; they do not change tool schemas
or test outcomes.
