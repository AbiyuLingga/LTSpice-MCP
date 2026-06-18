# ltspice-ai-agent

A local CLI and (later) MCP adapter for safely generating, simulating, and
inspecting LTspice circuits from structured input. Designed for AI coding
agents (Codex, Claude Code, OpenCode, Cursor, Cline) and humans who want
repeatable LTspice workflows without trusting an LLM to edit `.asc` files
directly.

> **Status:** Phase 0 (scaffolding). The runner, IR, netlist generator,
> schematic writer, template system, and MCP server arrive in later phases.
> See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the full plan.

## What works in Phase 0

- `ltagent --version` / `ltagent --help`
- `ltagent doctor [--json] [--simulate]` &mdash; reports Python, Wine,
  LTspice executable, workspace writability, and (optionally) attempts a
  tiny `.op` smoke simulation
- `ltagent init [DIR]` &mdash; create a new project workspace
- `ltagent config show|validate [--json]` &mdash; inspect the active config
- Structured JSON output contract for every command

## What does not work yet

- Circuit IR loading and validation (Phase 1)
- `.cir` netlist generation (Phase 2)
- LTspice batch simulation as part of a project workflow (Phase 3)
- `.log` / `.meas` parsing (Phase 4)
- `.asc` schematic generation (Phase 5)
- Template library, evaluator, promoter (Phases 6, 9)
- Natural language planner (Phase 8)
- MCP server (Phase 10)

## Supported OS / runtime

- **Linux** with LTspice XVII (or newer) installed under Wine 10+
- **Windows** with LTspice installed natively (planned; not exercised in Phase 0)
- Python 3.11 or newer

## Install

```bash
git clone https://github.com/abiyulinx/ltspice-ai-agent.git
cd ltspice-ai-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```bash
# Check whether the local LTspice setup is usable
ltagent doctor --json

# Without --simulate, doctor only inspects paths and versions.
ltagent doctor --simulate --json   # also runs a tiny .op sim (slow, may time out)

# Create a project workspace (later phases fill it with circuits)
ltagent init my_first_project

# Inspect the resolved configuration
ltagent config show --json
ltagent config validate --json
```

## Configuration

`ltagent` reads its configuration from the first file that exists, in order:

1. `./config.toml` (project-local override)
2. `~/.config/ltagent/config.toml` (user override)
3. Built-in defaults

If no file is present, defaults are used. See [`config.example.toml`](config.example.toml)
for every available field.

## Development

```bash
# Run the test suite (no LTspice required)
pytest

# Lint and typecheck
ruff check .
mypy src

# Build sdist + wheel
python -m build
```

## License

MIT. See [`LICENSE`](LICENSE).
