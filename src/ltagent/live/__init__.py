"""File-based live editing for LTspice circuits.

The :mod:`ltagent.live` package wraps the Circuit Graph with a small set
of safe edit operations. AI agents MUST mutate a circuit through these
operations instead of editing raw ``.asc``/``.cir``/``.ir.json`` files.

Public surface
--------------

- :mod:`ltagent.live.edit_result` -- the structured result type returned
  by every edit operation.
- :mod:`ltagent.live.edit_ops`    -- the eight MVP edit operations
  (``add_component``, ``remove_component``, ``set_component_value``,
  ``connect_pin``, ``disconnect_pin``, ``rename_net``,
  ``add_directive``, ``add_measurement``).

Stability
---------

Edit operations are pure functions: they do not mutate the input graph.
Every operation returns an :class:`ltagent.live.edit_result.EditResult`
that carries the new graph, the changes that were applied, and a
structured list of errors and warnings. The schema is part of the MCP
contract for live editing tools.
"""

from __future__ import annotations

from .edit_ops import (
    DIRECTIVE_ALLOWLIST,
    GROUND_NODE,
    KIND_ARITY,
    KIND_TO_SPICE_PREFIX,
    MEASUREMENT_ANALYSIS_KINDS,
    NET_TYPE_GROUND,
    NET_TYPE_SIGNAL,
    PIN_NAMES,
    add_component,
    add_directive,
    add_measurement,
    connect_pin,
    disconnect_pin,
    remove_component,
    rename_net,
    set_component_value,
)
from .edit_result import (
    EditChange,
    EditError,
    EditResult,
    EditWarning,
)
from .graph import (
    create_empty_graph,
    graph_from_dict,
    graph_from_dict_safe,
    graph_to_dict,
    list_components,
    list_nets,
    validate_graph,
)
from .graph_to_ir import graph_to_ir
from .history import append_history, make_history_event, next_step, read_history
from .project import (
    LiveProjectError,
    ProjectPaths,
    apply_operation,
    create_live_project,
    open_live_project,
    read_graph,
    write_graph,
)
from .sim_loop import run_and_verify as run_verification_loop
from .sim_loop import run_project_and_verify
from .snapshot import create_snapshot, diff_snapshot, list_snapshots, restore_snapshot

__all__ = [
    "DIRECTIVE_ALLOWLIST",
    "GROUND_NODE",
    "KIND_ARITY",
    "KIND_TO_SPICE_PREFIX",
    "MEASUREMENT_ANALYSIS_KINDS",
    "NET_TYPE_GROUND",
    "NET_TYPE_SIGNAL",
    "PIN_NAMES",
    "EditChange",
    "EditError",
    "EditResult",
    "EditWarning",
    "LiveProjectError",
    "ProjectPaths",
    "add_component",
    "add_directive",
    "add_measurement",
    "append_history",
    "apply_operation",
    "connect_pin",
    "create_empty_graph",
    "create_live_project",
    "create_snapshot",
    "diff_snapshot",
    "disconnect_pin",
    "graph_from_dict",
    "graph_from_dict_safe",
    "graph_to_dict",
    "graph_to_ir",
    "list_components",
    "list_nets",
    "list_snapshots",
    "make_history_event",
    "next_step",
    "open_live_project",
    "read_graph",
    "read_history",
    "remove_component",
    "rename_net",
    "restore_snapshot",
    "run_project_and_verify",
    "run_verification_loop",
    "set_component_value",
    "validate_graph",
    "write_graph",
]
