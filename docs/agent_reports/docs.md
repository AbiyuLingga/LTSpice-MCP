# Agent 8 — Docs Report

> File-Based Live Editing + Math Core — user-facing documentation
> sweep. This is the canonical report for Agent 8's slice; it
> covers what landed, what was deliberately deferred, and the
> integration requests I have open against other agents.

## 1. Scope and role

Agent 8 is the Docs agent for the File-Based Live Editing + Math
Core workstream described in
[`ltspice_file_based_live_editing_math_plan.md`](../../ltspice_file_based_live_editing_math_plan.md).

Allowed surface (per [`AGENT_LOCKS.md`](../AGENT_LOCKS.md) §9):

* `docs/live_editing.md` (new)
* `docs/math_core.md` (new)
* `docs/agent_workflow.md` (new)
* `docs/examples/` (new directory + three worked examples)
* `docs/agent_reports/docs.md` (this file)
* `docs/adr/0002-…`, `0003-…`, `0004-…` (deferred; see §6)
* `README.md` — short cross-link additions only (not exercised this
  iteration)

Forbidden surface:

* `src/ltagent/**/*.py` — any source file.
* `tests/**/*.py` — any test file.
* `pyproject.toml` — Agent 0.
* `AGENTS.md`, `CLAUDE.md`, `OPENCODE.md`, `MCP.md` — Agent 0.
* `docs/AGENT_LOCKS.md` — Agent 0.
* `docs/PROJECT_PLAN.md`, `docs/SPEC.md`, `docs/security.md`,
  `docs/runner.md`, `docs/runner_troubleshooting.md`,
  `docs/ltspice_setup.md`, `docs/mcp_setup.md` — Agent 0.
* Other agents' reports under `docs/agent_reports/` — each agent
  owns only its own file.

## 2. Plan read and source of truth

Plan sections cited as the basis for the new docs:

* `live_editing.md` ← plan §4 (architecture), §6 (project layout),
  §7 (Circuit Graph), §8 (edit operations), §9 (snapshot / undo),
  §10 (CLI), §11 (MCP tools).
* `math_core.md` ← plan §13 (accuracy principles), §14 (modules),
  §15 (formula library), §16 (calculation report), §17
  (verification).
* `agent_workflow.md` ← AGENT_LOCKS §1-13; plan §5 (repository
  structure) and §12 (subagent architecture).
* `examples/rc_lowpass_live_edit.md` ← plan §0 example, §15.2
  (RC lowpass formula), §16.1 (calculation.md), §17 (verification).
* `examples/noninv_opamp_calculation.md` ← plan §15.2 (noninv opamp
  formula), §16 (calculation report), §17 (verification).
* `examples/ldr_dark_detector_workflow.md` ← plan §2.2 (UX
  target), §6 (project layout), §7.3 (validation), §8 (edit ops).

Public surface cross-checked against:

* `src/ltagent/units.py` — Phase 2 SI parser (precursor to
  `math_core.units`).
* `src/ltagent/security.py` — `safe_resolve_under`, `validate_slug`
  (Agent 0 shared).
* `src/ltagent/ir.py` — Circuit IR v0.1 contract (Phase 1).
* `src/ltagent/live/project.py` — file-name constants, project
  layout (Agent 3).
* `src/ltagent/math_core/units.py`,
  `math_core/standard_values.py`,
  `math_core/formulas.py`,
  `math_core/calculation_report.py`,
  `math_core/verification_math.py` (read from working tree on
  `agent-6-mcp-tools` for cross-reference; none of these are edited
  by Agent 8).
* `src/ltagent/live/verification.py` (read-only) — the verification
  dataclasses, check kinds, error codes.
* `examples/rc_lowpass.ir.json`,
  `examples/noninv_opamp.ir.json` — the canonical Circuit IR seeds
  that the new examples cross-reference.

The math core, the live core, and the MCP live tools are still being
landed by Agents 1-6 in their own branches. The new docs reflect the
**intended contract** as defined in the plan and the source files
already on disk; sections that depend on a module that has not yet
landed are explicitly marked **[Planned]**.

## 3. Documentation checklist

Status legend: `[x]` green, `[~]` drafted, `[ ]` pending.

### 3.1 `docs/live_editing.md` (new)

* `[x]` Concept section (plan §4, §0 example).
* `[x]` Source-of-truth ordering (plan §4.3).
* `[x]` Project directory layout (plan §6).
* `[x]` `metadata.json` shape (plan §6.1).
* `[x]` `edit_history.jsonl` shape (plan §6.2).
* `[x]` Snapshot + undo (plan §9).
* `[x]` Edit operation catalog (plan §8.3, §8.4).
* `[x]` Apply workflow (plan §8.5).
* `[x]` Worked workflow (plan §0 example, expanded).
* `[x]` Edit existing project workflow (plan §0 second example).
* `[x]` MCP tool groups (plan §11.2).
* `[x]` Design principle (plan §11.1).
* `[x]` Cross-references to math_core and agent_workflow.

### 3.2 `docs/math_core.md` (new)

* `[x]` Why the LLM is not the calculator (plan §13.2).
* `[x]` Module map (plan §14.1).
* `[x]` Unit parser contract (plan §14.2).
* `[x]` SI prefix table (plan §14.2 + the
  `ltagent.math_core.units.SI_PREFIXES` source).
* `[x]` Quantity hint from the unit letter (plan §14.2).
* `[x]` `UnitError` shape (matches the source dataclass).
* `[x]` `format_value` companion (source: `ltagent.math_core.units.format_value`).
* `[x]` Formula engine contract (plan §14.3 + the
  `FormulaResult` dataclass).
* `[x]` MVP formula catalog (plan §14.3 + §15.2).
* `[x]` Worked example: RC low-pass resistor (plan §15.2).
* `[x]` Stable formula error codes (matches
  `ltagent.math_core.formulas` module constants).
* `[x]` Standard-value selection (plan §14.4 + the
  `StandardValueSelection` dataclass).
* `[x]` Selection algorithm (plan §14.4 + the source).
* `[x]` `calculation.json` shape (plan §16.2).
* `[x]` `calculation.md` shape (plan §16.1).
* `[x]` Verification gate (plan §17).
* `[x]` Verification levels (plan §17.2).
* `[x]` Confidence scoring ladder (plan §17.3).
* `[x]` Tolerance and worst-case analysis (plan §14.8).
* `[x]` Optimizer (plan §14.7, §18).
* `[x]` Cross-references to live_editing and agent_workflow.

### 3.3 `docs/agent_workflow.md` (new)

* `[x]` Why the workstream is split (plan §12).
* `[x]` Agent roster table (matches AGENT_LOCKS §0).
* `[x]` Agent contract (AGENT_LOCKS §13 implicit; §1-9 explicit).
* `[x]` File ownership matrix quick reference (AGENT_LOCKS §1-9).
* `[x]` Branch + worktree strategy (AGENT_LOCKS §13; plan §5).
* `[x]` Day-to-day workflow (commit + cross-agent hand-off).
* `[x]` Integration Request format (AGENT_LOCKS §10).
* `[x]` Conflict resolution (AGENT_LOCKS §11).
* `[x]` Merge to `main` order (AGENT_LOCKS §13).
* `[x]` Communication expectations.
* `[x]` "Done" definition per agent.
* `[x]` Cross-references.

### 3.4 `docs/examples/`

* `[x]` `rc_lowpass_live_edit.md` — plan §0 example, traced through
  every apply step.
* `[x]` `noninv_opamp_calculation.md` — full math core transcript,
  including failure modes.
* `[x]` `ldr_dark_detector_workflow.md` — multi-stage design,
  iterate-loop with snapshot/restore.

### 3.5 `docs/agent_reports/docs.md`

* `[x]` This file.

## 4. Validation commands run

Agent 8 only writes Markdown; no Python changed. The validation
applied is **documentation-grade**:

```bash
# All new files exist and are non-empty.
ls -la docs/live_editing.md docs/math_core.md docs/agent_workflow.md
ls -la docs/examples/

# Section headings render correctly. (Manual review.)
rg -n '^# ' docs/live_editing.md docs/math_core.md docs/agent_workflow.md
rg -n '^# ' docs/examples/

# Cross-references resolve to existing files (manual check).
#   docs/live_editing.md  -> ../ltspice_file_based_live_editing_math_plan.md
#                           (lives at the repo root, cross-ref documented)
#                        -> docs/math_core.md, docs/agent_workflow.md
#                        -> docs/AGENT_LOCKS.md
#                        -> docs/examples/rc_lowpass_live_edit.md
#   docs/math_core.md     -> ../ltspice_file_based_live_editing_math_plan.md
#                        -> docs/live_editing.md, docs/agent_workflow.md
#                        -> docs/examples/noninv_opamp_calculation.md
#                        -> docs/AGENT_LOCKS.md
#   docs/agent_workflow.md -> docs/AGENT_LOCKS.md
#                        -> docs/agent_reports/
#                        -> ../ltspice_file_based_live_editing_math_plan.md
#                        -> docs/live_editing.md, docs/math_core.md
#   docs/examples/*.md    -> ../live_editing.md, ../math_core.md, ../agent_workflow.md
#                        -> ../ltspice_file_based_live_editing_math_plan.md
#                        -> examples/rc_lowpass.ir.json (repo root)
#                        -> examples/noninv_opamp.ir.json (repo root)
```

Branch + ownership checks:

```bash
# I am on my own branch, not main.
git branch --show-current    # -> agent-8-docs

# I touched only files in my Owns list.
git status --short
# -> only docs/ additions and the docs/agent_reports/docs.md file

# No drive-by format / lint / typecheck run.
git diff agent-8-docs..main -- '*.py' 'pyproject.toml' '*.cfg' '*.toml'
# -> empty (no Python touched)
```

## 5. Files changed (canonical list)

Created on branch `agent-8-docs`:

* `docs/live_editing.md` — 12 sections, cross-referenced with
  `math_core.md`, `agent_workflow.md`, the plan, and the AGENT_LOCKS
  document.
* `docs/math_core.md` — 12 sections, cross-referenced with the same
  documents; source-accurate to
  `ltagent.math_core.{units,formulas,standard_values,calculation_report,verification_math}`
  and `ltagent.live.verification`.
* `docs/agent_workflow.md` — 11 sections, mirrors the AGENT_LOCKS
  ownership matrix in narrative form and lays out the branch /
  worktree / merge conventions.
* `docs/examples/rc_lowpass_live_edit.md` — 12 sections, traced
  step-by-step from prompt to verified state.
* `docs/examples/noninv_opamp_calculation.md` — 8 sections,
  math-core-only transcript with failure modes.
* `docs/examples/ldr_dark_detector_workflow.md` — 8 sections,
  multi-stage sensor design with iterate-loop.
* `docs/agent_reports/docs.md` — this report.

Not touched (deliberate deferrals):

* `src/ltagent/**/*.py` — Agent 0 / Agent 1-6 / Agent 7.
* `tests/**/*.py` — Agent 7.
* `pyproject.toml`, `README.md`, `AGENTS.md`, `CLAUDE.md`,
  `OPENCODE.md`, `MCP.md`, `docs/AGENT_LOCKS.md`,
  `docs/PROJECT_PLAN.md`, `docs/SPEC.md`, `docs/security.md`,
  `docs/runner.md`, `docs/runner_troubleshooting.md`,
  `docs/ltspice_setup.md`, `docs/mcp_setup.md` — Agent 0.
* `docs/adr/0002-circuit-graph.md`,
  `docs/adr/0003-file-based-live-editing.md`,
  `docs/adr/0004-math-core-and-verification.md` — see §6.

## 6. Open questions / deferred items

### 6.1 ADRs not yet drafted

`AGENT_LOCKS.md` lists three ADRs under Agent 8's `Owns`:

* `docs/adr/0002-circuit-graph.md`
* `docs/adr/0003-file-based-live-editing.md`
* `docs/adr/0004-math-core-and-verification.md`

These are **not drafted** in this iteration. Reasoning:

* ADR `0004-hybrid-hdl-spice.md` already exists at the file path
  `docs/adr/0004-hybrid-hdl-spice.md`; the AGENT_LOCKS entry
  `0004-math-core-and-verification.md` would be a *different* file
  with a confusingly similar name. Filed as an Integration Request
  to Agent 0 (§7) to resolve the path conflict before drafting.
* ADRs `0002-circuit-graph.md` and `0003-file-based-live-editing.md`
  depend on the decisions that Agents 1, 2, and 3 are still
  landing. Drafting them now would pre-commit Agent 0 to decisions
  that the source-owning agents have not finalised. Deferred until
  the source modules have landed and a second review pass is
  possible.

### 6.2 `README.md` cross-link

`README.md` has a "Phase X is complete" status block at the top. The
new docs do not add a Phase 13/14 entry to that block. That entry
is owned by Agent 0 (per AGENT_LOCKS §1, "decisions Agent 0 makes
unilaterally"). Deferred until Agent 0 chooses the Phase name and
the merge order.

### 6.3 Live editing CLI command reference

`live_editing.md` describes the **intended** CLI surface
(`ltagent live open`, `ltagent live apply`, etc.) per plan §10. The
CLI surface itself is owned by Agent 0; Agent 8 does not implement
or document the actual flag set on each subcommand. When Agent 0
adds the `live` subcommand parser, the new docs may need a
flag-by-flag reference cross-link. Filed as an Integration Request
to Agent 0 (§7).

### 6.4 MCP live tool reference

`live_editing.md` §8 names the tool groups from plan §11.2 and
references `ltagent.mcp_live_tools` for the function bodies and
`ltagent.mcp_server` for the registration. The exact tool name list
and input schemas will be owned by Agent 6 (function bodies) and
Agent 0 (registration). The docs are written to be
**forward-compatible**: when Agent 6 commits, the docs already
match the planned surface. If the shipped surface diverges, the
docs will need a follow-up patch on this branch.

## 7. Integration requests

### Integration Request — ADR path conflict

- **From:** Agent 8
- **To:**   Agent 0
- **File:** `docs/adr/`
- **Why:**  AGENT_LOCKS §9 lists
  `docs/adr/0004-math-core-and-verification.md` under Agent 8's
  `Owns`, but `docs/adr/0004-hybrid-hdl-spice.md` already exists
  with a sibling number. Two files numbered `0004` will collide in
  directory listings and cross-references.
- **What:** Either rename the existing `0004-hybrid-hdl-spice.md`
  to `0005-…` and let Agent 8 use `0004-math-core-and-verification.md`,
  or rename the planned ADR to `0006-math-core-and-verification.md`
  and keep the hybrid-hdl-spice one as `0004`. Either way, please
  update AGENT_LOCKS §9 to match.
- **Risk:** low
- **Status:** pending

### Integration Request — CLI flag surface for live editing

- **From:** Agent 8
- **To:**   Agent 0
- **File:** `src/ltagent/cli.py` (live subcommand parser)
- **Why:**  `docs/live_editing.md` §4.1 and §10 reference the
  planned `ltagent live <verb>` CLI surface from plan §10, but the
  flags (--reason, --json, etc.) are not yet wired. Agent 0 owns
  the CLI parser.
- **What:** When the `live` subcommand parser lands, please expose
  the flags listed in plan §10 verbatim (`--json`, `--reason`,
  project id as positional argument) so the docs remain accurate.
  If you want to add or rename flags, file an Integration Request
  back to Agent 8 so the docs stay in sync.
- **Risk:** medium
- **Status:** pending

### Integration Request — MCP live tool registration status

- **From:** Agent 8
- **To:**   Agent 0
- **File:** `src/ltagent/mcp_server.py` (and the FastMCP
  registration loop)
- **Why:**  `docs/live_editing.md` §8 lists the MCP live tool
  groups as planned. Agent 6 has committed
  `src/ltagent/mcp_live_tools.py` on `agent-6-mcp-tools` but the
  FastMCP decorators in `mcp_server.py` are not yet wired. Agent 0
  owns that registration.
- **What:** When wiring `ltagent.mcp_live_tools` into
  `ltagent.mcp_server`, please reuse the function names in
  `docs/live_editing.md` §8 verbatim. If a tool name changes, file
  an Integration Request back to Agent 8 so the docs can be
  updated.
- **Risk:** low
- **Status:** pending

### Integration Request — Calculation report schema version

- **From:** Agent 8
- **To:**   Agent 4
- **File:** `src/ltagent/math_core/calculation_report.py`
- **Why:**  `docs/math_core.md` §6.1 quotes the `calculation.json`
  shape from plan §16.2 verbatim, including the
  `schemaVersion: "0.1"` field. The actual constant
  `CALCULATION_SCHEMA_VERSION` lives in Agent 4's module; Agent 8
  cannot edit the source file but the docs reflect the planned
  value.
- **What:** If the shipped `CALCULATION_SCHEMA_VERSION` constant
  changes to a value other than `"0.1"`, please file an Integration
  Request back to Agent 8 so the docs can be updated. Otherwise no
  action required.
- **Risk:** low
- **Status:** pending

## 8. Risks and known limitations

* **Forward-compatibility.** The new docs are written against the
  *plan* and the *source files already on disk*. When Agents 1-6
  land their modules, the surface they ship may differ in field
  names, error codes, or subcommand names. The docs are written to
  survive minor renames (the structure follows the plan, not
  speculative implementation details), but any breaking change in
  Agent 4's `FormulaResult` or Agent 5's `VerificationResult` will
  require a docs patch.
* **No executable verification.** Agent 8's "validation" is
  structural — does the file exist, do the cross-references resolve,
  do the section headings parse. Agent 8 does not run `pytest` or
  `ruff` (those are Agent 7's surface).
* **Markdown style is informal.** The docs use a mix of prose,
  bullet lists, and code blocks. AGENT_LOCKS §1 item 4 reserves
  Markdown style of `docs/agent_reports/*.md` to Agent 0. The
  *narrative* docs (`live_editing.md`, `math_core.md`,
  `agent_workflow.md`) are Agent 8's; if Agent 0 standardises
  something later, Agent 8 will follow in a follow-up patch.
* **Numerical examples are illustrative.** The RC lowpass, op-amp,
  and LDR worked examples contain numerical values that match the
  math-core formulas and the textbook formulas they describe. The
  *shapes* of the JSON envelopes are canonical; the specific
  measured values (e.g. `GAIN_1K = -0.0107`) depend on the
  eventual LTspice measurement behaviour, which is owned by Agent
  5. Where a number is illustrative rather than measured, the
  example flags it explicitly.
* **Concurrent-branch hazard.** The repo's working tree was being
  used by multiple agents in parallel during this work; the agent's
  branch was switched by other agents' `git checkout` operations
  multiple times during the session. Mitigation: Agent 8's edits
  are written via the `write` tool (which targets an absolute path)
  and verified post-hoc with `ls` and `wc -l`. The intended branch
  for these files remains `agent-8-docs`; if the integrator finds
  them on a different branch, the `write` paths still place the
  files under the right absolute location and a simple `git
  checkout agent-8-docs -- docs/…` will surface them.

## 9. How to read these docs

| Reader | Start here |
|---|---|
| New user (human) | `docs/live_editing.md` §1-3, then `docs/examples/rc_lowpass_live_edit.md` |
| Power user (human) | All three example docs, then `docs/math_core.md` §4-7 |
| Coding agent | `docs/agent_workflow.md`, then the file ownership matrix in `docs/AGENT_LOCKS.md` |
| Integrator / Agent 0 | This report's §7 (Integration Requests) |
| Tester / Agent 7 | `docs/live_editing.md` §4 (snapshot/undo) and §5 (edit operations) |
| Math reviewer / Agent 4 | `docs/math_core.md` §3-5 |

## 10. Cross-references

* [`../live_editing.md`](../live_editing.md) — File-Based Live Editing
  surface.
* [`../math_core.md`](../math_core.md) — calculation engine.
* [`../agent_workflow.md`](../agent_workflow.md) — multi-agent
  collaboration contract.
* [`../AGENT_LOCKS.md`](../AGENT_LOCKS.md) — authoritative file
  ownership matrix.
* [`../examples/`](../examples/) — worked transcripts.
* [`../../ltspice_file_based_live_editing_math_plan.md`](../../ltspice_file_based_live_editing_math_plan.md)
  — plan of record.
* [`../../AGENTS.md`](../../AGENTS.md) — operational guide for all
  coding agents.
* `tests_qa.md` (historical branch-only artifact, not integrated) — Agent 7's report; Agent 8's docs are
  consumed by Agent 7's test descriptions.
