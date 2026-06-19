"""Roadmap Milestone 1 acceptance slices for simple analog circuits."""

from __future__ import annotations

from ltagent.asc import render_asc
from ltagent.ir import ComponentKind, load_ir
from ltagent.layout_checker import score_layout
from ltagent.netlist import render_netlist
from tests._testdata import EXAMPLES_DIR


def _led_ir():
    return load_ir(EXAMPLES_DIR / "led_resistor.ir.json")


def test_led_resistor_example_is_a_valid_circuit_ir() -> None:
    ir = _led_ir()

    assert ir.topology == "led_resistor"
    assert [component.kind for component in ir.components] == [
        ComponentKind.VOLTAGE_SOURCE,
        ComponentKind.RESISTOR,
        ComponentKind.DIODE,
    ]
    assert ir.metadata.model_dump()["safetyLevel"] == "simulation_only"


def test_led_resistor_netlist_is_deterministic_and_complete() -> None:
    ir = _led_ir()

    first = render_netlist(ir)
    second = render_netlist(ir)

    assert first.text == second.text
    assert "V1 vin 0 DC 5" in first.text
    assert "R1 vin led_anode 300" in first.text
    assert "D1 led_anode 0 LED_RED" in first.text
    assert ".model LED_RED D (IS=1e-20 N=2 RS=10 EG=2.0)" in first.text
    assert ".op" in first.text
    assert ".meas op LED_CURRENT I(D1)" in first.text


def test_led_resistor_schematic_is_deterministic_and_readable() -> None:
    ir = _led_ir()

    first = render_asc(ir)
    second = render_asc(ir)
    layout = score_layout(first)

    assert first.text == second.text
    assert first.component_count == 3
    assert "SYMBOL voltage" in first.text
    assert "SYMBOL res" in first.text
    assert "SYMBOL diode" in first.text
    assert layout.overlaps == 0
    assert layout.missing_ground is False
    assert layout.score >= 85
