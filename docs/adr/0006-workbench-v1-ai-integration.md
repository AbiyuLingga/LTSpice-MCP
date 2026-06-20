# ADR 0006 — Workbench v1 with AI-Assisted Design Workflow

- Status: Accepted
- Date: 2026-06-20
- Supersedes: none
- Extends: ADR 0005 (local desktop workbench sidecar), ADR 0004 (hybrid HDL/SPICE)

## Context

The repository already implements a deterministic analog + Tiny8 digital
pipeline exposed through CLI and a curated stdio MCP adapter. ADR 0005
introduced a Tauri/React desktop workbench that wraps the same Python
core through a JSON-RPC sidecar (`ltagent-engine`) and the first
versioned project manifest. The next user-visible product step is to
let users prompt the application to draft a circuit or HDL block and
review, edit, simulate, and persist the result, with Codex able to
control the same project from a second seat.

The previous roadmap documents (`AI_HARDWARE_AGENT_ROADMAP.md`,
`SINGLE_AGENT_EXECUTION_PLAN.md`, `REPO_AUDIT.md`) describe the long
arc but do not lock down the boundary between:

- the deterministic engine (Python core, runner, parsers, IR),
- the AI provider adapter (OpenAI Responses + OpenAI-compatible),
- the desktop workbench UI (Tauri/React, PixiJS canvas later),
- the Codex MCP surface (existing, to be extended with workbench v2).

## Decision

We formalise **Workbench v1** as the next product release line. It is
intentionally not framed as a full LTspice, KiCad, or FPGA suite; the
honest market-positioning name is "local-first AI Hardware Design
Workbench".

### Layer contract

```text
React/PixiJS UI  or  Codex
        |
Tauri IPC  or  MCP stdio
        |
Versioned Engine Service  (ltagent-engine JSON-RPC, NDJSON)
        |
RequirementSpec -> AIProposal -> ChangeSet
        |
CircuitGraph / DigitalDesignIR  +  SchematicView
        |
Deterministic generators
        |
Bounded jobs: ngspice, Icarus, Verilator, Yosys
        |
RunManifest + ResultBundle + waveform chunks
```

### Canonical project v2 layout

```text
hardware.project.json
design/requirements.json
design/analog/main.graph.json
design/schematic/main.view.json
design/digital/main.digital.json
design/system.json
firmware/
verification/
runs/<runId>/
.workbench/history/
.workbench/snapshots/
.workbench/transactions/
```

The current 1.0 manifest (`hardware.project.json` + separate
`design/*` documents under `workbench.py`) is the migration source
and must round-trip through the 2.0 staged migrator.

### Engine surface (v1)

Required engine methods:

```text
engine.handshake
project.create | project.open | project.validate | project.migrate
design.get     | design.applyChanges | design.undo | design.redo
job.start      | job.status | job.cancel
artifact.readSlice
provider.list  | provider.save | provider.delete | provider.selfTest
ai.contextPreview | ai.plan | ai.repair
```

All methods carry `{code, message, details, retryable}` errors.

### Hard invariants (carry-overs from ADR 0004 and ADR 0005)

1. The AI never writes `.asc`, generated Verilog, arbitrary files, or
   shell commands directly. It only produces a typed `RequirementSpec`
   and a typed `ChangeSet`.
2. All paths live under the canonical projects root and are validated
   through `Path.resolve()`.
3. Every subprocess is launched through a registered tool ID with an
   argv list. `shell=True` is forbidden.
4. The frontend receives no filesystem or shell capability. The
   Tauri Rust shim is the only bridge.
5. API keys never appear in project files, logs, artefacts, Git, or
   frontend state. Providers are stored in the OS keyring.
6. MCP never exposes `run_shell`, `execute_python`, generic
   read/write, raw-path access, or unrestricted network.
7. The application distinguishes `success`, `failed`, `skipped`,
   `unsupported`, and `timed-out` in every structured result.
8. Project migration is snapshot-first, atomic, rollbackable, and
   never overwrites the source files of an old project.

## Consequences

- The existing Python core, CLI, MCP adapter, and ADR 0005 sidecar
  remain the foundation. Workbench v1 is the next layer on top, not
  a replacement.
- The 12-phase execution plan attached to this ADR
  (`docs/SINGLE_AGENT_EXECUTION_PLAN.md`, "Master Execution Brief"
  revision 2026-06-20) becomes the source of truth for sequencing.
  The previous "Single Agent Roadmap" sections are retained as
  reference but no longer drive work.
- Phase 0 (this document's release) only stabilises the baseline,
  records the dirty worktree, and produces an honest capability
  matrix. No schema v2, no new runtime dependencies, no architectural
  replacements are introduced in Phase 0.
- The desktop application must not ship as a replacement for LTspice,
  KiCad, FPGA suites, or professional EDA. The product copy must use
  the "local-first AI Hardware Design Workbench" framing and
  enumerate the supported v1 surface explicitly.
- Future ADRs will lock down RequirementSpec, AIContextManifest,
  AIProposal, JobManifest, and RunManifest shapes before their
  corresponding implementation phases.

## Exit gates

Workbench v1 ships only when the production acceptance in the
"Single-Agent Execution Plan" Master Execution Brief is green. The
production gate is a fresh Ubuntu VM running the installer (`.deb`
or AppImage) without source repository, npm, uv, or developer PATH
present, and completing the full create/open/migrate/recover/AI
preview/propose/diff/accept/manual edit/simulate/waveform/Codex path.
