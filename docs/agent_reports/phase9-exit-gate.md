# Phase 9 Exit Gate — Codex MCP Integration

Date: 2026-06-20
Branch: `main`

## Scope

Surface the workbench v2 contract to MCP clients (notably Codex),
and add the Codex-side wiring commands.

## Files

- `src/ltagent/mcp_workbench_tools.py` — three new tool handlers
  (`tool_wb_v2_inspect_project`, `tool_wb_v2_apply_change_set`,
  `tool_wb_v2_propose_ai_design`) and the static capabilities
  resource body.
- `src/ltagent/mcp_server.py` — registered the new tools and
  resources; bumped `_TOOL_NAMES` to 27 and `_RESOURCE_URIS` to 16.
- `src/ltagent/codex_install.py` — `codex_install`,
  `codex_uninstall`, `codex_doctor`, plus a small purpose-built
  TOML emitter that uses dotted sub-table syntax.
- `src/ltagent/cli.py` — three new subcommands
  (`ltagent codex install|uninstall|doctor`) with
  `--config` (explicit config path) and `--dry-run` flags.
- `tests/test_phase9.py` — 24 tests.
- `tests/test_mcp_digital.py` — bumped tool/resource count
  assertions to 27/16.

## Acceptance

- `ltagent codex install` writes a valid
  `[mcp_servers.ltagent]` section into the local Codex config
  and is idempotent.
- `ltagent codex install` preserves any pre-existing
  `[mcp_servers.*]` sections; it only touches the
  `ltagent` entry.
- `ltagent codex install --dry-run` reports what would change
  without writing the file.
- `ltagent codex uninstall` removes the ltagent entry and
  deletes the config file when no other entries remain.
- `ltagent codex doctor` reports the structured state of the
  config (path, exists, server entry, issues, MCP SDK status,
  install hint).
- The MCP surface exposes 27 tools and 16 resources,
  including:
  - `wb_v2_inspect_project` — read-only project state.
  - `wb_v2_apply_change_set` — typed change-set apply via
    `DesignService`.
  - `wb_v2_propose_ai_design` — run the AI workflow and
    return a `WorkflowResult` (never auto-applies).
  - `ltagent://workbench/v2/capabilities`.
  - `ltagent://workbench/v2/projects/{project_id}/manifest`.
- All workbench v2 tool handlers reject traversal
  (`project_id` containing `/` or starting with `.`) and
  unknown projects with stable codes (`WB_PROJECT_ID_INVALID`,
  `WB_PROJECT_NOT_FOUND`).
- `wb_v2_apply_change_set` validates the change set through
  the v2 Pydantic contract and routes the apply through
  `DesignService`; failures become `WB_CHANGESET_INVALID` or
  the underlying `WorkbenchV2Error.code`.

## Numbers

- `pytest -q`: **1486 passed**, 15 skipped, 3 failed
  (3 pre-existing MCP-SDK env gap, unchanged from Phase 0 baseline).
- `ruff check` on new modules: clean.
- `mypy` on new modules: clean.
- `mypy` on `mcp_server.py`: net +1 (15 → 16) because two new
  resource handlers follow the same `mcp.resource(...)`
  untyped-decorator pattern that the existing 12 resources
  already use.

## Invariants

- API key handling unchanged: workbench v2 tools do not touch
  the provider layer directly. The AI workflow uses
  `ProviderRegistry.open(roots_root)` and falls back to no
  provider if none is configured.
- The proposal path never auto-applies. The client must call
  `wb_v2_apply_change_set` after the user accepts.
- Path-bearing tools resolve the canonical projects root via
  `ltagent.projects_root.resolve_projects_root`. They do not
  touch the legacy `cfg.workspace.projects_dir` location used
  by the analog tools.
- The TOML emitter is purpose-built and does not write
  anything outside the explicit `[mcp_servers.ltagent]`
  section.

## Next

Phase 10 — Production hardening: bundled sidecar, .deb/AppImage,
CI, smoke tests.
