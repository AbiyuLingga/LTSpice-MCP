# LTspice AI Agent Engineering Plan

Created: 2026-06-17
Owner: abiyulinx
Working title: `ltspice-ai-agent`
Primary target: local CLI plus optional MCP server for Codex, Claude Code, OpenCode, Cursor, Cline, and similar coding agents.

## 1. Executive Summary

This project should be built as a local, testable automation tool around LTspice, not as a free-form "LLM draws schematics" system.

Recommended architecture:

```text
User prompt or Circuit IR
-> ltagent CLI
-> deterministic planner and validators
-> Circuit IR JSON
-> template matcher
-> .cir netlist generator
-> .asc schematic generator
-> LTspice runner
-> .log/.meas parser
-> result.json
-> template evaluator
-> optional MCP adapter
```

The most important design rule:

```text
The AI agent may propose intent, parameters, and topology.
The Python core owns validation, file generation, simulation execution, parsing, safety checks, and template promotion.
```

The original plan in `/home/abiyulinx/Downloads/LTspice_AI_Agent_Project_Plan(1).md` is directionally strong. It already identifies the right foundation: CLI first, MCP second, Circuit IR, deterministic `.asc`, templates, simulation loop, and structured output.

The plan should be tightened in these areas:

1. Add `ltagent doctor` as Phase 0 before any serious runner work.
2. Treat Wine/LTspice batch reliability as a first-class risk, because the local smoke test timed out.
3. Use the current MCP specification as the target and keep MCP as an adapter only.
4. Keep MVP topology smaller: voltage divider, RC low-pass, and RC high-pass.
5. Do not make PyLTSpice/spicelib mandatory until licensing is decided.
6. Add strict safety rules for directives, file paths, tool execution, project writes, and MCP resources.
7. Add a stable output contract so agents do not infer success from prose.

## 2. Current Local Context

Observed local files and environment:

```text
/home/abiyulinx/Downloads/LTspice_AI_Agent_Project_Plan(1).md
/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe
/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/LTspiceHelp.pdf
/home/abiyulinx/.wine/drive_c/users/abiyulinx/AppData/Roaming/LTspiceXVII.ini
/home/abiyulinx/Documents/LTspiceXVII/Draft1.asc
/opt/wine-stable/bin/wine
```

Observed local facts:

- LTspice XVII is installed under Wine.
- Wine exists at `/opt/wine-stable/bin/wine`.
- `wine` is not on the default shell PATH, but `/opt/wine-stable/bin/wine --version` returns `wine-11.0`.
- A simple LTspice batch smoke test using `XVIIx64.exe -b smoke.cir` timed out after 20 seconds and did not produce `smoke.log`.
- `LTspiceXVII.ini` records working directory `C:\users\abiyulinx\Documents\LTspiceXVII`.
- Local LTspice help documents command-line switches such as `-b`, `-ascii`, `-netlist`, `-Run`, and `-FastAccess`.

Implication:

```text
The project must not assume that LTspice batch execution works just because LTspice is installed.
Runner health-check and diagnostics must be built before depending on automated simulation.
```

## 3. Product Goal

Build a tool that lets an AI coding agent safely create, simulate, inspect, debug, and reuse LTspice circuits.

Primary users:

- You as a local developer using Codex, Claude Code, OpenCode, Cursor, or terminal agents.
- Future technical users who want text-to-LTspice workflows without trusting the LLM to edit `.asc` files directly.
- Future students or analog learners who need repeatable circuit generation and simulation results.

Main outcomes:

- Generate valid `.cir` files from structured input.
- Generate simple, readable `.asc` schematics from deterministic layout rules or templates.
- Run LTspice in batch mode when available.
- Parse `.log` and `.meas` into machine-readable `result.json`.
- Reuse verified circuit templates.
- Expose safe, curated MCP tools after the CLI core is stable.

## 4. Non-Goals For MVP

Do not include these in MVP:

- General arbitrary circuit design from open-ended prompts.
- Free-form LLM generation of production `.asc` coordinates.
- Image/photo-to-schematic conversion.
- PCB layout.
- Proteus integration.
- Arduino, ESP32, or MCU simulation.
- Full web app.
- Fine-tuned LLM.
- Remote MCP server.
- Arbitrary shell execution through MCP.
- Automatic deletion or rewriting of official templates.
- Mandatory dependency on GPL libraries unless the project license is intentionally GPL-compatible.

## 5. Evidence Reviewed

### 5.1 Local Plan

Source:

- `/home/abiyulinx/Downloads/LTspice_AI_Agent_Project_Plan(1).md`

What it supports:

- The existing direction is correct: CLI first, Circuit IR, deterministic `.asc`, template memory, result parser, and MCP as wrapper.

How it affects this plan:

- Keep the architecture, but narrow MVP and harden runner/security.

### 5.2 Local LTspice Documentation

Source:

- `/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/LTspiceHelp.pdf`

What it supports:

- LTspice supports command-line switches including batch simulation, netlist conversion, and ASCII raw output.
- `-ascii` is documented as slower, so it should not be default.

How it affects this plan:

- Default workflow should parse `.log` and `.meas` first.
- Raw waveform access should be advanced and opt-in.

### 5.3 Analog Devices LTspice Page

Source:

- https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html

What it supports:

- LTspice is the official simulator, schematic capture, and waveform viewer.
- Current LTspice releases should be considered separately from legacy XVII.

How it affects this plan:

- The runner should support current LTspice and legacy XVII instead of hardcoding one executable path.

### 5.4 Model Context Protocol

Sources:

- https://modelcontextprotocol.io/specification/2025-11-25
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- https://github.com/modelcontextprotocol/python-sdk

What it supports:

- MCP servers expose tools, resources, and prompts through structured contracts.
- Tools should have schemas and clear results.
- Resources should use controlled URIs.

How it affects this plan:

- MCP must wrap the core Python engine, not duplicate it.
- MCP tools must be curated, schema-driven, and path-safe.

### 5.5 PyLTSpice And spicelib

Sources:

- https://github.com/nunobrum/PyLTSpice
- https://pyltspice.readthedocs.io/
- https://github.com/nunobrum/spicelib
- https://spicelib.readthedocs.io/

What it supports:

- These projects already solve parts of LTspice automation, raw parsing, batch simulation, and netlist/schematic editing.

Limitations:

- They do not solve text prompt to safe agent workflow.
- Their licensing must be reviewed before becoming hard dependencies.

How it affects this plan:

- Use them as references.
- Consider them as optional extras after a license decision.
- Keep the core architecture independent.

### 5.6 Schemato Research

Source:

- https://arxiv.org/abs/2411.13899

What it supports:

- LLM-based netlist-to-schematic generation is relevant and promising.
- Compilation success is not the same as consistently readable layout.

How it affects this plan:

- Do not trust LLM-generated `.asc` layout for MVP.
- Use deterministic layout and templates first.

### 5.7 Related Projects

Sources:

- https://github.com/f18m/netlist-viewer
- https://github.com/mahmut-aksakalli/circuit_recognizer
- https://github.com/ckuhlmann/lt2circuitikz
- https://github.com/ahaensler/asc_viewer
- https://github.com/xuio/ltspice-mcp

What they support:

- Netlist visualization and `.asc` parsing are real problem areas.
- LTspice plus MCP is feasible.

How it affects this plan:

- Study their interfaces and file handling.
- Do not depend on them for MVP.

### 5.8 Security References

Sources:

- https://genai.owasp.org/owasp-top-10-for-llm-applications/
- https://owasp.org/www-project-top-ten/

What it supports:

- Prompt injection, insecure output handling, insecure plugin/tool design, and excessive agency are direct risks for local agent tools.

How it affects this plan:

- Do not let prompts choose shell commands, executable paths, output paths, or file reads.
- Validate all structured inputs at boundaries.
- Keep MCP tools narrow.

### 5.9 Alternative Simulator And Circuit Tooling

Sources:

- https://ngspice.sourceforge.io/docs.html
- https://pyspice.fabrice-salvaire.fr/
- https://devbisme.github.io/skidl/

What they support:

- Other SPICE and circuit-generation ecosystems exist.

How it affects this plan:

- Do not make the MVP cross-simulator.
- Keep enough boundary discipline that ngspice or SKiDL can be evaluated later.

## 6. Evidence-Based Decisions

| Decision | Evidence | Result |
|---|---|---|
| CLI first, MCP second | Local plan, MCP spec, testing skill | Core stays testable and reusable. |
| Add `ltagent doctor` first | Local Wine timeout | Runner health must be proven before simulation features. |
| Deterministic `.asc` layout | Schemato, netlist-viewer, local `.asc` structure | Avoid free-form LLM coordinates. |
| Parse `.meas` before `.raw` | LTspice help, runner complexity | Result extraction is simpler and faster. |
| Optional PyLTSpice/spicelib | Existing projects, license uncertainty | Avoid locking architecture early. |
| Local stdio MCP only for v1 | MCP docs, security risk | No remote auth or network surface in MVP. |
| MVP topology limited to 3 | Task sizing and risk | Faster proof of complete loop. |

## 7. Recommended Architecture

### 7.1 Modules

```text
src/ltagent/
  __init__.py
  cli.py
  config.py
  doctor.py
  ir.py
  units.py
  values.py
  netlist.py
  asc.py
  layout.py
  layout_checker.py
  runner.py
  log_parser.py
  result.py
  templates.py
  evaluator.py
  planner.py
  project.py
  security.py
  mcp_server.py
```

### 7.2 Data Flow

```text
Prompt or IR JSON
-> validate input
-> normalize units
-> create CircuitIR
-> match template if possible
-> generate .cir
-> generate .asc if topology supported
-> run simulation if doctor says LTspice is available
-> parse .log/.meas
-> run assertions
-> check layout
-> write project artifacts
-> emit JSON summary
```

### 7.3 Directory Structure

```text
ltspice-ai-agent/
  README.md
  AGENTS.md
  CLAUDE.md
  OPENCODE.md
  MCP.md
  pyproject.toml
  config.example.toml
  .gitignore

  docs/
    SPEC.md
    PROJECT_PLAN.md
    ADR-0001-cli-core-before-mcp.md
    ADR-0002-ir-contract.md
    ADR-0003-template-promotion.md
    ltspice_setup.md
    mcp_setup.md
    security.md
    troubleshooting.md

  examples/
    voltage_divider.ir.json
    rc_lowpass.ir.json
    rc_highpass.ir.json

  src/ltagent/
    ...

  templates/
    index.json
    official/
    candidates/
    rejected/

  projects/
    .gitkeep

  tests/
    test_ir.py
    test_units.py
    test_netlist.py
    test_asc.py
    test_layout_checker.py
    test_runner.py
    test_log_parser.py
    test_templates.py
    test_planner.py
    test_mcp_server.py
```

## 8. Public CLI Contract

### 8.1 Required Commands

```bash
ltagent --help
ltagent doctor --json
ltagent init
ltagent config show
ltagent config validate
ltagent ir validate examples/rc_lowpass.ir.json --json
ltagent netlist examples/rc_lowpass.ir.json --out circuit.cir --json
ltagent asc examples/rc_lowpass.ir.json --out circuit.asc --json
ltagent run circuit.cir --json
ltagent parse-log circuit.log --json
ltagent create examples/rc_lowpass.ir.json --json
ltagent create "buat RC low-pass cutoff 1kHz dengan C 100nF" --json
ltagent template list --json
ltagent template show rc_lowpass --json
ltagent template match examples/rc_lowpass.ir.json --json
ltagent template evaluate projects/example --json
ltagent template promote projects/example --json
ltagent template audit --json
ltagent-mcp --help
```

### 8.2 CLI Output Rule

Every command with `--json` must return:

```json
{
  "success": true,
  "command": "create",
  "message": "Project created",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Failure must still be structured:

```json
{
  "success": false,
  "command": "run",
  "message": "LTspice batch run timed out",
  "data": {
    "timeoutSeconds": 20,
    "executable": "/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe"
  },
  "warnings": [],
  "errors": [
    {
      "code": "LTSPICE_TIMEOUT",
      "detail": "No .log file was produced before timeout"
    }
  ]
}
```

## 9. Config Contract

Example:

```toml
[workspace]
projects_dir = "projects"
templates_dir = "templates"

[ltspice]
mode = "wine"
executable = "/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe"
wine_command = "/opt/wine-stable/bin/wine"
working_dir = "/home/abiyulinx/Documents/LTspiceXVII"

[runner]
timeout_seconds = 30
kill_on_timeout = true
save_raw = false
force_ascii_raw = false
run_in_temp_dir = true

[layout]
grid_x = 160
grid_y = 96
main_y = 160
ground_y = 352
min_spacing = 80
official_template_min_score = 85

[templates]
auto_promote = false
candidate_threshold = 3
official_threshold = 6

[agent]
json_output_default = true
safe_mode = true
```

## 10. Circuit IR v0.1

### 10.1 Design Rules

Circuit IR is the stable contract between AI intent and generated LTspice files.

Rules:

- Use `schemaVersion`.
- Use explicit `topology`.
- Node ground must be exactly `0`.
- Components must be typed and arity-checked.
- Simulation analysis must be structured, not a raw arbitrary directive by default.
- Raw directives require allowlist validation.
- Values are strings for SPICE fidelity, but numeric fields should be normalized into metadata when possible.

### 10.2 Example

```json
{
  "schemaVersion": "0.1",
  "name": "rc_lowpass_1khz",
  "topology": "rc_lowpass",
  "description": "First-order RC low-pass filter.",
  "nodes": ["in", "out", "0"],
  "components": [
    {
      "id": "Vin",
      "kind": "voltage_source",
      "spicePrefix": "V",
      "nodes": ["in", "0"],
      "value": "SINE(0 1 1k)",
      "role": "input_source"
    },
    {
      "id": "R1",
      "kind": "resistor",
      "spicePrefix": "R",
      "nodes": ["in", "out"],
      "value": "1.59k",
      "role": "series_resistor"
    },
    {
      "id": "C1",
      "kind": "capacitor",
      "spicePrefix": "C",
      "nodes": ["out", "0"],
      "value": "100n",
      "role": "shunt_capacitor"
    }
  ],
  "analysis": {
    "kind": "tran",
    "stopTime": "5m"
  },
  "measurements": [
    {
      "name": "VOUT_MAX",
      "analysis": "tran",
      "expression": "MAX V(out)"
    },
    {
      "name": "VOUT_MIN",
      "analysis": "tran",
      "expression": "MIN V(out)"
    }
  ],
  "probes": ["V(in)", "V(out)"],
  "constraints": {
    "targetCutoffHz": 1000
  },
  "metadata": {
    "createdBy": "ltagent",
    "source": "example"
  }
}
```

### 10.3 Validation

Required validation:

- `schemaVersion` supported.
- `name` safe for file paths.
- `topology` in supported list.
- `nodes` contains `0`.
- Component IDs are unique.
- Component `nodes` all exist in `nodes`.
- Component kind matches expected node count.
- Voltage/current source value is non-empty.
- At least one analysis exists.
- Measurements have safe names and supported analysis types.
- Unsupported raw directives are rejected.

## 11. Netlist Generator

### 11.1 Scope

Generate `.cir` from validated IR.

MVP supported elements:

| Kind | Prefix | Example |
|---|---|---|
| voltage_source | V | `V1 in 0 DC 12` |
| current_source | I | `I1 in 0 DC 1m` |
| resistor | R | `R1 in out 1k` |
| capacitor | C | `C1 out 0 100n` |
| inductor | L | `L1 in out 10u` |
| diode | D | later phase |
| bjt | Q | later phase |
| mosfet | M | later phase |
| subckt | X | later phase |

### 11.2 Output Rules

- Header comment includes generator version.
- Do not include `.end` in IR.
- Netlist generator appends `.end`.
- Analysis generated from structured fields.
- Measurements generated from structured fields.
- Unsafe directives blocked by default.

Example output:

```spice
* Generated by ltspice-ai-agent
* Project: rc_lowpass_1khz
Vin in 0 SINE(0 1 1k)
R1 in out 1.59k
C1 out 0 100n
.tran 0 5m
.meas tran VOUT_MAX MAX V(out)
.meas tran VOUT_MIN MIN V(out)
.end
```

## 12. ASC Schematic Generator

### 12.1 Design Principle

The `.asc` writer is deterministic.

The AI agent must not write production coordinate lines. It may request topology and parameters; Python writes the final layout.

### 12.2 MVP Layout Rules

Global rules:

- Input left.
- Output right.
- Ground below.
- Signal flow left to right.
- Series components horizontal.
- Shunt components vertical.
- Use orthogonal wires only.
- Use fixed grid constants.
- Use labels for long connections.

Grid:

```python
GRID_X = 160
GRID_Y = 96
MAIN_Y = 160
GROUND_Y = 352
INPUT_X = 80
```

### 12.3 Supported MVP Layouts

Voltage divider:

```text
Vin -> R1 -> Vout -> R2 -> GND
```

RC low-pass:

```text
Vin -> R1 -> Vout
             |
             C1
             |
            GND
```

RC high-pass:

```text
Vin -> C1 -> Vout
             |
             R1
             |
            GND
```

### 12.4 Layout Checker

Return:

```json
{
  "layoutScore": 92,
  "overlaps": 0,
  "wireCrossings": 0,
  "danglingWires": 0,
  "missingGround": false,
  "warnings": []
}
```

Initial scoring:

```text
start = 100
-30 component overlap
-20 missing ground
-10 per wire crossing
-5 per label collision
-3 per long wire warning
-2 per min-spacing violation
```

Policy:

```text
score >= 85: acceptable for official template
70 <= score < 85: project output only
score < 70: do not promote
```

## 13. LTspice Runner

### 13.1 Runner Requirements

Runner must:

- Execute only the configured LTspice executable.
- Never execute arbitrary shell from user prompt.
- Support Windows native path and Linux/Wine path.
- Run in a controlled project or temp directory.
- Capture stdout and stderr.
- Enforce timeout.
- Kill child process on timeout when possible.
- Detect `.log`, `.raw`, and exit status.
- Return structured `RunResult`.

### 13.2 Health Check

`ltagent doctor --json` must check:

- Python version.
- Package version.
- Workspace write access.
- LTspice executable exists.
- Wine command exists if mode is `wine`.
- LTspice executable file type if possible.
- Current working directory.
- Ability to write temp `.cir`.
- Ability to run a tiny `.op` simulation.
- Whether `.log` is produced.
- Whether measurement parsing succeeds.

### 13.3 Local Risk

On this machine, a smoke test using Wine and LTspice XVII timed out.

Plan implication:

```text
Do not build higher-level features assuming the runner is healthy.
Make doctor and runner diagnostics Phase 0.
```

Potential causes to investigate in implementation:

- LTspice GUI executable waiting on display even with `-b`.
- Wine prefix state.
- File path conversion between Linux and Wine.
- Stale running LTspice process.
- Working directory mismatch.
- Need to run from inside Wine-visible path.
- Need current LTspice instead of legacy XVII.

## 14. Log Parser And Result JSON

### 14.1 Parser Scope

MVP parser should parse:

- `.meas op`
- `.meas tran`
- LTspice error lines
- Warning lines
- Measurement names and numeric values

### 14.2 Result Contract

```json
{
  "success": true,
  "projectId": "2026-06-17_rc_lowpass_1khz",
  "files": {
    "ir": "circuit.ir.json",
    "cir": "circuit.cir",
    "asc": "circuit.asc",
    "log": "circuit.log",
    "raw": null,
    "result": "result.json"
  },
  "run": {
    "attempted": true,
    "success": true,
    "timeoutSeconds": 30,
    "durationMs": 812
  },
  "measurements": {
    "VOUT_MAX": 0.707
  },
  "assertions": [
    {
      "name": "simulation_has_no_errors",
      "passed": true
    }
  ],
  "layout": {
    "score": 92,
    "warnings": []
  },
  "template": {
    "used": "rc_lowpass",
    "promoted": false
  },
  "warnings": [],
  "errors": []
}
```

## 15. Template System

### 15.1 Template Levels

```text
projects/      all generated projects
candidates/    reusable but not official
official/      verified stable templates
rejected/      rejected templates retained for audit
```

### 15.2 Template Metadata

```json
{
  "templateId": "rc_lowpass",
  "schemaVersion": "0.1",
  "name": "RC Low Pass Filter",
  "topology": "rc_lowpass",
  "status": "official",
  "tags": ["filter", "rc", "lowpass"],
  "files": {
    "ir": "template.ir.json",
    "cir": "template.cir",
    "asc": "template.asc"
  },
  "parameters": {
    "R1": {
      "description": "Series resistor",
      "default": "1.59k",
      "editable": true
    },
    "C1": {
      "description": "Shunt capacitor",
      "default": "100n",
      "editable": true
    }
  },
  "formula": {
    "cutoffFrequency": "fc = 1 / (2*pi*R*C)"
  },
  "layoutScore": 95,
  "simulationVerified": true,
  "useCount": 0,
  "createdAt": "2026-06-17",
  "updatedAt": "2026-06-17"
}
```

### 15.3 Promotion Rules

Score:

```text
+3 explicit user says save as template
+2 reusable topology
+2 simulation succeeded
+2 no similar template exists
+1 parameters are editable
+1 layout score >= 85
-3 same topology with only value changes
-3 simulation failed
-2 too specific
-2 layout score < 70
-1 incomplete metadata
```

Policy:

```text
score >= 6: official candidate, still requires manual promotion in MVP
score 3-5: candidate
score < 3: keep as project only
```

Hard rule:

```text
RC low-pass 1 kHz, 500 Hz, and 10 kHz are one template with different parameters.
They are not separate official templates.
```

## 16. Natural Language Planner v1

### 16.1 Strategy

Do not start with a full LLM planning layer.

Start with a deterministic parser for a small set of English and Indonesian prompts.

Supported MVP prompts:

- `buat pembagi tegangan 12V ke 5V`
- `make voltage divider 12V to 5V`
- `buat RC low-pass cutoff 1kHz dengan C 100nF`
- `make RC low pass cutoff 1kHz with C 100nF`
- `buat RC high-pass cutoff 500Hz`

### 16.2 Planner Output

Planner returns Circuit IR or a structured refusal:

```json
{
  "success": false,
  "code": "UNSUPPORTED_PROMPT",
  "message": "Prompt not recognized",
  "supportedTopologies": [
    "voltage_divider",
    "rc_lowpass",
    "rc_highpass"
  ],
  "nextStep": "Provide an IR JSON file or use one of the supported prompt formats."
}
```

## 17. MCP Server v1

### 17.1 Principle

MCP is not the core.

```text
MCP server -> calls ltagent core functions -> returns structured results
```

No business logic should live only in MCP.

### 17.2 Transport

MVP:

```text
local stdio MCP server
```

Do not implement remote HTTP MCP in v1.

### 17.3 Tools

MVP tools:

```text
create_project
inspect_project
generate_netlist
generate_schematic
run_simulation
read_measurements
check_layout
find_template
```

Advanced later:

```text
query_raw_vector
assert_results
apply_template
snapshot_project
restore_snapshot
evaluate_template_candidate
promote_template
reject_template
render_schematic
render_waveform
```

### 17.4 Resources

MVP resources:

```text
ltagent://projects
ltagent://projects/{project_id}/metadata
ltagent://projects/{project_id}/result
ltagent://projects/{project_id}/circuit-ir
ltagent://projects/{project_id}/netlist
ltagent://projects/{project_id}/log
ltagent://templates
ltagent://templates/{template_id}/metadata
```

Do not expose full `.raw` files as resources by default.

### 17.5 MCP Safety

MCP tools must:

- Validate all input schemas.
- Reject path traversal.
- Write only under configured workspace.
- Run only configured LTspice executable.
- Return structured errors.
- Snapshot before modifying templates.
- Never expose `run_shell`, `execute_python`, or arbitrary file read/write.

## 18. Security Model

### 18.1 Threats

Main threats:

- Prompt injection asks agent to run shell or read files.
- Unsafe `.include`, `.lib`, or directive pulls files outside workspace.
- MCP resource path traversal.
- Generated output overwrites important files.
- Template promotion pollutes official library.
- Large `.raw` files fill disk.
- Agent misreads failed simulation as success.

### 18.2 Controls

Controls:

- Validate IR at boundary.
- Allowlist supported directives in MVP.
- Validate output paths with resolved absolute paths.
- Keep writes under workspace.
- No arbitrary shell.
- No raw full-file resource exposure.
- Timeout all simulations.
- Structured success flag.
- Snapshot before template modification.
- Manual template promotion in MVP.
- `.gitignore` generated outputs.

Example `.gitignore`:

```gitignore
__pycache__/
.venv/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.env
config.toml
projects/*/*.raw
projects/*/*.log
projects/*/*.tmp
projects/*/.snapshots/
```

## 19. Testing Strategy

### 19.1 Test Pyramid

Most tests should be small unit tests.

```text
Unit tests: IR, unit parsing, netlist, ASC writer, layout checker, parser
Integration tests: LTspice runner, project create
Manual tests: open .asc in LTspice, inspect waveform
MCP tests: list tools, call tools against temp workspace
```

### 19.2 Required Unit Tests

- Valid IR loads.
- Invalid IR gives actionable errors.
- Ground node is required.
- Component IDs must be unique.
- RC low-pass netlist matches expected lines.
- Voltage divider netlist ends with `.end`.
- Unsafe directive is rejected.
- `.asc` writer includes `Version`, `SHEET`, `WIRE`, `FLAG`, `SYMBOL`, `SYMATTR`, and `TEXT`.
- Layout checker detects missing ground.
- Layout checker detects component overlap.
- Log parser extracts `.meas` results.
- Log parser detects common error lines.
- Template matcher returns existing topology.
- Template evaluator avoids duplicate topology for different values.
- Planner handles Indonesian prompts.
- Planner rejects unsupported prompts with structured response.

### 19.3 Integration Tests

Integration tests should be skipped unless doctor passes:

```bash
pytest -m integration
```

Test cases:

- Generate voltage divider `.cir`.
- Run `.op`.
- Parse `VOUT`.
- Generate RC low-pass `.cir`.
- Run `.tran`.
- Parse measurements.
- Create full project directory.

### 19.4 MCP Tests

- Server lists tools.
- Tool schemas are present.
- `create_project` creates same output as CLI.
- `run_simulation` rejects non-project paths.
- `read_measurements` returns same result as parser.
- Resource URI path traversal is rejected.
- No tool can execute arbitrary shell.

## 20. CI/CD Plan

MVP GitHub Actions:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -U pip
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy src
      - run: pytest
      - run: python -m build
```

Integration tests with LTspice should not run in normal CI unless a licensed/approved runner is configured.

## 21. Implementation Phases

### Phase 0: Spec, Repo, Doctor

Goal:

Create a repo that can be safely worked on by agents and prove local LTspice feasibility.

Tasks:

- Create Python project structure.
- Add `pyproject.toml`.
- Add `AGENTS.md`, `CLAUDE.md`, `OPENCODE.md`, and `MCP.md`.
- Add docs skeleton.
- Add minimal CLI.
- Add `ltagent doctor`.
- Add smoke simulation test path.

Acceptance:

- `ltagent --help` works.
- `ltagent doctor --json` reports LTspice/Wine state.
- Unit tests run without LTspice installed.
- If LTspice run times out, failure is structured and actionable.

### Phase 1: Circuit IR

Goal:

Create stable validated Circuit IR.

Tasks:

- Implement IR models.
- Implement JSON load/save.
- Implement validation.
- Implement examples for 3 MVP topologies.
- Export JSON Schema.

Acceptance:

- Valid examples load.
- Invalid examples fail with clear error.
- Tests cover component arity, ground, duplicate IDs, unsupported topology.

### Phase 2: Netlist Generator

Goal:

Generate `.cir` from IR.

Tasks:

- Implement `netlist.py`.
- Generate component lines.
- Generate structured `.op` and `.tran`.
- Generate `.meas`.
- Append `.end`.
- Add CLI.

Acceptance:

- Voltage divider and RC examples generate valid-looking `.cir`.
- Snapshot tests check required lines.
- Unsafe directives rejected.

### Phase 3: Runner

Goal:

Run LTspice batch safely when available.

Tasks:

- Implement `runner.py`.
- Implement config path resolution.
- Implement Windows native and Wine command construction.
- Add timeout and cleanup.
- Detect output files.
- Add integration marker.

Acceptance:

- Missing executable gives clear error.
- Timeout gives clear error.
- Successful run returns `.log` path.
- Unit tests do not require LTspice.

### Phase 4: Log Parser And Results

Goal:

Convert `.log` and `.meas` into `result.json`.

Tasks:

- Implement parser.
- Add sample log fixtures.
- Detect common errors.
- Implement result writer.
- Add assertion engine.

Acceptance:

- `.meas op` parsed.
- `.meas tran` parsed.
- Failed simulation not marked success.

### Phase 5: ASC Writer And Layout

Goal:

Generate simple readable `.asc`.

Tasks:

- Implement primitive writer.
- Implement deterministic layouts for 3 MVP topologies.
- Add layout checker.
- Add CLI command.

Acceptance:

- Generated `.asc` contains required LTspice lines.
- Layout score computed.
- Manual open-test instructions documented.

### Phase 6: Template Library

Goal:

Reuse stable circuits without duplicate templates.

Tasks:

- Implement template manifest.
- Implement list/show/match.
- Implement candidate/official/rejected.
- Add use count.

Acceptance:

- Existing topology matches official template.
- Same topology with different values does not create duplicate official template.

### Phase 7: Create Project Workflow

Goal:

One command creates complete project artifacts.

Tasks:

- Load IR or prompt.
- Match template.
- Generate `.cir`.
- Generate `.asc`.
- Optionally run LTspice.
- Parse result.
- Write metadata.
- Return JSON.

Acceptance:

- `ltagent create examples/voltage_divider.ir.json --json` works.
- Project output includes `circuit.ir.json`, `circuit.cir`, `circuit.asc`, `metadata.json`, `result.json`.
- If LTspice unavailable, project still created and run status says skipped/failed clearly.

### Phase 8: Rule-Based Planner

Goal:

Support natural language for MVP prompts.

Tasks:

- Parse Indonesian and English keywords.
- Parse voltage, frequency, capacitance, resistance units.
- Calculate values for voltage divider and RC filters.
- Generate IR.

Acceptance:

- Supported prompts produce valid IR.
- Unsupported prompt gives supported topology list.

### Phase 9: Template Evaluator

Goal:

Score and promote templates safely.

Tasks:

- Implement evaluator.
- Detect duplicate topology.
- Require simulation success and layout score.
- Add manual promote command.
- Add audit command.

Acceptance:

- Failed simulation cannot become official.
- Low layout score cannot become official.
- Duplicate value-only templates rejected.

### Phase 10: MCP Server v1

Goal:

Expose stable CLI/core capabilities to AI agents through MCP.

Tasks:

- Add MCP dependency after CLI is stable.
- Implement stdio MCP server.
- Add curated tools.
- Add resources.
- Add docs for client setup.
- Add tests.

Acceptance:

- `ltagent-mcp --help` works.
- MCP server lists tools.
- MCP `create_project` matches CLI output.
- No arbitrary shell or broad file read tools exist.

### Phase 11: Advanced Circuit Templates

Goal:

Add useful analog templates after MVP.

Topologies:

- Inverting op-amp.
- Non-inverting op-amp.
- Comparator.
- Diode clipper.
- Half-wave rectifier.
- Bridge rectifier.
- Transistor switch.

Acceptance:

- Use hand-made official templates or deterministic topology-specific layout.
- No free-form auto-layout for these in first pass.

### Phase 12: Optimization Loop

Goal:

Choose practical component values.

Features:

- E12/E24/E96 resistor series.
- Common capacitor values.
- Voltage divider target.
- RC cutoff target.
- Op-amp gain target.
- Error percent report.

Acceptance:

- Reports ideal value, selected value, actual value, and percent error.

### Phase 13: Optional Web UI

Goal:

Add a UI only after CLI and MCP are stable.

Recommended minimal UI:

- Prompt input.
- IR preview.
- Download `.cir`.
- Download `.asc`.
- Result viewer.
- Template list.

Do not add auth, accounts, or cloud storage in the first UI version unless product scope changes.

## 22. Rollback And Recovery

Each project modification should support snapshots:

```text
projects/{project_id}/.snapshots/
  001_before_change/
  002_before_template_promotion/
```

Rollback policies:

- Generated project can be deleted manually.
- Official templates require snapshot before modification.
- MCP tools cannot delete official templates.
- Promotion can be reverted by moving template from `official` to `candidates` or `rejected` with audit metadata.

## 23. Documentation Plan

Required docs:

```text
README.md
docs/SPEC.md
docs/PROJECT_PLAN.md
docs/architecture.md
docs/ltspice_setup.md
docs/runner_troubleshooting.md
docs/ir_schema.md
docs/template_rules.md
docs/mcp_setup.md
docs/security.md
docs/testing.md
```

README must include:

- What the project does.
- Supported OS/runtime.
- Install commands.
- Quickstart with IR.
- Quickstart with natural language.
- How to run doctor.
- How to run tests.
- Known limitations.

## 24. Approval Gates

Require explicit approval before:

- Adding GPL dependency as mandatory.
- Implementing remote MCP.
- Adding web UI.
- Adding broad auto-layout.
- Supporting arbitrary SPICE directives.
- Promoting many templates automatically.
- Deleting project history or templates.
- Running commands outside configured LTspice executable.

## 25. Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Wine batch mode unreliable | Core simulation loop blocked | `ltagent doctor`, timeout, diagnostics, Windows-native support. |
| `.asc` layout ugly | User loses trust | Deterministic topology layouts, layout checker, manual template promotion. |
| LLM writes unsafe directives | Local file/system risk | IR validation and directive allowlist. |
| MCP tool too powerful | Security issue | Curated tools only, no arbitrary shell, path validation. |
| Template library polluted | Future outputs degrade | Candidate/official workflow and duplicate detection. |
| `.raw` files too large | Disk bloat | Do not save raw by default, ignore raw, query slices only later. |
| Prompt parser wrong | Incorrect circuits | Start with supported prompt list and structured refusal. |
| Dependency license mismatch | Project cannot be distributed as intended | Keep GPL libraries optional until license decision. |
| Current LTspice differs from XVII | Runner drift | Version detection and separate compatibility docs. |

## 26. Definition Of Done For MVP

MVP is done when:

1. `ltagent doctor --json` works and explains LTspice/Wine status.
2. `ltagent create examples/voltage_divider.ir.json --json` creates project artifacts.
3. `ltagent create examples/rc_lowpass.ir.json --json` creates project artifacts.
4. Generated project includes:

```text
circuit.ir.json
circuit.cir
circuit.asc
metadata.json
result.json
```

5. If LTspice is available, simulation runs and `.meas` appears in `result.json`.
6. If LTspice is unavailable or times out, failure is structured and not mistaken for success.
7. `.asc` files for MVP topologies open in LTspice and are readable.
8. Template matcher reuses `rc_lowpass` instead of creating duplicates for each cutoff.
9. Unit tests pass.
10. Integration tests are optional and skip cleanly.
11. MCP v1, if included in MVP, exposes only curated safe tools.

## 27. Recommended Immediate Next Steps

1. Create the actual project repo `ltspice-ai-agent`.
2. Copy this plan into `docs/PROJECT_PLAN.md`.
3. Write `docs/SPEC.md` with MVP success criteria.
4. Implement Phase 0 only:
   - package skeleton
   - CLI skeleton
   - `ltagent doctor`
   - local runner smoke diagnostic
5. Do not implement natural language, templates, or MCP until doctor and IR/netlist are stable.

## 28. Source Index

Primary sources:

- Analog LTspice official page: https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html
- Local LTspice help: `/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/LTspiceHelp.pdf`
- MCP specification: https://modelcontextprotocol.io/specification/2025-11-25
- MCP tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP resources: https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- MCP prompts: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk

LTspice automation and circuit tooling:

- PyLTSpice: https://github.com/nunobrum/PyLTSpice
- PyLTSpice docs: https://pyltspice.readthedocs.io/
- spicelib: https://github.com/nunobrum/spicelib
- spicelib docs: https://spicelib.readthedocs.io/
- ngspice docs: https://ngspice.sourceforge.io/docs.html
- PySpice: https://pyspice.fabrice-salvaire.fr/
- SKiDL: https://devbisme.github.io/skidl/

Related projects:

- Schemato paper: https://arxiv.org/abs/2411.13899
- netlist-viewer: https://github.com/f18m/netlist-viewer
- circuit_recognizer: https://github.com/mahmut-aksakalli/circuit_recognizer
- lt2circuitikz: https://github.com/ckuhlmann/lt2circuitikz
- asc_viewer: https://github.com/ahaensler/asc_viewer
- LTspice MCP reference project: https://github.com/xuio/ltspice-mcp

Agent/security references:

- OWASP Top 10 for LLM Applications: https://genai.owasp.org/owasp-top-10-for-llm-applications/
- OWASP Top 10: https://owasp.org/www-project-top-ten/
- OpenCode MCP servers: https://opencode.ai/docs/mcp-servers/
- Claude Code MCP docs should be rechecked at implementation time because client setup commands can change.

