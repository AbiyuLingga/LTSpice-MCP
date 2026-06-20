# Changelog

All notable changes to the Hardware Design Workbench will be
documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
* Workbench v2 contract (`hardware.project.json` + the
  `analog`, `schematic`, `digital`, `system`, `requirements`
  documents) with a typed `DesignService` that is the only
  writer of v2 projects.
* Staged 1.0 → 2.0 migration via `ltagent.workbench_migration`.
* Typed `ChangeSet` with 16 operation kinds (`add_component`,
  `remove_component`, `set_component_value`, `rename_component`,
  `connect_pin`, `disconnect_pin`, `rename_net`,
  `add_directive`, `add_measurement`, `place_node`,
  `move_node`, `rotate_node`, `set_wire_route`,
  `set_net_label`, `set_grid_size`, `replace_document`).
* Schematic editor split: Shell / Explorer / Canvas / Inspector
  / AI panel / Bottom panel with a TypeScript symbol + pin
  registry and an autosave debouncer.
* `ltagent.jobs` with `JobManifest`, `RunManifest`,
  `ResultBundle`, `WaveformBundle`, `WaveformTrace`, and
  `WaveformChunk` contracts.
* Analog workbench: `CircuitGraph` → netlist → ngspice runner,
  LTspice `.asc` parser, structured `skipped` / `failed` /
  `timed_out` outcomes.
* Generic digital workbench: `DigitalDesignIR` (kinds for
  combinational, sequential, FSM), Verilog-2001 generator,
  Icarus / Verilator / Yosys runner.
* AI provider infrastructure: `ProviderProfile` /
  `ProviderAdapter` / `ProviderRegistry`, system keyring
  (with in-memory fallback), `AIContextManifest`, secret /
  injection detection.
* `AIWorkflow` orchestrating requirement parsing, capability
  classification (English + Indonesian), manifest build,
  provider call, repair loop, and accept-as-ChangeSet apply.
* Codex MCP integration: 3 new workbench v2 tools
  (`wb_v2_inspect_project`, `wb_v2_apply_change_set`,
  `wb_v2_propose_ai_design`) and 2 new resources
  (`ltagent://workbench/v2/capabilities`,
  `ltagent://workbench/v2/projects/{project_id}/manifest`).
* `ltagent codex install | uninstall | doctor` subcommands
  for wiring the ltagent-mcp entry into the local Codex
  config.
* Production hardening scripts:
  `scripts/smoke_codex.py`, `scripts/smoke_workbench_v2.py`,
  `scripts/build_sidecar.py`.
* CI smoke step on the Python 3.12 matrix entry.

### Changed
* The MCP server now exposes 27 curated tools and 16 curated
  resources (was 24 / 14).
* `pyproject.toml` adds `httpx`, `keyring`, and
  `typing-extensions` to the runtime dependencies for the AI
  provider layer.

### Security
* API keys are stored in the system keyring only. They are
  never written to the project tree, never logged, and never
  sent to the client.
* `AIContextManifest.detect_prompt_injection` rejects context
  that contains known injection patterns before the provider
  is called.
* All path-bearing tools resolve their targets through
  `ltagent.projects_root.resolve_projects_root`; traversal
  is rejected with stable codes.

## [0.0.1] - 2026-06-20

### Added
* Initial Phase 0-12 baseline (analog + Tiny8 digital
  generation, LTspice runner, MCP server v1, evaluator,
  promoter, official template library).
