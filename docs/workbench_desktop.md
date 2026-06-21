# Hardware Design Workbench Foundation

The first desktop slice introduces a local project workspace without changing
the established CLI or MCP contracts.

## Included

- Versioned `hardware.project.json` projects with separate requirement,
  analog, schematic, digital, and system documents.
- Revision-guarded typed change sets with transaction recovery.
- `ltagent-engine`, a JSON-RPC/NDJSON sidecar exposing allowlisted project and
  document methods plus bounded Tiny8 emulation.
- A Tauri/React workbench shell with local project creation, Basic/Advanced
  mode, design surfaces, inspector, job panel, and 8x16 LED preview driven by
  a verified Tiny8 demo ROM.

## Local Development

```bash
uv run --no-sync python -m pytest -q tests/test_workbench.py tests/test_engine_server.py
cd apps/desktop
npm install
npm run dev
```

The browser dev server is useful for UI development. To launch the native
window in development, make the project virtual environment's
`ltagent-engine` available on `PATH`, then run `npm run tauri dev`.
Production bundles use the frozen sidecar copied next to the desktop binary.

On this Linux host, `cargo check` currently stops before compiling application
code because the system lacks GTK/Pango development headers. Install the
standard Tauri Linux prerequisites through the host package manager before
running the native desktop build. This is a host dependency, not a workaround
inside the application source.

## Boundary

No UI code may execute shell commands, select an executable, or read arbitrary
files. The only bridge is `engine_request`; the Python engine validates the
JSON-RPC method and confines project paths under its configured projects root.
