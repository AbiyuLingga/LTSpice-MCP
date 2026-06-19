"""Append-only edit history for live projects.

The :mod:`ltagent.live.history` module owns ``edit_history.jsonl``
inside a project directory. The file is the audit log of every
operation the agent performs (and every human edit, when the CLI is
used directly). It is JSONL, one event per line, and the line order
is the canonical event order.

Design contract
---------------

* The on-disk format is a strict subset of the plan §6.2 example.
  Every event is a JSON object on a single line; lines without
  content are silently skipped when reading.
* :class:`HistoryEvent` is the typed input. :func:`append_history`
  accepts the dataclass or a plain mapping. Mappings are validated
  lightly (must be a mapping; ``step`` must be an int; ``op`` must be
  a non-empty string) and stored verbatim.
* The file is opened in append mode, which on POSIX is atomic for
  small writes (``PIPE_BUF``). The size of any single event is
  bounded by a sanity check (1 MiB) to prevent one fat payload from
  blocking other writers.
* :func:`read_history` returns a list of plain dictionaries in
  order. The dataclass is *not* round-tripped because the event
  schema is allowed to evolve; the file is the source of truth.

This module never spawns a process and never opens a file outside
the project directory it was given. The path-safety guard is
delegated to :mod:`ltagent.live.project`, which means the same
``PATH_TRAVERSAL`` error code is used everywhere.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from .project import (
    ERR_PROJECT_IO,
    LiveProjectError,
    _resolve_project_path,
    get_project_paths,
)

#: Hard cap on a single event payload, in bytes. Anything larger
#: is rejected with a structured error so a runaway prompt cannot
#: grow the history file unbounded.
MAX_EVENT_BYTES: Final[int] = 1_048_576

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryEvent:
    """A single entry in ``edit_history.jsonl``.

    The dataclass matches the example in plan §6.2. ``step`` and
    ``op`` are required; everything else is optional so the dataclass
    can be reused for every event kind (create_project,
    set_component_value, run_simulation, snapshot, restore, ...).
    """

    step: int
    op: str
    time: str
    project_id: str | None = None
    reason: str | None = None
    target: str | None = None
    old: Any = None
    new: Any = None
    success: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable plain dictionary.

        ``None`` fields are kept (the schema allows them) so the
        round-trip preserves the full event. The function never
        mutates the dataclass.
        """
        return asdict(self)


def make_history_event(
    *,
    step: int,
    op: str,
    project_id: str | None = None,
    reason: str | None = None,
    target: str | None = None,
    old: Any = None,
    new: Any = None,
    success: bool | None = None,
    extra: Mapping[str, Any] | None = None,
    when: datetime | None = None,
) -> HistoryEvent:
    """Construct a :class:`HistoryEvent` with a default ``time`` stamp.

    The timestamp is an ISO-8601 string in UTC (the format used by
    ``datetime.isoformat()`` for an aware UTC datetime). Tests can
    pin the wall clock via the ``when`` argument.
    """
    when = when or datetime.now(UTC)
    return HistoryEvent(
        step=step,
        op=op,
        time=when.isoformat(),
        project_id=project_id,
        reason=reason,
        target=target,
        old=old,
        new=new,
        success=success,
        extra=dict(extra) if extra else {},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a payload that is about to be appended.

    Returns the payload unchanged on success. Raises
    :class:`LiveProjectError` with code ``LIVE_HISTORY_INVALID`` on
    any structural problem.

    The validation is intentionally light: the on-disk schema is
    the contract, and we want to remain forward-compatible with
    agent-emitted events that include extra fields.
    """
    if not isinstance(payload, Mapping):
        raise LiveProjectError(
            "LIVE_HISTORY_INVALID",
            f"event must be a mapping, got {type(payload).__name__}",
            data={"type": type(payload).__name__},
        )
    step = payload.get("step")
    if not isinstance(step, int) or isinstance(step, bool):
        raise LiveProjectError(
            "LIVE_HISTORY_INVALID",
            f"event 'step' must be an int, got {type(step).__name__}",
            data={"step": step},
        )
    op = payload.get("op")
    if not isinstance(op, str) or not op:
        raise LiveProjectError(
            "LIVE_HISTORY_INVALID",
            "event 'op' must be a non-empty string",
            data={"op": op},
        )
    if "time" not in payload or not isinstance(payload["time"], str):
        raise LiveProjectError(
            "LIVE_HISTORY_INVALID",
            "event 'time' must be an ISO-8601 string",
            data={"time": payload.get("time")},
        )
    return dict(payload)


def _serialise_event(payload: Mapping[str, Any]) -> str:
    """Serialise an event payload to a single JSON line.

    Sorts the top-level keys for deterministic output. ``ensure_ascii``
    is False so non-ASCII prompt text round-trips losslessly.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def append_history(
    project_dir: Path | str,
    event: HistoryEvent | Mapping[str, Any],
    *,
    projects_root: Path | str | None = None,
) -> Path:
    """Append ``event`` to ``edit_history.jsonl``.

    The file is created on first write. The append goes through
    plain ``open(..., "a")`` which is atomic for the small payloads
    this audit log stores.

    Parameters
    ----------
    project_dir:
        Path to the live project directory. May be relative or use
        ``~``; it is resolved via
        :func:`ltagent.live.project._resolve_project_path`.
    event:
        Either a :class:`HistoryEvent` or a plain mapping with the
        same shape (``step``, ``op``, ``time``, ...).
    projects_root:
        Optional containment root. When provided the project must
        resolve under it; otherwise the path is only required to
        exist.

    Returns
    -------
    Path
        The absolute path of the history file (for diagnostics).

    Raises
    ------
    LiveProjectError
        On validation failure, path traversal, or filesystem error.
    """
    paths = get_project_paths(
        _resolve_project_path(
            project_dir, projects_root=projects_root, must_exist=True
        )
    )
    payload = event.to_dict() if isinstance(event, HistoryEvent) else event
    payload = _validate_event_payload(payload)

    line = _serialise_event(payload) + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > MAX_EVENT_BYTES:
        raise LiveProjectError(
            "LIVE_HISTORY_TOO_LARGE",
            f"event payload is {len(encoded)} bytes; max is {MAX_EVENT_BYTES}",
            data={"size": len(encoded), "max": MAX_EVENT_BYTES},
        )

    paths.history.parent.mkdir(parents=True, exist_ok=True)
    try:
        with paths.history.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to append to {paths.history}: {exc}",
            data={"historyPath": str(paths.history), "phase": "append"},
        ) from exc
    return paths.history


def read_history(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Read the entire edit history.

    Returns a list of event dictionaries in file order. Lines that
    are empty or whitespace-only are skipped silently; a line that
    fails to parse raises :class:`LiveProjectError` with code
    ``LIVE_HISTORY_INVALID_JSON`` and the offending line number.

    The function does not round-trip through :class:`HistoryEvent`
    so it remains forward-compatible with events whose schema has
    drifted since the dataclass was last updated.
    """
    paths = get_project_paths(
        _resolve_project_path(
            project_dir, projects_root=projects_root, must_exist=True
        )
    )
    if not paths.history.exists():
        return []
    try:
        text = paths.history.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveProjectError(
            ERR_PROJECT_IO,
            f"failed to read {paths.history}: {exc}",
            data={"historyPath": str(paths.history), "phase": "read"},
        ) from exc

    events: list[dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LiveProjectError(
                "LIVE_HISTORY_INVALID_JSON",
                f"line {lineno} of {paths.history} is not valid JSON: {exc.msg}",
                data={
                    "historyPath": str(paths.history),
                    "line": lineno,
                    "column": exc.colno,
                },
            ) from exc
        if not isinstance(decoded, dict):
            raise LiveProjectError(
                "LIVE_HISTORY_INVALID_JSON",
                f"line {lineno} of {paths.history} is not a JSON object",
                data={"historyPath": str(paths.history), "line": lineno},
            )
        events.append(decoded)
    return events


def next_step(
    project_dir: Path | str,
    *,
    projects_root: Path | str | None = None,
) -> int:
    """Return the next ``step`` number for a new event.

    Reads the existing history and returns ``max(existing) + 1`` (or
    ``1`` when the file does not exist). The function is purely
    advisory — the caller may still pass any integer they want.
    """
    events = read_history(project_dir, projects_root=projects_root)
    if not events:
        return 1
    steps = [int(e["step"]) for e in events if isinstance(e.get("step"), int)]
    if not steps:
        return 1
    return max(steps) + 1


__all__ = [
    "MAX_EVENT_BYTES",
    "HistoryEvent",
    "append_history",
    "make_history_event",
    "next_step",
    "read_history",
]
