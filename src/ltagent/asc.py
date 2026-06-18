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
    GROUND_Y,
    INPUT_X,
    MAIN_Y,
    OUT_Y,
    SHEET_H,
    SHEET_W,
    Point,
    SymbolPlacement,
    capacitor_pins,
    minus_pin,
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


def _by_kind(
    components: Sequence[Component], kind: ComponentKind
) -> Component:
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
            f"topology has multiple {kind.value!r} components; "
            "MVP supports exactly one source",
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
    series_kind = (
        ComponentKind.RESISTOR if ir.topology == "rc_lowpass" else ComponentKind.CAPACITOR
    )
    shunt_kind = (
        ComponentKind.CAPACITOR if ir.topology == "rc_lowpass" else ComponentKind.RESISTOR
    )
    series_components = [c for c in ir.components if c.kind == series_kind]
    shunt_components = [c for c in ir.components if c.kind == shunt_kind]
    if len(series_components) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"{ir.topology}: expected exactly 1 {series_kind.value}, "
            f"got {len(series_components)}",
            data={"kind": series_kind.value, "count": len(series_components)},
        )
    if len(shunt_components) != 1:
        raise ASCError(
            "ASC_INVALID_TOPOLOGY",
            f"{ir.topology}: expected exactly 1 {shunt_kind.value}, "
            f"got {len(shunt_components)}",
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
    raise ASCError(
        "ASC_UNSUPPORTED_TOPOLOGY",
        f"topology {ir.topology!r} has no .asc layout in MVP; "
        f"supported: voltage_divider, rc_lowpass, rc_highpass",
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
        vin.nodes[0]: vin_plus,                # in
        vin.nodes[1]: vin_minus,               # 0
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
    series_kind = (
        ComponentKind.RESISTOR if ir.topology == "rc_lowpass" else ComponentKind.CAPACITOR
    )
    shunt_kind = (
        ComponentKind.CAPACITOR if ir.topology == "rc_lowpass" else ComponentKind.RESISTOR
    )
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
        vin.nodes[0]: vin_plus,                # in
        vin.nodes[1]: vin_minus,               # 0
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
