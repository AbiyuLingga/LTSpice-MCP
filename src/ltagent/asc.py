"""Phase 5: Generate ``.asc`` schematics from validated Circuit IR.

This module turns a :class:`~ltagent.ir.CircuitIR` into a syntactically
valid LTspice ``.asc`` schematic for the three MVP topologies
(``voltage_divider``, ``rc_lowpass``, ``rc_highpass``).

Design rules (from ``docs/PROJECT_PLAN.md`` sections 12 and 18):

* The layout is **deterministic**. The agent may not write production
  coordinate lines; Python owns the final placement. See AGENTS.md
  hard rule 1.
* Only the three MVP topologies are supported. Other topologies get a
  structured :class:`ASCError` with code ``ASC_UNSUPPORTED_TOPOLOGY``.
* Each generated schematic includes the required LTspice headers
  (``Version 4``, ``SHEET``), at least one ``WIRE``, ``FLAG`` for the
  ground node (``0``), one ``SYMBOL``/``SYMATTR`` pair per IR
  component, and a ``TEXT`` line with the analysis directive.
* Component values are emitted verbatim. The IR validator already
  enforced the safety policy on the value string; we do not
  re-validate.
* The writer never executes LTspice, never reads the filesystem
  beyond the single :func:`write_asc` helper, and never imports an
  IR model that wasn't already validated by :func:`ltagent.ir.load_ir`.

Coordinate convention
---------------------
All symbols are placed in R0 (native vertical) orientation. Series
components sit on the main horizontal signal line at ``MAIN_Y``;
ground rails are at ``GROUND_Y``. Wire endpoints land on the actual
pin positions documented in :mod:`ltagent.layout`. The MVP layout
guarantees zero wire crossings and one ground node, so a clean
schematic scores 100/100 on the layout checker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .ir import (
    Analysis,
    AnalysisKind,
    CircuitIR,
    Component,
    ComponentKind,
)
from .layout import (
    GRID_X,
    GRID_Y,
    GROUND_Y,
    INPUT_X,
    MAIN_Y,
    OUT_Y,
    SHEET_H,
    SHEET_W,
    Point,
    SymbolPlacement,
    bjt_pins,
    capacitor_pins,
    diode_pins,
    minus_pin,
    opamp_pins,
    plus_pin,
    resistor_pins,
)

GENERATOR_NAME = "ltspice-ai-agent"
"""Same name as the netlist generator, so files round-trip
consistently in :file:`result.json`."""

GROUND_NODE = "0"
"""The SPICE ground node. Required by the IR; the writer emits a
``FLAG 0`` for it on every supported topology."""


# --- error type -----------------------------------------------------------


class ASCError(Exception):
    """Raised when a Circuit IR cannot be turned into a ``.asc``.

    The error carries a stable ``code`` so the CLI layer can map it
    to the JSON output contract without relying on exception
    messages. Mirrors :class:`ltagent.netlist.NetlistError`.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        data: Mapping[str, Any] | None = None,
    ):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.data: dict[str, Any] = dict(data) if data else {}


# --- result type ----------------------------------------------------------


@dataclass(frozen=True)
class ASCResult:
    """The output of a successful ``.asc`` generation.

    Attributes:
        text: Full ``.asc`` body terminated with a single ``\\n``.
        line_count: Number of non-empty lines in ``text``.
        header: The two-line generator header that the writer emits
            at the top of the schematic. Mirrors the
            :class:`~ltagent.netlist.NetlistResult` contract so the
            CLI layer can format it identically.
        component_count: Number of ``SYMBOL`` blocks (one per IR
            component).
        wire_count: Number of ``WIRE`` lines.
        flag_count: Number of ``FLAG`` lines.
        topology: Topology that was rendered. Useful for tests and
            the CLI JSON.
        placements: List of symbol placements the writer produced, in
            the same order as the IR components. Lets the layout
            checker reason about overlap and routing.
        node_points: Mapping of node name -> last ``Point`` where the
            node was placed in the schematic. The layout checker uses
            this to validate ground presence and node labelling.
    """

    text: str
    line_count: int
    header: tuple[str, str]
    component_count: int
    wire_count: int
    flag_count: int
    topology: str
    placements: tuple[SymbolPlacement, ...]
    node_points: dict[str, Point] = field(default_factory=dict)


# --- component lookup -----------------------------------------------------


def _by_kind(components: Sequence[Component], kind: ComponentKind) -> Component:
    """Return the single component of a given kind.

    The MVP topologies each have exactly one source and either one or
    two resistors/capacitors. The layouts below assume the "main"
    resistor/capacitor is the series element and the second one (if
    present) is the shunt. This helper raises if the topology is
    not as expected.

    Raises:
        ASCError: with code ``ASC_MISSING_COMPONENT`` or
            ``ASC_DUPLICATE_COMPONENT`` if the topology is
            malformed.
    """
    matches = [c for c in components if c.kind == kind]
    if not matches:
        raise ASCError(
            "ASC_MISSING_COMPONENT",
            f"topology requires a {kind.value!r} component but IR has none",
            data={"kind": kind.value},
        )
    if len(matches) > 1 and kind == ComponentKind.VOLTAGE_SOURCE:
        # Only the voltage source is unique; resistors/capacitors can
        # appear in pairs.
        raise ASCError(
            "ASC_DUPLICATE_COMPONENT",
            f"topology has multiple {kind.value!r} components; MVP supports exactly one source",
            data={"kind": kind.value, "count": len(matches)},
        )
    return matches[0]


# --- placements per topology ---------------------------------------------


def _place_voltage_divider(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place components for ``voltage_divider``.

    Layout (signal flow left to right, all R0):

    * ``Vin`` (voltage source) on the left.
    * ``R_series`` first, sitting on the main signal line.
    * ``R_shunt`` second, shifted down so its top pin is on the
      ``out`` line (not on the ``in`` line) and its bottom pin sits
      above the ground rail.

    The two resistor pin As live on different y rows so the ``in``
    and ``out`` nets never get bridged by a shared horizontal
    wire. The visible schematic looks like a step-shape:

    ::

        Vin+ ----- R1A -- (in) ----- (out) -- R2A
                                         |
                                         R
                                         |
        Vin- ----- (0) ---------------- (0) ---- (0) GND
    """
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 2:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"voltage_divider requires exactly 2 resistors, got {len(resistors)}",
            data={"expected": 2, "actual": len(resistors)},
        )
    r_series, r_shunt = resistors[0], resistors[1]

    # The series resistor is the one whose left node matches the
    # source's + node. Reorder if needed.
    vin_plus = vin.nodes[0]
    if r_series.nodes[0] != vin_plus and r_series.nodes[1] == vin_plus:
        r_series, r_shunt = r_shunt, r_series
    elif r_series.nodes[0] != vin_plus and r_series.nodes[1] != vin_plus:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            "voltage_divider: series resistor must share a node with the source +",
            data={"sourceNodes": vin.nodes, "resistorNodes": r_series.nodes},
        )

    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            # Series resistor: top pin on the "in" line at MAIN_Y.
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=r_series.id,
            value=r_series.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            # Shunt resistor: shifted down so its top pin sits on the
            # "out" line at OUT_Y. For R0 resistor pin A is at
            # anchor.y + 16, so anchor.y = OUT_Y - 16.
            anchor=Point(INPUT_X + 2 * GRID_X - 16, OUT_Y - 16),
            rotation="R0",
            inst_name=r_shunt.id,
            value=r_shunt.value or "",
        ),
    ]


# Y-coordinate of the "out" line is defined in :mod:`ltagent.layout`
# as ``OUT_Y`` so the layout checker can reference the same constant
# when scoring. Importing it here keeps a single source of truth.


def _place_rc(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place components for ``rc_lowpass`` or ``rc_highpass``.

    Both topologies are signal-flow left-to-right with one series
    element and one shunt element. The series element's top pin
    sits on the main signal line at ``MAIN_Y``; the shunt element's
    top pin sits on the ``out`` line at ``OUT_Y`` so the two
    electrical nets never share a horizontal wire.

    For ``rc_lowpass`` the series element is a resistor; for
    ``rc_highpass`` it is a capacitor. The anchor y is chosen so
    each symbol's top pin lands on the appropriate line.
    """
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    series_kind = ComponentKind.RESISTOR if ir.topology == "rc_lowpass" else ComponentKind.CAPACITOR
    shunt_kind = ComponentKind.CAPACITOR if ir.topology == "rc_lowpass" else ComponentKind.RESISTOR
    series_components = [c for c in ir.components if c.kind == series_kind]
    shunt_components = [c for c in ir.components if c.kind == shunt_kind]
    if len(series_components) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"{ir.topology}: expected exactly 1 {series_kind.value}, got {len(series_components)}",
            data={"kind": series_kind.value, "count": len(series_components)},
        )
    if len(shunt_components) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"{ir.topology}: expected exactly 1 {shunt_kind.value}, got {len(shunt_components)}",
            data={"kind": shunt_kind.value, "count": len(shunt_components)},
        )
    series = series_components[0]
    shunt = shunt_components[0]
    series_symbol = "res" if series_kind == ComponentKind.RESISTOR else "cap"
    shunt_symbol = "res" if shunt_kind == ComponentKind.RESISTOR else "cap"

    # Series top pin must land on MAIN_Y. Resistor top pin is at
    # anchor.y + 16; capacitor top pin is at anchor.y + 0.
    series_anchor_y = MAIN_Y - 16 if series_kind == ComponentKind.RESISTOR else MAIN_Y
    # Shunt top pin must land on OUT_Y. Resistor top pin: anchor.y + 16.
    # Capacitor top pin: anchor.y + 0.
    shunt_anchor_y = OUT_Y - 16 if shunt_kind == ComponentKind.RESISTOR else OUT_Y

    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type=series_symbol,
            anchor=Point(INPUT_X + GRID_X - 16, series_anchor_y),
            rotation="R0",
            inst_name=series.id,
            value=series.value or "",
        ),
        SymbolPlacement(
            symbol_type=shunt_symbol,
            anchor=Point(INPUT_X + 2 * GRID_X - 16, shunt_anchor_y),
            rotation="R0",
            inst_name=shunt.id,
            value=shunt.value or "",
        ),
    ]


def _placements(ir: CircuitIR) -> list[SymbolPlacement]:
    """Dispatch to the per-topology placer."""
    if ir.topology == "voltage_divider":
        return _place_voltage_divider(ir)
    if ir.topology in {"rc_lowpass", "rc_highpass"}:
        return _place_rc(ir)
    placer = _PHASE11_PLACERS.get(ir.topology)
    if placer is not None:
        return placer(ir)  # type: ignore[no-any-return]
    raise ASCError(
        "ASC_UNSUPPORTED_TOPOLOGY",
        f"topology {ir.topology!r} has no .asc layout in MVP or Phase 11; "
        "supported: voltage_divider, rc_lowpass, rc_highpass, "
        "inverting_opamp, noninv_opamp, comparator, diode_clipper, "
        "halfwave_rectifier, bridge_rectifier, transistor_switch, led_resistor",
        data={"topology": ir.topology},
    )


# --- wire and flag emission ----------------------------------------------


def _emit_wire(p1: Point, p2: Point) -> str:
    """Return a single ``WIRE x1 y1 x2 y2`` line.

    Both endpoints are validated to be integer grid points; LTspice
    rejects fractional coordinates. Wires with identical endpoints
    would be a bug in the caller, so we raise instead of emitting a
    zero-length line.
    """
    if p1 == p2:
        raise ASCError(
            "ASC_INTERNAL",
            f"refusing to emit zero-length wire at {p1}",
            data={"point": str(p1)},
        )
    return f"WIRE {p1.x} {p1.y} {p2.x} {p2.y}"


def _route(
    p1: Point, p2: Point, *, via_x: int | None = None, via_y: int | None = None
) -> list[str]:
    """Build an orthogonal L-shaped route from ``p1`` to ``p2``.

    LTspice wires are axis-aligned: a non-orthogonal connection
    between two points must be split into one horizontal and one
    vertical segment meeting at a corner. The corner defaults to
    ``(p2.x, p1.y)`` (horizontal-then-vertical) which is the most
    common natural-route shape. Override with ``via_x`` /
    ``via_y`` when the default would cross another component.

    Returns an empty list when ``p1 == p2`` so the caller can route
    a node that happens to land on the same point as another
    without a special case.
    """
    if p1 == p2:
        return []
    if p1.x == p2.x or p1.y == p2.y:
        return [_emit_wire(p1, p2)]
    if via_y is not None:
        corner = Point(p1.x, via_y)
        return [
            _emit_wire(p1, corner),
            _emit_wire(corner, Point(p2.x, via_y)),
            _emit_wire(Point(p2.x, via_y), p2),
        ]
    if via_x is not None:
        corner = Point(via_x, p1.y)
        return [
            _emit_wire(p1, corner),
            _emit_wire(corner, Point(via_x, p2.y)),
            _emit_wire(Point(via_x, p2.y), p2),
        ]
    # Default: horizontal-then-vertical.
    corner = Point(p2.x, p1.y)
    return [_emit_wire(p1, corner), _emit_wire(corner, p2)]


def _emit_flag(p: Point, label: str) -> str:
    """Return a single ``FLAG x y label`` line."""
    return f"FLAG {p.x} {p.y} {label}"


def _emit_symbol(placement: SymbolPlacement) -> list[str]:
    """Return the lines for one ``SYMBOL``/``SYMATTR`` block.

    Every symbol gets a ``SYMATTR InstName`` and a ``SYMATTR Value``.
    The MVP does not emit WINDOW lines (they are visual-only and
    LTspice regenerates them on first save).
    """
    return [
        f"SYMBOL {placement.symbol_type} "
        f"{placement.anchor.x} {placement.anchor.y} {placement.rotation}",
        f"SYMATTR InstName {placement.inst_name}",
        f"SYMATTR Value {placement.value}",
    ]


def _build_layout_sections(
    ir: CircuitIR, placements: list[SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    """Build the WIRE, FLAG, SYMBOL, TEXT sections for the IR.

    Returns:
        lines: ordered list of schematic lines (not yet joined).
        node_points: map of node name -> Point that was chosen for
            its primary visual anchor (used for FLAGs).
        ground_points: list of Points that should carry a ``FLAG 0``.
    """
    # Identify components by IR id for routing decisions.
    placement_by_id: dict[str, SymbolPlacement] = {p.inst_name: p for p in placements}

    if ir.topology == "voltage_divider":
        return _layout_voltage_divider(ir, placement_by_id)
    if ir.topology in {"rc_lowpass", "rc_highpass"}:
        return _layout_rc(ir, placement_by_id)
    layout_fn = _PHASE11_LAYOUTS.get(ir.topology)
    if layout_fn is not None:
        return layout_fn(ir, placement_by_id)  # type: ignore[no-any-return]
    raise ASCError(
        "ASC_UNSUPPORTED_TOPOLOGY",
        f"no layout for topology {ir.topology!r}",
        data={"topology": ir.topology},
    )


def _layout_voltage_divider(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    r_series, r_shunt = resistors[0], resistors[1]
    if r_series.nodes[0] != vin.nodes[0] and r_series.nodes[1] == vin.nodes[0]:
        r_series, r_shunt = r_shunt, r_series

    vin_p = placement_by_id[vin.id]
    r1_p = placement_by_id[r_series.id]
    r2_p = placement_by_id[r_shunt.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    r1_a, r1_b = resistor_pins(r1_p)
    r2_a, r2_b = resistor_pins(r2_p)

    # "in": horizontal wire at MAIN_Y from Vin+ to R1 pin A.
    in_wire = _emit_wire(vin_plus, r1_a)
    # "out": horizontal wire at OUT_Y from R1 pin B to R2 pin A.
    out_wire = _emit_wire(r1_b, r2_a)
    # "0": Vin- drops to ground rail, R2 pin B drops to ground rail,
    # ground rail ties them together.
    vin_ground = _emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y))
    r2_ground = _emit_wire(r2_b, Point(r2_b.x, GROUND_Y))
    ground_rail = _emit_wire(Point(vin_minus.x, GROUND_Y), Point(r2_b.x, GROUND_Y))

    lines: list[str] = [
        in_wire,
        out_wire,
        vin_ground,
        r2_ground,
        ground_rail,
    ]
    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,  # in
        vin.nodes[1]: vin_minus,  # 0
        r_series.nodes[0]: r1_a,
        r_series.nodes[1]: r1_b,
        r_shunt.nodes[0]: r2_a,
        r_shunt.nodes[1]: r2_b,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(r2_b.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _layout_rc(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    series_kind = ComponentKind.RESISTOR if ir.topology == "rc_lowpass" else ComponentKind.CAPACITOR
    shunt_kind = ComponentKind.CAPACITOR if ir.topology == "rc_lowpass" else ComponentKind.RESISTOR
    series = next(c for c in ir.components if c.kind == series_kind)
    shunt = next(c for c in ir.components if c.kind == shunt_kind)

    vin_p = placement_by_id[vin.id]
    series_p = placement_by_id[series.id]
    shunt_p = placement_by_id[shunt.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    if series_kind == ComponentKind.RESISTOR:
        s_a, s_b = resistor_pins(series_p)
    else:
        s_a, s_b = capacitor_pins(series_p)
    if shunt_kind == ComponentKind.RESISTOR:
        h_a, h_b = resistor_pins(shunt_p)
    else:
        h_a, h_b = capacitor_pins(shunt_p)

    # "in": horizontal wire at MAIN_Y from Vin+ to series pin A.
    in_wire = _emit_wire(vin_plus, s_a)
    # "out": connect the series element's pin B to the shunt
    # element's pin A. In most cases both pins sit on the OUT_Y
    # line and a single horizontal wire is enough. When the
    # series element is a capacitor and the shunt is a resistor
    # the cap's pin B is 16 units above the resistor's pin A;
    # we add a short vertical segment to bridge them.
    out_wires = [_emit_wire(s_b, h_a)]
    if s_b.y != h_a.y:
        # Add a vertical bridge from the cap's pin B (y = MAIN_Y + 64)
        # up to the resistor's pin A y at OUT_Y. Pick a midpoint
        # x to avoid the body of the series element.
        bridge_x = s_b.x + (h_a.x - s_b.x) // 2
        out_wires = [
            _emit_wire(s_b, Point(bridge_x, s_b.y)),
            _emit_wire(Point(bridge_x, s_b.y), Point(bridge_x, h_a.y)),
            _emit_wire(Point(bridge_x, h_a.y), h_a),
        ]
    # "0": Vin- drops to ground rail, shunt pin B drops to ground
    # rail, ground rail ties them together.
    vin_ground = _emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y))
    shunt_ground = _emit_wire(h_b, Point(h_b.x, GROUND_Y))
    ground_rail = _emit_wire(Point(vin_minus.x, GROUND_Y), Point(h_b.x, GROUND_Y))

    lines: list[str] = [
        in_wire,
        *out_wires,
        vin_ground,
        shunt_ground,
        ground_rail,
    ]
    # Figure out which nodes map to which series element terminal
    # so the FLAG for "out" sits on the right spot.
    series_node_a = series.nodes[0]
    series_node_b = series.nodes[1]
    shunt_node_a = shunt.nodes[0]
    shunt_node_b = shunt.nodes[1]
    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,  # in
        vin.nodes[1]: vin_minus,  # 0
        series_node_a: s_a,
        series_node_b: s_b,
        shunt_node_a: h_a,
        shunt_node_b: h_b,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(h_b.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


# ---------------------------------------------------------------------------
# Phase 11: per-topology placers and wirers
# ---------------------------------------------------------------------------
#
# All seven analog templates share a common shape:
#
#   - One or more voltage sources for input / supply rails.
#   - One or more passive components (R / C).
#   - One or more semiconductors (D / Q / M) or opamp (X).
#   - A ground node ``0`` that drops from every source's negative
#     terminal and from any element whose IR node equals ``0``.
#
# The layouts are deliberately simple (single-row signal flow where
# possible, fixed grid coordinates) so the layout checker can verify
# them with the same per-topology rules as the MVP trio. Multi-element
# wirings use small jumper wires to keep the grid orthogonal.
#
# Component kinds accepted by each topology:
#
#   inverting_opamp     V, R, R, X (+ 2 supply V sources)
#   noninv_opamp        V, R, R, X (+ 2 supply V sources)
#   comparator          V, V (ref), R, X (+ 2 supply V sources)
#   diode_clipper       V, R, D, D
#   halfwave_rectifier  V, D, R (load) [, C (smoothing)]
#   bridge_rectifier    V, D x4, R (load), C (smoothing)
#   transistor_switch   V (input), R (base), Q (npn), R (load), V (vcc)


def _place_led_resistor(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a DC source, series resistor, and LED from left to right."""
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    diodes = [c for c in ir.components if c.kind == ComponentKind.DIODE]
    if len(resistors) != 1 or len(diodes) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            "led_resistor requires exactly one resistor and one diode",
            data={"resistors": len(resistors), "diodes": len(diodes)},
        )
    resistor = resistors[0]
    diode = diodes[0]
    if resistor.nodes[0] != vin.nodes[0] or resistor.nodes[1] != diode.nodes[0]:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            "led_resistor requires source -> resistor -> diode signal order",
            data={
                "sourceNodes": vin.nodes,
                "resistorNodes": resistor.nodes,
                "diodeNodes": diode.nodes,
            },
        )
    if diode.nodes[1] != GROUND_NODE:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            "led_resistor requires the diode cathode on ground",
            data={"diodeNodes": diode.nodes},
        )
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=resistor.id,
            value=resistor.value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=diode.id,
            value=diode.model or diode.value or "",
        ),
    ]


def _layout_led_resistor(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    resistor = next(c for c in ir.components if c.kind == ComponentKind.RESISTOR)
    diode = next(c for c in ir.components if c.kind == ComponentKind.DIODE)

    vin_plus = plus_pin(placement_by_id[vin.id])
    vin_minus = minus_pin(placement_by_id[vin.id])
    r_a, r_b = resistor_pins(placement_by_id[resistor.id])
    d_a, d_k = diode_pins(placement_by_id[diode.id])
    vin_ground = Point(vin_minus.x, GROUND_Y)
    diode_ground = Point(d_k.x, GROUND_Y)

    lines = [
        _emit_wire(vin_plus, r_a),
        _emit_wire(r_b, d_a),
        _emit_wire(vin_minus, vin_ground),
        _emit_wire(d_k, diode_ground),
        _emit_wire(vin_ground, diode_ground),
    ]
    node_points = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        resistor.nodes[0]: r_a,
        resistor.nodes[1]: r_b,
        diode.nodes[0]: d_a,
        diode.nodes[1]: d_k,
    }
    return lines, node_points, [vin_ground, diode_ground]


def _place_inverting_opamp(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place an inverting opamp: Vin -> R1 -> vfb -> R2 -> out."""
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    if len(sources) != 3:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"inverting_opamp: expected 3 voltage sources (Vin, Vcc, Vee), got {len(sources)}",
            data={"expected": 3, "count": len(sources)},
        )
    # The IR can name the sources anything; we identify them by their
    # role. Vin is the one whose + node is the input node (not vcc/vee).
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vee = next(s for s in sources if s.nodes[0] == "vee")
    vin = next(s for s in sources if s not in (vcc, vee))
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 2:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"inverting_opamp: expected 2 resistors, got {len(resistors)}",
            data={"count": len(resistors)},
        )
    r_in = next(r for r in resistors if r.nodes[1] == "vfb")
    r_fb = next(r for r in resistors if r.nodes[0] == "vfb")
    opamps = [c for c in ir.components if c.kind == ComponentKind.OPAMP]
    if len(opamps) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"inverting_opamp: expected 1 opamp, got {len(opamps)}",
            data={"count": len(opamps)},
        )
    opamp = opamps[0]
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=r_in.id,
            value=r_in.value or "",
        ),
        SymbolPlacement(
            symbol_type="opamp",
            anchor=Point(INPUT_X + 2 * GRID_X, MAIN_Y - 56),
            rotation="R0",
            inst_name=opamp.id,
            value=opamp.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + 3 * GRID_X - 16, OUT_Y - 16),
            rotation="R0",
            inst_name=r_fb.id,
            value=r_fb.value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vcc.id,
            value=vcc.value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vee.id,
            value=vee.value or "",
        ),
    ]


def _layout_inverting_opamp(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vee = next(s for s in sources if s.nodes[0] == "vee")
    vin = next(s for s in sources if s not in (vcc, vee))
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    r_in = next(r for r in resistors if r.nodes[1] == "vfb")
    r_fb = next(r for r in resistors if r.nodes[0] == "vfb")
    opamp = next(c for c in ir.components if c.kind == ComponentKind.OPAMP)

    vin_p = placement_by_id[vin.id]
    r_in_p = placement_by_id[r_in.id]
    r_fb_p = placement_by_id[r_fb.id]
    u_p = placement_by_id[opamp.id]
    vcc_p = placement_by_id[vcc.id]
    vee_p = placement_by_id[vee.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    r_in_a, r_in_b = resistor_pins(r_in_p)
    r_fb_a, r_fb_b = resistor_pins(r_fb_p)
    u_in_plus, u_in_minus, _u_out_unused, _u_v_plus_unused, _u_v_minus_unused = opamp_pins(u_p)
    # We re-fetch v+ and v- with the explicit offsets below.
    u_v_plus = u_p.pin((96, 16))
    u_v_minus = u_p.pin((96, 96))
    vcc_plus = plus_pin(vcc_p)
    vee_plus = plus_pin(vee_p)

    lines: list[str] = []
    # Input row: Vin+ -> R_in A (horizontal at MAIN_Y).
    lines.append(_emit_wire(vin_plus, r_in_a))
    # vfb bridge: R_in B -> U1 in+. R_in B is at OUT_Y, U1 in+ is above
    # MAIN_Y. Route via x = OUT_Y column then north past the opamp body
    # so the wire does not cross the opamp's body rectangle.
    lines.extend(_route(r_in_b, u_in_plus, via_x=u_p.anchor.x + 96))
    # in- drops to ground.
    lines.append(_emit_wire(u_in_minus, Point(u_in_minus.x, GROUND_Y)))
    # Output bridge: U1 out -> R_fb A. U1 out is at MAIN_Y, R_fb A is at
    # OUT_Y. Use the L-shape helper; default horizontal-then-vertical
    # is fine because there is no component body in the way.
    u_out = u_p.pin((96, 56))
    lines.extend(_route(u_out, r_fb_a))
    # R_fb B drops to ground at its column.
    lines.append(_emit_wire(r_fb_b, Point(r_fb_b.x, GROUND_Y)))
    # Source rails: Vin- and Vcc-/Vee- all drop to ground.
    lines.append(_emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)))
    # Ground rail spans every component's ground column.
    lines.append(
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(r_fb_b.x, GROUND_Y),
        )
    )
    # v+ to vcc plus, v- to vee plus.
    lines.extend(_route(u_v_plus, vcc_plus))
    lines.extend(_route(u_v_minus, vee_plus))

    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        vcc.nodes[0]: vcc_plus,
        vee.nodes[0]: vee_plus,
        r_in.nodes[0]: r_in_a,
        r_in.nodes[1]: r_in_b,
        r_fb.nodes[0]: r_fb_a,
        r_fb.nodes[1]: r_fb_b,
        opamp.nodes[0]: u_in_plus,
        opamp.nodes[1]: u_in_minus,
        opamp.nodes[2]: u_v_plus,
        opamp.nodes[3]: u_v_minus,
        opamp.nodes[4]: u_out,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(r_fb_b.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _place_noninv_opamp(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a non-inverting opamp: Vin -> R1 -> vfb; R2 vfb->0; feedback Rf out->vfb."""
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    if len(sources) != 3:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"noninv_opamp: expected 3 voltage sources, got {len(sources)}",
            data={"count": len(sources)},
        )
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vee = next(s for s in sources if s.nodes[0] == "vee")
    vin = next(s for s in sources if s not in (vcc, vee))
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 2:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"noninv_opamp: expected 2 resistors, got {len(resistors)}",
            data={"count": len(resistors)},
        )
    opamps = [c for c in ir.components if c.kind == ComponentKind.OPAMP]
    if len(opamps) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"noninv_opamp: expected 1 opamp, got {len(opamps)}",
            data={"count": len(opamps)},
        )
    opamp = opamps[0]
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=resistors[0].id,
            value=resistors[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="opamp",
            anchor=Point(INPUT_X + 2 * GRID_X, MAIN_Y - 56),
            rotation="R0",
            inst_name=opamp.id,
            value=opamp.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, OUT_Y + GRID_Y),
            rotation="R0",
            inst_name=resistors[1].id,
            value=resistors[1].value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vcc.id,
            value=vcc.value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vee.id,
            value=vee.value or "",
        ),
    ]


def _layout_noninv_opamp(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vee = next(s for s in sources if s.nodes[0] == "vee")
    vin = next(s for s in sources if s not in (vcc, vee))
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    opamp = next(c for c in ir.components if c.kind == ComponentKind.OPAMP)

    vin_p = placement_by_id[vin.id]
    r1_p = placement_by_id[resistors[0].id]
    r2_p = placement_by_id[resistors[1].id]
    u_p = placement_by_id[opamp.id]
    vcc_p = placement_by_id[vcc.id]
    vee_p = placement_by_id[vee.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    r1_a, r1_b = resistor_pins(r1_p)
    r2_a, r2_b = resistor_pins(r2_p)
    u_in_plus, u_in_minus, _u_v_plus, _u_v_minus, u_out = opamp_pins(u_p)

    lines: list[str] = [
        _emit_wire(vin_plus, r1_a),
        _emit_wire(r1_b, u_in_plus),
        _emit_wire(u_in_minus, Point(u_in_minus.x, GROUND_Y)),
        _emit_wire(u_out, Point(u_out.x, OUT_Y)),
    ]
    # Feedback wire from u_out down to vfb row (r1_b level).
    # u_out is at OUT_Y level already (opamp_pins sets it at 56 from anchor.y).
    lines.append(_emit_wire(Point(u_out.x, OUT_Y), Point(r1_b.x, OUT_Y)))
    lines.append(_emit_wire(Point(r1_b.x, OUT_Y), Point(r1_b.x, MAIN_Y + 16)))
    # R2 from vfb to ground.
    lines.append(_emit_wire(r1_b, r2_a))
    lines.append(_emit_wire(r2_b, Point(r2_b.x, GROUND_Y)))
    # Source rails.
    lines.append(_emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)))
    lines.append(
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(r2_b.x, GROUND_Y),
        )
    )
    # v+ / v- wires.
    vcc_plus = plus_pin(vcc_p)
    vee_plus = plus_pin(vee_p)
    u_v_plus = u_p.pin((96, 16))
    u_v_minus = u_p.pin((96, 96))
    lines.append(_emit_wire(u_v_plus, vcc_plus))
    lines.append(_emit_wire(u_v_minus, vee_plus))

    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        vcc.nodes[0]: vcc_plus,
        vee.nodes[0]: vee_plus,
        resistors[0].nodes[0]: r1_a,
        resistors[0].nodes[1]: r1_b,
        resistors[1].nodes[0]: r2_a,
        resistors[1].nodes[1]: r2_b,
        opamp.nodes[0]: u_in_plus,
        opamp.nodes[1]: u_in_minus,
        opamp.nodes[2]: u_v_plus,
        opamp.nodes[3]: u_v_minus,
        opamp.nodes[4]: u_out,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(r2_b.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _place_comparator(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a comparator: Vin -> R1 -> in+; Vref -> in-; X -> out."""
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    if len(sources) != 3:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"comparator: expected 3 sources (Vin, Vref, Vcc), got {len(sources)}",
            data={"count": len(sources)},
        )
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vee = next(s for s in sources if s.nodes[0] == "vee")
    vin = next(s for s in sources if s not in (vcc, vee))
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"comparator: expected 1 resistor, got {len(resistors)}",
            data={"count": len(resistors)},
        )
    opamps = [c for c in ir.components if c.kind == ComponentKind.OPAMP]
    if len(opamps) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"comparator: expected 1 opamp, got {len(opamps)}",
            data={"count": len(opamps)},
        )
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=resistors[0].id,
            value=resistors[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="opamp",
            anchor=Point(INPUT_X + 2 * GRID_X, MAIN_Y - 56),
            rotation="R0",
            inst_name=opamps[0].id,
            value=opamps[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vcc.id,
            value=vcc.value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vee.id,
            value=vee.value or "",
        ),
    ]


def _layout_comparator(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vee = next(s for s in sources if s.nodes[0] == "vee")
    vin = next(s for s in sources if s not in (vcc, vee))
    resistor = next(c for c in ir.components if c.kind == ComponentKind.RESISTOR)
    opamp = next(c for c in ir.components if c.kind == ComponentKind.OPAMP)

    vin_p = placement_by_id[vin.id]
    r_p = placement_by_id[resistor.id]
    u_p = placement_by_id[opamp.id]
    vcc_p = placement_by_id[vcc.id]
    vee_p = placement_by_id[vee.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    r_a, r_b = resistor_pins(r_p)
    u_in_plus, u_in_minus, _u_v_plus, _u_v_minus, u_out = opamp_pins(u_p)
    vcc_plus = plus_pin(vcc_p)
    vee_plus = plus_pin(vee_p)
    u_v_plus = u_p.pin((96, 16))
    u_v_minus = u_p.pin((96, 96))

    lines: list[str] = [
        _emit_wire(vin_plus, r_a),
        _emit_wire(r_b, u_in_plus),
        # in- connects to vcc (the "reference" voltage); in this
        # topology vcc and in- are the same node.
        _emit_wire(u_in_minus, vcc_plus),
        _emit_wire(u_v_plus, vcc_plus),
        _emit_wire(u_v_minus, vee_plus),
        _emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)),
        _emit_wire(vee_plus, Point(vee_plus.x, GROUND_Y)),
    ]
    lines.append(
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(vee_plus.x, GROUND_Y),
        )
    )

    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        vcc.nodes[0]: vcc_plus,
        vee.nodes[0]: vee_plus,
        resistor.nodes[0]: r_a,
        resistor.nodes[1]: r_b,
        opamp.nodes[0]: u_in_plus,
        opamp.nodes[1]: u_in_minus,
        opamp.nodes[2]: u_v_plus,
        opamp.nodes[3]: u_v_minus,
        opamp.nodes[4]: u_out,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(vee_plus.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _place_diode_clipper(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a diode clipper: Vin -> R1 -> out; D1/D2 clamp out to bias rails."""
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"diode_clipper: expected 1 resistor, got {len(resistors)}",
            data={"count": len(resistors)},
        )
    diodes = [c for c in ir.components if c.kind == ComponentKind.DIODE]
    if len(diodes) != 2:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"diode_clipper: expected 2 diodes, got {len(diodes)}",
            data={"count": len(diodes)},
        )
    d_high = next(d for d in diodes if d.nodes[0] == "out")
    d_low = next(d for d in diodes if d.nodes[1] == "out")
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=resistors[0].id,
            value=resistors[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, OUT_Y),
            rotation="R0",
            inst_name=d_high.id,
            value=d_high.value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + GRID_X - 16, OUT_Y + GRID_Y),
            rotation="R0",
            inst_name=d_low.id,
            value=d_low.value or "",
        ),
    ]


def _layout_diode_clipper(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    resistor = next(c for c in ir.components if c.kind == ComponentKind.RESISTOR)
    diodes = [c for c in ir.components if c.kind == ComponentKind.DIODE]
    d_high = next(d for d in diodes if d.nodes[0] == "out")
    d_low = next(d for d in diodes if d.nodes[1] == "out")

    vin_p = placement_by_id[vin.id]
    r_p = placement_by_id[resistor.id]
    d_high_p = placement_by_id[d_high.id]
    d_low_p = placement_by_id[d_low.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    r_a, r_b = resistor_pins(r_p)
    dh_a, dh_k = diode_pins(d_high_p)
    dl_a, dl_k = diode_pins(d_low_p)

    lines: list[str] = [
        _emit_wire(vin_plus, r_a),
        _emit_wire(r_b, dh_a),
        # Both diodes share the out node at the cathode of d_high /
        # anode of d_low. Tie them via the out line.
        _emit_wire(dh_k, Point(dh_k.x, OUT_Y + GRID_Y // 2)),
        _emit_wire(dl_a, Point(dl_a.x, OUT_Y + GRID_Y // 2)),
        _emit_wire(Point(dh_k.x, OUT_Y + GRID_Y // 2), Point(dl_a.x, OUT_Y + GRID_Y // 2)),
        # D_low cathode drops to ground.
        _emit_wire(dl_k, Point(dl_k.x, GROUND_Y)),
        # Source rails.
        _emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)),
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(dl_k.x, GROUND_Y),
        ),
    ]
    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        resistor.nodes[0]: r_a,
        resistor.nodes[1]: r_b,
        d_high.nodes[0]: dh_a,
        d_high.nodes[1]: dh_k,
        d_low.nodes[0]: dl_a,
        d_low.nodes[1]: dl_k,
        "out": dh_k,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(dl_k.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _place_halfwave_rectifier(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a half-wave rectifier: Vin -> D1 -> out; R1 load out->0; C1 optional."""
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    diodes = [c for c in ir.components if c.kind == ComponentKind.DIODE]
    if len(diodes) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"halfwave_rectifier: expected 1 diode, got {len(diodes)}",
            data={"count": len(diodes)},
        )
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"halfwave_rectifier: expected 1 resistor, got {len(resistors)}",
            data={"count": len(resistors)},
        )
    caps = [c for c in ir.components if c.kind == ComponentKind.CAPACITOR]
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=diodes[0].id,
            value=diodes[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=resistors[0].id,
            value=resistors[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="cap",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, OUT_Y),
            rotation="R0",
            inst_name=caps[0].id if caps else "C1",
            value=caps[0].value if caps else "",  # type: ignore[arg-type]
        ),
    ]


def _layout_halfwave_rectifier(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    diode = next(c for c in ir.components if c.kind == ComponentKind.DIODE)
    resistor = next(c for c in ir.components if c.kind == ComponentKind.RESISTOR)
    caps = [c for c in ir.components if c.kind == ComponentKind.CAPACITOR]
    cap = caps[0] if caps else None

    vin_p = placement_by_id[vin.id]
    d_p = placement_by_id[diode.id]
    r_p = placement_by_id[resistor.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    d_a, d_k = diode_pins(d_p)
    r_a, r_b = resistor_pins(r_p)

    lines: list[str] = [
        _emit_wire(vin_plus, d_a),
        _emit_wire(d_k, r_a),
        _emit_wire(r_b, Point(r_b.x, GROUND_Y)),
        _emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)),
    ]
    lines.append(
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(r_b.x, GROUND_Y),
        )
    )
    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        diode.nodes[0]: d_a,
        diode.nodes[1]: d_k,
        resistor.nodes[0]: r_a,
        resistor.nodes[1]: r_b,
        "out": r_a,
    }
    if cap is not None:
        c_p = placement_by_id[cap.id]
        c_a, c_b = capacitor_pins(c_p)
        lines.append(_emit_wire(r_a, c_a))
        lines.append(_emit_wire(c_b, Point(c_b.x, GROUND_Y)))
        lines.append(
            _emit_wire(
                Point(r_b.x, GROUND_Y),
                Point(c_b.x, GROUND_Y),
            )
        )
        node_points[cap.nodes[0]] = c_a
        node_points[cap.nodes[1]] = c_b
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(r_b.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _place_bridge_rectifier(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a bridge rectifier: 4 diodes arranged in a diamond + DC load (R, C)."""
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    diodes = [c for c in ir.components if c.kind == ComponentKind.DIODE]
    if len(diodes) != 4:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"bridge_rectifier: expected 4 diodes, got {len(diodes)}",
            data={"count": len(diodes)},
        )
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"bridge_rectifier: expected 1 load resistor, got {len(resistors)}",
            data={"count": len(resistors)},
        )
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=diodes[0].id,
            value=diodes[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=diodes[1].id,
            value=diodes[1].value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + GRID_X - 16, OUT_Y + GRID_Y // 2),
            rotation="R0",
            inst_name=diodes[2].id,
            value=diodes[2].value or "",
        ),
        SymbolPlacement(
            symbol_type="diode",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, OUT_Y + GRID_Y // 2),
            rotation="R0",
            inst_name=diodes[3].id,
            value=diodes[3].value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + 3 * GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=resistors[0].id,
            value=resistors[0].value or "",
        ),
    ]


def _layout_bridge_rectifier(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    vin = _by_kind(ir.components, ComponentKind.VOLTAGE_SOURCE)
    diodes = [c for c in ir.components if c.kind == ComponentKind.DIODE]
    resistor = next(c for c in ir.components if c.kind == ComponentKind.RESISTOR)

    vin_p = placement_by_id[vin.id]
    d0_p = placement_by_id[diodes[0].id]
    d1_p = placement_by_id[diodes[1].id]
    d2_p = placement_by_id[diodes[2].id]
    d3_p = placement_by_id[diodes[3].id]
    r_p = placement_by_id[resistor.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    d0_a, d0_k = diode_pins(d0_p)
    d1_a, d1_k = diode_pins(d1_p)
    d2_a, d2_k = diode_pins(d2_p)
    d3_a, d3_k = diode_pins(d3_p)
    r_a, r_b = resistor_pins(r_p)

    lines: list[str] = [
        _emit_wire(vin_plus, d0_a),
        _emit_wire(d0_k, d2_a),
        _emit_wire(d1_a, d3_k),
        _emit_wire(vin_minus, d1_a),
        # Both bottom-diode cathodes need to reach the resistor
        # column. D2 cathode at (400, 224) -> D3 anode at (240, 336).
        # Route via y=336 (D3 anode level) so the wire does not cross
        # D3's body (which is at y in [320, 384]).
        _emit_wire(d2_k, Point(d2_k.x, 336)),
        _emit_wire(Point(d2_k.x, 336), Point(d3_a.x, 336)),
        _emit_wire(d3_a, Point(d3_a.x, 336)),
        # D3 anode (240, 336) -> D4 anode (400, 336): horizontal at y=336.
        # Output rail: extend the bridge output (D2 cathode / D3 anode
        # line) to the resistor column.
        _emit_wire(
            Point(d2_k.x, 336),
            Point(r_a.x, 336),
        ),
        # Resistor drop to ground.
        _emit_wire(r_b, Point(r_b.x, GROUND_Y)),
    ]
    lines.append(_emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)))
    lines.append(
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(r_b.x, GROUND_Y),
        )
    )
    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        diodes[0].nodes[0]: d0_a,
        diodes[0].nodes[1]: d0_k,
        diodes[1].nodes[0]: d1_a,
        diodes[1].nodes[1]: d1_k,
        diodes[2].nodes[0]: d2_a,
        diodes[2].nodes[1]: d2_k,
        diodes[3].nodes[0]: d3_a,
        diodes[3].nodes[1]: d3_k,
        resistor.nodes[0]: r_a,
        resistor.nodes[1]: r_b,
        "out": r_a,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(r_b.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


def _place_transistor_switch(ir: CircuitIR) -> list[SymbolPlacement]:
    """Place a BJT low-side switch: Vin -> Rb -> base; Q collector -> R_load -> Vcc; emitter -> 0."""
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    if len(sources) != 2:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"transistor_switch: expected 2 voltage sources (Vin, Vcc), got {len(sources)}",
            data={"count": len(sources)},
        )
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vin = next(s for s in sources if s.nodes[0] != "vcc")
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    if len(resistors) != 2:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"transistor_switch: expected 2 resistors (base, load), got {len(resistors)}",
            data={"count": len(resistors)},
        )
    r_base = next(r for r in resistors if r.nodes[1] == "base")
    r_load = next(r for r in resistors if r.nodes[0] == "vcc")
    bjts = [c for c in ir.components if c.kind == ComponentKind.NPN]
    if len(bjts) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"transistor_switch: expected 1 NPN, got {len(bjts)}",
            data={"count": len(bjts)},
        )
    return [
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X, MAIN_Y - 16),
            rotation="R0",
            inst_name=vin.id,
            value=vin.value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=r_base.id,
            value=r_base.value or "",
        ),
        SymbolPlacement(
            symbol_type="npn",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, MAIN_Y - 16),
            rotation="R0",
            inst_name=bjts[0].id,
            value=bjts[0].value or "",
        ),
        SymbolPlacement(
            symbol_type="res",
            anchor=Point(INPUT_X + 2 * GRID_X - 16, MAIN_Y + 96),
            rotation="R0",
            inst_name=r_load.id,
            value=r_load.value or "",
        ),
        SymbolPlacement(
            symbol_type="voltage",
            anchor=Point(INPUT_X + GRID_X - 16, GROUND_Y + 96),
            rotation="R0",
            inst_name=vcc.id,
            value=vcc.value or "",
        ),
    ]


def _layout_transistor_switch(
    ir: CircuitIR, placement_by_id: Mapping[str, SymbolPlacement]
) -> tuple[list[str], dict[str, Point], list[Point]]:
    sources = [c for c in ir.components if c.kind == ComponentKind.VOLTAGE_SOURCE]
    vcc = next(s for s in sources if s.nodes[0] == "vcc")
    vin = next(s for s in sources if s.nodes[0] != "vcc")
    resistors = [c for c in ir.components if c.kind == ComponentKind.RESISTOR]
    r_base = next(r for r in resistors if r.nodes[1] == "base")
    r_load = next(r for r in resistors if r.nodes[0] == "vcc")
    bjt = next(c for c in ir.components if c.kind == ComponentKind.NPN)

    vin_p = placement_by_id[vin.id]
    r_base_p = placement_by_id[r_base.id]
    r_load_p = placement_by_id[r_load.id]
    q_p = placement_by_id[bjt.id]
    vcc_p = placement_by_id[vcc.id]

    vin_plus = plus_pin(vin_p)
    vin_minus = minus_pin(vin_p)
    rb_a, rb_b = resistor_pins(r_base_p)
    rl_a, rl_b = resistor_pins(r_load_p)
    q_c, q_b, q_e = bjt_pins(q_p)
    vcc_plus = plus_pin(vcc_p)

    lines: list[str] = [
        _emit_wire(vin_plus, rb_a),
        _emit_wire(rb_b, q_b),
        _emit_wire(q_c, rl_a),
        _emit_wire(rl_b, q_e),
        _emit_wire(q_e, Point(q_e.x, GROUND_Y)),
        _emit_wire(vcc_plus, Point(vcc_plus.x, MAIN_Y - 16)),
        _emit_wire(Point(vcc_plus.x, MAIN_Y - 16), rl_a),
        _emit_wire(vin_minus, Point(vin_minus.x, GROUND_Y)),
    ]
    lines.append(
        _emit_wire(
            Point(vin_minus.x, GROUND_Y),
            Point(q_e.x, GROUND_Y),
        )
    )
    node_points: dict[str, Point] = {
        vin.nodes[0]: vin_plus,
        vin.nodes[1]: vin_minus,
        vcc.nodes[0]: vcc_plus,
        r_base.nodes[0]: rb_a,
        r_base.nodes[1]: rb_b,
        r_load.nodes[0]: rl_a,
        r_load.nodes[1]: rl_b,
        bjt.nodes[0]: q_c,
        bjt.nodes[1]: q_b,
        bjt.nodes[2]: q_e,
        "out": q_e,
    }
    ground_points: list[Point] = [
        Point(vin_minus.x, GROUND_Y),
        Point(q_e.x, GROUND_Y),
    ]
    return lines, node_points, ground_points


_PHASE11_PLACERS: dict[str, Any] = {
    "inverting_opamp": _place_inverting_opamp,
    "noninv_opamp": _place_noninv_opamp,
    "comparator": _place_comparator,
    "diode_clipper": _place_diode_clipper,
    "halfwave_rectifier": _place_halfwave_rectifier,
    "bridge_rectifier": _place_bridge_rectifier,
    "transistor_switch": _place_transistor_switch,
    "led_resistor": _place_led_resistor,
}


_PHASE11_LAYOUTS: dict[str, Any] = {
    "inverting_opamp": _layout_inverting_opamp,
    "noninv_opamp": _layout_noninv_opamp,
    "comparator": _layout_comparator,
    "diode_clipper": _layout_diode_clipper,
    "halfwave_rectifier": _layout_halfwave_rectifier,
    "bridge_rectifier": _layout_bridge_rectifier,
    "transistor_switch": _layout_transistor_switch,
    "led_resistor": _layout_led_resistor,
}


# --- analysis directive ---------------------------------------------------


def _format_analysis(a: Analysis) -> str:
    """Return a SPICE directive line for one analysis block.

    Mirrors the netlist generator's formatting so the simulation
    command in the schematic matches the netlist.
    """
    if a.kind == AnalysisKind.OP:
        return ".op"
    if a.kind == AnalysisKind.TRAN:
        start = a.startTime or "0"
        stop = a.stopTime or ""
        if a.stepTime:
            return f".tran {a.stepTime} {start} {stop}".rstrip()
        return f".tran {start} {stop}".rstrip()
    if a.kind == AnalysisKind.AC:
        p = a.pointsPerDecade or 10
        return f".ac dec {p} {a.stopFreq or ''}".rstrip()
    if a.kind == AnalysisKind.DC:
        return (
            f".dc {a.sweepVariable or ''} {a.sweepStart or ''} "
            f"{a.sweepStop or ''} {a.sweepStep or ''}"
        ).rstrip()
    return f".{a.kind.value}"


# --- header ---------------------------------------------------------------


def _build_header(ir: CircuitIR) -> tuple[str, str]:
    """The two-line generator header prepended to the schematic.

    We use the same ``*`` comment style as the netlist header so
    diffs between ``.cir`` and ``.asc`` stay readable.
    """
    return (
        f"* Generated by {GENERATOR_NAME} {__version__}",
        f"* Project: {ir.name}",
    )


# --- public API -----------------------------------------------------------


def render_asc(ir: CircuitIR) -> ASCResult:
    """Render ``ir`` to an :class:`ASCResult` without touching the filesystem.

    The result's ``text`` is a complete ``.asc`` body that LTspice
    XVII can open. The agent may request topology and parameters
    only; Python owns every coordinate in the output (AGENTS.md hard
    rule 1).

    Raises:
        ASCError: if the topology is not in the MVP set, or the IR
            violates a placement invariant (e.g. wrong number of
            components for the named topology).
    """
    header = _build_header(ir)
    placements = _placements(ir)
    wire_lines, node_points, ground_points = _build_layout_sections(ir, placements)

    lines: list[str] = []
    lines.append("Version 4")
    lines.append(f"SHEET 1 {SHEET_W} {SHEET_H}")
    lines.extend(wire_lines)
    # FLAGs: one for ground (label "0") at each ground point, and
    # one for every non-ground node we identified.
    flag_lines: list[str] = []
    for gp in ground_points:
        flag_lines.append(_emit_flag(gp, GROUND_NODE))
    for node_name, pt in node_points.items():
        if node_name == GROUND_NODE:
            continue
        # Skip duplicates of ground points; the ground rail already
        # carries "0" labels.
        if any(pt == gp for gp in ground_points):
            continue
        flag_lines.append(_emit_flag(pt, node_name))
    lines.extend(flag_lines)
    # SYMBOLs.
    for placement in placements:
        lines.extend(_emit_symbol(placement))
    # TEXT: emit one directive per analysis. The ! prefix tells
    # LTspice to treat the rest of the line as a SPICE directive.
    text_y = GROUND_Y + 96
    for idx, analysis in enumerate(ir.analysis):
        directive = _format_analysis(analysis)
        text_x = INPUT_X + idx * GRID_X
        lines.append(f"TEXT {text_x} {text_y} Left 2 !{directive}")
    # Closing banner.
    lines.append(f"* End of {GENERATOR_NAME} output")

    text = "\n".join(lines) + "\n"
    line_count = sum(1 for ln in text.splitlines() if ln.strip())
    return ASCResult(
        text=text,
        line_count=line_count,
        header=header,
        component_count=len(placements),
        wire_count=len(wire_lines),
        flag_count=len(flag_lines),
        topology=ir.topology,
        placements=tuple(placements),
        node_points=node_points,
    )


def write_asc(ir: CircuitIR, out_path: Path | str) -> ASCResult:
    """Render ``ir`` to ``.asc`` and write it to ``out_path``.

    The caller is responsible for path safety (no traversal, parent
    directory exists). This function performs the render and the
    write and returns the same :class:`ASCResult` as
    :func:`render_asc` so the CLI layer can report counters.
    """
    result = render_asc(ir)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.text, encoding="utf-8")
    return result


__all__ = [
    "GENERATOR_NAME",
    "GROUND_NODE",
    "ASCError",
    "ASCResult",
    "render_asc",
    "write_asc",
]
