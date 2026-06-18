# SPEC — ltspice-ai-agent MVP

> Version: Phase 11 (Advanced Analog Templates) complete. The full
> MVP definition is in [`PROJECT_PLAN.md`](PROJECT_PLAN.md) section
> 26 ("Definition Of Done For MVP"). This document only specifies
> what each phase must deliver and what the agent-facing contract
> looks like.

## 1. Phasing

| Phase | Scope | Done when |
|---|---|---|
| 0 | Repo, CLI skeleton, `ltagent doctor`, config, tests, CI | This document's "Phase 0 acceptance" |
| 1 | Circuit IR models, JSON load/save, validation, examples | All Phase 1 unit tests green |
| 2 | `.cir` netlist generator from IR | Snapshot tests match for the 3 MVP topologies |
| 3 | LTspice runner (Wine + native) with timeout | Integration test green when LTspice is available; skipped otherwise |
| 4 | `.log` / `.meas` parser + `result.json` | Sample-log fixtures parse to expected values |
| 5 | `.asc` writer + layout checker for 3 MVP topologies | Generated `.asc` opens in LTspice; layout score ≥ 85 |
| 6 | Template library (`list`, `show`, `match`, `audit`, `seed`) | Matcher reuses `rc_lowpass` for different cutoffs; cross-status id collisions rejected |
| 7 | `ltagent create` end-to-end workflow | A single command creates a complete project |
| 8 | Rule-based planner for English + Indonesian | Supported prompts produce valid IR; others get a structured refusal |
| 9 | Template evaluator + manual promoter | Failed sims and low-score layouts cannot be promoted |
| 10 | MCP server v1 (stdio, curated tools) | `ltagent-mcp` lists tools; tools match CLI output |
| 11 | Advanced analog templates (op-amp, rectifier, BJT switch, …) | Hand-made official templates in IR + deterministic .asc layout |
| 12 | E-series optimization loop | Reports ideal, selected, actual, error % |
| 13 | Optional minimal web UI | UI reads projects dir, no auth, no cloud |

Phases 1+ require Phase 0 to be green. Do not start a phase until the
previous one is merged and CI is green.

## 2. Phase 11 acceptance (current phase)

- `ltagent ir validate examples/inverting_opamp.ir.json --json`
  (and the other 6 new examples) accepts the IR and emits a
  structured success payload.
- `ltagent netlist` + `ltagent asc` produce a complete
  `circuit.cir` / `circuit.asc` for every Phase 11 topology. The
  `.cir` contains the right `.model` and `.subckt` blocks plus
  the diode / BJT / MOSFET / opamp SPICE lines.
- `ltagent template list --status official` returns 10 templates
  (3 MVP passive + 7 Phase 11 analog).
- Each new official template has `simulationVerified=true` and
  `layoutScore >= 85` (per plan §15.3 + §21 acceptance).
- `pytest tests/test_phase11.py` passes with zero LTspice / Wine
  invocations (53 tests).
- `ruff check` and `mypy src` are clean on the touched files.

## 2. Phase 0 acceptance (this phase)

**CLI surface**

```
ltagent --version
ltagent --help
ltagent doctor [--json] [--simulate]
ltagent init [DIR]
ltagent config show [--json]
ltagent config validate [--json]
```

**`ltagent doctor` checks (all implemented as pure functions, testable
without LTspice):**

1. Python version (≥ 3.11)
2. `ltspice-ai-agent` package version
3. Config file presence and parseability
4. Workspace writability
5. LTspice executable presence and file type
6. Wine command presence (auto-detect `/opt/wine-stable/bin/wine` and
   `which wine`)
7. Wine prefix hint (`~/.wine/drive_c/...`)
8. Temp directory writability (write/read/delete a tiny `.cir`)
9. Optional LTspice smoke simulation (only when `--simulate`)

**`ltagent init`** creates a project directory with `circuit.ir.json`
template, `circuit.cir` template, `metadata.json`, and `.gitignore`.
The directory is empty of real circuits in Phase 0; this command
exercises path safety and JSON output.

**`ltagent config show|validate`** reports the resolved config as JSON
and validates that types are well-formed (no required fields in Phase 0).

**JSON output contract (every `--json` command):**

```json
{
  "success": true,
  "command": "doctor",
  "message": "Doctor completed with 1 warning",
  "data": { "checks": [...] },
  "warnings": [{ "code": "WINE_NOT_ON_PATH", "detail": "..." }],
  "errors": []
}
```

Failure:

```json
{
  "success": false,
  "command": "run",
  "message": "LTspice batch run timed out",
  "data": { "timeoutSeconds": 20 },
  "warnings": [],
  "errors": [{ "code": "LTSPICE_TIMEOUT", "detail": "..." }]
}
```

**Tests:** `pytest` passes with no real LTspice / Wine invocations.
Every check in `doctor.py` is exercised via monkeypatched fixtures
in `tests/test_doctor.py`.

**Quality gates:** `ruff check .`, `mypy src --strict`, and
`python -m build` all succeed.

**CI:** GitHub Actions runs the quality gates on Python 3.11 and
3.12. Integration tests (`-m integration`) are tagged and skipped by
default; they will be enabled in Phase 3.

## 4. Phase 6 acceptance (Template Library)

**Goal:** Reuse stable circuits without creating duplicate official
templates for value-only variants of the same topology.

**Public Python API (`src/ltagent/templates.py`):**

```python
from ltagent.templates import (
    TemplateStatus,            # OFFICIAL | CANDIDATE | REJECTED
    TemplateManifest,          # frozen dataclass, .to_dict() / .from_dict()
    TemplateError,             # carries stable error code + data
    MatchResult,               # (matched, template, isValueVariant, ...)
    AuditReport,
    # IO
    load_manifest, dump_manifest,
    # Library queries
    list_templates, show_template, find_by_topology,
    # Match + use count
    match_template, increment_use_count,
    # Status transitions
    create_candidate_from_ir, move_template,
    # Maintenance
    audit_templates, write_index,
    seed_default_templates,
)
```

**Directory layout (created on first access by `_ensure_root` and on
`ltagent template seed`):**

```
<workspace>/templates/
    index.json
    official/<id>/{manifest.json,template.ir.json}
    candidates/<id>/{manifest.json,template.ir.json}
    rejected/<id>/{manifest.json,template.ir.json}
```

**CLI surface (Phase 6):**

```bash
ltagent template list [--status official|candidate|rejected] --json
ltagent template show <id> [--status <s>] --json
ltagent template match <ir.json> [--status <s>] [--no-bump] --json
ltagent template audit --json
ltagent template seed --json
```

**Acceptance behaviour:**

* `ltagent template list` returns manifests sorted by id. Missing dirs
  are created (idempotent). Status filter accepts singular and plural
  forms ("candidate" / "candidates", "rejected").
* `ltagent template match <ir>` for an IR with the same topology as an
  existing official template returns `matched=true` with the candidate
  manifest and `isValueVariant=true` if the structural signature
  differs (i.e. different values for the same component slots). This
  is the contract that prevents duplicate official templates.
* `match` increments `useCount` unless `--no-bump` is passed. The new
  count is returned in the payload.
* `audit` reports per-status counts, total manifests, per-topology
  counts, **and** within-status duplicate topologies (the bad case).
  Cross-status duplicates are allowed by design (an official supersedes
  a candidate of the same topology).
* `create_candidate_from_ir` refuses to create a template whose id
  already exists in any status directory. `move_template` refuses to
  clobber an existing destination.

**Security invariants (per plan section 18):**

* `template_id` must match `^[a-z][a-z0-9_]{0,63}$`. Path traversal
  patterns are rejected by the pattern, not at the OS layer.
* All writes are atomic (`mkstemp` + `Path.replace`).
* No subprocess is invoked; the module is pure file I/O.

**Out of scope for Phase 6 (lands later):**

* Scoring-driven promotion (Phase 9)
* `ltagent template evaluate` / `ltagent template promote` (Phase 9)
* MCP template resources (Phase 10)
* `ltagent create` end-to-end workflow (Phase 7)

## 2.1 Phase 3 acceptance (runner)

**Module:** `ltagent.runner` (no business logic in the CLI).

**Public API:**

```python
from ltagent.runner import (
    RunRequest,         # frozen dataclass: cir_path, workdir, timeout_seconds, mode,
                        #   executable, wine_command, extra_args, expected_log_name
    RunResult,          # frozen dataclass: success, command, message, data, warnings, errors
    build_argv,         # pure: request -> list[str], raises RunnerBuildError on pre-flight issues
    resolve_wine,       # configured/which/well-known -> str | None
    run_simulation,     # request -> RunResult, never raises
    run_from_config,    # convenience wrapper used by `ltagent run`
    RunnerBuildError,   # subclass of ValueError; carries .code, .detail, .data
    ERR_EXECUTABLE_NOT_SET,
    ERR_EXECUTABLE_MISSING,
    ERR_WINE_NOT_FOUND,
    ERR_MODE_INVALID,
    ERR_CIR_MISSING,
    ERR_CIR_NOT_FILE,
    ERR_LAUNCH,
    ERR_TIMEOUT,
    ERR_NO_LOG,
    INTEGRATION_MARKER,
)
```

**CLI:**

```
ltagent run <cir> [--workdir DIR] [--timeout SECONDS] [--ltspice-arg ARG ...] [--json|--text]
```

The subcommand must:

* Return the standard JSON contract documented in section 2.
* Report a missing executable as `LTSPICE_EXECUTABLE_NOT_SET` /
  `LTSPICE_EXECUTABLE_MISSING`, not a stack trace.
* Report a timeout as `LTSPICE_TIMEOUT` with the exact `argv` in the
  error payload so the user can reproduce it manually.
* Report a missing `.log` as `LTSPICE_NO_LOG`, not a false success.
* Pass a timeout floor of `MIN_TIMEOUT_SECONDS = 5` (anything below
  this is clamped, not respected).

**Tests:** `pytest` passes with **no** real LTspice / Wine invocations.
Every code path in `runner.py` is exercised via monkey-patched
`subprocess` stubs in `tests/test_runner.py`. The single integration
test (`test_run_simulation_real_smoke_circuit`) is marked
`@pytest.mark.integration` and auto-skips when the configured
executable is missing or wine cannot be resolved.

**Quality gates:** `ruff check .`, `mypy src`, and `python -m build`
all succeed.

## 2.2 Phase 5 acceptance (asc writer + layout checker)

**Modules:** `ltagent.asc` (writer) and `ltagent.layout_checker` (scorer).

**Public API:**

```python
from ltagent.asc import (
    ASCError,                # structured failure; carries .code, .detail, .data
    ASCResult,               # frozen dataclass: text, line_count, header, component_count,
                             #   wire_count, flag_count, topology, placements, node_points
    render_asc,              # pure: CircuitIR -> ASCResult, no filesystem
    write_asc,               # render + write; returns ASCResult
)
from ltagent.layout import (
    GRID_X, GRID_Y, MAIN_Y, OUT_Y, GROUND_Y, INPUT_X, SHEET_W, SHEET_H,
    Point, SymbolPlacement,
    plus_pin, minus_pin, resistor_pins, capacitor_pins,
    symbol_bounding_box, rect_overlaps, wire_crosses, wire_length, pairwise,
)
from ltagent.layout_checker import (
    OFFICIAL_THRESHOLD,      # 85
    PROJECT_THRESHOLD,       # 70
    LONG_WIRE_LIMIT,
    LayoutWarning, LayoutResult,
    classify_score, score_layout,
)
```

**CLI:**

```
ltagent asc <ir.json> [--out PATH] [--json|--text]
```

The subcommand must:

* Return the standard JSON contract documented in section 2.
* For each of the three MVP topologies (`voltage_divider`, `rc_lowpass`,
  `rc_highpass`), produce a valid ``.asc`` whose non-comment lines are
  drawn from the LTspice-accepted vocabulary only
  (``Version``, ``SHEET``, ``WIRE``, ``FLAG``, ``SYMBOL``, ``SYMATTR``,
  ``TEXT``, ``*``).
* Emit exactly one ``SYMBOL``/``SYMATTR InstName``/``SYMATTR Value``
  block per IR component, in IR order.
* Emit a ``FLAG 0`` for the ground node.
* Emit a ``TEXT x y Left 2 !<directive>`` for every analysis block
  in the IR, with the directive matching the netlist generator's
  formatting.
* Reject unsupported topologies with ``ASC_UNSUPPORTED_TOPOLOGY`` and
  reject malformed IRs with the same structured error codes as the
  netlist subcommand.
* Report a ``layoutScore`` (0-100) and ``layoutClassification``
  (``"official"``/``"project"``/``"reject"``) in the JSON output.
* The MVP layouts must score exactly 100 and classify as
  ``"official"``.

**Layout policy** (plan section 12.4):

```
start = 100
-30 component overlap
-20 missing ground
-10 per wire crossing
-5  per label collision
-3  per long wire
-2  per min-spacing violation
score >= 85  -> official
70 <= score < 85 -> project
score < 70  -> reject
```

**Tests:** ``pytest`` passes with **no** real LTspice / Wine invocations.
Every scoring rule is exercised in ``tests/test_layout_checker.py``
via hand-built ``ASCResult`` fixtures; every writer path is covered
in ``tests/test_asc.py``; the grid helpers in
``tests/test_layout.py``. CLI surface is covered in
``tests/test_cli.py::test_asc_*``.

**Quality gates:** ``ruff check .``, ``mypy src``, and
``python -m build`` succeed for the Phase 5 files.

## 2.3 Phase 8 acceptance (rule-based planner)

**Module:** `ltagent.planner`. Pure functions only. No filesystem I/O, no
subprocess, no LLM.

**Public API:**

```python
from ltagent.planner import (
    PlannerRefusal,            # frozen dataclass; .code, .message,
                               #   .supported_topologies, .next_step, .data
    REFUSAL_UNSUPPORTED_PROMPT,
    REFUSAL_MISSING_PARAM,
    REFUSAL_INVALID_VALUE,
    REFUSAL_AMBIGUOUS_PROMPT,
    plan_prompt,               # str -> CircuitIR | PlannerRefusal
)
```

**CLI:**

```
ltagent plan "<prompt>" [--out PATH] [--json|--text]
```

**Behaviour:**

* Supported prompts (English or Indonesian) produce a `CircuitIR` whose
  model round-trips through `ltagent.ir.validate_dict` cleanly.
  Examples:
  * `make voltage divider 12V to 5V`
  * `buat pembagi tegangan 12V ke 5V`
  * `make RC low-pass cutoff 1kHz` (default C = 100 nF)
  * `buat RC low-pass cutoff 1kHz dengan C 100nF`
  * `make RC low-pass cutoff 1kHz with R 1.59k`
  * `make RC high-pass cutoff 500Hz`
  * `buat RC high-pass cutoff 1kHz dengan C 1uF`
* Unsupported prompts return a `PlannerRefusal` whose `code` is one of
  the stable refusal codes. The CLI surfaces this as `success=false`
  with a single error entry carrying the same code and a
  `supportedTopologies` list in `data`.
* The generated IR's `name` matches the IR slug pattern
  (`^[a-z][a-z0-9_-]{0,63}$`).
* Voltage-divider default: `R2 = 1 kΩ`, then
  `R1 = R2 * (Vin - Vout) / Vout`. `Vin > Vout > 0` is enforced.
* RC-filter default: `C = 100 nF` if neither C nor R is given; the
  missing component is computed from
  `R = 1 / (2π * fc * C)`. A `SINE(0 1 fc)` source is generated.
* If `--out PATH` is given, the IR is also written to that path. The
  path must be inside the current working directory; otherwise the CLI
  returns `PATH_OUTSIDE_CWD` without writing.
* The planner never executes shell, never reads the filesystem, and
  never opens the network.

**Tests:** `pytest` passes with **no** LTspice / Wine invocations.
`tests/test_planner.py` covers topology detection (EN + ID), value
calculation, refusals, slug safety, IR round-trip, and unit extraction
edge cases. `tests/test_cli.py` covers the `plan` subcommand end to end.

**Quality gates:** `ruff check src/ltagent/planner.py tests/test_planner.py`
and `mypy src/ltagent/planner.py` are clean.

## 3. Agent-facing invariants (apply from Phase 0 forward)

- **Deterministic layout.** The `.asc` writer is Python. The agent may
  not write production coordinate lines.
- **No arbitrary shell.** Every `subprocess` call uses a list of args.
  The runner launches only the configured LTspice executable.
- **Workspace-bounded writes.** All output paths resolve under
  `workspace.projects_dir` or `workspace.templates_dir`. Path traversal
  is rejected.
- **JSON output contract.** Every `--json` command returns the contract
  above. Agents must not infer success from prose.
- **`safe_mode = true` by default.** Unrecognized SPICE directives are
  rejected. MCP resource paths that traverse are rejected.
