from __future__ import annotations

from ltagent.live.graph import graph_from_dict
from ltagent.live.graph_to_ir import graph_to_ir
from ltagent.netlist import render_netlist


def test_rc_graph_converts_to_valid_ir_and_netlist() -> None:
    graph = graph_from_dict(
        {
            "schemaVersion": "0.2",
            "projectId": "rc1k",
            "domain": "analog",
            "topology": "rc_lowpass",
            "components": {
                "Vin": {
                    "id": "Vin",
                    "kind": "voltage_source",
                    "value": "AC 1",
                    "pins": {"pins": {"+": "in", "-": "0"}},
                },
                "R1": {
                    "id": "R1",
                    "kind": "resistor",
                    "value": "1.6k",
                    "pins": {"pins": {"1": "in", "2": "out"}},
                },
                "C1": {
                    "id": "C1",
                    "kind": "capacitor",
                    "value": "100n",
                    "pins": {"pins": {"1": "out", "2": "0"}},
                },
            },
            "nets": {
                "0": {"name": "0", "type": "ground"},
                "in": {"name": "in", "type": "signal"},
                "out": {"name": "out", "type": "signal"},
            },
            "analyses": [
                {
                    "kind": "ac",
                    "startFreq": "10",
                    "stopFreq": "100k",
                    "pointsPerDecade": 100,
                }
            ],
            "measurements": [],
            "directives": [],
            "constraints": {"targetCutoffHz": 1000},
            "layoutHints": {"flow": "left_to_right", "inputNode": "in", "outputNode": "out"},
        }
    )

    ir = graph_to_ir(graph)
    rendered = render_netlist(ir)

    assert ir.name == "rc1k"
    assert [component.id for component in ir.components] == ["Vin", "R1", "C1"]
    assert "R1 in out 1.6k" in rendered.text
    assert ".ac dec 100 10 100k" in rendered.text


def test_graph_to_ir_rejects_directive_arguments() -> None:
    graph = graph_from_dict(
        {
            "schemaVersion": "0.2",
            "projectId": "demo",
            "topology": "rc_lowpass",
            "components": {},
            "nets": {"0": {"name": "0", "type": "ground"}},
            "analyses": [],
            "directives": [{"name": ".param", "args": "x=1"}],
        }
    )

    try:
        graph_to_ir(graph)
    except ValueError as exc:
        assert "directive arguments" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("graph_to_ir accepted raw directive arguments")
