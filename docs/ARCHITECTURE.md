# Architecture

## Operating Model

One main AI agent owns decisions and serialized edits. Deterministic modules
own validation, generation, execution, parsing, verification, and reporting.
No independent AI subagent may modify project direction or files in parallel.

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
```

Analog core currently lives in `ir.py`, `netlist.py`, `asc.py`, `runner.py`,
`log_parser.py`, `result.py`, `templates.py`, `live/`, and `math_core/`.
Digital core currently lives in the `digital_*.py` modules and
`digital_templates.py`. The flat layout is retained while it remains coherent;
the roadmap folder tree is not a mandate for cosmetic migration.

## Dependency Rules

1. Schemas and pure math do not import CLI or MCP.
2. Generators consume validated IR only.
3. Runners accept constrained typed requests and never user-authored commands.
4. Parsers convert tool artifacts into structured results.
5. CLI calls core APIs.
6. MCP calls the same core APIs and contains no unique business logic.
7. Templates become official only through explicit evaluation and promotion.

## Missing Target Contracts

`RequirementSpec` and `SystemIR` are planned contracts. They must be added
additively with JSON schema and tests before mixed-system or repair-loop work.

## Capability Classification

- Supported: acceptance tests and required tool evidence pass.
- Experimental: implemented but missing part of its real-tool or integration gate.
- Planned: roadmap only.
- Unsafe: simulation-only or refused for physical build guidance.

See `REPO_AUDIT.md` for the current milestone-by-milestone classification.
