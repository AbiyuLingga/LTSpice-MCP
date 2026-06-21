"""Phase 5: score a generated ``.asc`` for readability and safety.

This module implements the layout quality policy from
``docs/PROJECT_PLAN.md`` section 12.4. The score is a single integer
between 0 and 100; the project policy maps that to three buckets:

* ``score >= OFFICIAL_THRESHOLD`` (85): acceptable as an official
  template
* ``PROJECT_THRESHOLD <= score < OFFICIAL_THRESHOLD`` (70-84):
  project output only
* ``score < PROJECT_THRESHOLD`` (70): do not promote

The scoring rules (plan 12.4):

* start at 100
* component overlap: -30 each
* missing ground node: -20
* wire crossing (interior): -10 each
* label collision: -5 each
* long wire: -3 each
* min-spacing violation: -2 each

The checker is a pure function over the :class:`ltagent.asc.ASCResult`
returned by :func:`ltagent.asc.render_asc`. It never modifies the
schematic and never executes anything, so it is fully unit-testable
without LTspice.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final

from .asc import GROUND_NODE, ASCResult
from .layout import (
    SYMBOL_BODY_OVERLAP_PADDING,
    Point,
    SymbolPlacement,
    pairwise,
    rect_overlaps,
    symbol_bounding_box,
    wire_crosses,
    wire_length,
)

OFFICIAL_THRESHOLD: Final[int] = 85
"""Score at or above which a layout is acceptable as an official
template (plan 12.4)."""

PROJECT_THRESHOLD: Final[int] = 70
"""Score below which a layout must not be promoted (plan 12.4)."""

LONG_WIRE_LIMIT: Final[int] = 480
"""Wire length (Manhattan distance) above which a wire is flagged
as "long" and -3 points. The MVP signal paths are typically well
under this limit; a long wire usually means the placement helper
made a routing error."""


# --- score weights --------------------------------------------------------

WEIGHT_MISSING_GROUND: Final[int] = 20
WEIGHT_OVERLAP: Final[int] = 30
WEIGHT_CROSSING: Final[int] = 10
WEIGHT_LABEL_COLLISION: Final[int] = 5
WEIGHT_LONG_WIRE: Final[int] = 3
WEIGHT_MIN_SPACING: Final[int] = 2

MIN_SPACING_PADDING: Final[int] = SYMBOL_BODY_OVERLAP_PADDING
"""Padding around a symbol's bounding box below which a neighbour
counts as a min-spacing violation. Equal to
:data:`ltagent.layout.SYMBOL_BODY_OVERLAP_PADDING` so the rule and
the geometry stay in lockstep."""


# --- types ---------------------------------------------------------------


@dataclass(frozen=True)
class LayoutWarning:
    """A single layout issue, surfaced for the agent and the CLI.

    Attributes:
        code: Stable code that the CLI can map to the JSON
            contract's ``warnings`` array.
        detail: Human-readable explanation.
        data: Structured context (positions, IDs, etc.) for the
            agent to act on.
    """

    code: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LayoutResult:
    """The output of :func:`score_layout`.

    Attributes:
        score: Integer 0-100. Clamped to that range.
        overlaps: Number of pairs of symbols whose bounding boxes
            overlap (with padding).
        wire_crossings: Number of interior wire-wire intersections.
        label_collisions: Number of pairs of FLAGs at the same
            coordinate with the same label.
        long_wires: Number of wires longer than
            :data:`LONG_WIRE_LIMIT`.
        min_spacing_violations: Number of pairs of symbols closer
            than :data:`MIN_SPACING_PADDING` but not actually
            overlapping.
        missing_ground: True if no ``FLAG 0`` was emitted in the
            schematic.
        warnings: Ordered list of :class:`LayoutWarning` records.
        classification: One of ``"official"``, ``"project"``,
            ``"reject"``. Convenience for the CLI.
    """

    score: int
    overlaps: int
    wire_crossings: int
    label_collisions: int
    long_wires: int
    min_spacing_violations: int
    missing_ground: bool
    warnings: tuple[LayoutWarning, ...]
    classification: str


# --- classification ------------------------------------------------------


def classify_score(score: int) -> str:
    """Return ``"official"``, ``"project"``, or ``"reject"``.

    Mirrors the project policy from plan 12.4.
    """
    if score >= OFFICIAL_THRESHOLD:
        return "official"
    if score >= PROJECT_THRESHOLD:
        return "project"
    return "reject"


# --- helpers --------------------------------------------------------------


def _wires_from_result(result: ASCResult) -> list[tuple[Point, Point]]:
    """Reconstruct the wire segments emitted by the writer.

    The writer emits each ``WIRE`` line as four integers. We
    re-parse the rendered text rather than threading the wire list
    through :class:`ASCResult` so the checker can also score
    hand-written schematics in tests.
    """
    import re

    wires: list[tuple[Point, Point]] = []
    for m in re.finditer(
        r"^WIRE\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*$",
        result.text,
        flags=re.MULTILINE,
    ):
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        wires.append((Point(x1, y1), Point(x2, y2)))
    return wires


def _flags_from_result(result: ASCResult) -> list[tuple[Point, str]]:
    """Reconstruct the FLAG lines emitted by the writer."""
    import re

    flags: list[tuple[Point, str]] = []
    pattern = re.compile(
        r"^FLAG\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s*$",
        flags=re.MULTILINE,
    )
    for m in pattern.finditer(result.text):
        x, y, label = int(m.group(1)), int(m.group(2)), m.group(3)
        flags.append((Point(x, y), label))
    return flags


def _flags_at_same_point_with_same_label(
    flags: Iterable[tuple[Point, str]],
) -> int:
    """Count pairs of FLAGs at the same point with the same label.

    A pair at the same point but with different labels is *not* a
    collision; the two are simply distinct nets that meet visually.
    """
    seen: dict[tuple[int, int, str], int] = {}
    for p, label in flags:
        key = (p.x, p.y, label)
        seen[key] = seen.get(key, 0) + 1
    return sum(c - 1 for c in seen.values() if c > 1)


# --- main entry point ----------------------------------------------------


def score_layout(result: ASCResult) -> LayoutResult:
    """Score an :class:`ASCResult` and return the structured report.

    The function is pure: it never mutates ``result`` and never
    touches the filesystem. All heuristics are explicit constants
    at the top of the module so the scoring policy is reviewable in
    one place.
    """
    score = 100
    warnings: list[LayoutWarning] = []

    # --- missing ground -------------------------------------------------
    flags = _flags_from_result(result)
    ground_flags = [p for p, label in flags if label == GROUND_NODE]
    missing_ground = len(ground_flags) == 0
    if missing_ground:
        score -= WEIGHT_MISSING_GROUND
        warnings.append(
            LayoutWarning(
                code="LAYOUT_MISSING_GROUND",
                detail=("no FLAG 0 was emitted; the schematic has no ground reference"),
                data={},
            )
        )

    # --- component overlaps and min-spacing violations ------------------
    placements = list(result.placements)
    overlaps = 0
    min_spacing_violations = 0
    boxes: list[tuple[SymbolPlacement, Point, tuple[int, int]]] = []
    for p in placements:
        anchor, size = symbol_bounding_box(p)
        boxes.append((p, anchor, size))
    for box_a, box_b in pairwise(boxes):
        a, a_anchor, a_size = box_a
        b, b_anchor, b_size = box_b
        if rect_overlaps(a_anchor, a_size, b_anchor, b_size):
            overlaps += 1
            warnings.append(
                LayoutWarning(
                    code="LAYOUT_OVERLAP",
                    detail=(
                        f"symbols {a.inst_name!r} and {b.inst_name!r} "
                        "have overlapping bounding boxes"
                    ),
                    data={
                        "a": {"anchor": a_anchor, "size": a_size},
                        "b": {"anchor": b_anchor, "size": b_size},
                    },
                )
            )
        elif rect_overlaps(a_anchor, a_size, b_anchor, b_size, padding=MIN_SPACING_PADDING):
            # Boxes are separate but closer than the minimum
            # spacing. This is a soft warning.
            min_spacing_violations += 1
            warnings.append(
                LayoutWarning(
                    code="LAYOUT_MIN_SPACING",
                    detail=(
                        f"symbols {a.inst_name!r} and {b.inst_name!r} are "
                        f"closer than {MIN_SPACING_PADDING} units"
                    ),
                    data={
                        "a": {"anchor": a_anchor, "size": a_size},
                        "b": {"anchor": b_anchor, "size": b_size},
                    },
                )
            )
    score -= overlaps * WEIGHT_OVERLAP
    score -= min_spacing_violations * WEIGHT_MIN_SPACING

    # --- wire crossings ------------------------------------------------
    wires = _wires_from_result(result)
    wire_crossings = 0
    for wire_a, wire_b in pairwise(wires):
        a1, a2 = wire_a
        b1, b2 = wire_b
        if wire_crosses((a1, a2), (b1, b2)):
            wire_crossings += 1
            warnings.append(
                LayoutWarning(
                    code="LAYOUT_WIRE_CROSSING",
                    detail=(
                        f"wires ({a1.x},{a1.y})-({a2.x},{a2.y}) and "
                        f"({b1.x},{b1.y})-({b2.x},{b2.y}) cross"
                    ),
                    data={
                        "wireA": {"from": a1, "to": a2},
                        "wireB": {"from": b1, "to": b2},
                    },
                )
            )
    score -= wire_crossings * WEIGHT_CROSSING

    # --- long wires ----------------------------------------------------
    long_wires = 0
    for p1, p2 in wires:
        if wire_length(p1, p2) > LONG_WIRE_LIMIT:
            long_wires += 1
            warnings.append(
                LayoutWarning(
                    code="LAYOUT_LONG_WIRE",
                    detail=(
                        f"wire ({p1.x},{p1.y})-({p2.x},{p2.y}) is longer "
                        f"than {LONG_WIRE_LIMIT} units; check routing"
                    ),
                    data={
                        "from": p1,
                        "to": p2,
                        "length": wire_length(p1, p2),
                    },
                )
            )
    score -= long_wires * WEIGHT_LONG_WIRE

    # --- label collisions ---------------------------------------------
    label_collisions = _flags_at_same_point_with_same_label(flags)
    if label_collisions:
        warnings.append(
            LayoutWarning(
                code="LAYOUT_LABEL_COLLISION",
                detail=(
                    f"{label_collisions} FLAG(s) collide with another "
                    "FLAG carrying the same label at the same point"
                ),
                data={"collisionCount": label_collisions},
            )
        )
    score -= label_collisions * WEIGHT_LABEL_COLLISION

    # Clamp and classify.
    score = max(0, min(100, score))
    classification = classify_score(score)
    return LayoutResult(
        score=score,
        overlaps=overlaps,
        wire_crossings=wire_crossings,
        label_collisions=label_collisions,
        long_wires=long_wires,
        min_spacing_violations=min_spacing_violations,
        missing_ground=missing_ground,
        warnings=tuple(warnings),
        classification=classification,
    )


__all__ = [
    "LONG_WIRE_LIMIT",
    "MIN_SPACING_PADDING",
    "OFFICIAL_THRESHOLD",
    "PROJECT_THRESHOLD",
    "WEIGHT_CROSSING",
    "WEIGHT_LABEL_COLLISION",
    "WEIGHT_LONG_WIRE",
    "WEIGHT_MIN_SPACING",
    "WEIGHT_MISSING_GROUND",
    "WEIGHT_OVERLAP",
    "LayoutResult",
    "LayoutWarning",
    "classify_score",
    "score_layout",
]
