# ltspice-ai-agent

A local CLI and MCP server for safely generating, simulating, and
inspecting LTspice circuits from structured input. Designed for AI
coding agents (Codex, Claude Code, OpenCode, Cursor, Cline) and
humans who want repeatable LTspice workflows without trusting an LLM
to edit `.asc` files directly.

> **Status:** Phase 12 (Tiny8) is complete and the Phase 13
> File-Based Live Editing + Math Core foundation is integrated as a
> prototype. The `ltagent-mcp` stdio server exposes 24 curated tools
> and 14 curated resources. The Circuit IR supports 6 new component kinds
> (diode, BJT, MOSFET, opamp) and 7 new analog topologies
> (inverting_opamp, noninv_opamp, comparator, diode_clipper,
> halfwave_rectifier, bridge_rectifier, transistor_switch) with
> hand-crafted official templates and deterministic .asc layouts
> (Phase 11). See
> [`docs/AI_HARDWARE_AGENT_ROADMAP.md`](docs/AI_HARDWARE_AGENT_ROADMAP.md)
> for the active single-agent roadmap,
> [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the historical analog MVP,
> and
> [`ltspice_file_based_live_editing_math_plan.md`](ltspice_file_based_live_editing_math_plan.md)
> for the live-editing workstream.

## What works

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
- **Phase 10** &mdash; stdio MCP server (`ltagent-mcp`); same Python
  core, no `run_shell`, no `.raw`
  exposure, structured JSON contract on every call
- **Phase 11** &mdash; 6 new component kinds (D / Q / M / X), 7 new
  analog topologies, 7 new hand-crafted official templates, 10
  total official templates. The netlist generator emits `.model`
  and `.subckt` blocks from the structured IR; the .asc writer
  has deterministic per-topology placers for every Phase 11
  topology. The bundled official library auto-seeds on the first
  read path; `ltagent template seed` remains available as the
  explicit, idempotent form.
- **Phase 12** &mdash; deterministic Tiny8 planning, generation,
  assembly, simulation, synthesis checks, and roadmap reporting.
- **Phase 13 prototype** &mdash; Circuit Graph, safe edit operations,
  snapshots/history, Graph-to-IR generation, deterministic Math Core,
  formula-versus-simulation verification, and 8 live/math MCP tools.
- **Single-agent roadmap Milestone 1 (in progress)** &mdash; an experimental
  `led_resistor` CircuitIR example now generates deterministic netlist and ASC
  artifacts; planner and official-template support remain planned.
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
- Multi-variable optimizer, tolerance/Monte Carlo analysis, visual
  diff, and the planned live CLI remain future work.

## Supported OS / runtime

- **Linux** with LTspice XVII (or newer) installed under Wine 10+
- **Windows** with LTspice installed natively (supported by the
  runner; Wine is optional)
- Python 3.11 or newer

> **Note on `ltagent doctor --simulate`:** the smoke simulation
> invokes the configured LTspice executable with a tiny `.op`
> circuit. On hosts where Wine/LTspice batch mode is unreliable
> (see [`docs/runner_troubleshooting.md`](docs/runner_troubleshooting.md))
> the smoke run returns a structured ``LTSPICE_TIMEOUT`` error
> rather than crashing. This is the expected, diagnosable outcome
> &mdash; the doctor exists precisely to surface it.

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

# Create a full project from an IR file or a prompt.
# The first call in a fresh workspace auto-seeds the bundled
# official templates; subsequent calls are no-ops.
ltagent create examples/rc_lowpass.ir.json --run --json
ltagent create examples/inverting_opamp.ir.json --run --json
ltagent create "make RC low pass cutoff 1kHz" --json

# Templates (auto-seed is the default; ``seed`` is the explicit form)
ltagent template list --status official --json
ltagent template match examples/rc_lowpass.ir.json --json
ltagent template seed --json
ltagent template audit --json

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

# Regenerate the Circuit IR JSON Schema (writes both
# schemas/circuit_ir.schema.json and the packaged
# src/ltagent/resources/circuit_ir.schema.json in lockstep)
PYTHONPATH=src .venv/bin/python tools/generate_schema.py
```

## License

MIT. See [`LICENSE`](LICENSE).# LTSpice-MCP
