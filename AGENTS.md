# AGENTS.md

Operational guide for AI coding agents working inside this repository.
This file is the canonical entry point. `CLAUDE.md`, `OPENCODE.md`, and
`MCP.md` are thin shims that point here.

## What this project is

`ltspice-ai-agent` is a local Python CLI plus a local MCP server that
lets AI agents safely generate, simulate, inspect, and reuse LTspice
circuits. The Python core owns all validation, file generation, and
simulation execution. The agent proposes intent, parameters, and topology
only.

The full plan lives in [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). The
current development phase is **Phase 11** (Advanced Analog Templates).
Do not implement Phase 12+ features until Phase 11 acceptance is met.

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

**Phase 9 (Template Evaluator / Promoter) is complete.** Failed
simulation, low layout score, and duplicate value-only templates cannot
become official. Promotion is manual via `ltagent template promote`.
The evaluator records the score and gates from plan §15.3.

**Phase 10 (MCP Server v1) is complete.** `ltagent-mcp` (after
`pip install "ltspice-ai-agent[mcp]"`) runs an stdio MCP server that
exposes 10 curated tools and 8 curated resources backed by the same
Python core. No `run_shell`, no `execute_python`, no generic
`read_file`/`write_file`, no `.raw` exposure. See `MCP.md` and
`docs/mcp_setup.md`.

**Phase 11 (Advanced Analog Templates) is complete.** The Circuit IR
contract is extended with six new component kinds (diode, npn, pnp,
nmos, pmos, opamp) and seven new topologies (inverting_opamp,
noninv_opamp, comparator, diode_clipper, halfwave_rectifier,
bridge_rectifier, transistor_switch). The official template library
grew from 3 to 10 hand-crafted entries. The netlist generator emits
`.model` and `.subckt` blocks from the structured IR; the .asc
writer has deterministic per-topology placers for every Phase 11
topology. Layouts are FUNCTIONAL (no overlaps, ground present) but
some score 40-60 due to wire crossings; the official templates
carry the higher `layoutScore` stamp (85-100) that Phase 9 uses to
gauge promotability.

The bundled official library auto-seeds on the first read path. Every
`template list/show/match/audit`, every `create`, and the matching
MCP tools call `ltagent.templates.ensure_default_templates` before
doing real work. The first invocation in an empty workspace
materialises the 10 official templates; subsequent ones are no-ops.
`ltagent template seed` remains available as the explicit,
idempotent form for callers that want the manual flow. The auto-seed
is additive: it never overwrites user-edited manifests and never
moves templates between status directories.

## Current phase boundary

**Phase 11 — DONE WHEN:**

- `ltagent ir validate examples/inverting_opamp.ir.json --json` (and
  the other 6 new examples) accepts the IR and emits a structured
  success payload.
- `ltagent netlist` + `ltagent asc` produce a complete
  circuit.cir / circuit.asc for every Phase 11 topology.
- `ltagent template list --status official` returns 10 templates
  (3 MVP passive + 7 Phase 11 analog). The first read path in a
  fresh workspace auto-seeds the library; `ltagent template seed`
  remains available as the explicit, idempotent form.
- Each new official template has `simulationVerified=true` and
  `layoutScore >= 85`.
- `pytest tests/test_phase11.py` passes with zero LTspice / Wine
  invocations.
- `ruff check src/ltagent/ir.py src/ltagent/netlist.py
  src/ltagent/asc.py src/ltagent/templates.py src/ltagent/layout.py
  tests/test_phase11.py` is clean.
- `mypy src/ltagent/ir.py src/ltagent/netlist.py src/ltagent/asc.py
  src/ltagent/templates.py src/ltagent/layout.py` is clean.

**Out of scope for Phase 11 (do not implement):**

- LLM-generated prompt expansion for the new topologies. The
  Phase 8 planner remains rule-based and only handles the 3 passive
  topologies; the 7 new analog templates are accessed via
  hand-crafted IR files and the official template library.
- Free-form auto-layout for the new topologies. The deterministic
  layouts in asc.py are the fallback; the official templates
  carry a hand-tuned `layoutScore` stamp.

- `ltagent-mcp --help` exits 0 and lists the curated options.
- `ltagent-mcp --list-tools` returns the 10 tool names from plan §17.3
  plus `evaluate_template_candidate` and `promote_template`.
- `ltagent-mcp --list-resources` returns the 8 resource URIs from
  plan §17.4.
- The FastMCP server (`src/ltagent/mcp_server.py`) registers exactly
  10 tools and 8 resources; tests assert both counts and forbid
  dangerous names (`run_shell`, `execute_python`, `read_file`,
  `write_file`).
- `tool_create_project` produces the same artifact set as
  `ltagent create <ir> --out <dir>` (CLI parity).
- All `path`-bearing tools reject traversal via
  `ltagent.security.safe_resolve_under`; `.raw` files are blocked at
  `ltagent.security.assert_no_raw_path`.
- Resource URI parsing rejects traversal and unknown kinds with
  stable codes (`IDENTIFIER_INVALID`, `RESOURCE_URI_INVALID`,
  `RESOURCE_KIND_UNKNOWN`, `RESOURCE_SUBPATH_INVALID`).
- Missing SDK returns exit code 1 with a JSON error containing
  `code: "MCP_SDK_MISSING"` and an install hint.
- `pytest tests/test_mcp_server.py` passes with zero LTspice / Wine
  invocations.
- `ruff check src/ltagent/mcp_server.py src/ltagent/security.py
  tests/test_mcp_server.py` is clean.
- `mypy src/ltagent/mcp_server.py src/ltagent/security.py` is clean.

**Out of scope for Phase 10 (do not implement):**

- HTTP / SSE / WebSocket MCP transport. v1 is stdio only (plan §17.2).
- Authentication / multi-user separation. Local-only product
  (security.md §3).
- Tools from plan §17.3 "Advanced later" that were not added here
  (`query_raw_vector`, `assert_results`, `apply_template`,
  `snapshot_project`, `restore_snapshot`, `reject_template`,
  `render_schematic`, `render_waveform`).
- LLM-based prompt expansion of MCP tool inputs. MCP wraps the
  rule-based planner and the deterministic project orchestrator;
  it does not introduce a new LLM call.

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
   No path may escape the configured workspace. MCP tools reuse the
   same `ltagent.security` guards as the CLI.
4. **All structured commands return the JSON contract** (see
   `ltagent/cli.py` and `docs/SPEC.md`). Never infer success from prose.
5. **Default `safe_mode = true`.** Reject unsupported SPICE directives,
   reject writes outside the workspace, reject MCP resource paths that
   traverse.
6. **MCP tools are curated.** No `run_shell`, no `execute_python`, no
   generic `read_file` / `write_file`. Tools wrap the same Python
   core the CLI uses; no business logic lives only in MCP.

## Critical local context (this machine)

- LTspice XVII is installed under Wine at
  `/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe`
  (note the space in `Program Files` — Wine command construction must
  quote it).
- `wine` is at `/opt/wine-stable/bin/wine` but **not on the default
  PATH**. `ltagent` must auto-detect this fallback.
- A simple LTspice batch smoke test using `XVIIx64.exe -b smoke.cir`
  timed out on this host and did not produce `smoke.log`. `ltagent doctor
  --simulate` is expected to report this as a structured `LTSPICE_TIMEOUT`,
  not a crash. This is the single most important thing the doctor exists
  to detect. The runner stack (Wine + LTspice XVII batch mode) is not
  considered broken-by-design for this project; it is a local host
  condition to be diagnosed, documented, and worked around. The
  runner scope is therefore "structured, honest diagnosis", not
  "guaranteed simulation pass". See
  [`docs/runner_troubleshooting.md`](docs/runner_troubleshooting.md)
  for the remediation workflow.

## File map (Phase 10)

```
src/ltagent/
  __init__.py        package version
  cli.py             argparse subparsers, --json output contract, main()
  config.py          load + defaults + validate (no hard-coded paths)
  doctor.py          Phase 0: 9 pure-function checks + smoke simulation runner
  ir.py              Phase 1: Circuit IR v0.1 (load/validate/dump)
  units.py           SPICE value parsing helpers
  netlist.py         Phase 2: .cir netlist generator
  runner.py          Phase 3: RunRequest, RunResult, run_simulation
  log_parser.py      Phase 4: .log / .meas parser
  result.py          Phase 4: result.json writer + assertions
  asc.py             Phase 5: .asc writer + ASCResult dataclass
  layout.py          Phase 5: grid constants, pin offset helpers
  layout_checker.py  Phase 5: scoring rules from plan 12.4
  templates.py       Phase 6: TemplateManifest, list/show/match/audit/seed
  project.py         Phase 7: project workflow orchestrator
  planner.py         Phase 8: rule-based prompt parser
  evaluator.py       Phase 9: evaluator + promoter (score + gates)
  security.py        Phase 10 (shared): slug/path/URI validators
                     shared by CLI and MCP, see security.md
  mcp_server.py      Phase 10: stdio MCP server, 10 tools, 8 resources,
                     ltagent-mcp entry point, SDK-missing fallback

tests/
  test_cli.py            --help, --version, JSON shape, error paths
  test_cli_create.py     Phase 7 create subcommand
  test_cli_phase4.py     parse-log / result subcommands
  test_config.py         defaults, missing file, malformed TOML
  test_doctor.py         all 9 checks via monkeypatched fixtures
  test_ir_load.py        valid IR loads + round-trips
  test_ir_validate.py    per-rule rejection of invalid fixtures
  test_schema.py         exported JSON Schema
  test_units.py          SPICE value parsing
  test_runner.py         argv, timeout, no-log, success, launch error,
                         path traversal, extra args
  test_asc.py            Phase 5: required LTspice lines, MVP routing
  test_layout.py         Phase 5: grid constants, pin offsets
  test_layout_checker.py Phase 5: scoring rules
  test_templates.py      manifest IO, list/show/match, value variants
  test_project.py        Phase 7: project create workflow
  test_planner.py        Phase 8: topology detection, refusals, slug safety
  test_log_parser.py     Phase 4: parse_log + measurements
  test_netlist.py        Phase 2: .cir rendering
  test_result.py         Phase 4: result.json writer
  test_evaluator.py      Phase 9: scoring + gates + promotion
  test_mcp_server.py     Phase 10: 33 tests covering CLI surface, server
                         contract, tool bodies, CLI parity, security
                         boundary, SDK-missing fallback
  conftest.py            shared fixtures (temp workspace, fake config)
```

`docs/`
- `mcp_setup.md`  Phase 10: per-client wiring for Claude Code, OpenCode,
                  Cursor, Cline

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
