# AGENTS.md

Operational guide for AI coding agents working inside this repository.
This file is the canonical entry point. `CLAUDE.md`, `OPENCODE.md`, and
`MCP.md` are thin shims that point here.

## What this project is

`ltspice-ai-agent` is a local Python CLI plus (later) an MCP server that
lets AI agents safely generate, simulate, inspect, and reuse LTspice
circuits. The Python core owns all validation, file generation, and
simulation execution. The agent proposes intent, parameters, and topology
only.

The full plan lives in [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). The
current development phase is **Phase 8** (Rule-Based Planner).
Do not implement Phase 9+ features until Phase 8 acceptance is met.

**Phase 7 (Create Project Workflow) is complete.** `ltagent create` takes
either an IR JSON file path or a natural-language prompt, runs the planner,
and writes the full project artefact set. Refusal paths return the
`create.refused` structured payload.

**Phase 8 (Rule-Based Planner) is complete.** `ltagent plan "<prompt>"`
parses a small, deterministic set of English and Indonesian prompts into a
validated `CircuitIR`. See `src/ltagent/planner.py` and the public API:
`plan_prompt(text: str) -> CircuitIR | PlannerRefusal`. The CLI subcommand
mirrors that. Do not introduce an LLM-backed planner before documenting
the contract change in an ADR; Phase 8 is intentionally rule-based.

## Current phase boundary

**Phase 8 — DONE WHEN:**

- `ltagent plan "<prompt>" --json` parses a deterministic set of English
  and Indonesian prompts into a validated `CircuitIR`
- The planner's IR round-trips through `validate_dict` cleanly (covered
  by `tests/test_planner.py::test_planner_output_round_trips_through_ir`)
- Supported prompts cover the three MVP topologies:
  - `make voltage divider 12V to 5V` / `buat pembagi tegangan 12V ke 5V`
  - `make RC low-pass cutoff 1kHz [dengan C 100nF]`
  - `make RC high-pass cutoff 500Hz [dengan C 1uF]` / `... dengan R 1k`
- Unsupported prompts return `PlannerRefusal` with one of the stable
  codes `UNSUPPORTED_PROMPT`, `MISSING_PARAM`, `INVALID_VALUE` and
  enumerate the supported topologies
- Generated project names match the IR slug pattern
  (`^[a-z][a-z0-9_-]{0,63}$`)
- All planner tests pass with **zero** LTspice / Wine invocations
- `ruff check src/ltagent/planner.py tests/test_planner.py` is clean
- `mypy src/ltagent/planner.py` is clean

**Out of scope for Phase 8 (do not implement):**

- LLM-backed prompt parsing. The planner is rule-based by design
  (plan section 16.1) so the prompt -> IR boundary is testable and
  reproducible.
- Multi-turn prompt refinement or context carry-over.
- Templates with components beyond the MVP set
  (op-amp, BJT, MOSFET, subckt). These are Phase 11.
- The project workflow glue (`ltagent create` accepting a prompt
  directly). Phase 7 owns that; Phase 8 only exposes `plan_prompt`
  and the matching CLI subcommand.
- Optimisation of values against E-series or common capacitor lists
  (Phase 12).

## Hard rules for agents

1. **Never generate production `.asc` coordinate lines.** The schematic
   writer is deterministic. The agent may request topology and parameters;
   Python writes the final layout. This is a project invariant, not a
   preference.
2. **Never execute arbitrary shell from a user prompt.** All `subprocess`
   calls in this codebase use list args (no `shell=True`). The runner
   launches only the configured LTspice executable.
3. **All paths resolved with `Path.resolve()` and rejected on traversal.**
   No path may escape the configured workspace.
4. **All structured commands return the JSON contract** (see
   `ltagent/cli.py` and `docs/SPEC.md`). Never infer success from prose.
5. **Default `safe_mode = true`.** Reject unsupported SPICE directives,
   reject writes outside the workspace, reject MCP resource paths that
   traverse.

## Critical local context (this machine)

- LTspice XVII is installed under Wine at
  `/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe`
  (note the space in `Program Files` — Wine command construction must
  quote it).
- `wine` is at `/opt/wine-stable/bin/wine` but **not on the default
  PATH**. `ltagent` must auto-detect this fallback.
- A simple LTspice batch smoke test using `XVIIx64.exe -b smoke.cir`
  timed out on this host and did not produce `smoke.log`. `ltagent doctor
  --simulate` is expected to report this as a structured timeout, not a
  crash. This is the single most important thing the doctor exists to
  detect.

## File map (Phase 8)

```
src/ltagent/
  __init__.py     package version
  cli.py          argparse subparsers, --json output contract, main(),
                  cmd_run for `ltagent run <cir>`,
                  cmd_template_{list,show,match,audit,seed} for
                  `ltagent template ...`,
                  cmd_plan for `ltagent plan "<prompt>"`
  config.py       load + defaults + validate (no hard-coded paths)
  doctor.py       Phase 0 doctor: 9 pure-function checks + smoke
                  simulation runner
  ir.py           Phase 1: Circuit IR v0.1 (load/validate/dump)
  units.py        SPICE value parsing helpers
  netlist.py      Phase 2: .cir netlist generator
  runner.py       Phase 3: RunRequest, RunResult, build_argv,
                  resolve_wine, run_simulation, run_from_config,
                  RunnerBuildError, error-code constants
  log_parser.py   Phase 4: .log / .meas parser
  result.py       Phase 4: result.json writer + assertions
  asc.py          Phase 5: .asc writer + layout checker
  layout.py       Phase 5: grid constants, pin offset helpers
  layout_checker.py Phase 5: scoring rules from plan 12.4
  templates.py    Phase 6: TemplateManifest, list/show/match/audit/seed
  project.py      Phase 7: project workflow glue (load IR / run planner,
                  generate .cir + .asc, optionally run, parse, write
                  metadata + result.json)
  planner.py      Phase 8: rule-based prompt parser,
                  plan_prompt() -> CircuitIR | PlannerRefusal,
                  PlannerRefusal dataclass with stable refusal codes,
                  unit extraction, value calculation for voltage
                  divider and RC filters, slug-safe project name
                  generation

tests/
  test_cli.py        --help, --version, JSON shape, error paths,
                      Phase 8 plan subcommand
  test_config.py     defaults, missing file, malformed TOML
  test_doctor.py     all 9 checks via monkeypatched fixtures
  test_ir_load.py    valid IR loads + round-trips
  test_ir_validate.py per-rule rejection of invalid fixtures
  test_schema.py     exported JSON Schema
  test_units.py      SPICE value parsing
  test_runner.py     argv, timeout, no-log, success, launch error,
                     path traversal, extra args
  test_asc.py        Phase 5: required LTspice lines, MVP routing
  test_layout.py     Phase 5: grid constants, pin offsets
  test_layout_checker.py Phase 5: scoring rules
  test_templates.py  manifest IO, list/show/match, value variants
  test_project.py    Phase 7: project create workflow
  test_planner.py    Phase 8: topology detection (EN+ID), voltage
                     divider math, RC math, refusals, slug safety,
                     IR round-trip, unit extraction edge cases
  conftest.py        shared fixtures (temp workspace, fake config)
```

## Working agreements

- **One change per commit message.** Phase 0 lands as a small set of
  reviewable commits (skeleton, CLI, config, doctor, tests, CI).
- **No drive-by refactors.** Touch only what the current task requires.
- **Add new dependencies only with justification** in the commit
  message. Phase 0 adds zero runtime dependencies; dev deps live in the
  `[dev]` extra.
- **Run the full test suite + ruff + mypy before declaring done.**
  `pytest && ruff check . && mypy src` should be the last commands of
  any task that edits Python.

## When you're stuck

Read these in order:

1. `docs/PROJECT_PLAN.md` — what we are building and why
2. `docs/SPEC.md` — what the MVP must do, and the Phase 3 acceptance
3. `docs/runner.md` — the runner contract: CLI, Python API, error codes
4. `docs/ltspice_setup.md` — how to get LTspice working on this host
5. `docs/runner_troubleshooting.md` — what to do when the runner breaks
6. `docs/security.md` — what the agent is and is not allowed to do
