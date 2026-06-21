"""Deterministic Circuit Graph to Circuit IR conversion."""

from __future__ import annotations

from typing import Any

from ltagent.ir import KIND_TO_SPICE_PREFIX, SCHEMA_VERSION, CircuitIR

from .graph_schema import CircuitGraph


def graph_to_ir(graph: CircuitGraph) -> CircuitIR:
    """Convert a validated live graph into the existing generation contract."""
    components: list[dict[str, Any]] = []
    for component in graph.components.values():
        components.append(
            {
                "id": component.id,
                "kind": component.kind.value,
                "spicePrefix": KIND_TO_SPICE_PREFIX[component.kind.value],
                "nodes": list(component.pins.pins.values()),
                "value": component.value,
                "model": component.model,
                "role": component.role,
            }
        )

    directives: list[str] = []
    for directive in graph.directives:
        if directive.args.strip():
            raise ValueError(
                "directive arguments cannot cross the Graph-to-IR boundary; "
                "use structured analyses or measurements"
            )
        directives.append(directive.name)

    nodes = list(graph.nets)
    if "0" in nodes:
        nodes.remove("0")
        nodes.insert(0, "0")
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "name": graph.projectId,
        "topology": graph.topology,
        "description": graph.description,
        "nodes": nodes,
        "components": components,
        "analysis": [item.model_dump(mode="json") for item in graph.analyses],
        "measurements": [item.model_dump(mode="json") for item in graph.measurements],
        "probes": [],
        "directives": directives,
        "constraints": (
            graph.constraints.model_dump(mode="json") if graph.constraints is not None else None
        ),
        "models": [],
        "subcircuits": [],
        "metadata": None,
    }
    return CircuitIR.model_validate(payload)


__all__ = ["graph_to_ir"]
