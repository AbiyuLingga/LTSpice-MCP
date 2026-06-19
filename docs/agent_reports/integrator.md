# Plan Understanding Checklist

## Core Goal

- [x] Provide file-based creation and incremental editing of LTspice projects.
- [x] Keep `circuit.graph.json` as the primary editable source of truth.
- [x] Derive validated IR, netlist, schematic, calculations, and verification artifacts from structured inputs.
- [x] Use deterministic math and simulator evidence instead of LLM-generated numeric claims.

## Required Modules

- [x] Circuit Graph models, validation, and Graph/IR conversion.
- [x] Safe edit operations with structured results.
- [x] Live project structure, generation workflow, metadata, and edit history.
- [x] Snapshot, restore, and diff support.
- [x] Math Core unit parser, formulas, preferred-value selector, and calculation reports.
- [x] Measurement abstraction and formula-versus-simulation verification.
- [x] Thin MCP adapters for live operations.
- [x] Unit, integration, golden, failure, and documentation coverage.

## Required Safety Rules

- [x] No arbitrary shell execution or unrestricted file access.
- [x] Resolve and constrain every project path under the configured projects/workspace root.
- [x] Reject traversal, foreign absolute paths, and symlink escapes.
- [x] Do not expose `allow_outside_workspace` through public MCP inputs.
- [x] Validate directives, includes, measurements, operations, and graph data at boundaries.
- [x] Snapshot before mutation and record edits in an append-only history.
- [x] Apply simulation timeouts and return structured failures.
- [x] Never generate production `.asc` coordinates from unconstrained agent text.

## Required Math Accuracy Rules

- [x] Parse SPICE/SI prefixes deterministically, including `Meg` and Unicode micro.
- [x] Reject dimension mismatches, ambiguous suffixes, non-finite values, and invalid domains.
- [x] Implement validated formulas for divider, RC filters, op-amp gains, LED resistor, and ideal converters.
- [x] Select supported E-series values and report ideal, selected, predicted, and error values.
- [x] Keep assumptions explicit and separate formula prediction from simulation evidence.
- [x] Treat the verification engine—not prose—as pass/fail authority.

## Required MCP Rules

- [x] MCP is a schema-driven adapter; business logic remains in core modules.
- [x] Inputs are narrow and validated; outputs and errors are JSON-serializable and structured.
- [x] Live edits call edit operations, snapshots call snapshot APIs, calculations call Math Core, and run/verify calls verification APIs.
- [x] Existing curated tools remain compatible; no generic shell or file tools are introduced.

## Required Tests

- [x] Unit parsing, formula, standard-value, and calculation-report tests.
- [x] Graph validation and Graph/IR round-trip tests.
- [x] Edit operation, project consistency, snapshot/restore hash, and history tests.
- [x] Measurement and formula-versus-simulation verification tests using fake runners.
- [x] MCP contract, JSON serialization, traversal, absolute-path, and symlink-escape tests.
- [x] Full pytest, Ruff, and mypy validation; LTspice-dependent tests skip cleanly when unavailable.

## Integration Priorities

1. Match the main plan and preserve existing public contracts.
2. Close security and path-boundary gaps.
3. Correct Math Core and domain-validation defects.
4. Resolve import/API conflicts with minimal changes.
5. Restore focused and full test-suite health.
6. Verify MCP output serialization and adapter boundaries.
7. Verify snapshot/undo atomicity and project consistency.
8. Correct documentation claims and record remaining limitations.
