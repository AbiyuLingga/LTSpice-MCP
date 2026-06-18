"""Phase 5: Layout constants and coordinate helpers for the ``.asc`` writer.

This module owns the grid that the deterministic schematic writer uses.
The agent may not write production coordinate lines (AGENTS.md hard rule
1). Every placement decision in :mod:`ltagent.asc` goes through helpers
in this module so the layout is reviewable, testable, and consistent
across the three MVP topologies.

Coordinate system
-----------------
LTspice schematics use a fixed integer grid. Coordinates are in
1/100-inch-like units (the exact scale is not documented, but
adjacent integer coordinates are visually adjacent). All helpers in
this module return ``Point`` tuples in this integer space.

We pick a single orientation for the MVP (R0, vertical) and a single
signal-flow direction (left-to-right) to keep the layout easy to read
and the layout checker easy to reason about. The grid is wider than
tall so series components sit comfortably next to each other.

Grid constants (from plan section 12.2)
---------------------------------------
``GRID_X``         spacing between adjacent series components
``GRID_Y``         spacing between horizontal levels
``MAIN_Y``         y of the main horizontal signal line
``GROUND_Y``       y of the ground rail
``INPUT_X``        x of the input source
``SHEET_W`` / ``SHEET_H``  total sheet dimensions

Pin positions
-------------
For the standard LTspice symbols (``voltage``, ``res``, ``cap``) the
pin locations depend on the symbol's internal coordinate system (see
the ``.asy`` files in ``lib/sym/``). We treat the SYMBOL line anchor
as the top-left of the symbol's bounding box, then offset to the pin
positions:

* ``voltage`` R0: + pin at ``(x, y + 16)``, - pin at ``(x, y + 96)``
* ``res`` R0:     pin A at ``(x + 16, y + 16)``, pin B at ``(x + 16, y + 96)``
* ``cap`` R0:     pin A at ``(x + 16, y)``, pin B at ``(x + 16, y + 64)``

The ASC writer uses these positions to place wire endpoints so they
land on the visible pins. The exact pixel coordinates chosen for the
MVP layouts are documented in :mod:`ltagent.asc`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, TypeVar

# --- grid constants -------------------------------------------------------

GRID_X: Final[int] = 160
"""Horizontal grid spacing between series components (plan 12.2)."""

GRID_Y: Final[int] = 96
"""Vertical grid spacing between adjacent levels (plan 12.2)."""

MAIN_Y: Final[int] = 160
"""Y of the main horizontal signal line that the source and series
elements sit on (plan 12.2)."""

GROUND_Y: Final[int] = 352
"""Y of the ground rail. 192 units below MAIN_Y keeps ground labels
well separated from the signal line (plan 12.2)."""

OUT_Y: Final[int] = MAIN_Y + 80
"""Y of the ``out`` line that joins the series element's bottom
pin to the shunt element's top pin. Equal to ``MAIN_Y + 80`` so
both pins land on the same row when both use resistor-style pin
spacing (anchor.y + 16 = pin A y, pin B y = pin A y + 80)."""

INPUT_X: Final[int] = 80
"""X of the input voltage source (plan 12.2)."""

SHEET_W: Final[int] = 880
"""Total schematic width in LTspice grid units. Matches the default
``SHEET 1 880 680`` size used in official LTspice examples."""

SHEET_H: Final[int] = 680
"""Total schematic height in LTspice grid units."""

# --- pin offsets ----------------------------------------------------------
#
# These are derived from the standard symbol ``.asy`` files in
# ``lib/sym/``. Do not change them without re-checking the pin
# landing on real symbols.
#
# The SYMBOL line anchor (X, Y) in the .asc file is the position of
# the symbol's internal origin (0, 0). Pins are at fixed offsets
# from that origin.

VOLTAGE_PLUS_OFFSET: Final[tuple[int, int]] = (0, 16)
"""Offset of the + pin from the voltage source anchor (R0)."""

VOLTAGE_MINUS_OFFSET: Final[tuple[int, int]] = (0, 96)
"""Offset of the - pin from the voltage source anchor (R0)."""

RESISTOR_A_OFFSET: Final[tuple[int, int]] = (16, 16)
"""Offset of pin A (top) from a resistor anchor (R0)."""

RESISTOR_B_OFFSET: Final[tuple[int, int]] = (16, 96)
"""Offset of pin B (bottom) from a resistor anchor (R0)."""

CAPACITOR_A_OFFSET: Final[tuple[int, int]] = (16, 0)
"""Offset of pin A (top) from a capacitor anchor (R0)."""

CAPACITOR_B_OFFSET: Final[tuple[int, int]] = (16, 64)
"""Offset of pin B (bottom) from a capacitor anchor (R0)."""

SYMBOL_BODY_OVERLAP_PADDING: Final[int] = 8
"""Minimum clearance (grid units) between two symbols' bounding
boxes. Used by the layout checker to detect overlaps."""


# --- types ---------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    """An integer (x, y) point on the schematic grid.

    Frozen so placements are hashable and shareable. ``x`` and ``y``
    are intentionally ``int`` because LTspice rejects fractional
    coordinates in the ``.asc`` format.
    """

    x: int
    y: int

    def translate(self, dx: int, dy: int) -> Point:
        """Return a new point shifted by ``(dx, dy)`` units."""
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True)
class SymbolPlacement:
    """Where a single symbol sits on the schematic.

    Attributes:
        symbol_type:  LTspice symbol name as it appears in ``SYMBOL``,
            e.g. ``"res"``, ``"cap"``, ``"voltage"``.
        anchor:       Top-left of the symbol's bounding box.
        rotation:     ``"R0"``, ``"R90"``, ``"R180"``, ``"R270"``,
            ``"M0"`` etc. The MVP uses ``R0`` for everything.
        inst_name:    Component instance name, e.g. ``"R1"``.
        value:        Component value string, e.g. ``"1k"`` or
            ``"SINE(0 1 1k)"``. The writer is verbatim; the agent
            has already validated this through ``CircuitIR``.
    """

    symbol_type: str
    anchor: Point
    rotation: str
    inst_name: str
    value: str

    def pin(self, offset: tuple[int, int]) -> Point:
        """Return the absolute pin position for a symbol-relative offset.

        The ``.asc`` writer needs the *absolute* pin coordinates to
        place wire endpoints, so this is the only sanctioned way to
        read pin locations.
        """
        dx, dy = offset
        return Point(self.anchor.x + dx, self.anchor.y + dy)


# --- helpers -------------------------------------------------------------


def plus_pin(placement: SymbolPlacement) -> Point:
    """Return the + pin of a voltage source placement.

    Raises:
        ValueError: if the placement is not a voltage source.
    """
    if placement.symbol_type != "voltage":
        raise ValueError(
            f"plus_pin() requires a voltage source, got {placement.symbol_type!r}"
        )
    return placement.pin(VOLTAGE_PLUS_OFFSET)


def minus_pin(placement: SymbolPlacement) -> Point:
    """Return the - pin of a voltage source placement."""
    if placement.symbol_type != "voltage":
        raise ValueError(
            f"minus_pin() requires a voltage source, got {placement.symbol_type!r}"
        )
    return placement.pin(VOLTAGE_MINUS_OFFSET)


def resistor_pins(placement: SymbolPlacement) -> tuple[Point, Point]:
    """Return ``(pin_a, pin_b)`` of a resistor placement."""
    if placement.symbol_type != "res":
        raise ValueError(
            f"resistor_pins() requires a resistor, got {placement.symbol_type!r}"
        )
    return placement.pin(RESISTOR_A_OFFSET), placement.pin(RESISTOR_B_OFFSET)


def capacitor_pins(placement: SymbolPlacement) -> tuple[Point, Point]:
    """Return ``(pin_a, pin_b)`` of a capacitor placement."""
    if placement.symbol_type != "cap":
        raise ValueError(
            f"capacitor_pins() requires a capacitor, got {placement.symbol_type!r}"
        )
    return placement.pin(CAPACITOR_A_OFFSET), placement.pin(CAPACITOR_B_OFFSET)


def rect_overlaps(
    a_anchor: Point,
    a_size: tuple[int, int],
    b_anchor: Point,
    b_size: tuple[int, int],
    *,
    padding: int = 0,
) -> bool:
    """True if the two rectangles overlap (with optional padding).

    Both rectangles are axis-aligned, defined by their top-left
    anchor and ``(width, height)``. Used by the layout checker to
    detect symbol-on-symbol overlap.
    """
    ax1, ay1 = a_anchor.x, a_anchor.y
    ax2, ay2 = ax1 + a_size[0], ay1 + a_size[1]
    bx1, by1 = b_anchor.x, b_anchor.y
    bx2, by2 = bx1 + b_size[0], by1 + b_size[1]
    return not (
        ax2 + padding <= bx1
        or bx2 + padding <= ax1
        or ay2 + padding <= by1
        or by2 + padding <= ay1
    )


def symbol_bounding_box(placement: SymbolPlacement) -> tuple[Point, tuple[int, int]]:
    """Return ``(top_left, (width, height))`` for a symbol placement.

    The bounding box is the symbol's visible extent. For R0 the
    standard ``res``/``cap``/``voltage`` symbols all fit in a 32
    unit-wide column, but we use the actual pin offsets to derive
    the height per symbol type for accuracy.
    """
    if placement.symbol_type == "voltage":
        width = 32
        # Pins at (0, 16) and (0, 96); bounding box covers y=16 to y=96.
        return placement.anchor, (width, 96 - 16)
    if placement.symbol_type == "res":
        width = 32
        # Pins at (16, 16) and (16, 96); visible body y=16 to y=96.
        return placement.anchor, (width, 96 - 16)
    if placement.symbol_type == "cap":
        width = 32
        # Pins at (16, 0) and (16, 64); visible body y=0 to y=64.
        return placement.anchor, (width, 64)
    # Default conservative box for unknown symbol types.
    return placement.anchor, (32, 96)


def wire_length(p1: Point, p2: Point) -> int:
    """Return the Manhattan length of a straight wire between two points.

    The ASC format only stores straight wires; bends are encoded as
    multiple ``WIRE`` lines. The checker uses this to flag wires
    longer than the configured limit.
    """
    return abs(p1.x - p2.x) + abs(p1.y - p2.y)


def wire_crosses(a: tuple[Point, Point], b: tuple[Point, Point]) -> bool:
    """True if two axis-aligned wires cross at an interior point.

    "Interior" means neither endpoint of ``a`` touches ``b``; a
    shared endpoint is a junction, not a crossing, and is allowed.
    The MVP only ever emits orthogonal wires so a simple segment
    intersection test is sufficient.
    """
    (a1, a2), (b1, b2) = a, b
    # If both wires are horizontal, they cross only if their y matches
    # and their x ranges overlap strictly.
    if a1.y == a2.y and b1.y == b2.y:
        if a1.y != b1.y:
            return False
        ax_lo, ax_hi = sorted((a1.x, a2.x))
        bx_lo, bx_hi = sorted((b1.x, b2.x))
        return ax_hi > bx_lo and ax_lo < bx_hi and bx_hi > ax_lo and bx_lo < ax_hi
    # If both wires are vertical, the analogous test.
    if a1.x == a2.x and b1.x == b2.x:
        if a1.x != b1.x:
            return False
        ay_lo, ay_hi = sorted((a1.y, a2.y))
        by_lo, by_hi = sorted((b1.y, b2.y))
        return ay_hi > by_lo and ay_lo < by_hi and by_hi > ay_lo and by_lo < ay_hi
    # One horizontal, one vertical. They cross if the vertical's x is
    # within the horizontal's x range AND the horizontal's y is within
    # the vertical's y range, both strictly (not at an endpoint).
    if a1.y == a2.y:  # a horizontal
        hx_lo, hx_hi = sorted((a1.x, a2.x))
        vy_lo, vy_hi = sorted((b1.y, b2.y))
        return (
            hx_lo < b1.x < hx_hi
            and vy_lo < a1.y < vy_hi
        )
    # b horizontal, a vertical.
    hx_lo, hx_hi = sorted((b1.x, b2.x))
    vy_lo, vy_hi = sorted((a1.y, a2.y))
    return hx_lo < a1.x < hx_hi and vy_lo < b1.y < vy_hi


_T = TypeVar("_T")


def pairwise(items: Iterable[_T]) -> list[tuple[_T, _T]]:
    """Return all unordered pairs from ``items`` (i < j).

    Generic over the element type so the layout checker can
    iterate over typed placements and wires without losing the
    inner element's type.
    """
    out: list[tuple[_T, _T]] = []
    arr = list(items)
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            out.append((arr[i], arr[j]))
    return out


__all__ = [
    "CAPACITOR_A_OFFSET",
    "CAPACITOR_B_OFFSET",
    "GRID_X",
    "GRID_Y",
    "GROUND_Y",
    "INPUT_X",
    "MAIN_Y",
    "OUT_Y",
    "RESISTOR_A_OFFSET",
    "RESISTOR_B_OFFSET",
    "SHEET_H",
    "SHEET_W",
    "SYMBOL_BODY_OVERLAP_PADDING",
    "VOLTAGE_MINUS_OFFSET",
    "VOLTAGE_PLUS_OFFSET",
    "Point",
    "SymbolPlacement",
    "capacitor_pins",
    "minus_pin",
    "pairwise",
    "plus_pin",
    "rect_overlaps",
    "resistor_pins",
    "symbol_bounding_box",
    "wire_crosses",
    "wire_length",
]
