# Phase 0 Baseline Audit

**Date:** 2026-06-20
**Branch:** `main` (11 commits ahead of `origin/main`)
**Auditor:** single AI editing owner
**Active roadmap:** `docs/SINGLE_AGENT_EXECUTION_PLAN.md`
**This report:** records the dirty tree, toolchain evidence, and capability
classification that P0.1–P0.5 require before Phase 1 (project schema v2) may
begin.

## 1. Dirty Tree Inventory (P0.1)

`git status --short --branch` at start of Phase 0:

```text
## main...origin/main [ahead 11]
 M apps/desktop/src/App.test.tsx
 M apps/desktop/src/App.tsx
 M apps/desktop/src/components/WorkspaceSurface.tsx
 M apps/desktop/src/styles.css
?? apps/desktop/src/components/SchematicSymbol.tsx
?? apps/desktop/src-tauri/gen/
?? apps/desktop/src-tauri/icons/
?? uv.lock
```

| Item | Class | Decision | Reason |
|---|---|---|---|
| `apps/desktop/src/components/SchematicSymbol.tsx` | New source | **commit** | First new SVG symbol library for the workbench; required by the dirty `WorkspaceSurface.tsx`. |
| `apps/desktop/src/App.tsx` | Modified source | **commit** | Adds the `moveComponent` handler that wires the drag UX into `design.applyChanges`. |
| `apps/desktop/src/components/WorkspaceSurface.tsx` | Modified source | **commit** | Renders SVG symbols, adds pointer/keyboard drag with grid snap, exposes the move callback. |
| `apps/desktop/src/App.test.tsx` | Modified source | **commit** | Covers symbol rendering, drag persistence, and no-replace-on-symbol-click regressions. |
| `apps/desktop/src/styles.css` | Modified source | **commit** | New visual rules for `.schematic-symbol`, `.schematic-node.is-dragging`, and SVG strokes. |
| `apps/desktop/src-tauri/gen/` | Tauri-generated | **gitignore** | `tauri build` and the Tauri CLI regenerate this from `capabilities/`. Already contains `capabilities.json`, `acl-manifests.json`, `desktop-schema.json`, `linux-schema.json`. |
| `apps/desktop/src-tauri/icons/` | Real asset bundle | **commit** | 18 PNG/SVG/ICO/ICNS files required by `tauri.conf.json` bundle config; the Tauri template expects them in tree. |
| `uv.lock` | Python lockfile | **commit** | Reproducible installs for the application; aligns with the new production phase. |

Generated `dist-baseline/` and `target/` artefacts are local-only and must
remain ignored (`.gitignore` already covers them).

## 2. Toolchain Evidence (P0.2)

All commands run from `/home/abiyulinx/computing/ltspice-ai-agent` on
2026-06-20 against the dirty tree above.

### Python

| Command | Result |
|---|---|
| `uv run pytest` | **1343 passed, 15 skipped, 3 failed in 25.85s** |
| `uv run ruff check .` | **All checks passed** |
| `uv run python -m mypy src` | **15 errors in `src/ltagent/mcp_server.py`** (1× `unused-ignore`, 14× `untyped-decorator` from FastMCP resource decorators) |
| `uv run python -m build --sdist --wheel --outdir dist-baseline` | **Success** (`ltspice_ai_agent-0.0.1-py3-none-any.whl` + sdist) |
| `uv run ruff format --check .` | 87 files would be reformatted (30 clean) — pre-existing, not introduced by the dirty tree |

Pytest details:

- 3 failures are all `tests/test_mcp_server.py` `*_subprocess` tests that
  shell out to `ltagent-mcp`. They return `MCP_SDK_MISSING` because the
  optional `[mcp]` extra is not installed in the venv. They are *expected*
  skips in the absence of the SDK, not regressions.
- 15 skips are split: 14 are SDK-missing (mcp.server.fastmcp import) and
  1 is `ltspice.executable not configured` (`tests/test_runner.py:676`).
- The `dist-baseline/` wheel was rebuilt; it is left on disk for
  Phase-1 traceability.

mypy details: every error originates in `mcp_server.py`. The
`untyped-decorator` errors come from FastMCP resource registration
(`@server.resource(...)`); the `unused-ignore` is on a `type: ignore`
that is no longer needed because the underlying function is now typed.
The current `AGENTS.md` does not claim `mypy` clean for `mcp_server.py`;
this is a known gap that Phase 7 (AI) and Phase 9 (Codex MCP) will
exercise.

### Frontend (apps/desktop)

| Command | Result |
|---|---|
| `npm test` (vitest) | **7/7 passed in 2.89s** — covers project creation, place resistor, symbol render, drag persistence, no-clobber on symbol click. |
| `npx tsc --noEmit` | **clean** |
| `npm run build` | (not re-run in Phase 0; vitest + tsc suffice for evidence.) |

### Rust (apps/desktop/src-tauri)

| Command | Result |
|---|---|
| `cargo check` | **Finished `dev` profile in 1.43s** (target/ already present from a previous build). |
| `cargo test` | (deferred — no Rust test files committed today; Phase 10 will add them.) |

System prerequisites already present on this host (verified):

- `libgtk-3-dev` 3.24.41, `libwebkit2gtk-4.1-dev` 2.52.3, `librsvg2-dev`.
- `cargo` 1.95.0 (2026-03-21).

### External EDA tools (used in golden projects)

None of the following are on `$PATH` today; the runner stack remains
honestly classified as "host missing" rather than "broken":

- `ngspice`, `iverilog`, `vvp`, `verilator`, `yosys`, `sby`.
- `wine` is at `/opt/wine-stable/bin/wine` (off the default PATH; the
  Python `runner.py` must keep its auto-detect fallback).
- LTspice XVII lives at
  `/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe`
  (note the space — runner must quote it).

## 3. Local Dev Recap

The standard workbench flow continues to work:

```bash
uv run --no-sync python -m pytest -q tests/test_workbench.py tests/test_engine_server.py
cd apps/desktop && npm install && npm run dev      # browser preview
# Native Tauri dev needs ltagent-engine on PATH (see docs/workbench_desktop.md).
```

## 4. Open Risks and Skipped Evidence

- **MCP SDK not in venv**: 3 test failures and 14 skips trace to a
  missing optional dep. Install with `uv sync --extra mcp` to clear.
  Phase 9 (Codex MCP) will require this extra as part of its exit gate.
- **`mypy` errors in `mcp_server.py`**: 15 errors from FastMCP
  decorators. Phase 7/9 work will need a type stub or a focused fix.
- **Rust system deps**: GTK/WebKit are present today; a CI runner will
  still need `apt install libgtk-3-dev libwebkit2gtk-4.1-dev librsvg2-dev`
  in its bootstrap.
- **No real EDA toolchain on host**: ngspice/iverilog/verilator/yosys
  must be installed before Phase 4 (Jobs) and Phase 5/6 acceptance
  (real-tool evidence) can be claimed.
- **No Rust tests committed**: the Rust crate exists but has no
  `#[test]` items. Phase 10 will add the bundled-sidecar tests.

## 5. Phase 0 Status

- P0.1 dirty tree audit — **done (this document, §1)**
- P0.2 baseline runs — **done (this document, §2)**
- P0.3 reviewable commits — pending (next task)
- P0.4 spec / ADR / architecture refresh — pending
- P0.5 capability matrix — pending
- P0.6 exit-gate report — pending
