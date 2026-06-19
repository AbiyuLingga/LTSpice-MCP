# MCP.md

This is a thin shim. The canonical agent guide for this repository is
[`AGENTS.md`](AGENTS.md). Read that first; everything below is just
MCP-specific pointers.

## MCP status

**The stdio MCP server is integrated through the Phase 13 prototype.**
The `ltagent-mcp` script exposes 24 curated tools and 14 curated
resources backed by the same `ltagent` Python core the CLI uses.

For installation, configuration, and per-client wiring, see
[`docs/mcp_setup.md`](docs/mcp_setup.md). The short version:

```bash
pip install "ltspice-ai-agent[mcp]"
ltagent-mcp --check
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

## Server surface

| Surface | Count | Included capabilities |
|---|---:|---|
| Analog MCP tools | 10 | Project creation/inspection, deterministic netlist and schematic generation, simulation/results, layout, and template evaluation/promotion |
| Live editing + Math Core tools | 8 | Open/inspect/edit, snapshot/restore, run-and-verify, calculate, and explain |
| Tiny8 digital tools | 6 | Plan/create, assemble, HDL simulation, synthesis check, and inspection |
| Resources | 14 | 8 analog project/template resources plus 6 digital capability/project resources |

Run `ltagent-mcp --list-tools` and `ltagent-mcp --list-resources` for
the authoritative machine-readable names.

Transport: **stdio only**. No HTTP / SSE in v1 (plan §17.2).

## Working agreement

Follow `AGENTS.md` for hard rules (no LLM-written `.asc`
coordinates, no arbitrary shell, no path traversal, no `shell=True`,
JSON output contract on every command). If an MCP-specific question
is not answered here, prefer the project-level rule in `AGENTS.md`,
then `docs/security.md`, then `docs/mcp_setup.md`.
