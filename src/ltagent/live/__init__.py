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
    "add_component",
    "add_directive",
    "add_measurement",
    "connect_pin",
    "disconnect_pin",
    "remove_component",
    "rename_net",
    "set_component_value",
]
