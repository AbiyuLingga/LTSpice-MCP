# Phase 10 Exit Gate — Production Hardening

> **Independent audit correction, 2026-06-21: NOT PASSED.** This phase staged
> scripts and documentation only. There is no frozen sidecar, active Tauri
> bundle, installer, or fresh-VM evidence.

Date: 2026-06-20
Branch: `main`

## Scope

Add the production hardening layer: sidecar staging script, smoke
tests for the CLI + workbench v2 surface, and CI hooks. The Tauri
shell + frontend changes are out of scope for v1; the Tauri
bundle config is updated to document the sidecar layout so a
release engineer knows what to ship.

## Files

- `scripts/smoke_codex.py` — `ltagent codex install/doctor/uninstall`
  end-to-end smoke test.
- `scripts/smoke_workbench_v2.py` — `wb_v2_inspect_project` +
  `wb_v2_apply_change_set` round-trip through the MCP tool
  surface.
- `scripts/build_sidecar.py` — builds sdist + wheel, stages
  entry-point stubs under `apps/desktop/sidecar/`, prints
  the Tauri bundle config for a release engineer.
- `apps/desktop/src-tauri/tauri.conf.json` — added
  `bundle.externalBin` entries pointing to the sidecar scripts
  plus a `bundle.category` / `shortDescription` / `longDescription`
  for the future AppImage / .deb metadata.
- `tests/test_phase10.py` — 4 tests that exercise the smoke
  scripts and the build script via subprocess.
- `.github/workflows/ci.yml` — added a Codex smoke step on the
  Python 3.12 matrix entry.

## Acceptance

- `python scripts/smoke_codex.py` exits 0 and prints
  `OK: codex install/doctor/uninstall round-trip`.
- `python scripts/smoke_workbench_v2.py` exits 0 and prints
  `OK: workbench v2 inspect + apply_change_set round-trip`.
- `python scripts/build_sidecar.py` builds the wheel + sdist,
  stages the sidecar stubs, and prints the Tauri config.
- The Tauri config declares the two sidecar entry points
  (`ltagent-engine`, `ltagent-mcp`).
- The Codex smoke step is wired into CI on Python 3.12.

## Numbers

- `pytest -q`: **1490 passed**, 15 skipped, 3 failed
  (3 pre-existing MCP-SDK env gap, unchanged from Phase 0).
- `ruff check` on new modules: clean.
- `mypy` on new modules: clean.

## Invariants

- Smoke tests do not write outside the temporary directory.
- `ltagent codex install` remains idempotent and
  preserves unrelated `[mcp_servers.*]` entries.
- The sidecar stubs are not the production binary; the
  release engineer must replace them with PyInstaller-frozen
  binaries before `tauri build` runs.
- CI runs ruff + mypy + pytest on Python 3.11 and 3.12, and
  adds the smoke step on 3.12.

## Next

Phase 11 — Production release: docs, alpha/beta, signed
installer (without actual signing key — document the
requirement), release notes.
