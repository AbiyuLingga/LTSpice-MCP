# Security model

This document describes the threat model and the controls `ltagent`
applies. It is intentionally short — the project's security
posture should be obvious from a 5-minute read.

## 1. Threat model

The product is a local CLI that eventually drives an LTspice
simulation, plus an MCP server. Its primary users are AI coding
agents and engineers using AI agents. The threats are:

| Threat | Scenario |
|---|---|
| Prompt injection | A user prompt or fetched content asks the agent to run a shell, read `/etc/passwd`, or write outside the workspace. |
| Unsafe SPICE directives | A generated IR includes `.include /etc/passwd` or `.lib /home/user/secret.lib`. |
| Path traversal | A project name like `../../etc` would let the runner or MCP resource handler escape the workspace. |
| Excessive tool surface | An MCP tool that does arbitrary shell or arbitrary file read/write is a footgun for agents. |
| Template pollution | A buggy auto-promoter turns every project into an "official" template, degrading future outputs. |
| `.raw` bloat | The default save-raw setting fills the disk on long transient sims. |
| Misread failure | An agent treats a timeout as success because the output was prose, not structured. |

## 2. Controls

### Input validation

- **Circuit IR** is validated at every entry point. Component IDs
  must be unique, the ground node must be `0`, kinds are arity-checked,
  and analyses / measurements are structured fields. Raw SPICE
  directives are an allowlist, not a free-form string.
- **Configuration** uses TOML types. The loader rejects malformed
  types and falls back to defaults rather than crashing.
- **MCP inputs** (Phase 10) are validated against JSON Schema before
  any side effect.

### Process execution

- `subprocess.run` is always called with a list `args`. No
  `shell=True` anywhere in the codebase.
- The runner launches only the configured `ltspice.executable`. Even
  if a prompt said "run `rm -rf /`", the agent has no path to a
  general shell.
- A configurable timeout is enforced on every simulation, and the
  child process is killed (`SIGTERM`, then `SIGKILL`) on timeout
  when possible.

### Filesystem boundaries

- Project output paths resolve under `workspace.projects_dir` after
  `Path.resolve()`. Anything that resolves outside is rejected.
- Template storage resolves under `workspace.templates_dir`. The
  runner never writes to user home, `/tmp`, or anywhere else by
  default; the temp directory used by the smoke simulation is
  cleaned up.
- `.gitignore` excludes generated `.raw`, `.log`, `.tmp`, and
  `.snapshots/` artifacts so they do not get committed accidentally.

### MCP surface (current integrated server)

- Tools are curated. There is **no** `run_shell`, `execute_python`,
  or generic `read_file` / `write_file` tool. All file access goes
  through project-scoped helpers.
- Resources are exposed under the `ltagent://` URI scheme and reject
  any path that traverses outside the project or template trees.

The current runtime surface contains 24 curated tools and 14 curated
resources across analog, Tiny8 digital, and live-editing/Math Core. Counts are
asserted in tests and can be inspected with `ltagent-mcp --list-tools` and
`ltagent-mcp --list-resources`.

### Promotion and review

- Template promotion is **manual** in MVP. `auto_promote` defaults
  to `false`. Even with it on, the evaluator blocks promotion of
  templates that failed to simulate or that have a low layout score.
- Official templates are snapshotted before modification
  (`.snapshots/00X_before_change/`). Rollback is "move it back".

### Output contract

- Every `--json` command returns the contract documented in
  [`SPEC.md`](SPEC.md) section 2 (`success`, `command`, `message`,
  `data`, `warnings`, `errors`). Agents must not infer success from
  prose.
- Failures include machine-readable `errors[].code` and `data`
  payloads. Doctor failures are never raised as Python exceptions
  to the caller; they are reported structurally.

## 3. Out of scope (intentionally)

- Authentication / multi-user separation. The product is local.
- Remote / HTTP / SSE MCP transport. v1 is stdio only.
- Sandboxing LTspice. The runner relies on the OS, the Wine prefix,
  and the configured `working_dir` for containment. A future phase
  may add a seccomp / firejail profile.
- Automatic destruction of official templates. There is no
  `template destroy` command and there will not be one in MVP.
