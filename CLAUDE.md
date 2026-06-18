# CLAUDE.md

This is a thin shim. The canonical agent guide for this repository is
[`AGENTS.md`](AGENTS.md). Read that first; everything below is just
Claude Code–specific pointers.

## Claude Code pointers

- The project uses `pip install -e ".[dev]"` for local development.
  Do not switch to uv/poetry without an ADR.
- Prefer `pytest -q` for short test output. Use `pytest -m integration`
  only when LTspice is verified working.
- Use `ruff check . --fix` and `mypy src --strict` before considering
  a task done.
- The CLI is registered as the `ltagent` script via the
  `[project.scripts]` entry in `pyproject.toml`. After `pip install -e
  .[dev]`, `ltagent` is on `PATH`.
- **Never bypass `ltagent` to invoke `wine` or `XVIIx64.exe` directly.**
  Every simulation must go through the runner so that doctor-style
  diagnostics, timeouts, and structured output are preserved.
- For MCP, the server entry point will be `ltagent-mcp` (registered in
  Phase 10). Until then, do not configure Claude Code to spawn an MCP
  server from this repo.

## Working agreement

Follow `AGENTS.md` for hard rules (no LLM-written `.asc` coordinates,
no arbitrary shell, no path traversal, no `shell=True`, JSON output
contract on every command). If a Claude Code-specific question is not
answered here, prefer the project-level rule in `AGENTS.md`.
