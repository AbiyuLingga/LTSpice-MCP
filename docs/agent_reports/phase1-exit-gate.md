# Phase 1 Exit-Gate Report — Workbench v1

**Phase:** 1 — Project schema v2
**Owner:** single AI editing owner
**Date:** 2026-06-20
**Active plan:** Master Execution Brief, 2026-06-20 revision (locked by
ADR 0006, sequenced in `docs/SINGLE_AGENT_EXECUTION_PLAN.md`).
**Previous phase:** Phase 0 (PASS) — see
`docs/agent_reports/phase0-exit-gate.md`.

## Per-task Report Block (per the brief §22)

### P1.1 — Pydantic v2 contracts and JSON Schema

- **Goal:** define the v2 project shape in Pydantic 2 and emit
  machine-readable JSON Schema files for every contract.
- **Files changed:** `src/ltagent/workbench_v2.py` (new),
  `tools/generate_workbench_schema.py` (new),
  `tests/test_workbench_v2.py` (new),
  `schemas/workbench_v2/*.schema.json` (6 new files),
  `src/ltagent/resources/workbench_v2/*.schema.json` (6 new files,
  packaged resources).
- **Behaviour implemented:**
  HardwareProject, Requirements, SchematicView (+ SchematicSymbol /
  SchematicWire / SchematicNetLabel), DigitalDesignDocument,
  SystemSpec, and the AnalogGraph alias for the existing
  `ltagent.live.graph_schema.CircuitGraph`. The v2 layout paths
  (`design/analog/main.graph.json`, `design/schematic/main.view.json`)
  are wired through `DOCUMENT_PATHS`. Symbol kinds, rotation
  multiples of 90, safety classes, and gridSize bounds are explicit
  allowlists.
- **Tests run:** `pytest tests/test_workbench_v2.py` (24 cases),
  `ruff check`, `mypy src/ltagent/workbench_v2.py`.
- **Result:** PASS. 24/24 green.
- **Commit:** `b53648f feat(workbench): add Workbench v2 Pydantic contracts and JSON Schema`.

### P1.2 — Make CircuitGraph canonical; add SchematicView

- **Goal:** declare CircuitGraph as the canonical analog source
  and add the cross-document consistency helper.
- **Files changed:** `src/ltagent/workbench_v2.py`,
  `tests/test_workbench_v2.py`.
- **Behaviour implemented:**
  `validate_schematic_view_against_graph` returns the list of
  inconsistencies (every schematic symbol id must reference a
  known component id; the graph does not require every component
  to be laid out). `raise_on_schematic_inconsistency` raises a
  structured `V2DocumentInconsistency` with the
  `WORKBENCH_V2_SCHEMATIC_ORPHAN_SYMBOL` code.
- **Tests run:** `pytest tests/test_workbench_v2.py` (29 cases).
- **Result:** PASS. 29/29 green.
- **Risks remaining:** the consistency check is intentionally
  narrow (one rule). Phase 2's ChangeSet will add a stricter
  invariant set.
- **Commit:** `d1d7d73 feat(workbench): make CircuitGraph canonical and add SchematicView consistency checks`.

### P1.3 — CircuitIR -> CircuitGraph with stable ID mapping

- **Goal:** add the reverse direction of the analog converter so
  old `circuit.ir.json` documents and AI-generated IR proposals
  can land in v2 projects without losing ids or connectivity.
- **Files changed:** `src/ltagent/live/ir_to_graph.py` (new),
  `src/ltagent/live/__init__.py`,
  `tests/test_ir_to_graph.py` (new).
- **Behaviour implemented:**
  `ir_to_graph(CircuitIR) -> CircuitGraph` preserves component
  ids, net names, the ground net ("0"), and the canonical
  pin map per kind. The converter is round-trippable through
  `graph_to_ir`. `IRToGraphError` carries stable codes
  (`IR_TO_GRAPH_PIN_ARITY`, `IR_TO_GRAPH_ANALYSIS`,
  `IR_TO_GRAPH_MEASUREMENT`, `IR_TO_GRAPH_PIN_UNKNOWN_KIND`).
- **Tests run:** `pytest tests/test_ir_to_graph.py` (12 cases).
- **Result:** PASS. 12/12 green.
- **Risks remaining:** IR analysis `startFreq` / `pointsPerDecade`
  semantics are preserved verbatim; the graph layer adds the
  analysis-level validation that the IR doesn't enforce.
- **Commit:** `3d90a44 feat(live): add CircuitIR -> CircuitGraph converter with stable id mapping`.

### P1.4 — Unify canonical projects root

- **Goal:** one source of truth for the directory that contains
  workbench projects; every entry point (desktop, CLI, engine,
  MCP) resolves the same path.
- **Files changed:** `src/ltagent/projects_root.py` (new),
  `src/ltagent/engine_server.py` (default path now uses the
  resolver), `tests/test_projects_root.py` (new).
- **Behaviour implemented:**
  `get_default_projects_root()` returns the XDG_DATA_HOME-based
  default (Linux/macOS) or LOCALAPPDATA-based default (Windows),
  with `~/.local/share/ltagent/projects` as a fallback.
  `resolve_projects_root` honours the resolution order: explicit
  caller > `LTAGENT_PROJECTS_ROOT` env > default.
  `ensure_projects_root` mkdirs the directory idempotently. The
  engine's `--projects-root` default is now the canonical default
  rather than `Path.cwd()/projects`.
- **Tests run:** `pytest tests/test_projects_root.py` (9 cases),
  engine smoke test.
- **Result:** PASS. 9/9 green; engine handshake still works.
- **Risks remaining:** the Tauri shim still passes an explicit
  `--projects-root` flag; Phase 10 will replace that with a
  bundled sidecar.
- **Commit:** `d21a833 feat(workbench): add canonical projects-root resolver`.

### P1.5 — Staged 1.0 -> 2.0 migration

- **Goal:** let a 1.0 project land in a v2 layout without losing
  data and without the migration ever corrupting the source.
- **Files changed:** `src/ltagent/workbench_migration.py` (new),
  `tests/test_workbench_migration.py` (new).
- **Behaviour implemented:**
  `migrate_workbench_project_to_v2` honours the master-plan
  invariant #9: snapshot-first (every 1.0 document is copied
  to `.workbench/migration-backup-<timestamp>/` before any
  rewrite), staged (v2 documents are written and Pydantic-
  validated in `.workbench/migration-staging-<timestamp>/`
  before swap), atomic (a failure removes staging and restores
  the 1.0 files from backup before raising a structured
  `WorkbenchError`), and non-destructive (the v1 backup survives
  a successful migration; the migrator never deletes source
  files). `rollback_workbench_project_to_v1` is the explicit
  exit for callers that find a bad state.
- **Per-document conversions:** requirements / digital / system
  get a clean schema bump; analog gets the v1 flat list of
  components / nets lifted into the CircuitGraph dict shape;
  schematic nodes get the v1 fields translated to the new
  SchematicSymbol (with rotation / mirror / label / properties
  defaults) and the v1 wires are preserved.
- **Tests run:** `pytest tests/test_workbench_migration.py` (7
  cases covering the happy path, the schematic node -> symbol
  conversion, the round-trip back to v1, the failure path with
  byte-identical source files, the requirements / digital /
  system persistence, the non-1.0 manifest rejection).
- **Result:** PASS. 7/7 green.
- **Risks remaining:** the v2 surface is still read-only — Phase
  2 widens it to accept a `ChangeSet` of typed edit operations.
  The v1 surface (workbench.py) continues to accept only 1.0
  manifests; that is by design.
- **Commit:** `485d506 feat(workbench): add staged 1.0 -> 2.0 migrator with snapshot + rollback`.

### P1.6 — Phase 1 exit-gate report

- **Goal:** produce the structured per-phase summary the brief §22
  requires.
- **Files changed:** this file.
- **Behaviour implemented:** none.
- **Tests run:** none required.
- **Result:** see "Phase status" block below.
- **Risks remaining:** none for Phase 1.
- **Next task:** begin Phase 2 (ChangeSet + shared service) once
  the user confirms the gate.
- **Commit:** to be added at the end of this Phase 1 work.

## Phase Status

```text
Phase status: PASS
```

### Exit-criteria evidence

| Criterion | Evidence |
|---|---|
| Pydantic v2 contracts for v2 | `src/ltagent/workbench_v2.py` (6 models) |
| Generated JSON Schema, byte-identical between repo and resource | `tools/generate_workbench_schema.py` asserts equality; tests in `tests/test_workbench_v2.py::test_generated_schemas_present_and_identical` |
| CircuitGraph is canonical analog source | `AnalogGraph` alias in `workbench_v2.py`; `tests/test_workbench_v2.py` asserts the alias points at `ltagent.live.graph_schema.CircuitGraph` |
| SchematicView canonical layout | `workbench_v2.py:SchematicView`; cross-document consistency helper |
| CircuitIR-to-graph converter with stable id mapping | `src/ltagent/live/ir_to_graph.py`; `test_ir_to_graph_round_trip_via_graph_to_ir` confirms id preservation |
| Unify projects root across entry points | `src/ltagent/projects_root.py`; engine_server.py uses it as default; explicit override is honoured |
| Staged 1.0 -> 2.0 migration | `src/ltagent/workbench_migration.py` |
| Migration is snapshot-first, atomic, rollbackable, never discards source | `test_migration_failure_leaves_source_unchanged` (byte-identical pre/post); `test_migration_round_trip_back_to_v1`; the backup directory is never deleted by the migrator |
| Old projects can be migrated and re-opened | `test_migration_happy_path_creates_v2_manifest` + the v2 surface re-validates with `HardwareProject.model_validate` |

### Real-tool evidence

- pytest: **1400 passed, 15 skipped, 3 failed** (the 3 failures
  and 14 of the 15 skips are the pre-existing `MCP_SDK_MISSING`
  family from Phase 0; 1 skip is `ltspice.executable not configured`).
- ruff check: **clean**.
- mypy: **15 errors in `src/ltagent/mcp_server.py` only** — the
  pre-existing FastMCP decorator typings documented in
  `docs/agent_reports/phase0-baseline.md`. No new errors
  introduced by Phase 1.
- frontend vitest: 7/7 (unchanged from Phase 0).
- frontend tsc: clean (unchanged).
- `cargo check` (`apps/desktop/src-tauri`): clean (unchanged).
- Engine smoke: `echo '{"jsonrpc":"2.0","id":1,"method":"engine.handshake"}' |
  uv run ltagent-engine` returns the 2.0 envelope with the
  expected `capabilities.methods` list. The engine still works
  after the projects-root default change.

### Skipped evidence and reason

- `mypy src/ltagent/mcp_server.py` errors: pre-existing FastMCP
  decorator typings; out of Phase 1 scope.
- MCP subprocess tests (3) + SDK skips (14): blocked on the
  optional `[mcp]` extra; out of Phase 1 scope.
- ngspice / iverilog / verilator / yosys / sby: still absent on
  host; recorded as Experimental in the capability matrix. Phase
  4 (Jobs) and Phase 5/6 acceptance need them installed.

### Regression result

- No pre-Phase-1 test failed after Phase 1. The full suite grew
  from **1343 passed (Phase 0)** to **1400 passed (Phase 1)**,
  a delta of **+57 tests**.
- The v1 workbench surface (`ltagent.workbench`) is untouched.
  The 1.0 manifest, 1.0 documents, and the 1.0 transaction journal
  work the same as before; the migration is the only path that
  produces a 2.0 manifest, and the migration is explicit
  (callers opt in).
- The Tauri desktop shell is untouched. The engine and CLI
  resolve the new projects-root default; their existing
  `--projects-root` flag overrides it.

### Migration / rollback evidence

- `tests/test_workbench_migration.py::test_migration_failure_leaves_source_unchanged`
  forces a conversion failure (schematic `nodes` field set to a
  string) and asserts every pre-migration file in the project
  is byte-identical to its post-failure state. The migrator
  restores from the backup directory before raising
  `WorkbenchError`.
- `tests/test_workbench_migration.py::test_migration_round_trip_back_to_v1`
  migrates a project, calls `rollback_workbench_project_to_v1`,
  and re-opens the project with the v1 surface. The v1 manifest
  is byte-identical to its pre-migration state, the v2 graph
  file is removed, and the legacy v1 analog file is restored.
- The migration manifest is written into the backup directory
  so future audits can see which files were upgraded and when.
  The migrator never deletes the backup.

### Known limitations (carried into Phase 2)

- The v2 surface is read-only. Phase 2 will add the typed
  `ChangeSet` operations (add / remove / connect / rename /
  placeNode / moveNode / rotateNode / setWireRoute /
  autoLayout), the revision conflict detection, and the shared
  design service that the desktop, CLI, engine, and MCP all
  consume.
- The workbench v2 layout reuses the v1 file paths for
  requirements, schematic, digital, and system; only the analog
  file is renamed (`main.circuit.json` -> `main.graph.json`).
  Phase 2 may introduce per-document `revision` sub-counters
  inside the manifest.
- The 15 `mcp_server.py` mypy errors are pre-existing and out of
  Phase 1 scope. They will be addressed by Phase 7 (AI) and
  Phase 9 (Codex MCP).
- The Tauri shell's sidecar still uses `ltagent-engine` from
  `PATH`; Phase 10 will replace that with a bundled sidecar.

## Phase 1 Commit Map

```text
485d506 feat(workbench): add staged 1.0 -> 2.0 migrator with snapshot + rollback
d21a833 feat(workbench): add canonical projects-root resolver
3d90a44 feat(live): add CircuitIR -> CircuitGraph converter with stable id mapping
d1d7d73 feat(workbench): make CircuitGraph canonical and add SchematicView consistency checks
b53648f feat(workbench): add Workbench v2 Pydantic contracts and JSON Schema
```

## Hand-off to Phase 2

Phase 2 (ChangeSet + shared service) is unblocked. Its first
concrete deliverable is the typed `ChangeSet` parser and the
shared design service that the desktop, CLI, engine, and MCP
all call. The contracts that the v2 surface reads are now stable;
the v1 surface continues to be a valid migration source.
