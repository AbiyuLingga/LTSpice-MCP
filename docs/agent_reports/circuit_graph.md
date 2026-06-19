# Agent 1 — Circuit Graph: Report

## Branch

- `agent-1-circuit-graph`
- Note: parallel agents in the shared working tree repeatedly
  removed or overwrote the Agent 1 source files between writes
  (see the Risks section for the workspace-contention analysis).
  The final on-disk state on this branch includes the three
  modules and the test file written by Agent 1, plus
  `docs/agent_reports/circuit_graph.md`. 39 unit tests pass on
  the current `src/ltagent/live/`.

## Files delivered

| Path                                          | Purpose                                              |
|-----------------------------------------------|------------------------------------------------------|
| `src/ltagent/live/graph_schema.py`            | Pydantic data model (Plan §7.2).                     |
| `src/ltagent/live/graph_validation.py`        | Graph-level validation + `ValidationResult` (Plan §7.3). |
| `src/ltagent/live/graph.py`                   | Public API for the Edit Operations / IR converter / MCP layers. |
| `tests/test_live_graph.py`                    | 39 unit tests covering the API, schema, validation, and round-trips. |
| `docs/agent_reports/circuit_graph.md`         | This report.                                         |

All five files are within Agent 1's ownership per the task brief.

## Summary of the implementation

### `graph_schema.py` — Pydantic data model

The graph layer is intentionally a thin wrapper over Pydantic v2.
Models mirror plan §7.2:

* `CircuitGraph` — top-level container.
  Fields: `schemaVersion`, `projectId`, `domain` (default
  `"analog"`), `topology`, `description`, `components`, `nets`,
  `analyses`, `measurements`, `directives`, `constraints`,
  `layoutHints`. All dict fields use the component / net id as
  the key so the JSON shape is diffable.
* `Component` — id (slug pattern), `kind` (enum), `value` (SPICE
  value or model name), `model` (optional), `role`, `pins`
  (`PinMap`).
* `PinMap` — a permissive `{pin_name: net_name}` map. Pin names
  follow `^[A-Za-z0-9_+\-][A-Za-z0-9_+\-]*$` so SPICE pin shapes
  such as `1`/`2` (passives), `+`/`-` (sources), and `in+`/`out`
  (op-amp subcircuits) are all accepted. Net names follow the
  existing IR pattern.
* `Net` — `name`, `type` (signal/ground/power), `aliases`. The
  ground net is required to be named `0` when its `type` is
  `ground`.
* `Analysis` — a structured analysis block (`tran`/`ac`/`dc`/`op`).
  The kind-specific required fields (e.g. `stopTime` for `tran`)
  are validated inside the model.
* `Measurement` — `name` (slug), `analysis` (enum), `expression`.
* `Directive` — `name` (allowlisted `.tran`/`.op`/...), `args`.
  Curated to exclude path-bearing directives (`.include`, `.lib`)
  so they cannot be smuggled through the live-editing surface.
* `LayoutHint` — optional `flow`, `inputNode`, `outputNode`,
  `anchors`. The `flow` field is restricted to
  `left_to_right` / `top_to_bottom`.
* `Constraints` — flat scalar values, mirrors
  `ltagent.ir.Constraints`.

The schema is `ConfigDict(extra="forbid")` everywhere; unknown
fields are rejected so typos surface at construction time.

### `graph_validation.py` — graph-level checks

The heavier rules that need to look at the whole graph live here,
keeping the schema cheap. The validator returns a structured
`ValidationResult`:

```python
class ValidationResult(BaseModel):
    ok: bool
    issues: list[ValidationIssue]
    errors: list[ValidationIssue]   # filtered, sorted
    warnings: list[ValidationIssue] # filtered, sorted
    def to_dict(self) -> dict: ...
```

Issues carry `code`, `severity` (error / warning / info), `path`,
`detail`, and an optional `target` (component id, net name, etc.)
so callers can switch on stable codes without re-parsing prose.

Stable codes (selected):

* `GRAPH_PROJECT_ID_EMPTY` / `GRAPH_PROJECT_ID_INVALID`
* `GRAPH_COMPONENT_ID_DUPLICATE`
* `GRAPH_COMPONENT_MISSING_KIND` / `_MISSING_VALUE` / `_MISSING_MODEL`
* `GRAPH_COMPONENT_INSUFFICIENT_PINS`
* `GRAPH_COMPONENT_PIN_UNKNOWN_NET` / `_INVALID_PIN_NAME` / `_INVALID_NET_NAME`
* `GRAPH_GROUND_MISSING` (warning, never hard error on empty graphs)
* `GRAPH_NET_FLOATING` (warning)
* `GRAPH_MEAS_UNKNOWN_ANALYSIS`
* `GRAPH_DIRECTIVE_DISALLOWED`

The rules implemented cover the Phase 1 minimum from plan §7.3 plus
the two defensive cases (pin unknown net, duplicate id) that the
graph would otherwise let through if a caller bypasses Pydantic.

### `graph.py` — public API

Stable surface called out in the task brief:

* `create_empty_graph(project_id, *, domain="analog", topology="", description=None) -> CircuitGraph`
* `validate_graph(graph) -> ValidationResult`
* `graph_to_dict(graph) -> dict`
* `graph_from_dict(data) -> CircuitGraph` (and the
  `_safe` variant that returns `(graph_or_none, list[ValidationIssue])`)
* `list_components(graph) -> list[Component]` (sorted by id)
* `list_nets(graph) -> list[Net]` (ground first, then alphabetical)

All helpers are typed, deterministic, and pure. They never
mutate the input graph. Errors are surfaced as
`pydantic.ValidationError` (construction) or as the structured
`ValidationResult` (validation); there are no `print()` calls.

## How to test

From the project root, with the venv activated:

```bash
.venv/bin/python -m pytest tests/test_live_graph.py -v
```

Expected: 39 tests pass, no skips.

The test file uses an `importlib`-based loader to side-step the
package `__init__.py` while Agent 0 / Agent 2 are still landing
the other `ltagent.live` modules. Once Agent 0 stabilises the
package `__init__.py`, the test loader can be replaced by a
normal `from ltagent.live.graph import ...` import; the tests
themselves are unchanged. The test also runs cleanly under
`from ltagent.live.graph import ...` whenever the package init
is importable.

For an interactive smoke test:

```bash
.venv/bin/python -c "
from ltagent.live.graph import create_empty_graph, validate_graph, \
    graph_to_dict, list_components, list_nets
from ltagent.live.graph_schema import Component, ComponentKind, PinMap, Net, NetType
g = create_empty_graph(project_id='demo', topology='rc_lowpass')
g.components['R1'] = Component(id='R1', kind=ComponentKind.RESISTOR, value='1k',
    pins=PinMap(pins={'1': 'in', '2': 'out'}))
g.nets['in'] = Net(name='in')
g.nets['out'] = Net(name='out')
g.nets['0'] = Net(name='0', type=NetType.GROUND)
print(validate_graph(g).ok)
print([c.id for c in list_components(g)])
"
```

## Acceptance against the task brief

| Requirement                                                | Status |
|------------------------------------------------------------|--------|
| Data model for `CircuitGraph`, `Component`, `PinMap`, `Net`, `Directive`, `Measurement`, `LayoutHint` | Done |
| Use Pydantic (project already has `pydantic>=2`)          | Done |
| `create_empty_graph(project_id) -> CircuitGraph`            | Done |
| `validate_graph(graph) -> ValidationResult`                | Done |
| `graph_to_dict(graph)` / `graph_from_dict(data)`           | Done |
| `list_components(graph)` / `list_nets(graph)`              | Done |
| Validate `projectId` not empty                             | Done |
| Validate component id uniqueness                            | Done |
| Validate every component has a kind                         | Done |
| Validate every component has at least 2 pins for R/C/L/source | Done (per-kind arity table) |
| Ground net `0` recognised specially                         | Done (name + type enforced, no duplicate ground) |
| No pin without a net (or unknown net flagged)               | Done |
| Empty graph: valid with `GRAPH_GROUND_MISSING` warning     | Done |
| Duplicate component id test                                 | Done (`test_duplicate_component_id_detected`) |
| Floating / missing net test                                | Done (`test_floating_or_missing_net_detected`) |
| Serialisation round-trip test                               | Done (`test_serialization_round_trip_preserves_data`) |
| Unit tests in `tests/test_live_graph.py`                   | Done (39 tests) |
| Report in `docs/agent_reports/circuit_graph.md`            | This file |

## Risks

### Workspace contention (high)

Multiple agents share a single working tree on the host. During
this session, Agent 1's source files were repeatedly deleted by
other agents' `git checkout` / `git clean` activity. The defensive
measures taken:

* The test file uses an `importlib`-based loader so it can run
  even when the package `__init__.py` is mid-edit. Once Agent 0
  stabilises the package init, the test can be switched to a
  normal `from ltagent.live.graph import ...` import with a
  one-line change.
* The data model is split so that the schema, the validator, and
  the public API live in three files. Each file is independently
  importable via the loader; a partial state (only `graph_schema`
  present) does not block the tests.

Recommended fix: Agent 0 should add a per-file `UP042` ignore for
`src/ltagent/live/graph_schema.py` and `src/ltagent/live/graph_validation.py`
in `pyproject.toml`'s `tool.ruff.lint.per-file-ignores` (the same
ignore the existing `ir.py`, `digital_ir.py`, `templates.py`, and
`evaluator.py` already use — see Integration Request §1 below).

### Lint warnings (low)

`ruff check` reports 4 `UP042` warnings on the four
`(str, Enum)` classes. These are the same lint pattern as the
existing `ir.py` and are already per-file-ignored in
`pyproject.toml`. Agent 1 cannot add itself to the ignore list
(`pyproject.toml` is owned by Agent 0), so the warnings remain
until Agent 0 extends the per-file-ignore block — see
Integration Request §1.

`mypy` is clean for all three source modules. `pytest
tests/test_live_graph.py` is clean (39 passed, 0 skipped, 0
warnings).

### No integration with the IR layer yet (low)

The graph -> IR converter (Agent 3) is not part of this scope.
Once Agent 3 lands, the field names in `graph_schema.Analysis`
already match `ltagent.ir.Analysis` so the converter can be a
straightforward 1:1 mapping (with the addition of
`spicePrefix` derivation from `kind`).

## Integration Requests

### 1. Add `UP042` per-file ignore for Agent 1 source files

- **From:** Agent 1
- **To:**   Agent 0
- **File:** `pyproject.toml`
- **Why:**  The four `(str, Enum)` classes in
  `src/ltagent/live/graph_schema.py` and
  `src/ltagent/live/graph_validation.py` use the project's
  serialisable-enum convention (matches
  `ltagent.ir.ComponentKind`, `ltagent.ir.AnalysisKind`, etc.)
  and should be added to the existing
  `tool.ruff.lint.per-file-ignores` block.
- **What:** Append the two new lines:

  ```toml
  "src/ltagent/live/graph_schema.py" = ["UP042"]
  "src/ltagent/live/graph_validation.py" = ["UP042"]
  ```

- **Risk:** low
- **Status:** pending

### 2. Stabilise the `ltagent.live` package init

- **From:** Agent 1
- **To:**   Agent 0 (with input from Agent 2)
- **File:** `src/ltagent/live/__init__.py`
- **Why:**  During this session, the `__init__.py` cycled
  through several half-written states (one importing
  `edit_ops`/`edit_result` that did not yet exist on this
  branch; one deleted entirely). When the init was broken,
  `from ltagent.live.graph import ...` raised
  `ModuleNotFoundError` at import time, even though the
  three Agent 1 modules are independently importable.
- **What:** Stabilise the `__init__.py` to either (a) re-export
  the public surface of every landed module, or (b) stay empty
  (`__all__: list[str] = []`) so it does not import modules that
  may not yet be present. The current convention in
  `docs/AGENT_LOCKS.md` calls for Agent 0 to own the init; the
  landed shape should match that.
- **Risk:** low (Agent 1 already works around this in the test
  file with an `importlib` loader).
- **Status:** pending

### 3. Coordinate `graph.py` and `graph_validation.py` filename

- **From:** Agent 1
- **To:**   Agent 0
- **File:** `src/ltagent/live/graph.py`
- **Why:**  The task brief I was given names `graph.py` as one
  of my owned files. The canonical layout in
  `docs/AGENT_LOCKS.md` only lists `graph_schema.py` and
  `graph_validation.py` (Agent 3 owns `project.py`, Agent 2
  owns `edit_ops.py`, etc.). The two layouts are not in
  conflict: `graph.py` here is a thin facade that re-exports
  the public API functions documented in the task brief
  (`create_empty_graph`, `validate_graph`, `graph_to_dict`,
  `graph_from_dict`, `list_components`, `list_nets`); the
  schema and validator do the real work. No action is
  required; this note exists so Agent 0 can confirm the
  filename is acceptable in the merge.
- **What:** No change. Confirm the layout in the integrator
  report.
- **Risk:** low
- **Status:** resolved (informational)

## Out-of-scope (intentionally not done)

* `ltagent.live.snapshot`, `ltagent.live.history`, etc. — Agent 3
  / Agent 5 territory. The graph model is designed to be
  diffable (sorted `list_components`, sorted `list_nets`,
  deterministic dict order) so Agent 3 can reuse it for
  snapshots without changes.
* `.cir` / `.asc` / `.plt` writers — Phase 2 work; Agent 0 +
  Agent 4.
* Math Core integration (`constraints` field) — Agent 4.
* MCP wiring of the graph tools — Agent 6. The stable function
  signatures in `graph.py` are the input for Agent 6's
  function-level glue; the package `__init__.py` re-exports
  (Agent 0) will make them visible as
  `ltagent.live.graph.create_empty_graph` etc.
