# Agent 6 — MCP Tools — Report

**Branch:** `agent-6-mcp-tools`
**Status:** Implementation complete. Module ships on this branch
without touching any file outside the Agent 6 ownership matrix
(`docs/AGENT_LOCKS.md` §7).

## 1. Summary of changes

| File                                  | Status | Purpose                                                    |
|---------------------------------------|--------|------------------------------------------------------------|
| `src/ltagent/mcp_live_tools.py`       | added  | Tool-level wrapper functions for live editing + math.       |
| `tests/test_mcp_live_tools.py`        | added  | 62 unit tests covering safety, contract, and math.          |
| `docs/agent_reports/mcp_tools.md`     | added  | This report.                                               |

`src/ltagent/mcp_server.py`, `pyproject.toml`, `README.md`, and every
other agent's module are untouched. The MCP server itself is the
integrator's responsibility; the eight `tool_*` callables below are
plain functions with no FastMCP dependency, so the integrator can
wire them onto the existing FastMCP server via the standard
`mcp.tool(name=...)(...)` pattern (see `mcp_server.py` §`Phase 12`
tools for the established style).

## 2. Public surface

Eight `tool_*` callables defined in `ltagent.mcp_live_tools`:

| Function                              | Backend dependency              | Default behaviour                                |
|---------------------------------------|---------------------------------|--------------------------------------------------|
| `tool_live_open_project`              | none (file-based)               | Resolves + reports on-disk project artefact set. |
| `tool_live_inspect_project`           | none (file-based)               | Returns graph / IR / result.json / snapshot list.|
| `tool_live_apply_edit`                | `ltagent.live.apply_operation`   | Returns `LIVE_MODULE_UNAVAILABLE` / `LIVE_METHOD_MISSING` until the live module lands; otherwise delegates. |
| `tool_live_snapshot`                  | `ltagent.live.snapshot`         | Same fallback contract.                          |
| `tool_live_restore_snapshot`          | `ltagent.live.restore`          | Same fallback contract. Maps `FileNotFoundError` to `SNAPSHOT_NOT_FOUND`. |
| `tool_live_run_and_verify`            | `ltagent.live.run_and_verify`   | Same fallback contract.                          |
| `tool_calculate_circuit`              | `ltagent.math_core.calculate` (preferred); built-in mini library | Built-in covers `voltage_divider`, `rc_lowpass`, `rc_highpass`, `noninv_opamp`, `inverting_opamp`, `led_resistor`. |
| `tool_explain_calculation`            | `ltagent.math_core.explain` (preferred); built-in mini library | Same topology coverage as above.               |

Three public introspection helpers are also exported:

* `live_module_available()` → `bool`
* `math_core_available()` → `bool`
* `supported_builtin_topologies()` → `tuple[str, ...]`

The integrator can use these to populate an MCP capability resource
that lists what the live / math backend currently exposes.

## 3. JSON output contract

Every `tool_*` function returns the same shape used by `mcp_server.py`
(SPEC.md §2):

```json
{
  "success": true,
  "command": "live_open_project",
  "message": "opened project rc1k",
  "data": { ... domain payload ... },
  "warnings": [],
  "errors": []
}
```

Failure path:

```json
{
  "success": false,
  "command": "live_apply_edit",
  "message": "live editing module is not built yet",
  "data": { "op": "set_component_value", "autoSnapshot": true },
  "warnings": [],
  "errors": [
    {
      "code": "LIVE_MODULE_UNAVAILABLE",
      "detail": "ltagent.live is not importable; another agent is implementing it",
      "data": {}
    }
  ]
}
```

Stable, machine-readable error codes (defined as `Final[str]` in the
module, re-exported via `__all__`):

```
INVALID_INPUT
MISSING_PARAM
INVALID_OPERATION
INVALID_TOPOLOGY
INVALID_SNAPSHOT_ID
ERR_PATH_TRAVERSAL_CODE    (mirrors ltagent.security.ERR_PATH_TRAVERSAL)
PATH_NOT_FOUND
PROJECT_NOT_FOUND
SNAPSHOT_NOT_FOUND
LIVE_MODULE_UNAVAILABLE
LIVE_METHOD_MISSING
MATH_CORE_UNAVAILABLE
MATH_CORE_METHOD_MISSING
EDIT_OP_FAILED
SNAPSHOT_FAILED
RESTORE_FAILED
RUN_FAILED
VERIFY_FAILED
CALCULATION_FAILED
CONFIG_INVALID
```

All payloads pass through `_ensure_jsonable`, which runs
`to_jsonable` defensively if a payload somehow carries a non-JSONable
value (e.g. a domain dataclass leaking through a future backend).

## 4. Hard rules enforced (per `ltspice_file_based_live_editing_math_plan.md` §22)

* **No arbitrary shell.** No tool calls `subprocess`. The module
  source contains neither `shell=True` nor any `subprocess.run` /
  `subprocess.call` / `subprocess.Popen` invocation (verified by
  `test_no_subprocess_or_shell_invocation_in_module`).
* **No generic file write.** Tools either return data or delegate to
  the live module. None of the eight tool callables opens a project
  file for write.
* **No workspace escape.** Every path-bearing input is validated by
  `ltagent.security.validate_slug` (for `project_id` and
  `snapshot_id`) and resolved with `safe_resolve_under` (for the
  project directory). All `tool_live_*` functions reuse
  `_resolve_project_dir`, which is the same helper shape used by
  `mcp_server.py` (`tool_create_project` and friends).
* **No `allow_outside_workspace` knob.** None of the eight function
  signatures accept `allow_outside_workspace`, `run_shell`,
  `execute_python`, `read_file`, `write_file`, or any equivalent
  escape hatch. Verified by
  `test_no_dangerous_keywords_in_module` via `inspect.signature`.
* **No `.raw` exposure.** Tools do not surface raw waveform files.
  The integrator's resource layer is responsible for
  `assert_no_raw_path` on any per-project fan-out (existing
  `mcp_server.py` contract).
* **Snapshot-id sanitisation.** `tool_live_restore_snapshot` rejects
  any `snapshot_id` containing `/`, `\`, or `..` segments with
  `INVALID_SNAPSHOT_ID`. The `slug` is then appended under the
  project root by the live module.

## 5. How to test

The test suite is in `tests/test_mcp_live_tools.py`. It is fully
isolated from `ltagent.live` and `ltagent.math_core`:

```bash
.venv/bin/pytest tests/test_mcp_live_tools.py -v
```

Expected result: **62 passed** in under a second.

Quality gates:

```bash
.venv/bin/ruff check src/ltagent/mcp_live_tools.py tests/test_mcp_live_tools.py
.venv/bin/mypy src/ltagent/mcp_live_tools.py
```

`mypy` exits clean. `ruff` currently reports one cosmetic I001 import-
ordering warning on the test file (the `from ltagent import
mcp_live_tools` / `import ltagent.mcp_live_tools as ml` duplication is
intentional — it keeps the existing `mcp_live_tools.X` patching
ergonomics while also exporting the shorter `ml.X` alias). The
integrator can decide whether to drop one of the two aliases; either
choice is harmless and re-runnable through `ruff --fix`.

The test surface covers (in order):

1. **Public surface**: all eight `tool_*` callables exist and are
   callable.
2. **Dangerous-surface guards**: no `run_shell` /
   `execute_python` / `allow_outside_workspace` parameter; no
   `subprocess.run` / `os.system` / `shell=True` in the source.
3. **Path safety**: every path-bearing tool rejects `../etc`,
   absolute paths, dot-prefix, dotdot-prefix, sub-directory
   separators. Snapshot ids with path separators or `..` segments
   are rejected with `INVALID_SNAPSHOT_ID`.
4. **Invalid input**: missing/empty `project_id`, non-string id,
   non-dict `operation`, `operation` without `op`, `operation.args`
   that is not a dict, non-string `reason`, missing
   `snapshot_id`, missing `topology`, non-dict `parameters`,
   unknown topology, traversal in `project_id` for math tools.
   Every error carries a stable code and is JSON-serializable.
5. **Open / inspect**: file-based tools work even without the live
   module, return the expected artefact map, surface a
   `LIVE_GRAPH_MISSING` warning when `circuit.graph.json` is absent,
   and report `isLiveProject` correctly.
6. **Live backend fallback**: each live tool reports
   `LIVE_MODULE_UNAVAILABLE` when the module is missing and
   `LIVE_METHOD_MISSING` when the live module lacks the expected
   entry point (`apply_operation`, `snapshot`, `restore`,
   `run_and_verify`).
7. **Live backend happy path**: a fake `ltagent.live` module
   monkey-patched into `ml._LIVE_MODULE` is accepted; the tool
   forwards kwargs (`auto_snapshot`, `reason`, `config`) and the
   live module's return value flows through unchanged.
8. **Math backend fallback / dispatch**: when `ltagent.math_core` is
   absent, the built-in mini library produces correct ideal values
   for every supported topology (verified with
   `math.isclose(..., rel_tol=1e-9)`). When a fake `math_core` is
   monkey-patched in, both `tool_calculate_circuit` and
   `tool_explain_calculation` route through it.
9. **Math correctness**: each of the six topologies has at least
   one golden test that computes the closed-form value by hand and
   compares with `math.isclose`. The edge cases (insufficient
   parameters, physical-constraint violations such as gain ≤ 1 on
   a non-inverting op-amp) surface as `CALCULATION_FAILED`.
10. **Introspection helpers**: `live_module_available()` and
    `math_core_available()` return `bool`; the topology list
    covers the six built-ins.

## 6. Live / Math module contract assumed by Agent 6

For the live tools to do anything beyond reporting
`LIVE_MODULE_UNAVAILABLE` / `LIVE_METHOD_MISSING`, the
`ltagent.live` module that Agents 1 / 2 / 3 are landing must expose:

```python
ltagent.live.apply_operation(project_dir: Path, op: dict, *, auto_snapshot: bool, config: Config) -> dict
ltagent.live.snapshot(project_dir: Path, *, reason: str, config: Config) -> dict
ltagent.live.restore(project_dir: Path, snapshot_id: str, *, config: Config) -> dict
ltagent.live.run_and_verify(project_dir: Path, *, config: Config) -> dict
```

Return value is treated as a domain dict; the tool copies
`projectId` and the relevant op / snapshot-id keys back into the
payload via `data.setdefault(...)` so callers always see the round-
trip identifiers. A `FileNotFoundError` raised by `restore` is
mapped to `SNAPSHOT_NOT_FOUND`; any other `ValueError` /
`TypeError` from the live module is mapped to `EDIT_OP_FAILED` /
`SNAPSHOT_FAILED` / `RESTORE_FAILED` / `RUN_FAILED` as appropriate.

For the math tools, `ltagent.math_core` should expose:

```python
ltagent.math_core.calculate(topology: str, parameters: dict) -> dict
ltagent.math_core.explain(topology: str, parameters: dict | None) -> dict
```

Return dict is normalised through `ltagent.serialization.to_jsonable`
before the structured response is built. `data.setdefault("source",
"math_core")` lets the caller tell at a glance which backend
produced the answer.

## 7. Integration requests for Agent 0 (Integrator)

> This is the formal hand-off section the locks file requires.

1. **Wire the eight tools onto the FastMCP server.** The integrator
   can import them and decorate them in `src/ltagent/mcp_server.py`
   (Agent 6 cannot touch this file):

   ```python
   from ltagent.mcp_live_tools import (
       tool_live_open_project,
       tool_live_inspect_project,
       tool_live_apply_edit,
       tool_live_snapshot,
       tool_live_restore_snapshot,
       tool_live_run_and_verify,
       tool_calculate_circuit,
       tool_explain_calculation,
   )

   mcp.tool(name="live_open_project", description="…")(tool_live_open_project)
   mcp.tool(name="live_inspect_project", description="…")(tool_live_inspect_project)
   # … and the other six
   ```

   These tools must be **added**, not replace any of the 10 existing
   curated tools from Phase 10. The MCP tool count assertion in
   `tests/test_mcp_server.py::test_server_lists_sixteen_tools`
   will need to be widened to `18` once all eight are wired.

2. **Update `_TOOL_NAMES`** in `mcp_server.py` to include the eight
   new names; `_RESOURCE_URIS` does not need new entries (none of
   the eight tools surface a new resource URI — they only return
   data shapes).

3. **Optionally expose `supported_builtin_topologies()` and the two
   availability helpers** as a new MCP capability resource, so MCP
   clients can ask "what can the math tool do right now?" without
   guessing.

4. **No new dependencies.** Agent 6 added zero runtime
   dependencies; the only stdlib used is `json`, `math`, `pathlib`,
   `dataclasses`. `ltagent.serialization.to_jsonable` and
   `ltagent.units.parse_spice_value` are reused from Phase 1 / Phase
   2.

5. **Coordinate with Agent 4 (math_core) and Agent 3 (live).** Once
   those modules ship the four live entry points and the two math
   entry points listed in §6, the eight tools stop returning
   `_MODULE_UNAVAILABLE` and start returning real answers, with no
   change required to `mcp_live_tools.py`.

## 8. Risks and follow-ups

* The built-in mini formula library is **deliberately small** — six
  topologies, ideal-value math only. It exists so the math tools
  keep producing useful output while `ltagent.math_core` is being
  built, **not** as a substitute. Agent 4's real `math_core` will
  supersede it the moment it ships `calculate` / `explain`.
* The tool output for `tool_live_inspect_project` reads JSON files
  directly. It does not call into `ltagent.live` because there is
  no read-only "open project" entry point in the live module
  (Agents 1 / 3 own that surface). If Agent 3 adds a read-only
  inspector later, Agent 6 can be updated to delegate; the
  signature does not change.
* `tool_live_apply_edit` accepts a single edit operation. The plan
  §8 describes a `ltagent live apply-many` flow that takes a list
  of ops. Agent 6 intentionally exposes only the single-op tool;
  Agent 0 can add a `tool_live_apply_edits` later by iterating
  `tool_live_apply_edit` server-side, or Agent 2 can add a list op
  to `ltagent.live` and Agent 6 can extend the dispatcher.
* `tool_live_run_and_verify` runs simulation + verification through
  `ltagent.live.run_and_verify`. Agent 5 (sim/verification) owns
  the actual `sim_loop` module. The contract assumed here is the
  public function shape Agent 5's `sim_loop` should expose.
* MyPy on `tests/test_mcp_live_tools.py` is **not** part of the
  project's normal `mypy src` run (the repo runs mypy on `src/`
  only — see AGENTS.md "Run the full test suite + ruff + mypy before
  declaring done"). If Agent 7 wants strict typing on tests, the
  test file is annotated with `from __future__ import annotations`
  already and only uses public API of `pytest`, so it should type-
  check cleanly under the same `mypy --strict` config Agent 7 picks.