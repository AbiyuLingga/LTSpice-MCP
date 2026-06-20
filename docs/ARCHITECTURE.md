# Architecture

## Operating Model

One main AI agent owns decisions and serialized edits. Deterministic modules
own validation, generation, execution, parsing, verification, and reporting.
No independent AI subagent may modify project direction or files in parallel.

## Workbench v1 Target Layers (ADR 0006)

The active product line is the **local-first AI Hardware Design Workbench**.
The new layers sit on top of the existing Python core:

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

Canonical project v2 layout:

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

Required engine methods:
`engine.handshake`, `project.{create,open,validate,migrate}`,
`design.{get,applyChanges,undo,redo}`, `job.{start,status,cancel}`,
`artifact.readSlice`, `provider.{list,save,delete,selfTest}`,
`ai.{contextPreview,plan,repair}`.

## Current Layers

```text
User intent
-> deterministic analog/digital planner
-> CircuitIR or DesignIR
-> deterministic artifact generator
-> bounded runner/parser
-> structured result and report
-> CLI
-> curated stdio MCP adapter
-> [workbench v1] versioned project + Tauri shell + AI workflow
```

Analog core currently lives in `ir.py`, `netlist.py`, `asc.py`, `runner.py`,
`log_parser.py`, `result.py`, `templates.py`, `live/`, and `math_core/`.
Digital core currently lives in the `digital_*.py` modules and
`digital_templates.py`. Workbench v1 lives in `workbench.py`,
`engine_server.py`, `live/project.py`, `live/graph_schema.py`, and
`apps/desktop/`. The flat layout is retained while it remains coherent;
the roadmap folder tree is not a mandate for cosmetic migration.

## Dependency Rules

1. Schemas and pure math do not import CLI or MCP.
2. Generators consume validated IR only.
3. Runners accept constrained typed requests and never user-authored commands.
4. Parsers convert tool artifacts into structured results.
5. CLI calls core APIs.
6. MCP calls the same core APIs and contains no unique business logic.
7. Templates become official only through explicit evaluation and promotion.
8. The Tauri/React desktop shell never imports Python directly. The
   Rust `engine_request` shim is the only bridge; the Python engine
   keeps its `projects_root` and stdio JSON-RPC contract.

## Missing Target Contracts

`RequirementSpec`, `AIContextManifest`, `AIProposal`, `ChangeSet`,
`SchematicView`, `DigitalDesignIR`, `JobManifest`, `RunManifest`, and
`ResultBundle` are planned contracts. They must be added additively
with JSON schema and tests before mixed-system or repair-loop work.
The existing 1.0 project manifest (`workbench.py`) is the source for
the staged 1.0 -> 2.0 migration.

## Capability Classification

- Supported: acceptance tests and required tool evidence pass.
- Experimental: implemented but missing part of its real-tool or integration gate.
- Planned: roadmap only.
- Unsafe: simulation-only or refused for physical build guidance.

See `REPO_AUDIT.md` for the current milestone-by-milestone classification.
