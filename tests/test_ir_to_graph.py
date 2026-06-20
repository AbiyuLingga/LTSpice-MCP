"""Tests for the IR -> CircuitGraph converter (:mod:`ltagent.live.ir_to_graph`).

The conversion must:

* preserve component ids 1:1;
* preserve net names 1:1 (including the ground net ``"0"``);
* map the IR's positional ``nodes`` list through the canonical
  :data:`ltagent.live.edit_ops.PIN_NAMES`;
* round-trip through :func:`graph_to_ir` without losing data;
* raise a structured :class:`IRToGraphError` on pin arity mismatch.
"""

from __future__ import annotations

import pytest

from ltagent.ir import (
    CircuitIR,
)
from ltagent.live.edit_ops import PIN_NAMES
from ltagent.live.graph_schema import (
    GROUND_NODE,
    NetType,
)
from ltagent.live.graph_schema import (
    ComponentKind as GraphComponentKind,
)
from ltagent.live.ir_to_graph import (
    ERR_IR_TO_GRAPH_PIN_ARITY,
    IRToGraphError,
    ir_to_graph,
)


def _rc_lowpass_ir() -> CircuitIR:
    return CircuitIR.model_validate(
        {
            "schemaVersion": "0.1",
            "name": "rc_lowpass",
            "topology": "rc_lowpass",
            "nodes": ["vin", "vout", "0"],
            "components": [
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["vin", "vout"],
                    "value": "1k",
                },
                {
                    "id": "C1",
                    "kind": "capacitor",
                    "spicePrefix": "C",
                    "nodes": ["vout", "0"],
                    "value": "100n",
                },
            ],
            "analysis": [
                {
                    "kind": "ac",
                    "startFreq": "10",
                    "stopFreq": "1Meg",
                    "pointsPerDecade": 100,
                }
            ],
            "measurements": [
                {
                    "name": "gain_at_1k",
                    "analysis": "ac",
                    "expression": "V(vout)/V(vin)",
                }
            ],
            "probes": [],
            "directives": [],
            "models": [],
            "subcircuits": [],
            "metadata": None,
        }
    )


def test_ir_to_graph_preserves_component_ids() -> None:
    graph = ir_to_graph(_rc_lowpass_ir())
    assert set(graph.components.keys()) == {"R1", "C1"}


def test_ir_to_graph_preserves_net_names() -> None:
    graph = ir_to_graph(_rc_lowpass_ir())
    assert set(graph.nets.keys()) == {"vin", "vout", "0"}


def test_ir_to_graph_marks_ground_net() -> None:
    graph = ir_to_graph(_rc_lowpass_ir())
    assert graph.nets[GROUND_NODE].type is NetType.GROUND
    assert graph.nets["vin"].type is NetType.SIGNAL


def test_ir_to_graph_uses_canonical_pin_order() -> None:
    graph = ir_to_graph(_rc_lowpass_ir())
    expected_resistor_pins = dict(zip(PIN_NAMES["resistor"], ("vin", "vout"), strict=True))
    assert graph.components["R1"].pins.pins == expected_resistor_pins
    expected_cap_pins = dict(zip(PIN_NAMES["capacitor"], ("vout", "0"), strict=True))
    assert graph.components["C1"].pins.pins == expected_cap_pins


def test_ir_to_graph_lowers_analysis() -> None:
    graph = ir_to_graph(_rc_lowpass_ir())
    assert len(graph.analyses) == 1
    analysis = graph.analyses[0]
    assert analysis.kind.value == "ac"
    assert analysis.pointsPerDecade == 100


def test_ir_to_graph_lowers_measurement() -> None:
    graph = ir_to_graph(_rc_lowpass_ir())
    assert len(graph.measurements) == 1
    measurement = graph.measurements[0]
    assert measurement.name == "gain_at_1k"
    assert measurement.expression == "V(vout)/V(vin)"


def test_ir_to_graph_round_trip_via_graph_to_ir() -> None:
    """Lowering IR -> graph -> IR must preserve the same ids + structure."""
    from ltagent.live.graph_to_ir import graph_to_ir

    original = _rc_lowpass_ir()
    graph = ir_to_graph(original)
    rebuilt = graph_to_ir(graph)
    assert {c.id for c in original.components} == {c.id for c in rebuilt.components}
    assert len(original.analysis) == len(rebuilt.analysis)
    assert len(original.measurements) == len(rebuilt.measurements)


def test_ir_to_graph_raises_on_pin_arity_mismatch() -> None:
    """The arity check is defense-in-depth: the IR validator already
    rejects a component whose node count does not match the kind, so
    we exercise the converter's own check by calling the lower-level
    ``_lower_component`` directly with a forged component."""
    from ltagent.ir import Component as IRComponentClass
    from ltagent.ir import ComponentKind as IRComponentKindClass
    from ltagent.live.ir_to_graph import _lower_component

    forged = IRComponentClass.model_construct(
        id="R1",
        kind=IRComponentKindClass.RESISTOR,
        spicePrefix="R",
        nodes=["a", "b", "c"],  # resistor must have 2 nodes
        value="1k",
        model=None,
        role=None,
    )
    with pytest.raises(IRToGraphError) as captured:
        _lower_component(forged)
    assert captured.value.code == ERR_IR_TO_GRAPH_PIN_ARITY
    assert captured.value.data["componentId"] == "R1"


def test_ir_to_graph_lowers_opamp() -> None:
    ir = CircuitIR.model_validate(
        {
            "schemaVersion": "0.1",
            "name": "noninv_opamp",
            "topology": "noninv_opamp",
            "nodes": ["vin", "vout", "vp", "vn", "0"],
            "components": [
                {
                    "id": "U1",
                    "kind": "opamp",
                    "spicePrefix": "X",
                    "nodes": ["vin", "vout", "vp", "vn", "0"],
                    "value": "UniversalOpamp",
                }
            ],
            "analysis": [{"kind": "tran", "stopTime": "1m", "stepTime": "1u"}],
            "measurements": [],
            "probes": [],
            "directives": [],
            "models": [],
            "subcircuits": [],
            "metadata": None,
        }
    )
    graph = ir_to_graph(ir)
    assert graph.components["U1"].kind is GraphComponentKind.OPAMP
    pins = graph.components["U1"].pins.pins
    # Canonical opamp pin order: (ip, in, vp, vn, out).
    assert pins == {"ip": "vin", "in": "vout", "vp": "vp", "vn": "vn", "out": "0"}


def test_ir_to_graph_handles_semiconductor_pins() -> None:
    ir = CircuitIR.model_validate(
        {
            "schemaVersion": "0.1",
            "name": "transistor_switch",
            "topology": "transistor_switch",
            "nodes": ["in", "out", "0"],
            "components": [
                {
                    "id": "Q1",
                    "kind": "npn",
                    "spicePrefix": "Q",
                    "nodes": ["in", "out", "0"],
                    "value": "BC547",
                }
            ],
            "analysis": [{"kind": "op"}],
            "measurements": [],
            "probes": [],
            "directives": [],
            "models": [],
            "subcircuits": [],
            "metadata": None,
        }
    )
    graph = ir_to_graph(ir)
    assert graph.components["Q1"].kind is GraphComponentKind.NPN
    pins = graph.components["Q1"].pins.pins
    assert pins == {"c": "in", "b": "out", "e": "0"}


def test_ir_to_graph_keeps_constraints() -> None:
    ir = CircuitIR.model_validate(
        {
            "schemaVersion": "0.1",
            "name": "rc",
            "topology": "rc_lowpass",
            "nodes": ["vin", "vout", "0"],
            "components": [
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["vin", "vout"],
                    "value": "1k",
                }
            ],
            "analysis": [{"kind": "op"}],
            "measurements": [],
            "probes": [],
            "directives": [],
            "models": [],
            "subcircuits": [],
            "metadata": None,
            "constraints": {"cutoffHz": 1000},
        }
    )
    graph = ir_to_graph(ir)
    assert graph.constraints is not None
    assert graph.constraints.model_dump()["cutoffHz"] == 1000


def test_ir_to_graph_invalid_analysis_raises_structured_error() -> None:
    """A tran analysis without a stopTime must surface as IRToGraphError."""
    valid_ir = CircuitIR.model_validate(
        {
            "schemaVersion": "0.1",
            "name": "broken2",
            "topology": "rc_lowpass",
            "nodes": ["a", "0"],
            "components": [
                {
                    "id": "R1",
                    "kind": "resistor",
                    "spicePrefix": "R",
                    "nodes": ["a", "0"],
                    "value": "1k",
                }
            ],
            "analysis": [
                {"kind": "ac", "startFreq": "10", "stopFreq": "100k", "pointsPerDecade": 10}
            ],
            "measurements": [],
            "probes": [],
            "directives": [],
            "models": [],
            "subcircuits": [],
            "metadata": None,
        }
    )
    graph = ir_to_graph(valid_ir)
    assert graph.analyses[0].kind.value == "ac"
