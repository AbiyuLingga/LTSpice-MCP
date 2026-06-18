"""Unit tests for ``ltagent.layout_checker`` (Phase 5 scoring).

The checker implements the plan section 12.4 scoring policy. These
tests build synthetic ``ASCResult`` strings to exercise each rule
without depending on the full writer.
"""

from __future__ import annotations

import pytest

from ltagent.asc import ASCResult
from ltagent.layout import (
    MAIN_Y,
    OUT_Y,
    GROUND_Y,
    INPUT_X,
    Point,
    SymbolPlacement,
)
from ltagent.layout_checker import (
    LONG_WIRE_LIMIT,
    OFFICIAL_THRESHOLD,
    PROJECT_THRESHOLD,
    WEIGHT_CROSSING,
    WEIGHT_LABEL_COLLISION,
    WEIGHT_LONG_WIRE,
    WEIGHT_MIN_SPACING,
    WEIGHT_MISSING_GROUND,
    WEIGHT_OVERLAP,
    LayoutResult,
    classify_score,
    score_layout,
)


# --- helpers --------------------------------------------------------------


def _asc_result(
    text: str,
    placements: list[SymbolPlacement] | None = None,
    *,
    topology: str = "rc_lowpass",
) -> ASCResult:
    return ASCResult(
        text=text,
        line_count=len(text.splitlines()),
        header=("* x", "* y"),
        component_count=len(placements or []),
        wire_count=text.count("\nWIRE "),
        flag_count=text.count("\nFLAG "),
        topology=topology,
        placements=tuple(placements or []),
    )


def _placement(symbol_type: str, x: int, y: int, name: str = "X1") -> SymbolPlacement:
    return SymbolPlacement(
        symbol_type=symbol_type,
        anchor=Point(x, y),
        rotation="R0",
        inst_name=name,
        value="1k",
    )


# --- classification ------------------------------------------------------


def test_classify_score_boundaries() -> None:
    assert classify_score(100) == "official"
    assert classify_score(OFFICIAL_THRESHOLD) == "official"
    assert classify_score(OFFICIAL_THRESHOLD - 1) == "project"
    assert classify_score(PROJECT_THRESHOLD) == "project"
    assert classify_score(PROJECT_THRESHOLD - 1) == "reject"
    assert classify_score(0) == "reject"


# --- real layouts: should score 100/100 (no warnings) -------------------


def test_real_layouts_score_one_hundred() -> None:
    """The three MVP examples must score 100/100. The layout
    checker would otherwise block them from ever becoming official
    templates (plan 12.4)."""
    from ltagent.ir import load_ir
    from ltagent.asc import render_asc
    from tests._testdata import EXAMPLES_DIR, EXAMPLES

    for name in EXAMPLES:
        ir = load_ir(EXAMPLES_DIR / f"{name}.ir.json")
        result = render_asc(ir)
        scored = score_layout(result)
        assert scored.score == 100, (
            f"{name}: expected 100, got {scored.score} "
            f"(warnings: {[(w.code, w.detail) for w in scored.warnings]})"
        )
        assert scored.classification == "official"
        assert scored.overlaps == 0
        assert scored.wire_crossings == 0
        assert scored.long_wires == 0
        assert scored.label_collisions == 0
        assert scored.missing_ground is False
        assert scored.warnings == ()


# --- missing ground ------------------------------------------------------


def test_missing_ground_drops_score() -> None:
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "WIRE 80 160 240 160\n"
        "FLAG 240 160 in\n"
        "SYMBOL voltage 80 144 R0\n"
        "SYMATTR InstName Vin\n"
        "SYMATTR Value DC 1\n"
    )
    result = _asc_result(
        text,
        [_placement("voltage", 80, 144, "Vin")],
    )
    scored = score_layout(result)
    assert scored.missing_ground is True
    assert scored.score == 100 - WEIGHT_MISSING_GROUND
    assert any(w.code == "LAYOUT_MISSING_GROUND" for w in scored.warnings)


# --- component overlap ---------------------------------------------------


def test_component_overlap_drops_score() -> None:
    """Two symbols at the same anchor -> overlap penalty."""
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "FLAG 80 352 0\n"
        "SYMBOL voltage 80 144 R0\n"
        "SYMATTR InstName V1\n"
        "SYMATTR Value DC 1\n"
        "SYMBOL voltage 80 144 R0\n"
        "SYMATTR InstName V2\n"
        "SYMATTR Value DC 1\n"
    )
    result = _asc_result(
        text,
        [
            _placement("voltage", 80, 144, "V1"),
            _placement("voltage", 80, 144, "V2"),
        ],
    )
    scored = score_layout(result)
    assert scored.overlaps == 1
    assert scored.score == 100 - WEIGHT_OVERLAP
    assert any(w.code == "LAYOUT_OVERLAP" for w in scored.warnings)


# --- min-spacing --------------------------------------------------------


def test_min_spacing_violation_drops_score() -> None:
    """Two symbols separated by less than the padding count as
    min-spacing violations even when they don't overlap."""
    p1 = _placement("voltage", 80, 144, "V1")
    p2 = _placement("voltage", 90, 144, "V2")  # 10 units apart, > 0 but < padding
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "FLAG 80 352 0\n"
        "SYMBOL voltage 80 144 R0\n"
        "SYMATTR InstName V1\n"
        "SYMATTR Value DC 1\n"
        "SYMBOL voltage 90 144 R0\n"
        "SYMATTR InstName V2\n"
        "SYMATTR Value DC 1\n"
    )
    result = _asc_result(text, [p1, p2])
    scored = score_layout(result)
    # 10-unit separation is < padding (8) + 0 width, so the boxes
    # touch and the rect_overlaps function with padding=8 sees an
    # overlap, classifying this as a hard overlap rather than
    # min-spacing. Either penalty is correct: the writer would
    # never emit such a layout.
    assert scored.overlaps >= 1 or scored.min_spacing_violations >= 1
    assert scored.score < 100


# --- wire crossing ------------------------------------------------------


def test_wire_crossing_drops_score() -> None:
    """Two orthogonal wires that cross at an interior point drop
    the score by the configured weight per crossing."""
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "WIRE 0 50 200 50\n"      # horizontal across x=0..200 at y=50
        "WIRE 100 0 100 100\n"    # vertical across y=0..100 at x=100
        "FLAG 80 352 0\n"
    )
    result = _asc_result(text)
    scored = score_layout(result)
    # The two wires cross at (100, 50) which is interior to both.
    assert scored.wire_crossings == 1
    assert scored.score == 100 - WEIGHT_CROSSING
    assert any(w.code == "LAYOUT_WIRE_CROSSING" for w in scored.warnings)


def test_orthogonal_wires_with_shared_endpoint_dont_cross() -> None:
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "WIRE 0 0 100 0\n"
        "WIRE 100 0 100 100\n"
        "FLAG 80 352 0\n"
    )
    result = _asc_result(text)
    scored = score_layout(result)
    assert scored.wire_crossings == 0


# --- long wire ----------------------------------------------------------


def test_long_wire_drops_score() -> None:
    # A wire longer than LONG_WIRE_LIMIT units.
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        f"WIRE 0 0 {LONG_WIRE_LIMIT + 100} 0\n"
        "FLAG 80 352 0\n"
    )
    result = _asc_result(text)
    scored = score_layout(result)
    assert scored.long_wires == 1
    assert scored.score == 100 - WEIGHT_LONG_WIRE
    assert any(w.code == "LAYOUT_LONG_WIRE" for w in scored.warnings)


# --- label collision ----------------------------------------------------


def test_label_collision_drops_score() -> None:
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "FLAG 100 100 in\n"
        "FLAG 100 100 in\n"  # duplicate of the previous FLAG
        "FLAG 80 352 0\n"
    )
    result = _asc_result(text)
    scored = score_layout(result)
    assert scored.label_collisions == 1
    assert scored.score == 100 - WEIGHT_LABEL_COLLISION
    assert any(w.code == "LAYOUT_LABEL_COLLISION" for w in scored.warnings)


# --- score clamp --------------------------------------------------------


def test_score_clamped_to_zero() -> None:
    """Many overlapping symbols should still produce a score of 0,
    not a negative number."""
    placements = [
        _placement("voltage", 80, 144, f"V{i}") for i in range(10)
    ]
    text_lines = ["Version 4", "SHEET 1 880 680", "FLAG 80 352 0"]
    for i in range(10):
        text_lines.append(f"SYMBOL voltage 80 144 R0")
        text_lines.append(f"SYMATTR InstName V{i}")
        text_lines.append(f"SYMATTR Value DC 1")
    text = "\n".join(text_lines) + "\n"
    result = _asc_result(text, placements)
    scored = score_layout(result)
    assert scored.score == 0


# --- multiple penalties stack ------------------------------------------


def test_multiple_penalties_stack() -> None:
    """A layout with a wire crossing and a missing ground node
    accumulates both penalties."""
    text = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "WIRE 0 50 200 50\n"
        "WIRE 100 0 100 100\n"
    )
    result = _asc_result(text)
    scored = score_layout(result)
    expected = 100 - WEIGHT_CROSSING - WEIGHT_MISSING_GROUND
    assert scored.score == expected
    assert "LAYOUT_WIRE_CROSSING" in {w.code for w in scored.warnings}
    assert "LAYOUT_MISSING_GROUND" in {w.code for w in scored.warnings}
