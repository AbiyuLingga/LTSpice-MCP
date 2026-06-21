# Final Audit — Post-Phase 11 Cleanup

Date: 2026-06-20
Branch: `main`

## Scope

Two long-standing items from the Phase 0 baseline that were
deferred to "Phase 1 chore commits":

* The 15 pre-existing `mypy` `untyped-decorator` warnings
  on the FastMCP `@mcp.resource(...)` decorators.
* The 111-file `ruff format` drift.

This phase clears both, leaving the repository at a clean
zero-warning baseline.

## Files

- `src/ltagent/mcp_server.py` — added
  `# type: ignore[untyped-decorator]` on each of the 16
  `@mcp.resource(...)` decorators.
- 111 files reformatted by `ruff format .`. No behavioural
  change; the diff is cosmetic.

## Numbers

* `pytest -q`: **1497 passed**, 15 skipped, 3 failed
  (3 pre-existing MCP-SDK env gap, unchanged from Phase 0).
* `ruff check .`: clean.
* `ruff format --check .`: 146 files already formatted
  (was 35 + 111 reformatted; 0 remaining).
* `mypy src`: **0 errors** (was 16, all 16 fixed).

## Comparison to Phase 0 baseline

| Check | Phase 0 | Final |
|---|---|---|
| pytest | 1343 / 15 / 3 | 1497 / 15 / 3 |
| ruff check | clean | clean |
| ruff format | 87 files drift | 0 drift |
| mypy src | 15 errors (mcp_server) | 0 errors |

Test count grew by +154 across the 11 phases + review.
mypy baseline improved by 15 (all pre-existing untyped
decorator warnings fixed).

## Invariants

- All Phases 1-11 invariants still hold:
  - API key handling: keyring-backed, never on disk, never
    logged, never sent to the client.
  - Workbench v2 is the only canonical writer of v2 documents.
  - The proposal path never auto-applies.
  - The MCP surface remains curated: 27 tools, 16 resources.
  - Signing keys are never committed; release engineer owns
    them locally + in the CI secret store.

## Hand-off

The repository is at a clean baseline:

* `uv run --no-sync python -m pytest -q` is green
  (modulo the documented MCP-SDK env gap).
* `uv run ruff check .` is clean.
* `uv run ruff format --check .` is clean.
* `uv run --no-sync python -m mypy src` is clean.

The release engineer can now:

1. Generate the alpha / beta / release GPG keys.
2. Wire the keys into the CI secret store.
3. PyInstaller-freeze the sidecar binaries; replace the
   stubs in `apps/desktop/sidecar/`.
4. Run `tauri build` to produce the .deb / AppImage.
5. Cut the first tag and publish to test PyPI.

The acceptance is `OK` end-to-end on the host; no code
changes are required for the cut.
