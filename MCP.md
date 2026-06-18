# MCP.md

This is a thin shim. The canonical agent guide for this repository is
[`AGENTS.md`](AGENTS.md). Read that first; everything below is just
MCP-specific pointers.

## MCP status

**Not yet implemented.** A Model Context Protocol server for `ltagent`
is **Phase 10** of [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md).
Phase 0 (this phase) only delivers the `ltagent` CLI.

Until Phase 10 lands, do not configure any MCP client (Claude Code,
OpenCode, Cursor, Cline, etc.) to connect to an MCP server spawned
from this repository. There is no `ltagent-mcp` script on `PATH` yet.

## When Phase 10 lands

The MCP server will:

- Use **local stdio transport only**. No remote / HTTP / SSE transport
  in v1.
- Expose only **curated tools** (no `run_shell`, no `execute_python`,
  no arbitrary file read/write). The full candidate list is in
  `docs/PROJECT_PLAN.md` section 17.3.
- Expose **resources** through controlled URI schemes
  (`ltagent://projects/...`, `ltagent://templates/...`) with path
  traversal rejected.
- Wrap the same `ltagent` Python core that the CLI uses. No business
  logic may live only in the MCP layer.

## Working agreement

Follow `AGENTS.md` for hard rules (no LLM-written `.asc` coordinates,
no arbitrary shell, no path traversal, no `shell=True`, JSON output
contract on every command). If an MCP-specific question is not
answered here, prefer the project-level rule in `AGENTS.md`.
