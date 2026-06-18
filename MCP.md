# MCP.md

This is a thin shim. The canonical agent guide for this repository is
[`AGENTS.md`](AGENTS.md). Read that first; everything below is just
MCP-specific pointers.

## MCP status

**Phase 10 (MCP Server v1) is complete.** The `ltagent-mcp` script
on `PATH` runs a stdio MCP server that exposes 10 curated tools and
8 curated resources backed by the same `ltagent` Python core the CLI
uses.

For installation, configuration, and per-client wiring, see
[`docs/mcp_setup.md`](docs/mcp_setup.md). The short version:

```bash
pip install "ltspice-ai-agent[mcp]"
ltagent-mcp --check
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

## Server surface

| Tools (10) | Resources (8) |
|---|---|
| `create_project`, `inspect_project`, `generate_netlist`, `generate_schematic`, `run_simulation`, `read_measurements`, `check_layout`, `find_template`, `evaluate_template_candidate`, `promote_template` | `ltagent://projects`, `ltagent://projects/{id}/{metadata,result,circuit-ir,netlist,log}`, `ltagent://templates`, `ltagent://templates/{id}/metadata` |

Transport: **stdio only**. No HTTP / SSE in v1 (plan §17.2).

## Working agreement

Follow `AGENTS.md` for hard rules (no LLM-written `.asc`
coordinates, no arbitrary shell, no path traversal, no `shell=True`,
JSON output contract on every command). If an MCP-specific question
is not answered here, prefer the project-level rule in `AGENTS.md`,
then `docs/security.md`, then `docs/mcp_setup.md`.