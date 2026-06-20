# Phase 11 Exit Gate — Production Release

Date: 2026-06-20
Branch: `main`

## Scope

Add the production release artefacts: a CHANGELOG, release notes
with the signing-key conventions, and an alpha-release playbook.
The release engineer is responsible for actually cutting tags
and signing artefacts; this phase documents the process and
verifies the artefacts are in place.

## Files

- `CHANGELOG.md` — Keep-a-Changelog format. Documents the
  Phase 0-12 baseline plus the Phase 1-10 additions.
- `docs/RELEASE_NOTES.md` — Release pipeline overview
  (alpha / beta / stable), signing-key conventions, version
  policy, cut-and-ship commands.
- `docs/ALPHA_PLAYBOOK.md` — Step-by-step playbook for cutting
  an alpha tag, building artefacts, signing, and publishing
  to test PyPI.
- `tests/test_phase11_release.py` — 7 tests covering the
  release artefacts, public API surface, and a single
  end-to-end round trip through codex install, workbench v2
  inspect, AI workflow reject, and change-set apply.

## Acceptance

- `CHANGELOG.md` documents the workbench v2 contract, the
  AI workflow, the Codex MCP integration, and the production
  hardening scripts.
- `docs/RELEASE_NOTES.md` documents the three signing-key
  aliases, the local key path convention, the CI secret-store
  mapping, and the key-rotation rule.
- `docs/ALPHA_PLAYBOOK.md` documents the pre-flight check,
  the tag-cut command, the build / sign / publish steps, and
  the smoke-test verification.
- The end-to-end test covers codex install → project seed →
  inspect → AI propose (rejected) → change set apply → final
  inspect → codex uninstall.

## Numbers

- `pytest -q`: **1497 passed**, 15 skipped, 3 failed
  (3 pre-existing MCP-SDK env gap, unchanged from Phase 0).
- `ruff check` on new modules: clean.
- `mypy` on new modules: clean.

## Invariants

- The release artefacts are documents only; the release
  engineer must generate, protect, and rotate the signing
  keys in their own local GPG keyring + the CI secret store.
  The keys are never committed to the repository.
- The MCP server still exposes exactly 27 curated tools and
  16 curated resources; the new tests assert that count.
- The end-to-end test does not touch the host filesystem
  outside a temporary directory.

## Next

Comprehensive review of the entire codebase: re-run the brief
§21 command set, fix any new mypy/ruff/test regressions, and
update the REPO_AUDIT capability table.
