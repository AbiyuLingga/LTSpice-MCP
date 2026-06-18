# ltspice-ai-agent

A local CLI and MCP server for safely generating, simulating, and
inspecting LTspice circuits from structured input. Designed for AI
coding agents (Codex, Claude Code, OpenCode, Cursor, Cline) and
humans who want repeatable LTspice workflows without trusting an LLM
to edit `.asc` files directly.

> **Status:** Phase 11 (Advanced Analog Templates) complete. The CLI
> covers the full project workflow (Phases 0-9); the `ltagent-mcp`
> stdio server exposes 10 curated tools and 8 curated resources
> (Phase 10); the Circuit IR supports 6 new component kinds
> (diode, BJT, MOSFET, opamp) and 7 new analog topologies
> (inverting_opamp, noninv_opamp, comparator, diode_clipper,
> halfwave_rectifier, bridge_rectifier, transistor_switch) with
> hand-crafted official templates and deterministic .asc layouts
> (Phase 11). See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md)
> for the full plan.

## What works in Phase 11

- **Phase 0** &mdash; `ltagent --version`, `ltagent doctor [--json]
  [--simulate]`, `ltagent init [DIR]`, `ltagent config show|validate`
- **Phase 1** &mdash; Circuit IR v0.1 load/validate + JSON Schema
- **Phase 2** &mdash; `.cir` netlist generation (`ltagent netlist`)
- **Phase 3** &mdash; LTspice batch runner (`ltagent run`,
  configurable Wine + executable, timeout, structured `RunResult`)
- **Phase 4** &mdash; `.log` / `.meas` parser (`ltagent parse-log`,
  `ltagent result`)
- **Phase 5** &mdash; `.asc` schematic writer + layout checker
  (`ltagent asc`)
- **Phase 6** &mdash; Template library (`ltagent template
  list/show/match/audit/seed`)
- **Phase 7** &mdash; One-command project workflow (`ltagent create`,
  accepts IR file or natural-language prompt)
- **Phase 8** &mdash; Rule-based prompt planner (`ltagent plan`)
- **Phase 9** &mdash; Template evaluator + promoter (`ltagent template
  evaluate/promote/audit-promotability`)
- **Phase 10** &mdash; stdio MCP server (`ltagent-mcp`) with 10 tools
  + 8 resources; same Python core, no `run_shell`, no `.raw`
  exposure, structured JSON contract on every call
- **Phase 11** &mdash; 6 new component kinds (D / Q / M / X), 7 new
  analog topologies, 7 new hand-crafted official templates, 10
  total official templates. The netlist generator emits `.model`
  and `.subckt` blocks from the structured IR; the .asc writer
  has deterministic per-topology placers for every Phase 11
  topology.
- Structured JSON output contract for every command
- Centralised path / URI / slug validators in `ltagent.security`,
  shared by CLI and MCP

## What is intentionally not in v1

- HTTP / SSE MCP transport. v1 is stdio only (plan §17.2).
- Remote authentication / multi-user separation. Local-only product.
- Tools from plan §17.3 "Advanced later" (`query_raw_vector`,
  `assert_results`, `apply_template`, `snapshot_project`,
  `restore_snapshot`, `reject_template`, `render_schematic`,
  `render_waveform`).
- LLM-based prompt expansion of MCP tool inputs. MCP wraps the
  rule-based planner and the deterministic project orchestrator;
  it does not introduce a new LLM call.
- Phase 11+ advanced analog templates (op-amp, rectifier, ...).
- Phase 12 E-series value optimisation.

## Supported OS / runtime

- **Linux** with LTspice XVII (or newer) installed under Wine 10+
- **Windows** with LTspice installed natively (supported by the
  runner; Wine is optional)
- Python 3.11 or newer

## Install

```bash
git clone https://github.com/abiyulinx/ltspice-ai-agent.git
cd ltspice-ai-agent
python3 -m venv .venv
source .venv/bin/activate

# CLI only
pip install -e ".[dev]"

# CLI + MCP server (Phase 10)
pip install -e ".[dev,mcp]"
```

## Quickstart

```bash
# Check whether the local LTspice setup is usable
ltagent doctor --json
ltagent doctor --simulate --json   # also runs a tiny .op sim

# Inspect the resolved configuration
ltagent config show --json
ltagent config validate --json

# Validate a Circuit IR
ltagent ir validate examples/rc_lowpass.ir.json --json
ltagent ir validate examples/inverting_opamp.ir.json --json
ltagent ir validate examples/halfwave_rectifier.ir.json --json

# Render a netlist + schematic
ltagent netlist examples/rc_lowpass.ir.json --out projects/demo/circuit.cir --json
ltagent asc     examples/rc_lowpass.ir.json --out projects/demo/circuit.asc --json
ltagent netlist examples/inverting_opamp.ir.json --out projects/opamp/circuit.cir --json
ltagent asc     examples/inverting_opamp.ir.json --out projects/opamp/circuit.asc --json

# Create a full project from an IR file or a prompt
ltagent create examples/rc_lowpass.ir.json --run --json
ltagent create examples/inverting_opamp.ir.json --run --json
ltagent create "make RC low pass cutoff 1kHz" --json

# Templates
ltagent template list --json
ltagent template match examples/rc_lowpass.ir.json --json

# MCP server (after `pip install "ltspice-ai-agent[mcp]"`)
ltagent-mcp --help
ltagent-mcp --check
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

## Configuration

`ltagent` reads its configuration from the first file that exists, in order:

1. `./config.toml` (project-local override)
2. `~/.config/ltagent/config.toml` (user override)
3. Built-in defaults

If no file is present, defaults are used. See [`config.example.toml`](config.example.toml)
for every available field.

## MCP client setup

See [`docs/mcp_setup.md`](docs/mcp_setup.md) for per-client wiring
instructions (Claude Code, OpenCode, Cursor, Cline, others).

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