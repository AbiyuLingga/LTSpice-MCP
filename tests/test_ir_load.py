"""Load and round-trip tests for valid Circuit IR examples.

Covers the acceptance criteria from plan section 21 (Phase 1):
- Valid examples load.
- Tests cover component arity, ground, duplicate IDs, unsupported
  topology (positive and negative).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltagent.ir import (
    SCHEMA_VERSION,
    AnalysisKind,
    CircuitIR,
    ComponentKind,
    dump_ir,
    load_ir,
)

from tests._testdata import EXAMPLES, EXAMPLES_DIR


@pytest.mark.parametrize("example_name", EXAMPLES)
def test_example_loads(example_name: str) -> None:
    path = EXAMPLES_DIR / f"{example_name}.ir.json"
    ir = load_ir(path)
    assert ir.schemaVersion == SCHEMA_VERSION
    assert ir.topology == example_name
    assert len(ir.components) >= 3
    assert len(ir.analysis) >= 1


def test_voltage_divider_structure() -> None:
    ir = load_ir(EXAMPLES_DIR / "voltage_divider.ir.json")
    assert ir.name == "voltage_divider_12v_to_5v"
    kinds = [c.kind for c in ir.components]
    assert kinds.count(ComponentKind.VOLTAGE_SOURCE) == 1
    assert kinds.count(ComponentKind.RESISTOR) == 2
    # Ground must be in nodes.
    assert "0" in ir.nodes
    # All components must reference known nodes.
    node_set = set(ir.nodes)
    for c in ir.components:
        for n in c.nodes:
            assert n in node_set


def test_rc_lowpass_has_tran() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    assert any(a.kind == AnalysisKind.TRAN for a in ir.analysis)
    tran = next(a for a in ir.analysis if a.kind == AnalysisKind.TRAN)
    assert tran.stopTime is not None


def test_rc_highpass_has_ac_and_tran() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_highpass.ir.json")
    kinds = {a.kind for a in ir.analysis}
    assert AnalysisKind.TRAN in kinds
    assert AnalysisKind.AC in kinds


def test_measurements_have_valid_analysis_kinds() -> None:
    for ex in EXAMPLES:
        ir = load_ir(EXAMPLES_DIR / f"{ex}.ir.json")
        available = {a.kind for a in ir.analysis}
        for m in ir.measurements:
            assert m.analysis in available, (
                f"{ex}: measurement {m.name} references {m.analysis} "
                f"which is not in analysis list"
            )


def test_round_trip_preserves_data() -> None:
    """Loading then dumping then reloading must yield the same logical IR."""
    for ex in EXAMPLES:
        path = EXAMPLES_DIR / f"{ex}.ir.json"
        ir1 = load_ir(path)
        dumped = dump_ir(ir1)
        ir2 = CircuitIR.model_validate(json.loads(dumped))
        assert ir1.model_dump() == ir2.model_dump()


def test_load_from_dict() -> None:
    """load_ir accepts a Python dict directly (no file)."""
    data = json.loads((EXAMPLES_DIR / "voltage_divider.ir.json").read_text())
    ir = load_ir(data)
    assert ir.name == "voltage_divider_12v_to_5v"


def test_dump_is_valid_json() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    dumped = dump_ir(ir)
    parsed = json.loads(dumped)  # must parse cleanly
    assert parsed["topology"] == "rc_lowpass"
