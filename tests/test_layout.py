"""Unit tests for ``ltagent.layout`` (Phase 5 grid helpers).

These tests cover the placement primitives the ASC writer relies on.
No LTspice, Wine, or network is required.
"""

from __future__ import annotations

import pytest

from ltagent.layout import (
    CAPACITOR_A_OFFSET,
    CAPACITOR_B_OFFSET,
    GRID_X,
    GROUND_Y,
    INPUT_X,
    MAIN_Y,
    OUT_Y,
    RESISTOR_A_OFFSET,
    RESISTOR_B_OFFSET,
    SHEET_H,
    SHEET_W,
    VOLTAGE_MINUS_OFFSET,
    VOLTAGE_PLUS_OFFSET,
    Point,
    SymbolPlacement,
    capacitor_pins,
    minus_pin,
    pairwise,
    plus_pin,
    rect_overlaps,
    resistor_pins,
    symbol_bounding_box,
    wire_crosses,
    wire_length,
)


# --- constants ------------------------------------------------------------


def test_grid_constants_match_plan() -> None:
    """Plan section 12.2 lists the grid constants. The writer must
    keep them in sync with the layout checker, so we lock them here."""
    assert GRID_X == 160
    assert GROUND_Y == 352
    assert MAIN_Y == 160
    assert INPUT_X == 80
    assert SHEET_W == 880
    assert SHEET_H == 680
    assert OUT_Y == MAIN_Y + 80


def test_pin_offsets_match_asy_files() -> None:
    """Pin offsets are derived from the standard ``.asy`` files in
    ``lib/sym/``. If they ever change the schematic would visually
    disconnect from the wires we emit, so this test guards the
    reference values."""
    assert VOLTAGE_PLUS_OFFSET == (0, 16)
    assert VOLTAGE_MINUS_OFFSET == (0, 96)
    assert RESISTOR_A_OFFSET == (16, 16)
    assert RESISTOR_B_OFFSET == (16, 96)
    assert CAPACITOR_A_OFFSET == (16, 0)
    assert CAPACITOR_B_OFFSET == (16, 64)


# --- Point ---------------------------------------------------------------


def test_point_is_frozen_and_hashable() -> None:
    a = Point(3, 4)
    b = Point(3, 4)
    assert a == b
    assert hash(a) == hash(b)
    with pytest.raises(Exception):
        a.x = 99  # type: ignore[misc]


def test_point_translate_returns_new_point() -> None:
    a = Point(10, 20)
    b = a.translate(5, -3)
    assert b == Point(15, 17)
    assert a == Point(10, 20)  # original unchanged


# --- pin helpers ----------------------------------------------------------


def _placement(symbol_type: str, x: int, y: int) -> SymbolPlacement:
    return SymbolPlacement(
        symbol_type=symbol_type,
        anchor=Point(x, y),
        rotation="R0",
        inst_name="X1",
        value="1k",
    )


def test_plus_pin_returns_offset_position() -> None:
    p = _placement("voltage", 100, 200)
    assert plus_pin(p) == Point(100 + VOLTAGE_PLUS_OFFSET[0],
                                200 + VOLTAGE_PLUS_OFFSET[1])


def test_minus_pin_returns_offset_position() -> None:
    p = _placement("voltage", 100, 200)
    assert minus_pin(p) == Point(100 + VOLTAGE_MINUS_OFFSET[0],
                                 200 + VOLTAGE_MINUS_OFFSET[1])


def test_plus_pin_rejects_non_voltage() -> None:
    p = _placement("res", 0, 0)
    with pytest.raises(ValueError):
        plus_pin(p)


def test_resistor_pins_returns_a_and_b() -> None:
    p = _placement("res", 100, 200)
    a, b = resistor_pins(p)
    assert a == Point(116, 216)
    assert b == Point(116, 296)


def test_capacitor_pins_returns_a_and_b() -> None:
    p = _placement("cap", 100, 200)
    a, b = capacitor_pins(p)
    assert a == Point(116, 200)
    assert b == Point(116, 264)


# --- rect_overlaps -------------------------------------------------------


def test_rect_overlaps_detects_overlap() -> None:
    assert rect_overlaps(Point(0, 0), (10, 10), Point(5, 5), (10, 10)) is True


def test_rect_overlaps_detects_separation() -> None:
    assert rect_overlaps(Point(0, 0), (10, 10), Point(20, 20), (10, 10)) is False
    # Touching edges are NOT overlap (we use strict inequality).
    assert rect_overlaps(Point(0, 0), (10, 10), Point(10, 0), (10, 10)) is False


def test_rect_overlaps_respects_padding() -> None:
    # Without padding: separated.
    assert rect_overlaps(Point(0, 0), (10, 10), Point(12, 0), (10, 10)) is False
    # With padding=3: effectively the boxes expand; they overlap.
    assert rect_overlaps(Point(0, 0), (10, 10), Point(12, 0), (10, 10), padding=3) is True


# --- wire_crosses ---------------------------------------------------------


def test_wire_crosses_horizontal_horizontal_overlap() -> None:
    a = (Point(0, 0), Point(20, 0))
    b = (Point(5, 0), Point(15, 0))
    assert wire_crosses(a, b) is True


def test_wire_crosses_horizontal_horizontal_no_overlap() -> None:
    a = (Point(0, 0), Point(5, 0))
    b = (Point(10, 0), Point(20, 0))
    assert wire_crosses(a, b) is False


def test_wire_crosses_parallel_different_axis() -> None:
    a = (Point(0, 0), Point(10, 0))
    b = (Point(0, 5), Point(10, 5))
    assert wire_crosses(a, b) is False


def test_wire_crosses_h_v_overlap() -> None:
    a = (Point(0, 5), Point(20, 5))  # horizontal
    b = (Point(10, 0), Point(10, 10))  # vertical
    assert wire_crosses(a, b) is True


def test_wire_crosses_shared_endpoint_is_not_crossing() -> None:
    """An L-junction is a connection, not a crossing."""
    a = (Point(0, 0), Point(10, 0))
    b = (Point(10, 0), Point(10, 10))
    assert wire_crosses(a, b) is False


def test_wire_crosses_v_h_first_arg() -> None:
    """Order shouldn't matter; vertical first then horizontal still crosses."""
    a = (Point(10, 0), Point(10, 10))  # vertical
    b = (Point(0, 5), Point(20, 5))  # horizontal
    assert wire_crosses(a, b) is True


# --- wire_length ----------------------------------------------------------


def test_wire_length_manhattan() -> None:
    assert wire_length(Point(0, 0), Point(3, 4)) == 7
    assert wire_length(Point(10, 10), Point(10, 10)) == 0


# --- pairwise -------------------------------------------------------------


def test_pairwise_returns_all_unique_pairs() -> None:
    pairs = pairwise([1, 2, 3, 4])
    assert pairs == [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)]


def test_pairwise_empty_input() -> None:
    assert pairwise([]) == []


# --- symbol_bounding_box --------------------------------------------------


def test_symbol_bounding_box_voltage() -> None:
    p = _placement("voltage", 50, 50)
    anchor, size = symbol_bounding_box(p)
    assert anchor == Point(50, 50)
    assert size == (32, 80)


def test_symbol_bounding_box_resistor() -> None:
    p = _placement("res", 50, 50)
    anchor, size = symbol_bounding_box(p)
    assert anchor == Point(50, 50)
    assert size == (32, 80)


def test_symbol_bounding_box_capacitor() -> None:
    p = _placement("cap", 50, 50)
    anchor, size = symbol_bounding_box(p)
    assert anchor == Point(50, 50)
    assert size == (32, 64)
