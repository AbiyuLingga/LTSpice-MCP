"""Deterministic Circuit IR to Circuit Graph conversion.

Sister of :mod:`ltagent.live.graph_to_ir`. The forward direction
(:func:`graph_to_ir`) is what the analog pipeline consumes; the reverse
(:func:`ir_to_graph`) is what the project migrator and the AI adapter
need when an old ``circuit.ir.json`` lands in a v2 project.

The conversion is the **id-preserving** direction:

* every IR component id becomes a graph component id;
* every IR node name becomes a graph net name (or stays an existing
  signal/ground net);
* the IR's positional ``nodes`` list is mapped to a graph
  :class:`PinMap` using the canonical pin names per kind declared in
  :data:`ltagent.live.edit_ops.PIN_NAMES`.

Anything that does not round-trip cleanly raises :class:`IRToGraphError`
with a stable code. The migrator (Phase 1.5) catches the error and
falls back to a snapshot restore.
"""

from __future__ import annotations

from typing import Final

from pydantic import ValidationError

from ltagent.ir import (
    SCHEMA_VERSION as IR_SCHEMA_VERSION,
)
from ltagent.ir import (
    AnalysisKind as IRAnalysisKind,
)
from ltagent.ir import (
    CircuitIR,
)
from ltagent.ir import (
    Component as IRComponent,
)
from ltagent.ir import (
    ComponentKind as IRComponentKind,
)
from ltagent.ir import (
    Measurement as IRMeasurement,
)

from .edit_ops import PIN_NAMES
from .graph_schema import (
    SCHEMA_VERSION as CIRCUIT_GRAPH_SCHEMA_VERSION,
)
from .graph_schema import (
    Analysis as GraphAnalysis,
)
from .graph_schema import (
    AnalysisKind as GraphAnalysisKind,
)
from .graph_schema import (
    CircuitGraph,
    Constraints,
    LayoutHint,
    NetType,
    PinMap,
)
from .graph_schema import (
    Component as GraphComponent,
)
from .graph_schema import (
    ComponentKind as GraphComponentKind,
)
from .graph_schema import (
    Measurement as GraphMeasurement,
)
from .graph_schema import (
    Net as GraphNet,
)

GROUND_NODE: Final[str] = "0"


class IRToGraphError(ValueError):
    """Raised when an IR document cannot be lowered to a CircuitGraph.

    The error carries a stable ``code`` and a ``data`` dict so the
    workbench surface (CLI, engine, MCP) can render a structured
    response without re-parsing the message text.
    """

    def __init__(self, code: str, message: str, *, data: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, object] = dict(data) if data else {}


ERR_IR_TO_GRAPH_PIN_ARITY: Final[str] = "IR_TO_GRAPH_PIN_ARITY"
ERR_IR_TO_GRAPH_PIN_UNKNOWN_KIND: Final[str] = "IR_TO_GRAPH_PIN_UNKNOWN_KIND"
ERR_IR_TO_GRAPH_ANALYSIS: Final[str] = "IR_TO_GRAPH_ANALYSIS"
ERR_IR_TO_GRAPH_MEASUREMENT: Final[str] = "IR_TO_GRAPH_MEASUREMENT"


_IR_TO_GRAPH_ANALYSIS: Final[dict[IRAnalysisKind, GraphAnalysisKind]] = {
    IRAnalysisKind.OP: GraphAnalysisKind.OP,
    IRAnalysisKind.DC: GraphAnalysisKind.DC,
    IRAnalysisKind.TRAN: GraphAnalysisKind.TRAN,
    IRAnalysisKind.AC: GraphAnalysisKind.AC,
}

_IR_TO_GRAPH_KIND: Final[dict[IRComponentKind, GraphComponentKind]] = {
    IRComponentKind.RESISTOR: GraphComponentKind.RESISTOR,
    IRComponentKind.CAPACITOR: GraphComponentKind.CAPACITOR,
    IRComponentKind.INDUCTOR: GraphComponentKind.INDUCTOR,
    IRComponentKind.VOLTAGE_SOURCE: GraphComponentKind.VOLTAGE_SOURCE,
    IRComponentKind.CURRENT_SOURCE: GraphComponentKind.CURRENT_SOURCE,
    IRComponentKind.DIODE: GraphComponentKind.DIODE,
    IRComponentKind.NPN: GraphComponentKind.NPN,
    IRComponentKind.PNP: GraphComponentKind.PNP,
    IRComponentKind.NMOS: GraphComponentKind.NMOS,
    IRComponentKind.PMOS: GraphComponentKind.PMOS,
    IRComponentKind.OPAMP: GraphComponentKind.OPAMP,
}


def _graph_constraints(ir: CircuitIR) -> Constraints | None:
    if ir.constraints is None:
        return None
    # The IR's constraints are already a validated :class:`Constraints`
    # Pydantic model; the graph layer accepts the same shape (flat
    # scalar dict). We re-dump to a dict and re-validate to keep the
    # graph model defensive against any IR evolution.
    payload: dict[str, object] = ir.constraints.model_dump(mode="json", exclude_none=True)
    if not payload:
        return None
    return Constraints.model_validate(payload)


def _graph_layout_hints(ir: CircuitIR) -> LayoutHint | None:
    metadata_raw = ir.metadata
    if metadata_raw is None:
        return None
    metadata: dict[str, object] = (
        metadata_raw.model_dump(mode="json")
        if hasattr(metadata_raw, "model_dump")
        else dict(metadata_raw)
    )
    if not metadata:
        return None
    hints_payload = metadata.get("layoutHints")
    if not isinstance(hints_payload, dict):
        return None
    return LayoutHint.model_validate(hints_payload)


def _build_pins(component: IRComponent, pin_names: tuple[str, ...]) -> dict[str, str]:
    if len(component.nodes) != len(pin_names):
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_PIN_ARITY,
            f"component {component.id!r} ({component.kind.value}) has "
            f"{len(component.nodes)} nodes but the canonical pin map "
            f"expects {len(pin_names)}",
            data={
                "componentId": component.id,
                "kind": component.kind.value,
                "expected": len(pin_names),
                "actual": len(component.nodes),
            },
        )
    return {pin: net for pin, net in zip(pin_names, component.nodes, strict=True)}


def _lower_component(component: IRComponent) -> GraphComponent:
    kind = _IR_TO_GRAPH_KIND.get(component.kind)
    if kind is None:
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_PIN_UNKNOWN_KIND,
            f"IR component kind {component.kind.value!r} has no graph mapping",
            data={"componentId": component.id, "kind": component.kind.value},
        )
    pin_names = PIN_NAMES.get(kind.value)
    if pin_names is None:
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_PIN_UNKNOWN_KIND,
            f"graph kind {kind.value!r} has no canonical pin map",
            data={"componentId": component.id, "kind": kind.value},
        )
    pins = _build_pins(component, pin_names)
    return GraphComponent(
        id=component.id,
        kind=kind,
        value=component.value,
        model=component.model,
        role=component.role,
        pins=PinMap(pins=pins),
    )


def _lower_analysis(analysis_ir: object) -> GraphAnalysis:
    kind_attr = getattr(analysis_ir, "kind", None)
    try:
        kind = _IR_TO_GRAPH_ANALYSIS[IRAnalysisKind(kind_attr)]
    except (KeyError, ValueError) as exc:
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_ANALYSIS,
            f"IR analysis kind {kind_attr!r} has no graph mapping",
            data={"kind": str(kind_attr)},
        ) from exc
    payload: dict[str, object] = {
        "kind": kind,
        "startTime": getattr(analysis_ir, "startTime", None),
        "stopTime": getattr(analysis_ir, "stopTime", None),
        "stepTime": getattr(analysis_ir, "stepTime", None),
        "startFreq": getattr(analysis_ir, "startFreq", None),
        "stopFreq": getattr(analysis_ir, "stopFreq", None),
        "pointsPerDecade": getattr(analysis_ir, "pointsPerDecade", None),
        "sweepVariable": getattr(analysis_ir, "sweepVariable", None),
        "sweepStart": getattr(analysis_ir, "sweepStart", None),
        "sweepStop": getattr(analysis_ir, "sweepStop", None),
        "sweepStep": getattr(analysis_ir, "sweepStep", None),
    }
    try:
        return GraphAnalysis.model_validate(payload)
    except ValidationError as exc:
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_ANALYSIS,
            f"IR analysis could not be lowered to graph: {exc}",
            data={"kind": str(kind_attr), "errors": exc.errors()},
        ) from exc


def _lower_measurement(measurement: IRMeasurement) -> GraphMeasurement:
    ir_kind = measurement.analysis
    try:
        graph_kind = _IR_TO_GRAPH_ANALYSIS[ir_kind]
    except KeyError as exc:
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_MEASUREMENT,
            f"IR measurement {measurement.name!r} analysis kind "
            f"{ir_kind.value!r} has no graph mapping",
            data={"measurement": measurement.name, "kind": ir_kind.value},
        ) from exc
    return GraphMeasurement(
        name=measurement.name,
        analysis=graph_kind,
        expression=measurement.expression,
    )


def ir_to_graph(ir: CircuitIR) -> CircuitGraph:
    """Lower a validated :class:`ltagent.ir.CircuitIR` to a :class:`CircuitGraph`.

    The conversion preserves ids and net names so a later ``graph_to_ir``
    round-trip is stable. The IR's positional ``nodes`` list is mapped
    through :data:`PIN_NAMES`; if the IR has a non-canonical node count
    the conversion raises :class:`IRToGraphError`.
    """
    components: dict[str, GraphComponent] = {}
    nets: dict[str, GraphNet] = {}

    for component in ir.components:
        graph_component = _lower_component(component)
        components[component.id] = graph_component
        for net_name in graph_component.pins.pins.values():
            if net_name in nets:
                continue
            nets[net_name] = _net_for(net_name)

    analyses = [_lower_analysis(item) for item in ir.analysis]
    measurements = [_lower_measurement(item) for item in ir.measurements]

    payload: dict[str, object] = {
        "schemaVersion": CIRCUIT_GRAPH_SCHEMA_VERSION,
        "projectId": ir.name,
        "topology": ir.topology,
        "description": ir.description,
        "components": components,
        "nets": nets,
        "analyses": analyses,
        "measurements": measurements,
        "directives": [],
        "constraints": _graph_constraints(ir),
        "layoutHints": _graph_layout_hints(ir),
    }
    try:
        return CircuitGraph.model_validate(payload)
    except ValidationError as exc:
        raise IRToGraphError(
            ERR_IR_TO_GRAPH_ANALYSIS,
            f"IR document could not be lowered to a CircuitGraph: {exc}",
            data={"errors": exc.errors()},
        ) from exc


def _net_for(name: str) -> GraphNet:
    """Return a fresh :class:`GraphNet` for ``name``.

    The ground net (literal ``"0"``) gets :class:`NetType.GROUND`; every
    other net starts as a :class:`NetType.SIGNAL`. The migrator is
    free to upgrade signals to power nets after the fact; the conversion
    here is deliberately minimal so the ir-to-graph direction is
    deterministic and round-trippable.
    """
    if name == GROUND_NODE:
        return GraphNet(name=name, type=NetType.GROUND)
    return GraphNet(name=name, type=NetType.SIGNAL)


__all__ = [
    "ERR_IR_TO_GRAPH_ANALYSIS",
    "ERR_IR_TO_GRAPH_MEASUREMENT",
    "ERR_IR_TO_GRAPH_PIN_ARITY",
    "ERR_IR_TO_GRAPH_PIN_UNKNOWN_KIND",
    "IR_SCHEMA_VERSION",
    "IRToGraphError",
    "ir_to_graph",
]
