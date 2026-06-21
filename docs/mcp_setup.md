# MCP setup guide

This guide explains how to wire `ltagent-mcp` into a Model Context
Protocol client so AI coding agents (Claude Code, OpenCode, Cursor,
Cline, etc.) can drive the `ltagent` Python core through MCP.

## 1. Install

The MCP server is an **optional extra**. The `ltagent` CLI works
without it.

```bash
# from PyPI (when published)
pip install "ltspice-ai-agent[mcp]"

# from this repo (editable)
git clone https://github.com/abiyulinx/ltspice-ai-agent
cd ltspice-ai-agent
pip install -e ".[dev,mcp]"
```

After install, `ltagent-mcp --help` must work and exit 0.

If the `[mcp]` extra is missing, `ltagent-mcp` exits 1 with this
JSON payload on stderr:

```json
{
  "success": false,
  "command": "ltagent-mcp",
  "message": "MCP SDK not installed",
  "data": {
    "installHint": "pip install \"ltspice-ai-agent[mcp]\""
  },
  "errors": [
    {
      "code": "MCP_SDK_MISSING",
      "detail": "ltagent-mcp requires the optional [mcp] extra which provides the modelcontextprotocol SDK."
    }
  ]
}
```

## 2. Verify the install

```bash
ltagent-mcp --check
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

Expected output is a structured JSON contract (SPEC.md §2). The
current integrated surface contains exactly **24 curated tools** and
**14 curated URIs** across analog, Tiny8 digital, and the Phase 13
live-editing/Math Core prototype. There is **no** `run_shell`,
`execute_python`, `read_file`, or `write_file` tool — and no `.raw`
resource.

## 3. Wire the server into a client

### 3.1 Claude Code

Add to `~/.claude/mcp_servers.json` (user-wide) or
`.claude/mcp_servers.json` (project-wide):

```json
{
  "mcpServers": {
    "ltagent": {
      "command": "ltagent-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

Claude Code spawns `ltagent-mcp` over stdio. No HTTP / SSE ports are
opened. The server reads only the directories you have configured in
`config.toml` (or the defaults `projects/` and `templates/`).

### 3.2 OpenCode

Add to `opencode.json` under `mcp`:

```json
{
  "mcp": {
    "ltagent": {
      "type": "local",
      "command": ["ltagent-mcp"],
      "environment": {}
    }
  }
}
```

OpenCode launches the server with stdio transport. The working
directory of the OpenCode session determines which `config.toml`
`ltagent` reads.

### 3.3 Cursor

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ltagent": {
      "command": "ltagent-mcp"
    }
  }
}
```

### 3.4 Cline

Add to Cline's MCP settings (`.cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "ltagent": {
      "command": "ltagent-mcp",
      "args": [],
      "disabled": false
    }
  }
}
```

### 3.5 Other clients

Any MCP client that speaks stdio JSON-RPC can connect. The server
runs as:

```bash
ltagent-mcp
```

…and reads JSON-RPC envelopes on stdin, writes responses on stdout.
Logging (WARNING level by default) goes to stderr.

## 4. Tools (27 current integrated tools)

| Tool | Purpose | Notes |
|---|---|---|
| `create_project` | Build a project from an IR file or a natural-language prompt | Mirrors `ltagent create`. Accepts `--out` and `--templates-dir`. |
| `inspect_project` | Return `metadata.json` + `result.json` as a combined view | No raw waveform exposure. |
| `generate_netlist` | Render `.cir` from an IR | Optional `--out` writes inside the workspace. |
| `generate_schematic` | Render `.asc` + layout score from an IR | Optional `--out`. |
| `run_simulation` | Run LTspice on a project's `circuit.cir` via the configured runner | Returns the runner's structured `RunResult`. |
| `read_measurements` | Parse `circuit.log` + return `result.json` | `.meas` results exposed under `measurements`. |
| `check_layout` | Score the layout of an `.asc` rendered from an IR | Returns the layout checker dataclass. |
| `find_template` | Find an official template matching an IR or topology | Empty args returns the full catalogue. |
| `evaluate_template_candidate` | Phase 9 evaluator on a candidate template | Returns score + gate results. |
| `promote_template` | Promote a candidate to `official/` | `--force` overrides gates (audit-logged). |
| `live_open_project` | Open a project for bounded live editing | Project id/path remains workspace-confined. |
| `live_inspect_project` | Inspect graph, IR, results, and snapshots | Structured project view only. |
| `live_apply_edit` | Apply one validated graph operation | Snapshot and history are created by the core. |
| `live_snapshot` | Create a bounded project snapshot | No unrestricted file copying. |
| `live_restore_snapshot` | Restore a validated snapshot | Snapshot ids and project paths are validated. |
| `live_run_and_verify` | Run the project verification boundary | Uses the constrained runner and structured checks. |
| `calculate_circuit` | Run deterministic Math Core calculations | MCP contains no duplicate formula engine. |
| `explain_calculation` | Return formulas, assumptions, and values | Delegates to Math Core. |
| `plan_digital_system` | Classify a Tiny8 prompt into validated digital intent | Unsupported larger systems return roadmap guidance. |
| `create_digital_project` | Generate a deterministic Tiny8 project | Writes only under the configured projects root. |
| `assemble_tiny8_program` | Assemble supported Tiny8 source | Uses the deterministic assembler, not a shell. |
| `simulate_hdl_project` | Run the bounded HDL simulation wrapper | Reports missing Icarus/VVP structurally. |
| `synth_check_hdl_project` | Run the bounded Yosys synthesis wrapper | Reports missing Yosys structurally. |
| `inspect_digital_project` | Inspect digital manifests and reports | Curated artifact view only. |
| `wb_v2_inspect_project` | Inspect a Workbench v2 project | Returns the versioned documents without mutation. |
| `wb_v2_apply_change_set` | Apply a typed Workbench v2 change set | Revision-guarded through `DesignService`. |
| `wb_v2_propose_ai_design` | Build a typed AI proposal | Never applies the proposal automatically. |

Every tool returns the JSON contract from `SPEC.md §2`. Failures are
reported structurally with stable error codes (`PATH_TRAVERSAL`,
`IR_LOAD_FAILED`, `RESOURCE_NOT_FOUND`, `TEMPLATE_NOT_FOUND`, etc.).

## 5. Resources (16 current integrated resources)

| URI | MIME | Content |
|---|---|---|
| `ltagent://projects` | `application/json` | Collection of project directories |
| `ltagent://projects/{project_id}/metadata` | `application/json` | Project `metadata.json` |
| `ltagent://projects/{project_id}/result` | `application/json` | Project `result.json` |
| `ltagent://projects/{project_id}/circuit-ir` | `application/json` | Project `circuit.ir.json` |
| `ltagent://projects/{project_id}/netlist` | `text/plain` | Project `circuit.cir` |
| `ltagent://projects/{project_id}/log` | `text/plain` | Project `circuit.log` |
| `ltagent://templates` | `application/json` | Collection of all known templates |
| `ltagent://templates/{template_id}/metadata` | `application/json` | Template manifest.json |
| `ltagent://digital/capabilities` | `application/json` | Supported digital scope and toolchain status |
| `ltagent://digital/tiny8/spec` | `application/json` | Tiny8 ISA/system specification |
| `ltagent://digital/templates` | `application/json` | Curated digital template catalogue |
| `ltagent://projects/{project_id}/digital-manifest` | `application/json` | Digital project manifest |
| `ltagent://projects/{project_id}/rtl` | `text/plain` | Generated top-level RTL |
| `ltagent://projects/{project_id}/verification-report` | `application/json` | Digital simulation/synthesis report |
| `ltagent://workbench/v2/capabilities` | `application/json` | Workbench v2 tool and document capabilities |
| `ltagent://workbench/v2/projects/{project_id}/manifest` | `application/json` | Workbench v2 project manifest |

Path traversal in `{project_id}` or `{template_id}` is rejected by
`parse_resource_uri` with stable codes (`IDENTIFIER_INVALID`,
`RESOURCE_URI_INVALID`, etc.).

## 6. Configuration

`ltagent-mcp` reads the same `config.toml` as the CLI. Use the search
order described in `docs/SPEC.md`:

1. `./config.toml` (cwd)
2. `$XDG_CONFIG_HOME/ltagent/config.toml` (or `~/.config/ltagent/config.toml`)
3. Built-in defaults

The MCP server only writes inside the configured `workspace.projects_dir`
and `workspace.templates_dir`. The runner launches only the executable
named in `ltspice.executable` (no shell).

## 7. Troubleshooting

### Server does not start

```bash
ltagent-mcp --check   # verify the SDK is installed
```

If `--check` fails with `MCP_SDK_MISSING`, reinstall with the extra:
`pip install "ltspice-ai-agent[mcp]"`.

### Tools return `CONFIG_INVALID`

The `config.toml` in cwd is malformed. Run `ltagent config validate`
to see which field is bad.

### Tools return `PATH_TRAVERSAL`

The path you supplied is outside the configured workspace. Either pass
an absolute path inside the workspace, or set `--allow-outside-workspace`
where supported (only `create_project`).

### `run_simulation` returns `RUNNER_BUILD_FAILED` or times out

`ltspice.executable` is empty, the Wine prefix is broken, or LTspice
XVII on this host is known to time out under Wine (`docs/runner_troubleshooting.md`).
The MCP server returns the same structured `RunResult` the CLI does —
do **not** infer success from a non-empty stdout.

### Client cannot list resources

MCP clients sometimes require explicit resource-template registration.
Confirm with `ltagent-mcp --list-resources` that the 8 URIs appear.
If your client supports only static resources, request the collection
URIs (`ltagent://projects`, `ltagent://templates`) and let the agent
navigate by file name.

## 8. Security recap

- **Transport**: stdio only. No HTTP / SSE / remote in v1.
- **No `run_shell` / `execute_python` / generic `read_file`**: the
  tool surface is curated.
- **Path traversal rejected**: `parse_resource_uri` and
  `safe_resolve_under` are the single source of truth for both CLI
  and MCP.
- **No raw waveform exposure**: `assert_no_raw_path` blocks every
  read.
- **Structured failures**: errors never reach MCP clients as raw
  exceptions; they are converted to the JSON contract via
  `@_security_boundary`.

See `docs/security.md` for the full threat model.
