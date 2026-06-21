from __future__ import annotations

import pytest
from pydantic import ValidationError

from ltagent.digital_ir_v2 import DigitalDesignIRV2, render_verilog_v2


def _counter_payload() -> dict[str, object]:
    return {
        "schemaVersion": "2.0",
        "topModule": "counter_top",
        "ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "rst_n", "direction": "input", "width": 1},
            {"name": "q", "direction": "output", "width": 8},
        ],
        "signals": [],
        "instances": [{"id": "counter0", "kind": "counter", "parameters": {"width": 8}}],
        "connections": [
            {"instanceId": "counter0", "pin": "clk", "signal": "clk"},
            {"instanceId": "counter0", "pin": "reset", "signal": "rst_n"},
            {"instanceId": "counter0", "pin": "q", "signal": "q"},
        ],
        "clock": {"signal": "clk", "periodNs": 10},
        "reset": {"signal": "rst_n", "active": "low"},
        "testGoals": [{"name": "counts", "signal": "q", "expected": 3, "afterCycles": 5}],
    }


def test_v2_counter_renders_deterministic_verilog() -> None:
    design = DigitalDesignIRV2.model_validate(_counter_payload())

    source = render_verilog_v2(design)

    assert "module counter_top(" in source
    assert "output reg [7:0] q" in source
    assert "always @(posedge clk)" in source
    assert '$dumpfile("waveform.vcd")' in source
    assert "$fatal(1" in source


def test_v2_rejects_raw_hdl_body() -> None:
    payload = _counter_payload()
    payload["body"] = 'initial $system("sh");'

    with pytest.raises(ValidationError):
        DigitalDesignIRV2.model_validate(payload)


def test_v2_rejects_connection_to_unknown_signal() -> None:
    payload = _counter_payload()
    payload["connections"] = [{"instanceId": "counter0", "pin": "clk", "signal": "missing"}]

    with pytest.raises(ValidationError):
        DigitalDesignIRV2.model_validate(payload)
