# OPENCODE.md

This is a thin shim. The canonical agent guide for this repository is
[`AGENTS.md`](AGENTS.md). Read that first; everything below is just
OpenCode-specific pointers.

## OpenCode pointers

- This repo's `pyproject.toml` is the source of truth for entry points.
  The CLI script `ltagent = "ltagent.cli:main"` is registered on `pip
  install -e ".[dev]"` and is the only supported way to invoke the
  tool.
- The CLI returns structured JSON on every `--json` command. OpenCode
  agents that need machine-readable output should always pass `--json`
  and never parse the human-readable form.
- The project does not ship an MCP server in Phase 0. OpenCode should
  invoke the `ltagent` CLI directly via the `bash` tool. A formal MCP
  adapter is Phase 10.
- Skill discovery: skills live at `~/.claude/skills/` (and equivalent
  for other agents). This repo does not ship its own `.opencode/`
  configuration in Phase 0.

## Working agreement

Follow `AGENTS.md` for hard rules (no LLM-written `.asc` coordinates,
no arbitrary shell, no path traversal, no `shell=True`, JSON output
contract on every command). If an OpenCode-specific question is not
answered here, prefer the project-level rule in `AGENTS.md`.
