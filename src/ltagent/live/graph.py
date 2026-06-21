"""Public API for the live-editing Circuit Graph.

This module is the entry point the rest of the codebase (Edit
Operations Agent, IR converter, MCP tools, tests) uses to build,
validate, serialise, and inspect a :class:`CircuitGraph`. It exposes
the small, stable surface called out in the ``Agent 1`` task brief:

* :func:`create_empty_graph` -- build a fresh graph for a project.
* :func:`validate_graph` -- run the graph-level checks.
* :func:`graph_to_dict` / :func:`graph_from_dict` -- JSON
  serialisation round-trips.
* :func:`list_components` / :func:`list_nets` -- deterministic
  read-only inspection helpers.

The functions are intentionally thin. They never mutate their
inputs in surprising ways and they never print. Errors are
surfaced as :class:`pydantic.ValidationError` (when construction
fails) or as the structured :class:`ValidationResult` returned by
:func:`validate_graph`. The module has zero dependency on the MCP
server, the CLI, or the rest of the agent; importing it pulls in
only Pydantic and the sibling modules in the same package.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .graph_schema import (
    GROUND_NODE,
    SCHEMA_VERSION,
    Analysis,
    AnalysisKind,
    CircuitGraph,
    Component,
    ComponentKind,
    Constraints,
    Directive,
    LayoutHint,
    Measurement,
    Net,
    NetType,
    PinMap,
)
from .graph_validation import (
    Severity,
    ValidationIssue,
    ValidationResult,
)
from .graph_validation import (
    validate_graph as _validate_graph_impl,
)

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def create_empty_graph(
    project_id: str,
    *,
    domain: str = "analog",
    topology: str = "",
    description: str | None = None,
) -> CircuitGraph:
    """Return a fresh :class:`CircuitGraph` for ``project_id``.

    The returned graph is intentionally minimal: empty
    ``components``, empty ``nets``, and no analyses, measurements,
    or directives. The Edit Operations Agent populates it
    incrementally; :func:`validate_graph` will warn that the
    ground net is missing until the caller adds it.

    Parameters
    ----------
    project_id:
        Slug-safe project identifier. Must match
        :data:`ltagent.live.graph_schema.PROJECT_ID_PATTERN`
        (lowercase letter first, then lowercase letters / digits /
        underscores / hyphens, up to 64 chars). The same pattern
        is used by the IR layer so a graph can be lowered into an
        IR without renaming.
    domain:
        Domain hint; ``"analog"`` is the only recognised value
        in Phase 1. Other values raise at construction time.
    topology:
        Optional topology name. Empty string while the AI is
        still assembling the circuit.
    description:
        Optional human-readable description.

    Raises
    ------
    pydantic.ValidationError
        If ``project_id`` is empty or does not match the slug
        pattern, or if ``domain`` is not recognised.
    """
    return CircuitGraph(
        schemaVersion=SCHEMA_VERSION,
        projectId=project_id,
        domain=domain,
        topology=topology,
        description=description,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_graph(graph: CircuitGraph) -> ValidationResult:
    """Run graph-level checks on ``graph``.

    The result is a structured :class:`ValidationResult` carrying
    errors (hard failures), warnings (soft signals), and the full
    sorted issue list. ``ok`` is True iff no error-severity issue
    was found. Pure function; never mutates ``graph``.

    Raises
    ------
    TypeError
        If ``graph`` is not a :class:`CircuitGraph`.
    """
    if not isinstance(graph, CircuitGraph):
        raise TypeError(f"validate_graph expects CircuitGraph, got {type(graph).__name__}")
    return _validate_graph_impl(graph)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def graph_to_dict(graph: CircuitGraph) -> dict[str, Any]:
    """Serialise ``graph`` to a plain ``dict`` ready for ``json.dump``.

    The shape matches the JSON document described in plan section
    7.2. The output is deterministic (dicts are returned in
    declaration order; sets become sorted lists via Pydantic's
    ``model_dump``) so two equivalent graphs produce equal dicts.

    Raises
    ------
    TypeError
        If ``graph`` is not a :class:`CircuitGraph`.
    """
    if not isinstance(graph, CircuitGraph):
        raise TypeError(f"graph_to_dict expects CircuitGraph, got {type(graph).__name__}")
    return graph.model_dump(mode="python", by_alias=False)


def graph_from_dict(data: dict[str, Any]) -> CircuitGraph:
    """Reconstruct a :class:`CircuitGraph` from a plain ``dict``.

    The reverse of :func:`graph_to_dict`. Accepts the same shape
    that ``graph.model_dump()`` produces; Pydantic is responsible
    for coercing and validating.

    Raises
    ------
    TypeError
        If ``data`` is not a mapping.
    pydantic.ValidationError
        If the payload is structurally invalid (e.g. unknown
        fields, missing required keys, bad identifiers).
    """
    if not isinstance(data, dict):
        raise TypeError(f"graph_from_dict expects a dict, got {type(data).__name__}")
    return CircuitGraph.model_validate(data)


def graph_from_dict_safe(
    data: dict[str, Any],
) -> tuple[CircuitGraph | None, list[ValidationIssue]]:
    """Variant of :func:`graph_from_dict` that returns structured issues.

    Useful for callers that want to surface Pydantic validation
    errors through the same ``ValidationIssue`` shape used by
    :func:`validate_graph`. Returns ``(graph, issues)`` where
    ``graph`` is ``None`` when construction failed and ``issues``
    is the structured error list (empty on success).
    """
    if not isinstance(data, dict):
        return None, [
            ValidationIssue(
                code="GRAPH_INPUT_INVALID",
                severity=Severity.ERROR,
                path="<root>",
                detail=f"graph_from_dict_safe expects a dict, got {type(data).__name__}",
            )
        ]
    try:
        return CircuitGraph.model_validate(data), []
    except PydanticValidationError as exc:
        issues: list[ValidationIssue] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            issues.append(
                ValidationIssue(
                    code=f"GRAPH_{err.get('type', 'INVALID').upper()}",
                    severity=Severity.ERROR,
                    path=loc,
                    detail=str(err.get("msg", "invalid value")),
                )
            )
        return None, issues


# ---------------------------------------------------------------------------
# Read-only inspection
# ---------------------------------------------------------------------------


def list_components(graph: CircuitGraph) -> list[Component]:
    """Return all components in ``graph`` sorted by id.

    Deterministic ordering helps the Edit Operations Agent diff
    graphs and helps the JSON contract stay stable across runs.
    Returns an empty list for an empty graph.

    Raises
    ------
    TypeError
        If ``graph`` is not a :class:`CircuitGraph`.
    """
    if not isinstance(graph, CircuitGraph):
        raise TypeError(f"list_components expects CircuitGraph, got {type(graph).__name__}")
    return [graph.components[key] for key in sorted(graph.components.keys())]


def list_nets(graph: CircuitGraph) -> list[Net]:
    """Return all nets in ``graph`` sorted by name.

    The ground net (when present) sorts at the very front because
    ``"0"`` is the only SPICE node name that starts with a digit;
    sorting puts it next to the other names naturally. Returns
    an empty list for an empty graph.

    Raises
    ------
    TypeError
        If ``graph`` is not a :class:`CircuitGraph`.
    """
    if not isinstance(graph, CircuitGraph):
        raise TypeError(f"list_nets expects CircuitGraph, got {type(graph).__name__}")
    nets = list(graph.nets.values())

    def _sort_key(net: Net) -> tuple[int, str]:
        return (0 if net.name == GROUND_NODE else 1, net.name)

    return sorted(nets, key=_sort_key)


__all__ = [
    "GROUND_NODE",
    "SCHEMA_VERSION",
    "Analysis",
    "AnalysisKind",
    "CircuitGraph",
    "Component",
    "ComponentKind",
    "Constraints",
    "Directive",
    "LayoutHint",
    "Measurement",
    "Net",
    "NetType",
    "PinMap",
    "Severity",
    "ValidationIssue",
    "ValidationResult",
    "create_empty_graph",
    "graph_from_dict",
    "graph_from_dict_safe",
    "graph_to_dict",
    "list_components",
    "list_nets",
    "validate_graph",
]
