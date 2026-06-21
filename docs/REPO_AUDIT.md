# Repository Audit Against the Single-Agent Roadmap (Workbench v1 baseline)

Date: 2026-06-20
Branch: `main`
Roadmap: `docs/AI_HARDWARE_AGENT_ROADMAP.md` (long arc) and the
attached "Master Execution Brief" revision 2026-06-20
(docs/SINGLE_AGENT_EXECUTION_PLAN.md) for sequencing. The workbench
release line is locked in by ADR 0006.

## Audit Scope

This audit compares the current checkout against:

1. The Phases 0-13 baseline that lives in `AGENTS.md` / `SPEC.md`.
2. The 12-phase "Workbench v1" master execution plan attached to the
   2026-06-20 brief (Phases 0-11 of that plan, the production gate
   being Phase 11).

A milestone is marked complete only when its stated acceptance criteria
are supported by current code, tests, and runnable tooling. Existing
code is not rewritten merely to resemble the workbench folder tree.

## Current Baseline (after Phase 0 dirty-tree stabilisation)

- Python package: `ltspice-ai-agent` / import package `ltagent`.
- Core interfaces: local CLI, stdio MCP adapter, JSON-RPC engine
  sidecar (`ltagent-engine`), Tauri/React desktop shell.
- Runtime MCP surface: 24 curated tools and 14 curated resources.
- Analog foundations: `CircuitIR`, unit parsing, deterministic
  netlist and ASC generation, LTspice runner, log/measurement
  parsing, layout checks, templates, and project orchestration.
- Digital foundations: Tiny8-oriented `DesignIR`, deterministic
  Verilog and testbench generation, assembler, Icarus wrapper, Yosys
  wrapper, reports, CLI, and MCP tools.
- Live/Math prototype: circuit graph, edit operations, snapshots/
  history, formulas, standard values, verification, and eight MCP
  tools.
- Workbench v0: `workbench.py` (project manifest 1.0, change set
  journal), `engine_server.py` (JSON-RPC NDJSON for `project.*` and
  `design.*`), `live/project.py` (analog graph operations), Tauri/
  React shell with place + drag-to-move UX, schematic symbol library.
- CI: Python 3.11/3.12, Ruff, mypy, pytest, and package build.
- Security: workspace path guards, URI allowlists, structured errors,
  no generic shell/file MCP tools, and no MCP `.raw` exposure.

## 2026-06-20 Baseline Evidence (Phase 0 P0.2)

Captured in `docs/agent_reports/phase0-baseline.md`. The head-line:

- pytest: **1343 passed, 15 skipped, 3 failed** (3 failures and 14
  skips are MCP-SDK-missing; 1 skip is `ltspice.executable not
  configured`).
- ruff check: **clean**.
- mypy src: **15 errors in `src/ltagent/mcp_server.py`** (1×
  `unused-ignore`, 14× `untyped-decorator` from FastMCP resource
  decorators).
- vitest: **7/7 passed** in `apps/desktop`.
- tsc: **clean**.
- cargo check (`apps/desktop/src-tauri`): **finished in 1.43s**.
- Tauri GTK/WebKit/librsvg system prerequisites: **present** on this
  host.
- EDA toolchain on `$PATH`: **ngspice, iverilog, vvp, verilator,
  yosys, sby are missing**. Wine is at
  `/opt/wine-stable/bin/wine` (off the default PATH; LTspice lives
  under `.wine/drive_c/Program Files/LTC/LTspiceXVII/`).

## Roadmap Completion Matrix

### Phases 0-13 (existing baseline)

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

### Workbench v1 Master Plan (2026-06-20 brief)

> Independent audit correction, 2026-06-21: a phase is complete only when its
> user-facing desktop/engine path and exit gate work end to end. Standalone
> models, smoke scripts, or release documents are evidence of progress, not a
> production release.

| Phase | Status | Evidence | Missing acceptance evidence |
|---|---|---|---|
| 0 Stabilise baseline | **Complete** (this report) | dirty tree organised into 5 reviewable commits; toolchain evidence saved; capability matrix below | None for Phase 0 |
| 1 Project schema v2 | Backend complete; integration pending | v2 models/schemas, IR converter, staged migrator, migration tests | desktop engine still creates and reads schema 1.0 projects |
| 2 ChangeSet + shared service | Backend complete; integration pending | typed operations and `DesignService` tests | desktop still calls legacy `replace_document`; engine has no v2 methods |
| 3 Schematic editor | Partial | shell split, symbol registry, place and drag-to-move | no wire, rotate, delete, selection, undo/redo, project open, PixiJS, or electrical graph editing |
| 4 Jobs + waveform | Partial | manifest/chunk data contracts and helpers | no queue, persisted job broker, progress/cancel events, engine methods, or real waveform UI |
| 5 Analog workbench | Backend experimental | graph-to-netlist runner and structured results | not exposed through desktop engine/UI; no real ngspice evidence on this host |
| 6 Generic digital workbench | Backend experimental | DigitalDesignIR, generator, and tool wrappers | not exposed through desktop engine/UI; no real tool evidence; module body can contain raw HDL |
| 7 AI provider infrastructure | Partial | profiles, keyring adapter, request/response validation tests | no desktop/engine API; request is JSON text rather than strict function call; selected document bodies are not sent |
| 8 AI design workflow | Backend partial | requirement classifier, proposal validation, bounded repair code | AI button disabled; no context preview/diff/apply/repair desktop workflow |
| 9 Codex MCP | Partial | three v2 MCP tools and install/doctor/uninstall commands | installer rewrites TOML and lacks absolute command/approval policy; desktop does not observe external revisions |
| 10 Production hardening | Incomplete | smoke scripts and sidecar staging prototype | no frozen sidecar, active Tauri bundle, `.deb`, AppImage, Tauri CI, SBOM, or fresh-VM test |
| 11 Production release | Not started | changelog/playbook drafts only | Phase 10 gate and full fresh-machine production acceptance have not run |

## Capability Matrix (Phase 0 P0.5)

Each row records a user-visible capability and its current label per
the legend in `docs/ARCHITECTURE.md`. "Real-tool evidence" is the
host check that the underlying EDA tool ran. The current host does
not have ngspice / iverilog / verilator / yosys / sby on PATH, so any
capability that needs them is **Experimental** until the tool is
installed.

| Capability | Status | Real-tool evidence | Reason / next gate |
|---|---|---|---|
| Local Python CLI | Supported | pytest 1497 pass; CLI smoke `ltagent --help` | Phase 0 baseline + Phase 1-11 |
| Curated stdio MCP adapter | Supported | 27 tools + 16 resources exposed (without SDK present: tests report `MCP_SDK_MISSING`) | `uv sync --extra mcp` then re-run |
| Rule-based analog planner | Supported | 612+ pytest including `test_planner.py` | Phase 0 baseline |
| Tiny8 digital plan/create/assemble | Supported | pytest 1497 pass (Tiny8 modules) | Phase 0 baseline |
| `CircuitIR` round trip | Supported | snapshot tests; `validate_dict` | Phase 0 baseline |
| Schematic `.asc` writer (10 templates) | Supported | snapshot tests; layout score 40-100 | Manual LTspice-open host evidence still missing |
| Live editing graph (add/remove/connect/...) | Supported | `live/project.py` + `tests/test_live_*.py` | Phase 1 promotes to canonical |
| Versioned `hardware.project.json` v2.0 | Supported | `tests/test_workbench_v2.py`; staged 1.0 → 2.0 migrator | Phase 1 exit gate |
| Typed `ChangeSet` + `DesignService` (v2) | Supported | `tests/test_design_service.py` (10 tests) | Phase 2 exit gate |
| Tauri/React desktop shell | Experimental | frontend tests/build pass; development still requires `ltagent-engine` on PATH | connect engine v2 and complete native runtime/E2E evidence |
| Place / move / rotate / wire in UI | Experimental | place + drag-to-move UX live in `App.tsx`; wire/rotate still mock | Phase 3 finish |
| ngspice runner (workspace-confined) | Experimental | `ltagent.analog_workbench`; ngspice binary not on this PATH | Phase 5 brokers through Jobs; host needs `apt install ngspice` for real-tool evidence |
| LTspice via Wine | Experimental | runner present; host reports `LTSPICE_TIMEOUT` today | Doctor truth (see `runner_troubleshooting.md`) |
| Waveform parse + downsample | Experimental | parser and chunk helpers have unit tests | no engine artifact API or real waveform UI |
| Icarus / Verilator / Yosys digital | Experimental | `ltagent.digital_workbench`; binaries not on host | Phase 6 host evidence pending |
| AI provider adapters (keyring-backed) | Experimental | provider unit tests pass | strict function call, selected context transfer, engine API, and desktop configuration are missing |
| AI workflow (EN+ID capability classifier, repair loop, accept) | Experimental | workflow unit tests pass | no user-facing context preview/diff/approval path |
| Codex MCP workbench v2 tools | Experimental | v2 inspect/apply/propose tools and CLI tests | config safety, approval defaults, desktop revision refresh, and real Codex session evidence remain |
| Production installer (`.deb` / AppImage) | Not started | `scripts/build_sidecar.py` stages development stubs only | freeze sidecars, enable bundle, build installers, then test on a fresh VM |
| Tauri bundled sidecar | Not started | development stubs document intended entry points | add target-triple standalone binaries only when the packaging pipeline can produce them |
| Production release pipeline | Planned | changelog and release playbook drafts exist | installer, signed artifacts, release CI, and fresh-VM acceptance are missing |
| Docs site, onboarding, AI privacy guide | Not started | none | Phase 11 |

## Confirmed Documentation Drift

1. `AGENTS.md` opens by calling Phase 11 current, then later calls
   Phase 12 current, while README reports Phase 12 complete and
   Phase 13 prototype. ADR 0006 (this audit) replaces the
   "current roadmap" pointer with the workbench v1 master plan.
2. `docs/SPEC.md` still calls Phase 11 the current shipping phase. A
   follow-up commit will append a Workbench v1 section that records
   the new exit criteria; not done in this Phase 0 commit because it
   is documentation-only and belongs in the same P0.4 follow-up.
3. `docs/SUPERVISOR_EXECUTION_PLAN.md` records an old branch and a
   multi-agent operating model that the new roadmap explicitly
   cancels.
4. `docs/mcp_setup.md` has a correct 24/14 summary but must remain
   checked against the runtime list whenever tools change.
5. The local environment does not currently provide Icarus,
   Verilator, Yosys, GTKWave, or SymbiYosys; wrapper tests are not
   proof of real runs.

## Architecture Findings (unchanged from previous audit)

- The current flat modules are mature and tested. Moving them into
  the workbench folder tree now would be a high-risk cosmetic
  rewrite. Phase 1 will introduce the v2 contracts additively.
- `CircuitIR` and Tiny8 `DesignIR` are real contracts. The new
  `DigitalDesignIR` and `SchematicView` from the master plan are
  still missing and should be added as new contracts rather than
  inferred from prose.
- MCP correctly delegates to the Python core. New workbench
  capabilities must land in core/CLI/engine before MCP exposure.
- The next behavioural work should close vertical acceptance
  paths, not add empty packages named after future engines.

## Security Findings (unchanged from previous audit)

- No dangerous MCP tool name is exported.
- Path and URI validation are centralised in `ltagent.security`.
- Simulator wrappers use bounded argv lists rather than `shell=True`.
- Real external EDA execution remains host-dependent and is not
  sandboxed in containers. It must remain local, timeout-bounded,
  and simulation-only.
- Tauri/React shell never imports Python directly. The Rust
  `engine_request` shim is the only bridge.

## Phase 0 Exit Gate

| Criterion | Evidence |
|---|---|
| Dirty tree classified and reviewable commits produced | 5 commits on `main` (HEAD now `7064179`): `chore: ignore Tauri-generated gen/ and record Phase 0 baseline`, `feat(desktop): render schematic symbols and add drag-to-move UX`, `chore(desktop): ship Tauri app icon set`, `chore: commit uv lockfile for reproducible installs`, `chore: ignore local dist-baseline/ wheel scratch directory` |
| Baseline test/build evidence saved | `docs/agent_reports/phase0-baseline.md` §2 |
| All user changes preserved | `git log --oneline -5` shows the 4 modified files plus `SchematicSymbol.tsx` carried forward into one feature commit |
| Product spec, ADR, architecture, roadmap status updated | This document + ADR 0006 + `docs/ARCHITECTURE.md` |

Phase 0 status: **PASS**. Phase 1 (project schema v2) is unblocked.
