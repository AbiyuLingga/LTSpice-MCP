# Agent 3 — Live Project + Snapshot — Report

**Branch:** `agent-3-live-project-snapshot`
**Status:** complete — `pytest`, `ruff check`, and `mypy` are clean on the
six files in scope. The wider `pytest` suite (858 tests, 1 skip for the
unconfigured LTspice executable) is also green.

---

## 1. Mission

Per the AGENT_LOCKS matrix and the task brief, Agent 3 owns the
**disk side** of the File-Based Live Editing pipeline (plan §6, §9,
§10):

* the standard project directory layout,
* the path-safety boundary around it,
* the snapshot / undo / restore / diff system,
* the append-only edit history.

The MCP layer, the circuit graph, the math core, and the simulation
runner are out of scope; Agent 3 only owns the storage primitives that
those layers will compose on top of.

---

## 2. Files delivered

| Path | Purpose | Status |
| --- | --- | --- |
| `src/ltagent/live/project.py` | `create_live_project`, `open_live_project`, `get_project_paths`, `write_graph`, `read_graph`, `ProjectPaths`, `LiveProjectError`, file-name constants, structured error codes. | committed |
| `src/ltagent/live/history.py` | `HistoryEvent`, `make_history_event`, `append_history`, `read_history`, `next_step`, JSONL serialisation with deterministic keys, 1 MiB event size cap. | committed |
| `src/ltagent/live/snapshot.py` | `create_snapshot`, `list_snapshots`, `restore_snapshot`, `diff_snapshot`, `SnapshotInfo`, `SnapshotDiff`, monotonic id counter, manifest schema. | committed |
| `tests/test_live_project.py` | 36 tests for project / history surface (create, open, paths, graph read/write, JSONL round-trip, path-traversal rejection, validation, size guard). | committed |
| `tests/test_live_snapshot.py` | 26 tests for snapshot surface (create, list, restore, diff, monotonic ids, manifest, isolation between projects, malformed id rejection). | committed |
| `docs/agent_reports/live_project_snapshot.md` | This report. | committed |

Commits on `agent-3-live-project-snapshot`:

```text
9b51b07 chore(live): address ruff + mypy findings
4157d20 fix(live): convert FileNotFoundError + PathSafetyError to LiveProjectError
2651205 test(live): project + snapshot + history tests
f389ecd feat(live): project + snapshot + history scaffolding
```

---

## 3. Public surface

### 3.1 Project layout

Per plan §6, a live project is a directory that owns these files:

```text
project_dir/
  circuit.graph.json
  circuit.ir.json
  circuit.cir
  circuit.asc
  metadata.json
  result.json
  calculation.json
  calculation.md
  edit_history.jsonl
  .snapshots/
```

`create_live_project` creates the project directory and `.snapshots/`
lazily; the other files are created by their respective owners
(Agent 1 for the graph, Agent 4 for the calculation report, etc.). The
file names are exposed as module-level constants
(`FILE_GRAPH`, `FILE_IR`, ...) so every other layer can refer to them
without string duplication.

### 3.2 Functions

```python
# project.py
create_live_project(projects_root, project_id) -> ProjectPaths
open_live_project(project_dir, *, projects_root=None) -> ProjectPaths
get_project_paths(project_dir) -> ProjectPaths
write_graph(project_dir, graph, *, projects_root=None) -> Path
read_graph(project_dir, *, projects_root=None) -> dict[str, Any]

# history.py
class HistoryEvent(step: int, op: str, time: str, ...)  # frozen dataclass
make_history_event(*, step, op, project_id=None, ...) -> HistoryEvent
append_history(project_dir, event, *, projects_root=None) -> Path
read_history(project_dir, *, projects_root=None) -> list[dict[str, Any]]
next_step(project_dir, *, projects_root=None) -> int

# snapshot.py
create_snapshot(project_dir, reason, *, files=None, projects_root=None, when=None) -> SnapshotInfo
list_snapshots(project_dir, *, projects_root=None) -> list[SnapshotInfo]
restore_snapshot(project_dir, snapshot_id, *, files=None, projects_root=None) -> list[str]
diff_snapshot(project_dir, snapshot_a, snapshot_b, *, projects_root=None) -> SnapshotDiff
```

### 3.3 Snapshot id scheme

A snapshot id is `NNN_<slug>`:

* `NNN` is a zero-padded 3+ digit counter derived from
  `_list_existing_snapshot_ids`. Garbage directories (no `NNN_` prefix)
  are ignored, so the counter is stable across copy / restore.
* `<slug>` is the lower-cased, slug-safe version of the human
  `reason`. Non-alphanumeric runs collapse to `_`. The slug is
  truncated to 48 characters and the empty input falls back to
  `snapshot` so the id is never just a number.

### 3.4 Error model

All errors raised by this slice are :class:`LiveProjectError` (or one
of its callers, in the snapshot module — same base). The error code is
the only thing callers should switch on; the message is human text.

Stable codes:

* `LIVE_INVALID_PROJECT_ID` — slug pattern failed.
* `LIVE_PROJECT_NOT_FOUND` — the directory does not exist on disk.
* `LIVE_NOT_A_DIRECTORY` — the path exists but is not a directory.
* `LIVE_GRAPH_NOT_FOUND` — `circuit.graph.json` missing.
* `LIVE_GRAPH_INVALID_JSON` — graph is missing, unparseable, or not a
  top-level object.
* `LIVE_PROJECT_IO_ERROR` — generic I/O failure (read, write, mkdir,
  copy).
* `LIVE_SNAPSHOT_NOT_FOUND`, `LIVE_SNAPSHOT_INVALID_ID`,
  `LIVE_SNAPSHOT_EXISTS`, `LIVE_SNAPSHOT_INVALID_MANIFEST`,
  `LIVE_SNAPSHOT_INVALID_FILES` — snapshot-specific codes.
* `LIVE_HISTORY_INVALID`, `LIVE_HISTORY_INVALID_JSON`,
  `LIVE_HISTORY_TOO_LARGE` — history-specific codes.
* `LIVE_SNAPSHOT_REASON_REQUIRED` — empty `reason` on `create_snapshot`.
* `PATH_TRAVERSAL` — the candidate escapes `projects_root`. This is
  re-raised from `ltagent.security.safe_resolve_under` so the upstream
  code is unchanged.

---

## 4. Security guarantees

* **No escape from `projects_root`.** Every entry point that accepts a
  `project_dir` also accepts an optional `projects_root`. When the
  root is given, the candidate is resolved through
  `ltagent.security.safe_resolve_under` and a path outside the root
  raises `LiveProjectError` with code `PATH_TRAVERSAL`. A missing
  target is surfaced as `LIVE_PROJECT_NOT_FOUND` (not a raw
  `FileNotFoundError`) so callers can match one error type per
  failure mode.
* **Slug-safe project id.** `create_live_project` re-validates the id
  through `ltagent.security.validate_slug` so the directory name can
  never be `..`, `/etc/passwd`, etc. The same guard is used for
  snapshot ids.
* **No `..` in snapshot file lists.** The `files` argument of
  `create_snapshot` rejects absolute paths and any path with `..`
  segments. The same rejection happens for every path that
  `_copy_files` walks.
* **Atomic graph writes.** `write_graph` writes to `*.json.tmp` first
  and `replace`s the target on success, so a crash mid-write cannot
  leave a truncated file at the canonical name. The test suite
  asserts no `.tmp` sibling is left behind.
* **JSONL append is bounded.** `append_history` caps the per-event
  payload at 1 MiB (`MAX_EVENT_BYTES`). Larger payloads raise
  `LIVE_HISTORY_TOO_LARGE` so a runaway prompt cannot grow the
  history file unbounded.
* **No subprocess.** The slice never spawns a process; the
  LTspice / Wine / Verilator concerns live one layer up in
  `ltagent.runner` and `ltagent.digital_runner`.

---

## 5. Test coverage

* `pytest tests/test_live_project.py tests/test_live_snapshot.py`
  collects **62 tests** and they all pass in < 1 s.
* The wider `pytest tests/` run is **858 passed, 1 skipped** (the
  skip is the pre-existing `ltspice.executable not configured` guard
  in `test_runner.py`).
* `ruff check` on the six files: **clean**.
* `mypy --strict` on the three source files: **clean**.

Highlights of the test plan:

* Project structure: directory exists, only `.snapshots/` is created,
  every constant has the expected path.
* Slug rejection: `..`, `nested/../../escape`, `/etc/passwd`, leading
  uppercase, leading digit, empty.
* Path-traversal rejection: with and without `projects_root`, for
  every entry point (`create_live_project`, `open_live_project`,
  `write_graph`, `read_graph`).
* Graph round-trip: write → read → on-disk JSON parse → atomic
  replace → no `.tmp` leftover.
* Graph validation: rejects non-mapping input, top-level JSON array,
  corrupt JSON, missing file.
* JSONL round-trip: events appended in order, every line is valid
  JSON, blank lines skipped on read, corrupt line fails loudly, size
  guard trips.
* Snapshot: copies the default file list, writes a manifest, monotonic
  ids, slug-safe ids, list in creation order, restore brings files
  back, restore can pick a subset, diff captures added / removed /
  changed / unchanged, corrupt manifest is skipped by list and
  rejected by restore / diff, snapshot id is rejected when malformed,
  the snapshot of one project never touches a sibling project.

---

## 6. How to verify the work locally

```bash
# from the repo root
git checkout agent-3-live-project-snapshot
.venv/bin/python -m pytest tests/test_live_project.py tests/test_live_snapshot.py
.venv/bin/python -m ruff check src/ltagent/live/project.py \
    src/ltagent/live/history.py src/ltagent/live/snapshot.py \
    tests/test_live_project.py tests/test_live_snapshot.py
.venv/bin/python -m mypy src/ltagent/live/project.py \
    src/ltagent/live/history.py src/ltagent/live/snapshot.py
```

Expected output:

* `62 passed` from pytest.
* `All checks passed!` from ruff.
* `Success: no issues found in 3 source files` from mypy.

A smoke test of the public surface (in one REPL session):

```python
from pathlib import Path
from ltagent.live.project import (
    create_live_project, open_live_project, write_graph, read_graph,
)
from ltagent.live.history import (
    make_history_event, append_history, read_history,
)
from ltagent.live.snapshot import (
    create_snapshot, list_snapshots, restore_snapshot, diff_snapshot,
)

root = Path("/tmp/agent3_demo")
root.mkdir(exist_ok=True)
paths = create_live_project(root, "rc_lowpass_1khz")
write_graph(paths.project_dir, {"v": 1, "topology": "rc_lowpass"})
append_history(paths.project_dir,
               make_history_event(step=1, op="create_project",
                                  project_id="rc_lowpass_1khz"))
snap = create_snapshot(paths.project_dir, "initial")
write_graph(paths.project_dir, {"v": 2, "topology": "rc_highpass"})
print(list_snapshots(paths.project_dir))
print(diff_snapshot(paths.project_dir, snap.snapshot_id,
                    list_snapshots(paths.project_dir)[-1].snapshot_id))
restore_snapshot(paths.project_dir, snap.snapshot_id, files=["circuit.graph.json"])
print(read_graph(paths.project_dir))
```

---

## 7. Integration Requests

The slice is intentionally minimal. The following are **hooks the
other agents can rely on**; no other slice is required to change for
Agent 3 to land.

1. **`ltagent.live.project.ProjectPaths`** is the canonical path
   record. Anyone who needs a project file path should accept a
   `ProjectPaths` and compose its fields — never join strings against
   the project directory manually.
2. **`append_history` accepts both `HistoryEvent` and a plain
   mapping.** Downstream agents can pass
   `{"step": ..., "op": "set_component_value", "target": "R1", ...}`
   without depending on the dataclass; the validator only checks
   that `step` is an int, `op` is a non-empty string, and `time` is
   a string.
3. **`next_step(project_dir)`** is the helper for "what step number
   should the next event have?". It reads the existing history and
   returns `max + 1` (or 1 on empty).
4. **Snapshot hooks** (Agent 2 edit operations) can call
   `create_snapshot` with a stable `reason` (e.g.
   `"before add_opamp"`) and store the returned `SnapshotInfo` so
   restore / diff are available.
5. **The MCP layer** (Agent 6) can mount the four snapshot functions
   and the six project / graph / history functions one-for-one. The
   error codes listed in §3.4 are stable, so the JSON output contract
   can switch on them.

If a later agent needs a new file inside the project directory, the
file name must be added to `PROJECT_FILE_NAMES` in `project.py` and
to `DEFAULT_SNAPSHOT_FILES` in `snapshot.py` (so a fresh project can
be round-tripped from a single snapshot). The integrator owns the
`__init__.py` for `ltagent.live` and is the right place to wire
these new names into a public re-export.

---

## 8. Risks and known limitations

* **No writer for `.cir` / `.asc` / `.plt`.** The slot is reserved in
  `ProjectPaths` but the file content is not produced. That is the
  responsibility of the generation layer (Agent 1 / Phase 7) and was
  explicitly out of scope for this task.
* **Counter-based snapshot ids.** Two `create_snapshot` calls with
  the same `reason` produce two distinct ids (`001_alpha`, `002_alpha`).
  This is intentional — restoring a snapshot by id is a stable
  operation, but two parallel agents creating snapshots at the same
  time both succeed. If a stricter "idempotent on `reason`" semantics
  is needed later, the counter must be replaced with a content hash
  or a caller-supplied id.
* **Byte-level diff.** `diff_snapshot` compares bytes, not JSON
  structure. Two snapshots whose `circuit.graph.json` differs only
  in key order are reported as `changed`. The line-level / JSON-level
  diff is the upstream agent's responsibility.
* **No compression / deduplication.** A snapshot is a verbatim copy.
  If `.snapshots/` becomes a storage concern, the layer above can
  wrap `create_snapshot` in a `tar` + `zstd` step without changing
  this module.
* **Path safety is best-effort on `open_live_project` without
  `projects_root`.** When the caller omits the root, the function
  only checks that the directory exists. This is the correct
  trade-off for a CLI / MCP entry point that wants to open an
  already-validated project by absolute path, but it means the
  containment check is the caller's responsibility in that mode.

---

## 9. Self-review

* **Correctness.** All 62 new tests pass; the wider 858-test suite
  is also green.
* **Minimal surface.** No subprocess, no `ltagent.runner`, no
  `ltagent.mcp_server`, no `ltagent.math_core` — only the shared
  `ltagent.security` helpers.
* **Determinism.** All file content is written with
  `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False)`;
  manifest timestamps come from a parameter (default `now(UTC)`) so
  tests can pin them.
* **MCP safety.** Every public function accepts a `projects_root`
  keyword and surfaces containment failures with the same code the
  MCP layer already understands (`PATH_TRAVERSAL`).
* **No drive-by edits.** I touched only the six files in my
  ownership list. The plan's "if you need a change in a shared
  file, write an integration request" path was not needed for this
  work.

The slice is ready for the integrator to review and merge.
