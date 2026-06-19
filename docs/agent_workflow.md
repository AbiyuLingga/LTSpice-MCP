# Multi-Agent Workflow

> **Historical archive:** This workflow was used for the completed Phase 13
> integration. It is superseded by the single-agent rules in
> `AI_HARDWARE_AGENT_ROADMAP.md`. Do not run parallel AI file-editing agents.

> **Scope:** how the AI agents that build the File-Based Live
> Editing + Math Core workstream collaborate, branch, merge, and
> resolve conflicts. The source of authority for file ownership is
> [`AGENT_LOCKS.md`](AGENT_LOCKS.md); this document is the
> *narrative* for human readers and other agents.

## 1. Why the workstream is split

The File-Based Live Editing + Math Core plan
([`ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md))
covers ~2500 lines of spec. Doing it serially would take weeks and
would risk losing context between sub-phases. Doing it with one
monolithic agent would risk losing precision on hard parts (math
core, verification) and would have no clean hand-off points.

The plan calls for **parallel specialised agents**, each owning a
narrow slice of the codebase and reporting in a fixed shape. The
agents are:

| ID    | Role                          | Branch                              | Owns                                                                 |
|-------|-------------------------------|-------------------------------------|----------------------------------------------------------------------|
| 0     | Integrator / Supervisor       | `agent-0-integrator`                | Shared scaffolding, `AGENT_LOCKS.md`, `pyproject.toml`, `README.md` |
| 1     | Circuit Graph                 | `agent-1-circuit-graph`             | `src/ltagent/live/graph_schema.py`, `graph_validation.py`           |
| 2     | Edit Operations               | `agent-2-edit-ops`                  | `src/ltagent/live/edit_result.py`, `edit_ops.py`                     |
| 3     | Live Project + Snapshot       | `agent-3-live-project-snapshot`     | `src/ltagent/live/project.py`, `snapshot.py`, `history.py`          |
| 4     | Math Core                     | `agent-4-math-core`                 | `src/ltagent/math_core/*.py` (except `__init__.py`)                 |
| 5     | Simulation + Verification     | `agent-5-sim-verification`          | `src/ltagent/live/sim_loop.py`, `verification.py`, `measurements.py`|
| 6     | MCP Tools                     | `agent-6-mcp-tools`                 | `src/ltagent/mcp_live_tools.py` (pure functions)                    |
| 7     | Test + QA                     | `agent-7-tests-qa`                  | New tests, `conftest.py` fixtures, QA report                          |
| 8     | Docs                          | `agent-8-docs`                      | `docs/live_editing.md`, `docs/math_core.md`, `docs/examples/`, ...  |

Two slices are intentionally **not** in this matrix:

* The MCP entry point `src/ltagent/mcp_server.py` — owned by Agent 0,
  because every agent reads it and any change ripples to every
  tool.
* `pyproject.toml`, `README.md`, `AGENTS.md`, `CLAUDE.md`,
  `OPENCODE.md`, `MCP.md` — owned by Agent 0.

These are the **shared / shared** files in AGENT_LOCKS §12.

## 2. The agent contract

Every agent that lands work for the workstream commits to:

1. **Working on its own branch.** No commits on `main`, no commits
   on another agent's branch.
2. **Touching only files under its `Owns` list.** A change that
   needs a file outside the list is filed as an **Integration
   Request** (see §6 below).
3. **Writing a structured report at
   `docs/agent_reports/<agent>.md`.** The report has a fixed shape:
   * §1 Scope and role
   * §2 Plan & source of truth read
   * §3 Workstream checklist (numbered, status-bounded)
   * §4 Validation commands actually run
   * §5 Files changed (canonical list)
   * §6 Risks / open bugs
   * §7 Integration Requests
   * §8 How to run (commands)
4. **Small, reviewable commits.** Agent 0 reads commit messages
   during the merge review; a squashed history loses the
   "what-then-why" trail.
5. **No drive-by refactors.** Touch only what the current task
   requires.
6. **No global formatter runs.** `ruff format .` on a feature
   branch is a fast way to break unrelated agents' work.

The report template is enforced by Agent 0 during the merge
review. An agent that lands work without a report is treated as a
review failure.

## 3. File ownership matrix

The complete matrix lives in [`AGENT_LOCKS.md`](AGENT_LOCKS.md).
This section is a quick reference; if you are about to edit a file
that is **not** in your **Owns** list, stop and file an Integration
Request instead.

### 3.1 What Agent 8 (Docs) may edit

* `docs/live_editing.md`
* `docs/math_core.md`
* `docs/agent_workflow.md` (this file)
* `docs/examples/`
* `docs/agent_reports/docs.md`
* `docs/adr/0002-circuit-graph.md`
* `docs/adr/0003-file-based-live-editing.md`
* `docs/adr/0004-math-core-and-verification.md`

The `README.md` may receive a short link to new docs sections, but
the body is owned by Agent 0. Everything under `src/` and `tests/`
is forbidden.

### 3.2 What Agent 8 may not edit

* `src/ltagent/**/*.py` — any source module.
* `tests/**/*.py` — any test file.
* `pyproject.toml` — owned by Agent 0.
* `AGENTS.md`, `CLAUDE.md`, `OPENCODE.md`, `MCP.md` — owned by Agent 0.
* `docs/AGENT_LOCKS.md` — owned by Agent 0.
* `docs/PROJECT_PLAN.md`, `docs/SPEC.md`, `docs/security.md`,
  `docs/runner.md`, `docs/runner_troubleshooting.md`,
  `docs/ltspice_setup.md`, `docs/mcp_setup.md` — owned by Agent 0.
* Other agents' reports under `docs/agent_reports/` — each agent
  owns only its own file.

## 4. Branch and worktree strategy

### 4.1 One agent, one branch

Each agent has **one** canonical working branch:

```text
agent-0-integrator
agent-1-circuit-graph
agent-2-edit-ops
agent-3-live-project-snapshot
agent-4-math-core
agent-5-sim-verification
agent-6-mcp-tools
agent-7-tests-qa
agent-8-docs
```

The branches are off `main` at the same commit (`483a19b` as of this
writing). No agent is allowed to create a personal scratch branch —
the integrator wants to know which branch to pull from when reviewing
each role's work.

### 4.2 Working tree isolation

Two equally valid options:

**Option A — single working tree, one branch at a time.**
The agent checks out its branch, works, commits, then checks out the
next branch. Stash any in-flight changes before switching. Pros:
simple, no extra disk. Cons: easy to forget to switch back, easy to
accidentally commit on `main`.

**Option B — git worktrees.**
Each agent gets a worktree under `~/worktrees/<branch>`. Pros: agents
can work in parallel without context-switching, no `git checkout`
required to switch roles. Cons: extra disk, more directories to
track.

The plan does not mandate a choice; pick whichever the local
environment prefers and document it in the agent's report.

### 4.3 Pre-flight checklist before the first commit

```text
1. git status  --  confirm you are on agent-<n>-<role>
2. git branch  --  confirm HEAD == agent-<n>-<role>
3. ls docs/agent_reports/<agent>.md  --  confirm your report slot exists
4. Read AGENT_LOCKS.md  --  confirm every file you are about to edit is in your Owns list
5. Read the plan section for your role  --  confirm scope
6. Then start coding.
```

If step 4 fails, **stop** and either file an Integration Request
or escalate to Agent 0.

## 5. Day-to-day workflow

### 5.1 Inside your own branch

```text
git checkout agent-<n>-<role>
# edit files in your Owns list
git add <file>
git commit -m "feat(<role>): <one-line summary>"
# (repeat for each logical change)
git log --oneline  # review your commits before reporting
```

The commit message convention used by the existing project:

```text
<type>(<scope>): <subject>

Examples:
  feat(live): snapshot before apply
  fix(math_core): reject zero denominator in rc_lowpass
  test(verif): add tolerance-monotonicity tests
  docs(plan): cross-link math_core and live_editing
```

`<type>` is one of `feat`, `fix`, `refactor`, `test`, `docs`,
`chore`. `<scope>` is the agent's role or the module name.

### 5.2 Between agents

The normal hand-off pattern is **Integration Request → Agent 0 →
shared review → merge**. An agent that needs a file outside its Owns
list:

```text
1. Does NOT touch that file.
2. Writes an Integration Request in docs/agent_reports/<agent>.md
   §7 (format in AGENT_LOCKS §10).
3. Notifies Agent 0 (or waits for Agent 0 to read the queue).
4. Once Agent 0 accepts the request, Agent 0 makes the change in
   the shared file (or the owning agent makes it on their branch).
```

Cross-agent merges without a recorded request are a review failure.

### 5.3 The "don't break your neighbour" rules

* **Do not change function signatures.** If you need to add a
  parameter, give it a default. Downstream callers should not have
  to change their code to compile against your branch.
* **Do not rename public symbols.** Renames ripple to every
  consumer. File an Integration Request for the new name and a
  compatibility shim instead.
* **Do not change shared JSON shapes.** The schemas in
  `schemas/circuit_ir.schema.json` and `schemas/circuit_graph.schema.json`
  are part of the public contract. Any change needs an ADR.
* **Do not run formatters across the repo.** `ruff format .` will
  touch every agent's code; the resulting PR is unreviewable.

## 6. Integration Request format

Every Integration Request lives in the requesting agent's report
under `docs/agent_reports/<agent>.md`, in a section titled
`## Integration Requests`. The shape (AGENT_LOCKS §10):

```markdown
### Integration Request — <short title>

- **From:** Agent N
- **To:**   Agent M (or "Agent 0" for shared files)
- **File:** `path/to/file.py`
- **Why:**  one sentence
- **What:** one or two-sentence spec of the requested change.
- **Risk:** low / medium / high
- **Status:** pending | accepted | declined
```

Agent 0 reviews the integration queue during the merge to `main`.
Cross-agent merges without a recorded request are a review failure.

## 7. Conflict resolution

When two agents edit the same file (it happens — especially for
shared modules), the resolution ladder is AGENT_LOCKS §11:

1. **The owning agent wins by default.** The file's `Owns` row in
   AGENT_LOCKS §1-9 names the owner; other agents file requests.
2. **If neither agent owns the file** (a genuinely shared file),
   Agent 0 decides.
3. **If both agents have a legitimate claim** (e.g. Agent 4 and
   Agent 5 both need to change `verification_math.py`), Agent 0
   picks one of:
   * **A** — promote the file to shared; Agent 0 owns the merge.
   * **B** — keep ownership with the upstream agent; the downstream
     agent files an Integration Request.
   * **C** — split the file by responsibility boundary.

Default is **B**. Resolution **A** is reserved for files that more
than one agent genuinely edits in every release cycle. Resolution
**C** is the cleanest when the responsibilities are already
different — e.g. splitting a `verification.py` into
`live.verification` (the orchestrator) and `math_core.verification_math`
(the math helpers).

## 8. The merge to `main`

When every agent has finished its slice:

1. Each agent opens a PR or marks their branch ready.
2. Agent 0 reads every report under `docs/agent_reports/` and
   every Integration Request.
3. Agent 0 merges the branches in a planned order. The order is
   chosen so that each merge leaves the working tree green:
   * shared scaffolding first (Agent 0's own changes if any),
   * then Agent 1 (graph) → Agent 2 (edit ops) → Agent 3 (project),
   * then Agent 4 (math core) and Agent 5 (verification) — these
     can land in parallel because they touch different files,
   * then Agent 6 (MCP tools) — depends on Agent 0's FastMCP
     registration,
   * then Agent 7 (tests) — tests always land last so they assert
     against the merged source,
   * Agent 8 (docs) can land in any window because the docs are
     written against the plan and the existing public surface, not
     against any in-flight source change.
4. Agent 0 resolves Integration Requests during the merge. A request
   from Agent N → Agent M is either honoured in M's merge commit
   or recorded as declined (with a one-line reason).
5. CI runs `pytest`, `ruff check`, `mypy` on the merged tree. If any
   of the three fail, Agent 0 reopens the responsible agent's
   branch and asks for a fix.

The merged tree is "the workstream" — what is documented in
`docs/live_editing.md` and `docs/math_core.md`.

## 9. Communication expectations

* **Commits are the primary record.** Anything that ends up in the
  merged code is the agent's responsibility, regardless of what was
  discussed out-of-band.
* **Reports are the secondary record.** Anything that did not make
  it into a commit (open questions, declined ideas, deferred work)
  belongs in the report under §6 (risks) or §7 (integration
  requests).
* **Integration Requests are the only sanctioned cross-agent
  communication.** A change that needs a file outside an agent's
  Owns list is filed as a request, not negotiated in chat.
* **Agent 0 is the only role that may edit the shared
  scaffolding.** If you find yourself wanting to fix a typo in
  `AGENTS.md` or `pyproject.toml`, that is an Integration Request
  to Agent 0, not a self-help edit.

## 10. What "done" looks like for an agent

* Every checkbox in the agent's report checklist is green.
* Every test that the agent is responsible for passes locally.
* `ruff check <agent's files>` is clean.
* `mypy <agent's files>` is clean.
* The branch has no uncommitted changes.
* The report's §6 (risks) is empty or honestly populated.
* The report's §7 (integration requests) is empty if no
  cross-cutting work is needed, or has pending requests with owners
  assigned.

When all of those are true, the agent pings Agent 0 for review.

## 11. Cross-references

* [`AGENT_LOCKS.md`](AGENT_LOCKS.md) — the authoritative file
  ownership matrix.
* [`docs/agent_reports/`](agent_reports/) — every agent's report.
* [`../ltspice_file_based_live_editing_math_plan.md`](../ltspice_file_based_live_editing_math_plan.md)
  — plan of record, sections 12 (subagent architecture) and 5
  (repository structure).
* [`live_editing.md`](live_editing.md) and [`math_core.md`](math_core.md)
  — what the agents are building.
