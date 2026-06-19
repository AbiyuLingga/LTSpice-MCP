# ADR 0005 - Local Desktop Workbench Sidecar

- Status: Accepted
- Date: 2026-06-19

## Context

The existing Python package provides validated circuit and digital services
through CLI and MCP, but has no desktop project workspace. A modern editor
must not grant its WebView arbitrary filesystem or shell access.

## Decision

- The desktop application lives in `apps/desktop` and uses Tauri 2 with a
  React/TypeScript frontend.
- The frontend may invoke only the Rust `engine_request` command. Rust keeps
  one `ltagent-engine` stdio process and forwards JSON-RPC requests serially.
- The engine project root is the application data directory. The renderer
  does not choose executables, shell arguments, or filesystem paths outside
  that root.
- `hardware.project.json` is the versioned project manifest. Design documents
  are separate JSON files, and `ChangeSet` writes use a journal plus revision
  check to recover from interrupted multi-file updates.
- The first engine capability set is project/document management plus bounded
  pure-Python Tiny8 emulation. External simulation, AI, waveform streaming,
  and bundled sidecar binaries are additive phases.

## Consequences

- Existing CLI/MCP behavior remains unchanged; both can adopt the workbench
  service layer later without a breaking contract migration.
- Desktop development on Linux requires Tauri's GTK/WebKit native headers.
  The source tree can still run its React/Vitest build without them.
- `ltagent-engine` must be on `PATH` for a development Tauri launch. Release
  packaging will replace that requirement with a target-specific bundled
  sidecar and a license audit.
