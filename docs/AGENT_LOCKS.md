# AGENT LOCKS — File Ownership Matrix

> **Historical archive:** This matrix documents the completed Phase 13
> multi-agent integration. New work must not recreate these branches or agent
> roles. Follow `AI_HARDWARE_AGENT_ROADMAP.md` and
> `SINGLE_AGENT_EXECUTION_PLAN.md`, which require one serialized AI editing
> owner.

**Scope:** `ltspice-ai-agent` repository, File-Based Live Editing + Math
Core workstream.

**Source plan:**
[`ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md)
(sections 1-28). If the plan file is missing from the working tree, see
`docs/agent_reports/integrator.md` §3 for the recovery note.

**Audience:** every AI coding agent (Agent 0 through Agent 8) that
takes ownership of a slice of the File-Based Live Editing + Math Core
workstream.

**Rule of thumb:** if a file path is not listed under **Owns**, it is
out of scope. Out-of-scope files are owned by Agent 0 (Integrator /
Supervisor). Agent 0 is the only role that may edit cross-cutting
artefacts (`README.md`, `pyproject.toml`, the MCP entry point
`src/ltagent/mcp_server.py`, this `AGENT_LOCKS.md` itself, and any
folder layout decision that more than one agent depends on).

---

## 0. Agent roster

| ID    | Role                          | Working branch            | Report                                  |
|-------|-------------------------------|---------------------------|-----------------------------------------|
| Agent 0 | Integrator / Supervisor     | `agent-0-integrator`      | `docs/agent_reports/integrator.md`       |
| Agent 1 | Circuit Graph                | `agent-1-circuit-graph`   | `docs/agent_reports/circuit_graph.md`    |
| Agent 2 | Edit Operations              | `agent-2-edit-ops`        | `docs/agent_reports/edit_ops.md`         |
| Agent 3 | Live Project + Snapshot      | `agent-3-live-project-snapshot` | `docs/agent_reports/live_project.md` |
| Agent 4 | Math Core                    | `agent-4-math-core`       | `docs/agent_reports/math_core.md`        |
| Agent 5 | Simulation + Verification    | `agent-5-sim-verification`| `docs/agent_reports/sim_verification.md` |
| Agent 6 | MCP Tools                    | `agent-6-mcp-tools`       | `docs/agent_reports/mcp_tools.md`        |
| Agent 7 | Test + QA                    | `agent-7-tests-qa`        | `docs/agent_reports/tests_qa.md`         |
| Agent 8 | Docs                         | `agent-8-docs`            | `docs/agent_reports/docs.md`             |

Each agent works on its own branch off `main`. Agents merge to `main`
only through Agent 0's integrator review (see §6 below).

---

## 1. Agent 0 — Integrator / Supervisor

**Mission:** keep the parallel workstream coherent. Agent 0 does not
implement live-editing or math features; Agent 0 only manages the
shared scaffolding other agents consume.

**Owns:**

| Path                                                                 | Purpose                                           |
|----------------------------------------------------------------------|---------------------------------------------------|
| `docs/AGENT_LOCKS.md`                                                | This file. Editable only by Agent 0.              |
| `docs/agent_reports/` (folder)                                       | Per-agent reports; each agent owns only its file. |
| `docs/agent_reports/integrator.md`                                   | Integrator's own report.                          |
| `docs/examples/` (folder + `README.md`)                              | Worked examples; Agent 8 fills in concrete IRs.   |
| `docs/ltspice_file_based_live_editing_math_plan.md`                  | Plan of record (moved from repo root if needed).  |
| `src/ltagent/live/__init__.py`                                       | Package public surface for `live`.                |
| `src/ltagent/math_core/__init__.py`                                  | Package public surface for `math_core`.           |
| `README.md` (only when adding a short link to new docs sections)     | Cross-link maintenance.                           |
| `pyproject.toml` (only when a new dependency is unavoidable)         | Dependency gating.                                |

**Forbidden under Agent 0's branch:**

* `src/ltagent/mcp_server.py` (MCP entry point — owned by Agent 6).
* `src/ltagent/live/*.py` other than `__init__.py` (owned by Agents
  1, 2, 3, 5).
* `src/ltagent/math_core/*.py` other than `__init__.py` (owned by
  Agent 4).
* Any per-agent report under `docs/agent_reports/` other than
  `integrator.md`.

**Decisions Agent 0 makes unilaterally:**

1. Branch names and the merge order from per-agent branches into
   `main`.
2. The folder layout of `docs/`, `docs/agent_reports/`,
   `docs/examples/`, `schemas/`, and the `src/ltagent/` subpackages.
3. The shared `__init__.py` exports that other agents must respect.
4. The Markdown style of `docs/agent_reports/*.md` (sections §1
   through §7 — see `docs/agent_reports/integrator.md` §2 for the
   template).
5. Whether a proposed dependency in `pyproject.toml` is approved; if
   approved, Agent 0 is the only role that edits that file for that
   change.

---

## 2. Agent 1 — Circuit Graph

**Mission:** the in-memory Circuit Graph model (plan §7). Pure data,
no I/O beyond JSON serialisation.

**Owns:**

| Path                                       | Purpose                                       |
|--------------------------------------------|-----------------------------------------------|
| `src/ltagent/live/graph_schema.py`         | Pydantic models for graph nodes / edges.      |
| `src/ltagent/live/graph_validation.py`     | Validation rules + issue codes (plan §7.3).   |
| `schemas/circuit_graph.schema.json`        | JSON Schema for graph export.                 |
| `tests/test_circuit_graph.py`              | Unit + golden tests for the graph model.      |

**Hard rule:** no `subprocess`, no `ltagent.runner`, no `ltagent.live.project`,
no `ltagent.math_core`. The graph package is pure data; downstream
agents wire it to disk and to math_core.

**Forbidden under Agent 1:**

* `src/ltagent/live/__init__.py` (Integrator).
* Any `*.cir` / `*.asc` / `*.plt` writing code.

---

## 3. Agent 2 — Edit Operations

**Mission:** the Edit Operation API (plan §8). Operations are
immutable transforms on a Circuit Graph and are the only sanctioned
way for the MCP layer and the CLI to mutate a graph.

**Owns:**

| Path                                       | Purpose                                          |
|--------------------------------------------|--------------------------------------------------|
| `src/ltagent/live/edit_result.py`          | Result envelope + error codes for edit ops.      |
| `src/ltagent/live/edit_ops.py` (if added)  | Pure functions: add_component, connect_nodes, … |
| `schemas/edit_operation.schema.json`       | JSON Schema for edit ops on disk.                |
| `tests/test_live_edit_ops.py`              | Unit + golden tests for edit ops.                |

**Reads but does not own (must import, not edit):**

* `ltagent.live.graph_schema`, `ltagent.live.graph_validation`
  (Agent 1).
* `ltagent.live.snapshot` (Agent 3) — for snapshot hooks.

**Hard rule:** edit operations are pure. They take a graph in, return
a graph out (or an `EditResult` envelope). They do not touch the
filesystem; Agent 3 owns the snapshot/project orchestration.

---

## 4. Agent 3 — Live Project + Snapshot

**Mission:** the Live Project orchestrator + Snapshot / Undo system
(plan §6, §9, §10). Owns the disk side: project directory layout,
snapshot manifest, edit history, atomic writes, path traversal
defenses.

**Owns:**

| Path                                       | Purpose                                          |
|--------------------------------------------|--------------------------------------------------|
| `src/ltagent/live/project.py`              | `load_project`, `save_project`, atomic writes.   |
| `src/ltagent/live/snapshot.py`             | Snapshot create / restore / list.                |
| `src/ltagent/live/history.py`              | `edit_history.jsonl` append-only writer.         |
| `tests/test_snapshot_restore.py`          | Snapshot round-trip tests.                       |
| `tests/test_path_traversal.py`             | Path-traversal rejection tests.                  |

**Reads but does not own:**

* `ltagent.live.graph_schema` (Agent 1) — graph is the source of truth.
* `ltagent.live.edit_result` (Agent 2) — for op error codes.
* `ltagent.security` (Phase 10, shared) — for `safe_resolve_under`.

**Hard rule:** all disk writes go through `ltagent.security.safe_resolve_under`
or an equivalent guard from `ltagent.security` (Agent 0 keeps the
shared security module stable). Project writes that bypass
`safe_resolve_under` are a hard fail in review.

---

## 5. Agent 4 — Math Core

**Mission:** the deterministic calculation engine (plan §13, §14, §15,
§16). Pure-Python, no subprocess, no LLM, no filesystem side effects
beyond reading optional reference tables.

**Owns:**

| Path                                            | Purpose                                    |
|-------------------------------------------------|--------------------------------------------|
| `src/ltagent/math_core/units.py`                | SPICE-like value parse / format.           |
| `src/ltagent/math_core/standard_values.py`      | E-series lookup.                           |
| `src/ltagent/math_core/formulas.py`             | Closed-form circuit formulas.              |
| `src/ltagent/math_core/formula_registry.py`     | Optional formula JSON registry loader.      |
| `src/ltagent/math_core/specs.py`                | Typed input specs for each formula.        |
| `src/ltagent/math_core/symbolic.py`             | Optional sympy-based helpers (no sympy at |
|                                                 | runtime unless the optional extra is on).  |
| `src/ltagent/math_core/mna.py`                  | Optional MNA helpers (numpy optional).     |
| `src/ltagent/math_core/optimizer.py`            | Deterministic value-space optimizer.       |
| `src/ltagent/math_core/tolerance.py`            | E-series tolerance analysis.               |
| `src/ltagent/math_core/calculation_report.py`   | `calculation.json` + `calculation.md` writer. |
| `tests/test_math_units.py`                      | Units parsing round-trips.                 |
| `tests/test_standard_values.py`                | E-series coverage.                         |
| `tests/test_math_formulas.py`                  | Per-formula golden values.                 |
| `tests/test_formula_correctness.py`            | Cross-check vs. textbook / SPICE reference.|

**Hard rule:** math_core must remain pure-Python at the default
extras level. `numpy`, `sympy`, and `scipy` are only allowed behind
optional extras (`pip install "ltspice-ai-agent[math]"`); the public
functions degrade to pure-Python implementations when the extra is
not installed. Agent 4 raises a structured
`MathCoreOptionalDependencyMissing` error rather than importing
`numpy` at module top level when the extra is off.

---

## 6. Agent 5 — Simulation + Verification

**Mission:** wrap the existing `ltagent.runner` + `ltagent.log_parser`
with the live-edit-aware simulation loop and the formula-vs-simulation
verification engine (plan §11, §17).

**Owns:**

| Path                                       | Purpose                                          |
|--------------------------------------------|--------------------------------------------------|
| `src/ltagent/live/sim_loop.py`             | Run-and-verify driver.                           |
| `src/ltagent/live/measurements.py`         | Measurement extraction on top of `log_parser`.   |
| `src/ltagent/live/verification.py`         | Verification engine + confidence scoring.        |
| `src/ltagent/math_core/verification_math.py` | Tolerance math shared with `live.verification`. |
| `tests/test_live_verification.py`          | End-to-end verification golden tests.            |

**Reads but does not own:**

* `ltagent.runner` (Phase 3, shared).
* `ltagent.log_parser` (Phase 4, shared).
* `ltagent.live.project` (Agent 3).
* `ltagent.math_core.*` (Agent 4).

**Hard rule:** Agent 5 does not invoke LTspice directly. All
subprocess calls go through `ltagent.runner.run_simulation` (Phase 3).
Wine / LTspice path detection is `ltagent.runner`'s responsibility, not
Agent 5's. The Phase 10 security rules apply — no `shell=True`,
no path traversal.

---

## 7. Agent 6 — MCP Tools

**Mission:** expose the live-edit surface through MCP (plan §11).
Tool body functions live in `ltagent.mcp_live_tools` and are
registered in `ltagent.mcp_server`.

**Owns:**

| Path                                       | Purpose                                          |
|--------------------------------------------|--------------------------------------------------|
| `src/ltagent/mcp_live_tools.py`            | Tool-level pure functions (no FastMCP import).   |
| `tests/test_mcp_live_tools.py`             | Tool-function unit tests.                        |

**Reads but does not own (must not modify in Agent 6's branch):**

* `src/ltagent/mcp_server.py` (shared entry point — see §9 below).
* `src/ltagent/security.py` (shared, Phase 10).

**Hard rule:** Agent 6 only adds **function-level glue** to
`mcp_live_tools.py`. Wiring a tool into the FastMCP server
(`@mcp_server.tool(...)` decorators and the `ltagent-mcp` entry
point) is Agent 0's responsibility, because it touches the shared
`mcp_server.py` that all eight agents depend on.

The integration request from Agent 6 → Agent 0 to wire a new tool is
one of the formal hand-offs listed in §10.

---

## 8. Agent 7 — Test + QA

**Mission:** the test strategy for the File-Based Live Editing + Math
Core workstream (plan §23). Owns the acceptance suites, the
`live_skip` / `math_core_skip` markers, and the QA report.

**Owns:**

| Path                                       | Purpose                                          |
|--------------------------------------------|--------------------------------------------------|
| `tests/conftest.py`                        | Shared fixtures (additive — no breaking change). |
| `tests/__init__.py`                        | Test package marker (additive).                  |
| `tests/test_path_traversal.py`             | Cross-cutting security regression tests.        |
| `tests/test_measurements.py`               | Cross-cutting measurement-extraction tests.      |
| `docs/agent_reports/tests_qa.md`           | QA report + risk register.                       |

**Hard rule:** Agent 7 does not edit `src/`. Bugs found by Agent 7
are filed in §6 of `tests_qa.md` ("Open Bugs") and routed back to the
owning agent. Agent 7 may add new test files and new `conftest.py`
fixtures, but never modifies an existing source file.

**Marker convention (Agent 7 owns):**

* `pytest.mark.live_skip` — test depends on a live module that may
  not be wired yet.
* `pytest.mark.math_core_skip` — test depends on a math_core module
  that has not landed.
* `pytest.mark.requires_ltspice` — test needs a real LTspice
  install. Skipped by default in CI; opt in with
  `pytest -m requires_ltspice`.

---

## 9. Agent 8 — Docs

**Mission:** user-facing documentation, examples, and ADRs for the
live-edit + math workstream (plan §5, §20, §26).

**Owns:**

| Path                                       | Purpose                                          |
|--------------------------------------------|--------------------------------------------------|
| `docs/examples/` (concrete examples)       | Worked IRs / graphs / calculations.              |
| `docs/agent_reports/docs.md`               | Docs Agent's own report.                         |
| `docs/adr/0002-circuit-graph.md`           | ADR for the graph schema.                        |
| `docs/adr/0003-file-based-live-editing.md` | ADR for live editing.                            |
| `docs/adr/0004-math-core-and-verification.md` | ADR for math core.                             |
| `README.md` additions limited to the **Docs** section | Cross-link maintenance.                |

**Hard rule:** Agent 8 does **not** modify `pyproject.toml`,
`src/ltagent/mcp_server.py`, or any file under `src/ltagent/live/` or
`src/ltagent/math_core/`. Docs live in `docs/` only.

---

## 10. Integration request protocol

When an agent needs a change in a file it does not own, the request
goes in the **Integration Requests** section at the bottom of that
agent's own `docs/agent_reports/<agent>.md` file. Format:

```markdown
### Integration Request — <short title>

- **From:** Agent N
- **To:**   Agent M
- **File:** `path/to/file.py`
- **Why:**  one sentence
- **What:** one or two-sentence spec of the requested change.
- **Risk:** low / medium / high
- **Status:** pending | accepted | declined
```

Agent 0 reviews the integration queue during the merge to `main`.
Cross-agent merges without a recorded request are a review failure.

---

## 11. Conflict resolution

When two agents edit the same file:

1. The agent whose **Owns** entry lists that file wins by default.
2. If neither agent owns the file (a shared file), Agent 0 decides.
3. If both agents have a legitimate claim (e.g. Agent 4 and Agent 5
   both need to change `verification.py`), Agent 0 picks one of the
   following resolutions and records it in `integrator.md`:
   * **A** — promote the file to shared and Agent 0 owns the merge.
   * **B** — keep ownership with the upstream agent; the downstream
     agent files an Integration Request.
   * **C** — split the file by responsibility boundary.

Default resolution is **B**. Resolution **A** is reserved for files
that more than one agent genuinely edits in every release cycle.

---

## 12. Out-of-scope shared files

These files are **never** edited by an individual agent's branch.
Agent 0 reviews and merges them.

* `src/ltagent/mcp_server.py` — MCP entry point (Agent 6 wires
  functions in `mcp_live_tools.py`; Agent 0 registers the FastMCP
  decorators).
* `src/ltagent/security.py` — shared security helpers (Phase 10).
* `src/ltagent/cli.py` — only Agent 0 may add a new top-level
  subcommand; subcommand bodies are owned by the agent that owns
  the underlying module.
* `pyproject.toml` — Agent 0 only.
* `README.md` — Agent 0 and Agent 8 only.
* `AGENTS.md` — Agent 0 only.
* `CLAUDE.md`, `OPENCODE.md`, `MCP.md` — Agent 0 only.
* `docs/PROJECT_PLAN.md`, `docs/SPEC.md`, `docs/security.md`,
  `docs/runner.md`, `docs/runner_troubleshooting.md`,
  `docs/ltspice_setup.md`, `docs/mcp_setup.md` — Agent 0 only.

---

## 13. Branch + merge rules

1. One agent, one branch, one `docs/agent_reports/<agent>.md`. Do not
   create personal scratch branches.
2. Do not commit on `main`.
3. Do not commit on another agent's branch.
4. Do not push to `origin` until Agent 0 has reviewed.
5. Land small, reviewable commits. Avoid squashing history; Agent 0
   reads commit messages during review.
6. If a commit touches a file outside the agent's **Owns** list,
   revert that change in the same commit and file an Integration
   Request.
