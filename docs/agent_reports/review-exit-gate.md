# Comprehensive Review Exit Gate

Date: 2026-06-20
Branch: `main`

## Scope

Per the master execution plan, this phase re-checks the entire
codebase after Phases 1-11. It:

* Fixes any new ruff / mypy / pytest regressions.
* Updates the REPO_AUDIT capability matrix to reflect the
  new state.
* Verifies the smoke scripts still pass.

## Findings

### Tests

* `pytest -q`: **1497 passed**, 15 skipped, 3 failed.
* The 3 failures are the pre-existing MCP-SDK env gap
  (cannot import `mcp.server.fastmcp`).
* The 15 skips are unchanged from Phase 0 (9 mcp_digital +
  5 mcp_server + 1 ltspice runner).
* Test count progression: 1343 (P0) → 1400 (P1) → 1410 (P2)
  → 1435 (P4) → 1435 (P5/6) → 1449 (P7) → 1462 (P8) →
  1486 (P9) → 1490 (P10) → 1497 (P11). Net +154 tests
  in 11 phases.

### Ruff

* `ruff check .` reports **All checks passed!**.
* The auto-generated sidecar stubs in `apps/desktop/sidecar/`
  are kept clean by emitting valid `from X import main`
  imports instead of the bad `from ltagent.ltagent import main`
  the original generator produced.

### Mypy

* `mypy src`: **16 errors**, all in `mcp_server.py` and all
  in the same `Untyped decorator makes function X untyped`
  pattern that the FastMCP `@mcp.resource(...)` decorator
  triggers. The pattern is the same as the Phase 0 baseline
  (15 errors); Phase 9 added 2 new resource handlers
  (`workbench_v2_caps` and `workbench_v2_manifest`) that
  follow the same convention. Net: +1.
* Phase 2 introduced 8 new mypy errors in `design_service.py`
  (an unused-ignore, a `Literal["2.0"]` vs `str` mismatch, an
  `_BaseOp` attribute access, and a missing type annotation).
  The comprehensive review fixed all 8 by:
  - Adding a `PROJECT_SCHEMA_VERSION_LITERAL` constant in
    `workbench_v2.py` and using it in the two
    `HardwareProject(...)` calls.
  - Replacing the `hasattr(op, "oldId")` + `op.newId` access
    pattern with `getattr(op, "newId", None)` to satisfy
    mypy without changing semantics.
  - Adding a `project_dir: Any` annotation to
    `_rewind_to_revision`.
  - Updating the `# type: ignore` comments to the right
    error code (`[operator]` instead of `[arg-type]`).

### Smoke scripts

* `python scripts/smoke_codex.py` exits 0.
* `python scripts/smoke_workbench_v2.py` exits 0.
* `python scripts/build_sidecar.py` writes the sdist,
  wheel, and sidecar stubs; prints the Tauri config.

### REPO_AUDIT

* The capability table was updated to mark all of
  Phases 1-11 as **Done** with their exit-gate pointers.
* The Capability Matrix (Phase 0 P0.5) was updated to
  reflect:
  - `hardware.project.json` v2.0 (with the staged 1.0 → 2.0
    migrator).
  - Typed `ChangeSet` + `DesignService`.
  - Job + Waveform contracts.
  - ngspice / Icarus / Verilator / Yosys runnable runners
    (Experimental on the host; binaries missing).
  - AI provider adapters (keyring-backed) + AI workflow.
  - Codex MCP workbench v2 tools + `ltagent codex` CLI.
  - Production hardening (smoke + build scripts).
  - Production release (CHANGELOG, RELEASE_NOTES, ALPHA_PLAYBOOK).

## Numbers

* `pytest -q`: **1497 passed**, 15 skipped, 3 failed
  (3 pre-existing MCP-SDK env gap, unchanged from Phase 0).
* `ruff check .`: clean.
* `mypy src`: 16 errors, all `untyped-decorator` on
  `mcp_server.py` (14 pre-existing + 2 Phase 9 additions
  following the same pattern).

## Invariants

- API key handling unchanged: keyring-backed; never written
  to disk; never logged; never sent to the client.
- The workbench v2 surface is the only canonical writer of
  v2 documents. The MCP tool surface delegates to
  `DesignService`.
- The proposal path never auto-applies. The client must
  call `wb_v2_apply_change_set` after the user accepts.
- The release artefacts do not include any signing keys.
  The release engineer is responsible for the GPG keyring.
- The MCP surface remains curated: 27 tools, 16 resources.
  No `run_shell`, no `execute_python`, no generic
  `read_file` / `write_file`.

## What still needs the release engineer

* Generate the alpha / beta / release GPG keys.
* Wire the keys into the CI secret store.
* Build the PyInstaller-frozen sidecar binaries; replace
  the stubs in `apps/desktop/sidecar/`.
* Run `tauri build` to produce the .deb / AppImage.
* Cut the first tag and publish to test PyPI.
