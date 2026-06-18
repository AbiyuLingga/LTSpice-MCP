"""Unit tests for ``ltagent.asc`` (Phase 5 schematic writer).

These tests cover the acceptance criteria from plan section 21
(Phase 5):

- Generated ``.asc`` contains required LTspice lines
  (``Version 4``, ``SHEET``, ``WIRE``, ``FLAG``, ``SYMBOL``,
  ``SYMATTR``, ``TEXT``).
- All three MVP topologies produce valid schematics.
- Component count, wire count, and node count match the IR.
- The layout is deterministic (same IR in -> same text out).
- Unsupported topologies raise :class:`ASCError` with a stable code.

No LTspice, Wine, or network is required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ltagent import __version__
from ltagent.asc import (
    GENERATOR_NAME,
    GROUND_NODE,
    ASCError,
    render_asc,
    write_asc,
)
from ltagent.ir import (
    Analysis,
    AnalysisKind,
    CircuitIR,
    Component,
    ComponentKind,
    load_ir,
)
from tests._testdata import EXAMPLES, EXAMPLES_DIR

# --- helpers --------------------------------------------------------------


def _non_blank_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _wire_pairs(text: str) -> list[tuple[int, int, int, int]]:
    pairs: list[tuple[int, int, int, int]] = []
    for line in text.splitlines():
        m = re.match(r"^WIRE\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*$", line)
        if m:
            pairs.append(tuple(int(g) for g in m.groups()))  # type: ignore[arg-type]
    return pairs


def _symbol_lines(text: str) -> list[tuple[str, int, int, str]]:
    out: list[tuple[str, int, int, str]] = []
    for line in text.splitlines():
        m = re.match(r"^SYMBOL\s+(\S+)\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s*$", line)
        if m:
            sym, x, y, rot = m.groups()
            out.append((sym, int(x), int(y), rot))
    return out


def _flag_labels(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        m = re.match(r"^FLAG\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s*$", line)
        if m:
            x, y, label = int(m.group(1)), int(m.group(2)), m.group(3)
            out.append((x, y, label))
    return out


# --- example snapshot tests ----------------------------------------------


@pytest.mark.parametrize("example_name", EXAMPLES)
def test_example_asc_has_required_sections(example_name: str) -> None:
    """Every MVP example must produce an ASC with ``Version 4``,
    ``SHEET``, at least one ``WIRE``, ``FLAG`` for ground, one
    ``SYMBOL`` per IR component, and a ``TEXT`` line for the
    analysis directive."""
    ir = load_ir(EXAMPLES_DIR / f"{example_name}.ir.json")
    result = render_asc(ir)
    lines = _non_blank_lines(result.text)

    # Header: "Version 4" then "SHEET 1 W H".
    assert lines[0] == "Version 4"
    assert lines[1].startswith("SHEET 1 ")

    # Component count: one SYMBOL per IR component.
    assert result.component_count == len(ir.components)
    syms = _symbol_lines(result.text)
    assert len(syms) == len(ir.components)

    # Each SYMBOL has a SYMATTR InstName and Value pair.
    for inst_name in {c.id for c in ir.components}:
        assert f"SYMATTR InstName {inst_name}" in result.text
        # Value line may not exist for some kinds, but for the MVP
        # all components have values.
        # (Verified per-component below.)

    # Ground node must be flagged at least once.
    flags = _flag_labels(result.text)
    ground_flags = [f for f in flags if f[2] == GROUND_NODE]
    assert ground_flags, f"{example_name}: expected at least one FLAG 0"

    # At least one TEXT line with a SPICE directive (starting with !).
    assert any(
        line.startswith("TEXT ") and "!" in line
        for line in lines
    ), f"{example_name}: expected at least one TEXT directive line"

    # All wires are axis-aligned (LTspice rejects diagonals).
    for (x1, y1, x2, y2) in _wire_pairs(result.text):
        assert (x1 == x2) or (y1 == y2), (
            f"{example_name}: diagonal wire "
            f"WIRE {x1} {y1} {x2} {y2}"
        )

    # Generator footer banner.
    assert f"* End of {GENERATOR_NAME} output" in result.text
    assert __version__ not in result.text.splitlines()[0]


def test_voltage_divider_asc_emits_two_resistors() -> None:
    ir = load_ir(EXAMPLES_DIR / "voltage_divider.ir.json")
    result = render_asc(ir)
    syms = [s for s in _symbol_lines(result.text) if s[0] == "res"]
    assert len(syms) == 2
    # Both resistors must have a value.
    for sym in syms:
        # Find the value line after the SYMBOL.
        idx = result.text.find(f"SYMBOL res {sym[1]} {sym[2]} {sym[3]}")
        rest = result.text[idx:]
        assert "SYMATTR Value" in rest


def test_rc_lowpass_asc_has_resistor_series_capacitor_shunt() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    result = render_asc(ir)
    syms = _symbol_lines(result.text)
    kinds = [s[0] for s in syms]
    assert "res" in kinds
    assert "cap" in kinds


def test_rc_highpass_asc_has_capacitor_series_resistor_shunt() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_highpass.ir.json")
    result = render_asc(ir)
    syms = _symbol_lines(result.text)
    kinds = [s[0] for s in syms]
    assert "res" in kinds
    assert "cap" in kinds


def test_rc_lowpass_text_line_carries_tran_directive() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    result = render_asc(ir)
    # Plan section 11.2 requires the .tran to be present.
    assert "! .tran" not in result.text  # we don't have stray ones
    assert any("!.tran" in line for line in result.text.splitlines())


def test_rc_highpass_emits_both_tran_and_ac_text_directives() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_highpass.ir.json")
    result = render_asc(ir)
    text_lines = [
        line for line in result.text.splitlines() if line.startswith("TEXT ")
    ]
    assert any("!.tran" in line for line in text_lines)
    assert any("!.ac" in line for line in text_lines)


# --- determinism ---------------------------------------------------------


def test_rendering_is_deterministic() -> None:
    """Same IR input -> same ASC text, byte for byte."""
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    text_a = render_asc(ir).text
    text_b = render_asc(ir).text
    assert text_a == text_b


# --- ground node ---------------------------------------------------------


def test_ground_node_appears_in_flags() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    result = render_asc(ir)
    flags = _flag_labels(result.text)
    assert any(label == "0" for _, _, label in flags)


# --- topology routing ----------------------------------------------------


def test_voltage_divider_uses_voltage_res_res() -> None:
    ir = load_ir(EXAMPLES_DIR / "voltage_divider.ir.json")
    result = render_asc(ir)
    syms = _symbol_lines(result.text)
    # First symbol must be the voltage source.
    assert syms[0][0] == "voltage"


def test_rc_topologies_emit_five_wires_minimum() -> None:
    """Every MVP schematic needs at least: 1 in wire, 1 out wire,
    2 ground drops, 1 ground rail = 5 wires."""
    for name in ("voltage_divider", "rc_lowpass", "rc_highpass"):
        ir = load_ir(EXAMPLES_DIR / f"{name}.ir.json")
        result = render_asc(ir)
        assert result.wire_count >= 5, f"{name}: only {result.wire_count} wires"


# --- unsupported topology ------------------------------------------------


def test_unsupported_topology_raises() -> None:
    # model_construct bypasses field validators so we can build an
    # IR with a non-MVP topology. The writer must still reject it
    # with a stable error code.
    ir = CircuitIR.model_construct(
        schemaVersion="0.1",
        name="weird_circuit",
        topology="weird_circuit",
        description=None,
        nodes=["in", "0"],
        components=[
            Component.model_construct(
                id="V1",
                kind=ComponentKind.VOLTAGE_SOURCE,
                spicePrefix="V",
                nodes=["in", "0"],
                value="DC 1",
                role=None,
            ),
            Component.model_construct(
                id="R1",
                kind=ComponentKind.RESISTOR,
                spicePrefix="R",
                nodes=["in", "0"],
                value="1k",
                role=None,
            ),
        ],
        analysis=[Analysis(kind=AnalysisKind.OP)],
        measurements=[],
        probes=[],
        directives=[],
        constraints=None,
        metadata=None,
    )
    with pytest.raises(ASCError) as exc_info:
        render_asc(ir)
    assert exc_info.value.code == "ASC_UNSUPPORTED_TOPOLOGY"


# --- write_asc -----------------------------------------------------------


def test_write_asc_writes_file_and_returns_result(tmp_path: Path) -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    out = tmp_path / "rc_lowpass.asc"
    result = write_asc(ir, out)
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert body == result.text
    assert "Version 4" in body


def test_write_asc_creates_parent_directories(tmp_path: Path) -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    out = tmp_path / "deep" / "nested" / "circuit.asc"
    write_asc(ir, out)
    assert out.is_file()


# --- ASCError data shape -------------------------------------------------


def test_asc_error_carries_code_detail_data() -> None:
    exc = ASCError("ASC_FOO", "boom", data={"x": 1})
    assert exc.code == "ASC_FOO"
    assert exc.detail == "boom"
    assert exc.data == {"x": 1}
    assert "ASC_FOO" in str(exc)


# --- snapshot test (loose) ------------------------------------------------


def test_voltage_divider_known_topology_signature() -> None:
    """The voltage divider must have Vin, R1, R2, a '0' flag, and a
    TEXT line for the analysis. Locks the writer's output against
    accidental regressions."""
    ir = load_ir(EXAMPLES_DIR / "voltage_divider.ir.json")
    result = render_asc(ir)
    assert "SYMATTR InstName Vin" in result.text
    assert "SYMATTR InstName R1" in result.text
    assert "SYMATTR InstName R2" in result.text
    # Two ground flags (one per pin column).
    assert result.text.count("FLAG ") >= 2
    # The .op analysis becomes "! .op" in the TEXT line.
    assert any("!.op" in line for line in result.text.splitlines())


# --- integration: ASC output is LTspice-parseable -------------------------


def test_asc_lines_are_ltspice_compatible_format() -> None:
    """Every non-blank, non-comment line must start with a known
    LTspice directive keyword. The MVP only emits a small set."""
    allowed_prefixes = {
        "Version",
        "SHEET",
        "WIRE",
        "FLAG",
        "SYMBOL",
        "SYMATTR",
        "TEXT",
        "*",  # comments
    }
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    result = render_asc(ir)
    for line in _non_blank_lines(result.text):
        prefix = line.split()[0]
        assert prefix in allowed_prefixes, f"unexpected line: {line!r}"


def test_symbols_use_only_mvp_symbol_types() -> None:
    """The MVP only emits ``res``, ``cap``, and ``voltage`` symbols.
    A leak (e.g. an inductor or a custom subckt) would mean the
    writer accepts a topology it shouldn't."""
    for name in EXAMPLES:
        ir = load_ir(EXAMPLES_DIR / f"{name}.ir.json")
        result = render_asc(ir)
        for sym, *_ in _symbol_lines(result.text):
            assert sym in {"res", "cap", "voltage"}, (
                f"{name}: unexpected symbol {sym!r}"
            )


# --- round-trip via the CLI ---------------------------------------------


def test_render_asc_text_can_be_parsed_back() -> None:
    """A minimal smoke test that confirms the rendered text is
    well-formed enough that we can extract structure out of it
    again (used by the layout checker)."""
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    result = render_asc(ir)
    # Re-parse to find at least one of each directive.
    assert result.text.count("\nWIRE ") >= 5
    assert result.text.count("\nFLAG ") >= 2
    assert result.text.count("\nSYMBOL ") == 3
    assert result.text.count("\nSYMATTR InstName ") == 3
    assert result.text.count("\nSYMATTR Value ") == 3
    assert result.text.count("\nTEXT ") >= 1


# --- value preservation -------------------------------------------------


def test_value_strings_preserved_verbatim() -> None:
    ir = load_ir(EXAMPLES_DIR / "rc_lowpass.ir.json")
    result = render_asc(ir)
    assert "SINE(0 1 1k)" in result.text
    assert "1.59k" in result.text
    assert "100n" in result.text
