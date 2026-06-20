"""Pydantic v2 contracts for the Workbench v2 project schema.

This module is additive: the existing :mod:`ltagent.workbench` (1.0)
module remains the source of truth for the current on-disk project
layout. ``workbench_v2`` defines the v2 contracts that the staged
migrator (added in P1.5) will write and read.

Canonical project v2 layout (ADR 0006):

    hardware.project.json
    design/requirements.json
    design/analog/main.graph.json
    design/schematic/main.view.json
    design/digital/main.digital.json
    design/system.json
    firmware/
    verification/
    runs/<runId>/
    .workbench/history/
    .workbench/snapshots/
    .workbench/transactions/

The contracts are deliberately narrow: the AI never writes these files
directly. They are produced by deterministic generators and validated
by Pydantic before any change set is applied.
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .live.graph_schema import (
    SCHEMA_VERSION as CIRCUIT_GRAPH_SCHEMA_VERSION,
)
from .live.graph_schema import (
    CircuitGraph,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_SCHEMA_VERSION: Final[str] = "2.0"
LEGACY_PROJECT_SCHEMA_VERSION: Final[str] = "1.0"

PROJECT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# File-name constants for the v2 layout. The 1.0 layout used
# ``design/analog/main.circuit.json``; v2 uses ``main.graph.json`` so the
# filename reflects the schema it carries.
FILE_MANIFEST: Final[str] = "hardware.project.json"
FILE_REQUIREMENTS: Final[str] = "design/requirements.json"
FILE_ANALOG_GRAPH: Final[str] = "design/analog/main.graph.json"
FILE_SCHEMATIC_VIEW: Final[str] = "design/schematic/main.view.json"
FILE_DIGITAL: Final[str] = "design/digital/main.digital.json"
FILE_SYSTEM: Final[str] = "design/system.json"
DIR_FIRMWARE: Final[str] = "firmware"
DIR_VERIFICATION: Final[str] = "verification"
DIR_RUNS: Final[str] = "runs"
DIR_INTERNAL: Final[str] = ".workbench"
DIR_INTERNAL_HISTORY: Final[str] = ".workbench/history"
DIR_INTERNAL_SNAPSHOTS: Final[str] = ".workbench/snapshots"
DIR_INTERNAL_TRANSACTIONS: Final[str] = ".workbench/transactions"
FILE_TRANSACTION: Final[str] = ".workbench/transaction.json"

# Document kind -> relative path. The ``analog`` and ``schematic`` documents
# are the two v2 additions; the others reuse the 1.0 names.
DOCUMENT_PATHS: Final[dict[str, str]] = {
    "requirements": FILE_REQUIREMENTS,
    "analog": FILE_ANALOG_GRAPH,
    "schematic": FILE_SCHEMATIC_VIEW,
    "digital": FILE_DIGITAL,
    "system": FILE_SYSTEM,
}

#: Symbol kinds the schematic view accepts in v1. The list is the union
#: of the live-editing component kinds plus the editorial-only ``gnd`` /
#: ``label`` kinds used by the prototype schematic.
SCHEMATIC_SYMBOL_KINDS: Final[frozenset[str]] = frozenset(
    {
        "resistor",
        "capacitor",
        "inductor",
        "diode",
        "npn",
        "pnp",
        "nmos",
        "pmos",
        "opamp",
        "voltage_source",
        "current_source",
        "gnd",
        "label",
    }
)

#: Allowed rotations in degrees. 90-degree increments only.
SCHEMATIC_ROTATION_VALUES: Final[frozenset[int]] = frozenset({0, 90, 180, 270})

#: Allowed safety classes. Matches the master plan's "simulation-only /
#: prototype-safe / mains-dangerous" ladder.
SAFETY_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "simulation_only",
        "prototype_safe",
        "mains_dangerous",
    }
)


# ---------------------------------------------------------------------------
# Enums / literals
# ---------------------------------------------------------------------------

SafetyClass = Literal["simulation_only", "prototype_safe", "mains_dangerous"]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class HardwareProject(BaseModel):
    """Top-level v2 manifest.

    Lives at ``hardware.project.json`` in the project directory. The
    ``revision`` field is a monotonically increasing counter; every
    accepted change set bumps it. ``schemaVersion`` is always ``"2.0"``
    on disk; older manifests are rewritten by the migrator.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    projectId: str
    displayName: str
    revision: int = Field(ge=0)
    createdAt: str | None = None
    updatedAt: str | None = None

    @field_validator("projectId")
    @classmethod
    def _project_id_matches(cls, v: str) -> str:
        if not PROJECT_ID_PATTERN.match(v):
            raise ValueError(
                f"projectId {v!r} must match {PROJECT_ID_PATTERN.pattern}"
            )
        return v

    @field_validator("displayName")
    @classmethod
    def _display_name_nonempty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("displayName must be a non-empty string")
        return v


# ---------------------------------------------------------------------------
# Document contracts
# ---------------------------------------------------------------------------


class Requirements(BaseModel):
    """Natural-language requirements + structured constraints/goals.

    Replaces the 1.0 ``requirements.json`` shape but keeps the same
    ``constraints`` / ``goals`` keys so the 1.0 -> 2.0 migrator is a
    near-identity copy with a schema bump.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    text: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    goals: list[str] = Field(default_factory=list)
    safetyClass: SafetyClass | None = None

    @field_validator("constraints")
    @classmethod
    def _flat_constraints(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key, value in v.items():
            if isinstance(value, (dict, list)):
                raise ValueError(
                    f"constraint {key!r} must be a scalar (str, int, float, bool)"
                )
        return v

    @field_validator("goals")
    @classmethod
    def _goals_are_strings(cls, v: list[str]) -> list[str]:
        for index, goal in enumerate(v):
            if not isinstance(goal, str):
                raise ValueError(f"goal[{index}] must be a string")
        return list(v)

    @field_validator("safetyClass")
    @classmethod
    def _safety_class_known(cls, v: str | None) -> str | None:
        if v is not None and v not in SAFETY_CLASSES:
            raise ValueError(
                f"safetyClass {v!r} is not recognised; expected one of "
                f"{sorted(SAFETY_CLASSES)}"
            )
        return v


# Re-export the existing CircuitGraph as the v2 analog document contract.
# The schema version is exposed at module level for the migrator.
AnalogGraph: Final[type] = CircuitGraph
ANALOG_GRAPH_SCHEMA_VERSION: Final[str] = CIRCUIT_GRAPH_SCHEMA_VERSION


class SchematicSymbol(BaseModel):
    """One placed symbol on the schematic view.

    The grid coordinates are in the schematic's ``gridSize`` units; the
    editor multiplies by ``gridSize`` for screen pixels. ``rotation`` is
    a multiple of 90 degrees; ``mirror`` flips horizontally. ``properties``
    carries editor-only metadata (e.g. ``value``, ``model``) that the
    generator projects into the netlist/IR on demand.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    kind: str
    x: int
    y: int
    rotation: int = 0
    mirror: bool = False
    label: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        if v not in SCHEMATIC_SYMBOL_KINDS:
            raise ValueError(
                f"schematic symbol kind {v!r} is not supported; "
                f"expected one of {sorted(SCHEMATIC_SYMBOL_KINDS)}"
            )
        return v

    @field_validator("rotation")
    @classmethod
    def _rotation_multiple_of_90(cls, v: int) -> int:
        if v not in SCHEMATIC_ROTATION_VALUES:
            raise ValueError(
                f"rotation {v} must be one of {sorted(SCHEMATIC_ROTATION_VALUES)}"
            )
        return v


class SchematicWire(BaseModel):
    """One orthogonal wire between two or more grid points."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    points: list[tuple[int, int]] = Field(min_length=2)
    net: str | None = None

    @field_validator("points")
    @classmethod
    def _grid_points(cls, v: list[tuple[int, int]]) -> list[tuple[int, int]]:
        for index, point in enumerate(v):
            if not isinstance(point, tuple) or len(point) != 2:
                raise ValueError(
                    f"wire point {index!r} must be a (x, y) tuple"
                )
            x, y = point
            if not isinstance(x, int) or not isinstance(y, int):
                raise ValueError(
                    f"wire point {index!r} must contain integers"
                )
        return list(v)


class SchematicNetLabel(BaseModel):
    """A text label that ties a screen position to a net name."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    x: int
    y: int
    net: str = Field(min_length=1, max_length=64)


class SchematicView(BaseModel):
    """Canonical schematic layout document.

    The schematic view is the layout-only companion to the analog graph.
    Phase 1 makes the schematic view a layout document with stable
    symbol ids; the AI (Phase 8) is responsible for keeping symbol ids
    in sync with the analog graph.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    gridSize: int = Field(default=16, ge=1, le=512)
    viewport: dict[str, int] | None = None
    symbols: list[SchematicSymbol] = Field(default_factory=list)
    wires: list[SchematicWire] = Field(default_factory=list)
    netLabels: list[SchematicNetLabel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _symbol_ids_unique(self) -> SchematicView:
        seen: set[str] = set()
        for symbol in self.symbols:
            if symbol.id in seen:
                raise ValueError(
                    f"schematic symbol id {symbol.id!r} is duplicated"
                )
            seen.add(symbol.id)
        return self

    @model_validator(mode="after")
    def _wire_ids_unique(self) -> SchematicView:
        seen: set[str] = set()
        for wire in self.wires:
            if wire.id in seen:
                raise ValueError(f"schematic wire id {wire.id!r} is duplicated")
            seen.add(wire.id)
        return self


class DigitalDesignDocument(BaseModel):
    """Digital design document. v1 keeps the Tiny8 ``DesignIR`` shape.

    Phase 6 will replace this with the generic :class:`DigitalDesignIR`
    contract. The v2 wrapper exists today so the canonical project
    layout has a typed document for ``design/digital`` from day one.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    design: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("design")
    @classmethod
    def _design_is_object(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("digital design payload must be an object")
        return dict(v)


class SystemSpec(BaseModel):
    """System-level block/connection description (Phase 8/9 work)."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    connections: list[dict[str, Any]] = Field(default_factory=list)
    clockHz: int | None = Field(default=None, ge=1)


__all__ = [
    "ANALOG_GRAPH_SCHEMA_VERSION",
    "DIR_FIRMWARE",
    "DIR_INTERNAL",
    "DIR_INTERNAL_HISTORY",
    "DIR_INTERNAL_SNAPSHOTS",
    "DIR_INTERNAL_TRANSACTIONS",
    "DIR_RUNS",
    "DIR_VERIFICATION",
    "DOCUMENT_PATHS",
    "FILE_ANALOG_GRAPH",
    "FILE_DIGITAL",
    "FILE_MANIFEST",
    "FILE_REQUIREMENTS",
    "FILE_SCHEMATIC_VIEW",
    "FILE_SYSTEM",
    "FILE_TRANSACTION",
    "LEGACY_PROJECT_SCHEMA_VERSION",
    "PROJECT_ID_PATTERN",
    "PROJECT_SCHEMA_VERSION",
    "SAFETY_CLASSES",
    "SCHEMATIC_ROTATION_VALUES",
    "SCHEMATIC_SYMBOL_KINDS",
    "AnalogGraph",
    "DigitalDesignDocument",
    "HardwareProject",
    "Requirements",
    "SafetyClass",
    "SchematicNetLabel",
    "SchematicSymbol",
    "SchematicView",
    "SchematicWire",
    "SystemSpec",
]
