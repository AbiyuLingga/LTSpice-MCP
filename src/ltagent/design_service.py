"""Typed ChangeSet parser and design service for Workbench v2.

The ChangeSet is the public contract for any project mutation:
manual edits from the desktop shell, AI-generated proposals, and
Codex MCP calls all flow through :class:`DesignService.apply_change_set`.

Public operation types (v1):

* ``add_component`` / ``remove_component`` / ``set_component_value`` /
  ``rename_component``
* ``connect_pin`` / ``disconnect_pin`` / ``rename_net``
* ``add_directive`` / ``add_measurement``
* ``place_node`` / ``move_node`` / ``rotate_node`` / ``set_wire_route`` /
  ``set_net_label`` / ``set_grid_size``
* ``replace_document`` (kept as a compatibility path for the
  v1 workbench surface)

Every operation carries the document it targets (``analog`` /
``schematic`` / ``digital`` / ``system`` / ``requirements``). The
design service is the only writer of the v2 documents; callers
never touch the on-disk JSON directly.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .digital_ir_v2 import DigitalDesignIRV2
from .live.graph_schema import (
    SCHEMA_VERSION as _GRAPH_SCHEMA_VERSION,
)
from .live.graph_schema import (
    SUPPORTED_ANALYSIS_KINDS as _GRAPH_ANALYSIS_KINDS,
)
from .live.graph_schema import (
    CircuitGraph,
    Component,
    ComponentKind,
    NetType,
    PinMap,
)
from .live.graph_schema import (
    Net as GraphNet,
)
from .security import (
    PathSafetyError,
    safe_resolve_under,
)
from .workbench_v2 import (
    FILE_ANALOG_GRAPH,
    FILE_DIGITAL,
    FILE_MANIFEST,
    FILE_REQUIREMENTS,
    FILE_SCHEMATIC_VIEW,
    FILE_SYSTEM,
    PROJECT_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION_LITERAL,
    SCHEMATIC_ROTATION_VALUES,
    SCHEMATIC_SYMBOL_KINDS,
    HardwareProject,
    Requirements,
    SchematicNetLabel,
    SchematicPinConnection,
    SchematicSymbol,
    SchematicView,
    SystemSpec,
)

# ---------------------------------------------------------------------------
# Stable error codes
# ---------------------------------------------------------------------------

ERR_CHANGESET_INVALID: Final[str] = "WORKBENCH_V2_CHANGESET_INVALID"
ERR_CHANGESET_CONFLICT: Final[str] = "WORKBENCH_V2_CHANGESET_CONFLICT"
ERR_CHANGESET_OPERATION_INVALID: Final[str] = "WORKBENCH_V2_CHANGESET_OPERATION_INVALID"
ERR_CHANGESET_DOCUMENT_INVALID: Final[str] = "WORKBENCH_V2_DOCUMENT_INVALID"
ERR_CHANGESET_IO: Final[str] = "WORKBENCH_V2_IO"
ERR_CHANGESET_VALIDATION: Final[str] = "WORKBENCH_V2_VALIDATION"
ERR_DOCUMENT_NOT_FOUND: Final[str] = "WORKBENCH_V2_DOCUMENT_NOT_FOUND"
ERR_PROJECT_NOT_FOUND: Final[str] = "WORKBENCH_V2_PROJECT_NOT_FOUND"

DOCUMENT_NAMES: Final[tuple[str, ...]] = (
    "requirements",
    "analog",
    "schematic",
    "digital",
    "system",
)

DOCUMENT_FILE_PATHS: Final[dict[str, str]] = {
    "requirements": FILE_REQUIREMENTS,
    "analog": FILE_ANALOG_GRAPH,
    "schematic": FILE_SCHEMATIC_VIEW,
    "digital": FILE_DIGITAL,
    "system": FILE_SYSTEM,
}


class WorkbenchV2Error(ValueError):
    """Structured error from the v2 design service."""

    def __init__(self, code: str, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, Any] = dict(data) if data else {}


# ---------------------------------------------------------------------------
# ChangeSet Pydantic contracts
# ---------------------------------------------------------------------------


class _BaseOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str
    type: str

    @field_validator("document")
    @classmethod
    def _document_known(cls, v: str) -> str:
        if v not in DOCUMENT_NAMES:
            raise ValueError(f"document {v!r} is not supported; allowed: {sorted(DOCUMENT_NAMES)}")
        return v


class AddComponentOp(_BaseOp):
    type: str = "add_component"
    componentId: str
    kind: str
    value: str | None = None
    model: str | None = None
    role: str | None = None
    pins: dict[str, str] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        try:
            return ComponentKind(v).value
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class RemoveComponentOp(_BaseOp):
    type: str = "remove_component"
    componentId: str


class SetComponentValueOp(_BaseOp):
    type: str = "set_component_value"
    componentId: str
    value: str


class RenameComponentOp(_BaseOp):
    type: str = "rename_component"
    oldId: str
    newId: str


class ConnectPinOp(_BaseOp):
    type: str = "connect_pin"
    componentId: str
    pin: str
    net: str


class DisconnectPinOp(_BaseOp):
    type: str = "disconnect_pin"
    componentId: str
    pin: str


class RenameNetOp(_BaseOp):
    type: str = "rename_net"
    oldName: str
    newName: str


class AddDirectiveOp(_BaseOp):
    type: str = "add_directive"
    name: str
    args: str = ""


class AddMeasurementOp(_BaseOp):
    type: str = "add_measurement"
    name: str
    analysis: str
    expression: str


class PlaceNodeOp(_BaseOp):
    type: str = "place_node"
    symbolId: str
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
            raise ValueError(f"schematic symbol kind {v!r} is not supported")
        return v


class MoveNodeOp(_BaseOp):
    type: str = "move_node"
    symbolId: str
    x: int
    y: int


class RotateNodeOp(_BaseOp):
    type: str = "rotate_node"
    symbolId: str
    rotation: int

    @field_validator("rotation")
    @classmethod
    def _rotation_ok(cls, v: int) -> int:
        if v not in SCHEMATIC_ROTATION_VALUES:
            raise ValueError(f"rotation {v} must be one of {sorted(SCHEMATIC_ROTATION_VALUES)}")
        return v


class DeleteNodeOp(_BaseOp):
    type: str = "delete_node"
    symbolId: str


class SetNodePropertiesOp(_BaseOp):
    type: str = "set_node_properties"
    symbolId: str
    label: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class SetWireRouteOp(_BaseOp):
    type: str = "set_wire_route"
    wireId: str
    points: list[tuple[int, int]] = Field(min_length=2)
    net: str | None = None
    connections: list[SchematicPinConnection] = Field(default_factory=list, max_length=2)


class RemoveWireOp(_BaseOp):
    type: str = "remove_wire"
    wireId: str


class SetNetLabelOp(_BaseOp):
    type: str = "set_net_label"
    labelId: str
    x: int
    y: int
    net: str


class SetGridSizeOp(_BaseOp):
    type: str = "set_grid_size"
    gridSize: int = Field(ge=1, le=512)


class SetDigitalDesignOp(_BaseOp):
    type: str = "set_digital_design"
    design: dict[str, Any]


class ReplaceDocumentOp(_BaseOp):
    type: str = "replace_document"
    value: dict[str, Any]


OP_TYPES: Final[tuple[type[_BaseOp], ...]] = (
    AddComponentOp,
    RemoveComponentOp,
    SetComponentValueOp,
    RenameComponentOp,
    ConnectPinOp,
    DisconnectPinOp,
    RenameNetOp,
    AddDirectiveOp,
    AddMeasurementOp,
    PlaceNodeOp,
    MoveNodeOp,
    RotateNodeOp,
    DeleteNodeOp,
    SetNodePropertiesOp,
    SetWireRouteOp,
    RemoveWireOp,
    SetNetLabelOp,
    SetGridSizeOp,
    SetDigitalDesignOp,
    ReplaceDocumentOp,
)


class ChangeSet(BaseModel):
    """A single revision-guarded change set."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str
    baseRevision: int = Field(ge=0)
    actor: str = "user"
    clientRequestId: str | None = None
    operations: list[dict[str, Any]] = Field(min_length=1)
    validationPlan: list[str] = Field(default_factory=list)

    @field_validator("schemaVersion")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != PROJECT_SCHEMA_VERSION:
            raise ValueError(
                f"change set schemaVersion {v!r} is not supported; expected "
                f"{PROJECT_SCHEMA_VERSION!r}"
            )
        return v

    @field_validator("operations")
    @classmethod
    def _ops_non_empty(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not v:
            raise ValueError("change set must contain at least one operation")
        return v


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSetResult:
    revision: int
    changed_documents: tuple[str, ...]
    history_step: int
    affected_component_ids: tuple[str, ...] = field(default_factory=tuple)
    affected_net_names: tuple[str, ...] = field(default_factory=tuple)
    affected_symbol_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "changedDocuments": list(self.changed_documents),
            "historyStep": self.history_step,
            "affectedComponentIds": list(self.affected_component_ids),
            "affectedNetNames": list(self.affected_net_names),
            "affectedSymbolIds": list(self.affected_symbol_ids),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_op_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``raw`` as a fresh dict with stable ordering."""
    return dict(raw)


def _read_json(path) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    import json

    text = path.read_text(encoding="utf-8")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise WorkbenchV2Error(
            ERR_DOCUMENT_NOT_FOUND,
            f"{path} must contain a JSON object",
            data={"path": str(path)},
        )
    return parsed


def _write_json_atomic(path, payload: Mapping[str, Any]) -> None:  # type: ignore[no-untyped-def]
    import json
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _history_step(history_path) -> int:  # type: ignore[no-untyped-def]
    import json
    from pathlib import Path

    history_path = Path(history_path)
    if not history_path.exists():
        return 1
    text = history_path.read_text(encoding="utf-8")
    last_step = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        step = record.get("step")
        if isinstance(step, int):
            last_step = max(last_step, step)
    return last_step + 1


def _append_history(history_path, record: Mapping[str, Any]) -> None:  # type: ignore[no-untyped-def]
    import json
    from pathlib import Path

    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _validate_analog(payload: Mapping[str, Any]) -> CircuitGraph:
    try:
        return CircuitGraph.model_validate(payload)
    except ValidationError as exc:
        raise WorkbenchV2Error(
            ERR_CHANGESET_VALIDATION,
            f"analog document failed validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc


def _validate_schematic(payload: Mapping[str, Any]) -> SchematicView:
    try:
        return SchematicView.model_validate(payload)
    except ValidationError as exc:
        raise WorkbenchV2Error(
            ERR_CHANGESET_VALIDATION,
            f"schematic document failed validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc


def _validate_requirements(payload: Mapping[str, Any]) -> Requirements:
    try:
        return Requirements.model_validate(payload)
    except ValidationError as exc:
        raise WorkbenchV2Error(
            ERR_CHANGESET_VALIDATION,
            f"requirements document failed validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc


def _validate_digital(payload: Mapping[str, Any]) -> DigitalDesignDocument:  # type: ignore[name-defined]  # noqa: F821
    from .workbench_v2 import DigitalDesignDocument

    try:
        return DigitalDesignDocument.model_validate(payload)
    except ValidationError as exc:
        raise WorkbenchV2Error(
            ERR_CHANGESET_VALIDATION,
            f"digital document failed validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc


def _validate_system(payload: Mapping[str, Any]) -> SystemSpec:
    try:
        return SystemSpec.model_validate(payload)
    except ValidationError as exc:
        raise WorkbenchV2Error(
            ERR_CHANGESET_VALIDATION,
            f"system document failed validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc


# ---------------------------------------------------------------------------
# Per-op reducers
# ---------------------------------------------------------------------------


def _op_add_component(state: dict[str, Any], op: AddComponentOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.componentId in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.componentId!r} already exists",
            data={"componentId": op.componentId},
        )
    component = Component(
        id=op.componentId,
        kind=ComponentKind(op.kind),
        value=op.value,
        model=op.model,
        role=op.role,
        pins=PinMap(pins=dict(op.pins)),
    )
    new_components = dict(graph.components)
    new_components[op.componentId] = component
    new_nets = dict(graph.nets)
    for net_name in component.pins.pins.values():
        if net_name not in new_nets:
            new_nets[net_name] = _net_for(net_name)
    new_graph = graph.model_copy(update={"components": new_components, "nets": new_nets})
    state["analog"] = new_graph.model_dump(mode="json", exclude_none=True)


def _net_for(name: str) -> GraphNet:
    if name == "0":
        return GraphNet(name=name, type=NetType.GROUND)
    return GraphNet(name=name, type=NetType.SIGNAL)


def _op_remove_component(state: dict[str, Any], op: RemoveComponentOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.componentId not in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.componentId!r} does not exist",
            data={"componentId": op.componentId},
        )
    new_components = {k: v for k, v in graph.components.items() if k != op.componentId}
    state["analog"] = graph.model_copy(update={"components": new_components}).model_dump(
        mode="json", exclude_none=True
    )


def _op_set_component_value(state: dict[str, Any], op: SetComponentValueOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.componentId not in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.componentId!r} does not exist",
            data={"componentId": op.componentId},
        )
    new_components = dict(graph.components)
    comp = new_components[op.componentId]
    new_components[op.componentId] = comp.model_copy(update={"value": op.value})
    state["analog"] = graph.model_copy(update={"components": new_components}).model_dump(
        mode="json", exclude_none=True
    )


def _op_rename_component(state: dict[str, Any], op: RenameComponentOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.oldId not in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.oldId!r} does not exist",
            data={"oldId": op.oldId},
        )
    if op.newId in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.newId!r} already exists",
            data={"newId": op.newId},
        )
    new_components = {op.newId if k == op.oldId else k: v for k, v in graph.components.items()}
    new_components[op.newId] = new_components.pop(op.oldId)
    new_components = {
        op.newId: new_components.pop(op.oldId),
        **{k: v for k, v in new_components.items() if k != op.newId},
    }
    state["analog"] = graph.model_copy(update={"components": new_components}).model_dump(
        mode="json", exclude_none=True
    )


def _op_connect_pin(state: dict[str, Any], op: ConnectPinOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.componentId not in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.componentId!r} does not exist",
            data={"componentId": op.componentId},
        )
    component = graph.components[op.componentId]
    new_pins = dict(component.pins.pins)
    new_pins[op.pin] = op.net
    new_components = dict(graph.components)
    new_components[op.componentId] = component.model_copy(update={"pins": PinMap(pins=new_pins)})
    new_nets = dict(graph.nets)
    if op.net not in new_nets:
        new_nets[op.net] = _net_for(op.net)
    state["analog"] = graph.model_copy(
        update={"components": new_components, "nets": new_nets}
    ).model_dump(mode="json", exclude_none=True)


def _op_disconnect_pin(state: dict[str, Any], op: DisconnectPinOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.componentId not in graph.components:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"component {op.componentId!r} does not exist",
            data={"componentId": op.componentId},
        )
    component = graph.components[op.componentId]
    new_pins = {k: v for k, v in component.pins.pins.items() if k != op.pin}
    new_components = dict(graph.components)
    new_components[op.componentId] = component.model_copy(update={"pins": PinMap(pins=new_pins)})
    state["analog"] = graph.model_copy(update={"components": new_components}).model_dump(
        mode="json", exclude_none=True
    )


def _op_rename_net(state: dict[str, Any], op: RenameNetOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.oldName not in graph.nets:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"net {op.oldName!r} does not exist",
            data={"oldName": op.oldName},
        )
    new_nets = {op.newName if k == op.oldName else k: v for k, v in graph.nets.items()}
    new_components: dict[str, Component] = {}
    for cid, comp in graph.components.items():
        remapped = {op.newName if n == op.oldName else n: n for n in comp.pins.pins.values()}
        new_components[cid] = comp.model_copy(update={"pins": PinMap(pins=remapped)})
    state["analog"] = graph.model_copy(
        update={"nets": new_nets, "components": new_components}
    ).model_dump(mode="json", exclude_none=True)


def _op_add_directive(state: dict[str, Any], op: AddDirectiveOp) -> None:
    # Directives are recorded as a simple list in the graph metadata
    # for Phase 2. Phase 5 will lift the graph's structured analysis
    # block instead.
    graph = _validate_analog(state["analog"])
    payload = {"name": op.name, "args": op.args}
    new_directives = [*list(graph.directives), payload]
    state["analog"] = graph.model_copy(update={"directives": new_directives}).model_dump(
        mode="json", exclude_none=True
    )


def _op_add_measurement(state: dict[str, Any], op: AddMeasurementOp) -> None:
    graph = _validate_analog(state["analog"])
    if op.analysis not in _GRAPH_ANALYSIS_KINDS:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"analysis {op.analysis!r} is not supported",
            data={"analysis": op.analysis},
        )
    from .live.graph_schema import AnalysisKind as GraphAnalysisKind
    from .live.graph_schema import Measurement as GraphMeasurement

    measurement = GraphMeasurement(
        name=op.name,
        analysis=GraphAnalysisKind(op.analysis),
        expression=op.expression,
    )
    new_measurements = [*list(graph.measurements), measurement]
    state["analog"] = graph.model_copy(update={"measurements": new_measurements}).model_dump(
        mode="json", exclude_none=True
    )


def _op_place_node(state: dict[str, Any], op: PlaceNodeOp) -> None:
    view = _validate_schematic(state["schematic"])
    if op.symbolId in {s.id for s in view.symbols}:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic symbol {op.symbolId!r} already exists",
            data={"symbolId": op.symbolId},
        )
    if op.kind not in SCHEMATIC_SYMBOL_KINDS:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic symbol kind {op.kind!r} is not supported",
            data={"kind": op.kind, "allowed": sorted(SCHEMATIC_SYMBOL_KINDS)},
        )
    symbol = SchematicSymbol(
        id=op.symbolId,
        kind=op.kind,
        x=op.x,
        y=op.y,
        rotation=op.rotation,
        mirror=op.mirror,
        label=op.label,
        properties=dict(op.properties),
    )
    state["schematic"] = view.model_copy(
        update={"symbols": [*list(view.symbols), symbol]}
    ).model_dump(mode="json", exclude_none=True)


def _op_move_node(state: dict[str, Any], op: MoveNodeOp) -> None:
    view = _validate_schematic(state["schematic"])
    new_symbols: list[SchematicSymbol] = []
    moved = False
    for symbol in view.symbols:
        if symbol.id == op.symbolId:
            new_symbols.append(symbol.model_copy(update={"x": op.x, "y": op.y}))
            moved = True
        else:
            new_symbols.append(symbol)
    if not moved:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic symbol {op.symbolId!r} does not exist",
            data={"symbolId": op.symbolId},
        )
    state["schematic"] = view.model_copy(update={"symbols": new_symbols}).model_dump(
        mode="json", exclude_none=True
    )


def _op_rotate_node(state: dict[str, Any], op: RotateNodeOp) -> None:
    view = _validate_schematic(state["schematic"])
    new_symbols: list[SchematicSymbol] = []
    rotated = False
    for symbol in view.symbols:
        if symbol.id == op.symbolId:
            new_symbols.append(symbol.model_copy(update={"rotation": op.rotation}))
            rotated = True
        else:
            new_symbols.append(symbol)
    if not rotated:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic symbol {op.symbolId!r} does not exist",
            data={"symbolId": op.symbolId},
        )
    state["schematic"] = view.model_copy(update={"symbols": new_symbols}).model_dump(
        mode="json", exclude_none=True
    )


def _op_delete_node(state: dict[str, Any], op: DeleteNodeOp) -> None:
    view = _validate_schematic(state["schematic"])
    symbols = [symbol for symbol in view.symbols if symbol.id != op.symbolId]
    if len(symbols) == len(view.symbols):
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic symbol {op.symbolId!r} does not exist",
            data={"symbolId": op.symbolId},
        )
    state["schematic"] = view.model_copy(update={"symbols": symbols}).model_dump(
        mode="json", exclude_none=True
    )


def _op_set_node_properties(state: dict[str, Any], op: SetNodePropertiesOp) -> None:
    view = _validate_schematic(state["schematic"])
    symbols: list[SchematicSymbol] = []
    updated = False
    for symbol in view.symbols:
        if symbol.id == op.symbolId:
            symbols.append(
                symbol.model_copy(update={"label": op.label, "properties": dict(op.properties)})
            )
            updated = True
        else:
            symbols.append(symbol)
    if not updated:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic symbol {op.symbolId!r} does not exist",
            data={"symbolId": op.symbolId},
        )
    state["schematic"] = view.model_copy(update={"symbols": symbols}).model_dump(
        mode="json", exclude_none=True
    )


def _op_set_wire_route(state: dict[str, Any], op: SetWireRouteOp) -> None:
    from .workbench_v2 import SchematicWire

    view = _validate_schematic(state["schematic"])
    wire = SchematicWire(
        id=op.wireId,
        points=list(op.points),
        net=op.net,
        connections=list(op.connections),
    )
    new_wires: list[SchematicWire] = []
    replaced = False
    for existing in view.wires:
        if existing.id == op.wireId:
            new_wires.append(wire)
            replaced = True
        else:
            new_wires.append(existing)
    if not replaced:
        new_wires.append(wire)
    state["schematic"] = view.model_copy(update={"wires": new_wires}).model_dump(
        mode="json", exclude_none=True
    )


def _op_remove_wire(state: dict[str, Any], op: RemoveWireOp) -> None:
    view = _validate_schematic(state["schematic"])
    wires = [wire for wire in view.wires if wire.id != op.wireId]
    if len(wires) == len(view.wires):
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"schematic wire {op.wireId!r} does not exist",
            data={"wireId": op.wireId},
        )
    state["schematic"] = view.model_copy(update={"wires": wires}).model_dump(
        mode="json", exclude_none=True
    )


def _op_set_net_label(state: dict[str, Any], op: SetNetLabelOp) -> None:
    view = _validate_schematic(state["schematic"])
    label = SchematicNetLabel(id=op.labelId, x=op.x, y=op.y, net=op.net)
    new_labels: list[SchematicNetLabel] = []
    replaced = False
    for existing in view.netLabels:
        if existing.id == op.labelId:
            new_labels.append(label)
            replaced = True
        else:
            new_labels.append(existing)
    if not replaced:
        new_labels.append(label)
    state["schematic"] = view.model_copy(update={"netLabels": new_labels}).model_dump(
        mode="json", exclude_none=True
    )


def _op_set_grid_size(state: dict[str, Any], op: SetGridSizeOp) -> None:
    view = _validate_schematic(state["schematic"])
    state["schematic"] = view.model_copy(update={"gridSize": op.gridSize}).model_dump(
        mode="json", exclude_none=True
    )


def _op_set_digital_design(state: dict[str, Any], op: SetDigitalDesignOp) -> None:
    document = _validate_digital(state["digital"])
    try:
        design = DigitalDesignIRV2.model_validate(op.design)
    except ValidationError as exc:
        raise WorkbenchV2Error(
            ERR_CHANGESET_OPERATION_INVALID,
            f"digital design failed v2 validation: {exc}",
            data={"errors": exc.errors()},
        ) from exc
    state["digital"] = document.model_copy(
        update={"design": design.model_dump(mode="json")}
    ).model_dump(mode="json", exclude_none=True)


def _op_replace_document(state: dict[str, Any], op: ReplaceDocumentOp) -> None:
    if op.document == "analog":
        _validate_analog(op.value)
    elif op.document == "schematic":
        _validate_schematic(op.value)
    elif op.document == "requirements":
        _validate_requirements(op.value)
    elif op.document == "digital":
        _validate_digital(op.value)
    elif op.document == "system":
        _validate_system(op.value)
    state[op.document] = dict(op.value)


OP_REDUCERS = {
    "add_component": _op_add_component,
    "remove_component": _op_remove_component,
    "set_component_value": _op_set_component_value,
    "rename_component": _op_rename_component,
    "connect_pin": _op_connect_pin,
    "disconnect_pin": _op_disconnect_pin,
    "rename_net": _op_rename_net,
    "add_directive": _op_add_directive,
    "add_measurement": _op_add_measurement,
    "place_node": _op_place_node,
    "move_node": _op_move_node,
    "rotate_node": _op_rotate_node,
    "delete_node": _op_delete_node,
    "set_node_properties": _op_set_node_properties,
    "set_wire_route": _op_set_wire_route,
    "remove_wire": _op_remove_wire,
    "set_net_label": _op_set_net_label,
    "set_grid_size": _op_set_grid_size,
    "set_digital_design": _op_set_digital_design,
    "replace_document": _op_replace_document,
}


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


@dataclass
class DesignService:
    """Apply typed ChangeSets to a v2 workbench project on disk.

    The service is the only writer of the v2 documents. Every
    mutation goes through :meth:`apply_change_set`, which:
    * loads the affected documents from disk;
    * validates the change set (``schemaVersion``, ``baseRevision``,
      per-operation Pydantic contracts);
    * runs every reducer against the in-memory state;
    * re-validates the post-state through the v2 contracts;
    * atomically writes the new documents, bumps the revision,
      and appends a history line.

    The service also keeps a per-project undo / redo stack
    (in-memory) of the last 32 change sets.
    """

    projects_root: str
    undo_depth: int = 32
    _undo: dict[str, list[ChangeSetResult]] = field(default_factory=dict)
    _redo: dict[str, list[ChangeSetResult]] = field(default_factory=dict)

    def _project_dir(self, project_id: str):  # type: ignore[no-untyped-def]
        from pathlib import Path

        root = Path(self.projects_root).expanduser().resolve(strict=False)
        try:
            return safe_resolve_under(root / project_id, root, must_exist=False)
        except PathSafetyError as exc:
            raise WorkbenchV2Error(
                ERR_PROJECT_NOT_FOUND,
                exc.message,
                data={"projectId": project_id, "projectsRoot": str(root)},
            ) from exc

    def open_project(self, project_id: str) -> HardwareProject:

        project_dir = self._project_dir(project_id)
        manifest_path = project_dir / FILE_MANIFEST
        if not manifest_path.is_file():
            raise WorkbenchV2Error(
                ERR_PROJECT_NOT_FOUND,
                f"project {project_id!r} has no manifest at {manifest_path}",
                data={"projectId": project_id},
            )
        try:
            manifest = HardwareProject.model_validate(_read_json(manifest_path))
        except ValidationError as exc:
            raise WorkbenchV2Error(
                ERR_CHANGESET_VALIDATION,
                f"manifest for project {project_id!r} is invalid: {exc}",
                data={"errors": exc.errors()},
            ) from exc
        return manifest

    def _load_state(self, project_dir) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        state: dict[str, Any] = {}
        for document, relative in DOCUMENT_FILE_PATHS.items():
            path = project_dir / relative
            if not path.is_file():
                state[document] = None
                continue
            state[document] = _read_json(path)
        return state

    def read_document(self, project_id: str, document: str) -> dict[str, Any]:
        """Read and validate one project document."""
        if document not in DOCUMENT_FILE_PATHS:
            raise WorkbenchV2Error(
                ERR_DOCUMENT_NOT_FOUND,
                f"document {document!r} is not supported",
                data={"document": document},
            )
        project = self.open_project(project_id)
        state = self._load_state(self._project_dir(project_id))
        payload = state.get(document) or self._default_state(project)[document]
        if document == "analog":
            return dict(_validate_analog(payload).model_dump(mode="json", exclude_none=True))
        if document == "schematic":
            return dict(_validate_schematic(payload).model_dump(mode="json", exclude_none=True))
        if document == "requirements":
            return dict(_validate_requirements(payload).model_dump(mode="json", exclude_none=True))
        if document == "digital":
            return dict(_validate_digital(payload).model_dump(mode="json", exclude_none=True))
        return dict(_validate_system(payload).model_dump(mode="json", exclude_none=True))

    def validate_project(self, project_id: str) -> dict[str, Any]:
        """Validate the manifest and every typed project document."""
        project = self.open_project(project_id)
        for document in DOCUMENT_NAMES:
            self.read_document(project_id, document)
        return {
            "projectId": project.projectId,
            "revision": project.revision,
            "status": "pass",
        }

    def _default_state(self, project: HardwareProject) -> dict[str, Any]:
        return {
            "requirements": {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "text": "",
                "constraints": {},
                "goals": [],
            },
            "analog": {
                "schemaVersion": _GRAPH_SCHEMA_VERSION,
                "projectId": project.projectId,
                "topology": "",
                "components": {},
                "nets": {},
                "analyses": [],
                "measurements": [],
                "directives": [],
            },
            "schematic": {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "gridSize": 16,
                "viewport": None,
                "symbols": [],
                "wires": [],
                "netLabels": [],
            },
            "digital": {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "design": {
                    "schemaVersion": "2.0",
                    "topModule": "top",
                    "ports": [],
                    "signals": [],
                    "instances": [],
                    "connections": [],
                    "testGoals": [],
                },
                "legacyDesign": None,
                "userHdl": "",
                "notes": "",
            },
            "system": {
                "schemaVersion": PROJECT_SCHEMA_VERSION,
                "blocks": [],
                "connections": [],
            },
        }

    def apply_change_set(self, project_id: str, change_set: Mapping[str, Any]) -> ChangeSetResult:
        try:
            cs = ChangeSet.model_validate(dict(change_set))
        except ValidationError as exc:
            raise WorkbenchV2Error(
                ERR_CHANGESET_INVALID,
                f"change set is not valid: {exc}",
                data={"errors": exc.errors()},
            ) from exc

        project = self.open_project(project_id)
        if cs.baseRevision != project.revision:
            raise WorkbenchV2Error(
                ERR_CHANGESET_CONFLICT,
                "change set baseRevision does not match the current project revision",
                data={
                    "actualRevision": project.revision,
                    "baseRevision": cs.baseRevision,
                },
            )

        project_dir = self._project_dir(project_id)
        state = self._load_state(project_dir)
        defaults = self._default_state(project)
        for document in DOCUMENT_NAMES:
            if state.get(document) is None:
                state[document] = defaults[document]

        affected_components: set[str] = set()
        affected_nets: set[str] = set()
        affected_symbols: set[str] = set()
        changed_documents: set[str] = set()

        for index, raw_op in enumerate(cs.operations):
            op_type = raw_op.get("type")
            reducer = OP_REDUCERS.get(op_type or "")
            if reducer is None:
                raise WorkbenchV2Error(
                    ERR_CHANGESET_OPERATION_INVALID,
                    f"unsupported operation type {op_type!r}",
                    data={"index": index, "type": str(op_type)},
                )
            for model_cls in OP_TYPES:
                if model_cls.model_fields["type"].default == op_type:
                    try:
                        op = model_cls.model_validate(_as_op_dict(raw_op))
                    except ValidationError as exc:
                        raise WorkbenchV2Error(
                            ERR_CHANGESET_OPERATION_INVALID,
                            f"operation {index} is not valid: {exc}",
                            data={"index": index, "errors": exc.errors()},
                        ) from exc
                    break
            else:  # pragma: no cover - guarded by OP_REDUCERS lookup
                raise WorkbenchV2Error(
                    ERR_CHANGESET_OPERATION_INVALID,
                    f"unsupported operation type {op_type!r}",
                    data={"index": index, "type": str(op_type)},
                )

            document = op.document
            try:
                reducer(state, op)  # type: ignore[operator]
            except WorkbenchV2Error as exc:
                raise WorkbenchV2Error(
                    exc.code,
                    f"operation {index} ({op_type}) failed: {exc.message}",
                    data={"index": index, **exc.data},
                ) from exc
            changed_documents.add(document)
            if hasattr(op, "componentId"):
                affected_components.add(op.componentId)
            new_id = getattr(op, "newId", None)
            if new_id is not None:
                old_id = getattr(op, "oldId", None)
                if old_id is not None:
                    affected_components.add(old_id)
                affected_components.add(new_id)
            if hasattr(op, "net"):
                affected_nets.add(op.net)
            new_name = getattr(op, "newName", None)
            if new_name is not None:
                old_name = getattr(op, "oldName", None)
                if old_name is not None:
                    affected_nets.add(old_name)
                affected_nets.add(new_name)
            if hasattr(op, "symbolId"):
                affected_symbols.add(op.symbolId)
            if hasattr(op, "labelId"):
                affected_symbols.add(op.labelId)
            if hasattr(op, "wireId"):
                affected_symbols.add(op.wireId)

        # Atomically write the new manifest and changed documents.
        new_revision = project.revision + 1
        from datetime import UTC, datetime

        updated_at = datetime.now(UTC).isoformat()
        new_manifest = HardwareProject(
            schemaVersion=PROJECT_SCHEMA_VERSION_LITERAL,
            projectId=project.projectId,
            displayName=project.displayName,
            revision=new_revision,
            createdAt=project.createdAt or updated_at,
            updatedAt=updated_at,
        )
        try:
            for document in sorted(changed_documents):
                _write_json_atomic(project_dir / DOCUMENT_FILE_PATHS[document], state[document])
            _write_json_atomic(
                project_dir / FILE_MANIFEST,
                new_manifest.model_dump(mode="json"),
            )
        except OSError as exc:
            raise WorkbenchV2Error(
                ERR_CHANGESET_IO,
                f"failed to persist change set: {exc}",
                data={"projectDir": str(project_dir)},
            ) from exc

        step = _history_step(project_dir / ".workbench" / "history" / "changes.jsonl")
        _append_history(
            project_dir / ".workbench" / "history" / "changes.jsonl",
            {
                "step": step,
                "actor": cs.actor,
                "clientRequestId": cs.clientRequestId,
                "revision": new_revision,
                "operations": list(cs.operations),
                "changedDocuments": sorted(changed_documents),
                "affectedComponentIds": sorted(affected_components),
                "affectedNetNames": sorted(affected_nets),
                "affectedSymbolIds": sorted(affected_symbols),
            },
        )

        result = ChangeSetResult(
            revision=new_revision,
            changed_documents=tuple(sorted(changed_documents)),
            history_step=step,
            affected_component_ids=tuple(sorted(affected_components)),
            affected_net_names=tuple(sorted(affected_nets)),
            affected_symbol_ids=tuple(sorted(affected_symbols)),
        )
        self._undo.setdefault(project_id, []).append(result)
        if len(self._undo[project_id]) > self.undo_depth:
            self._undo[project_id] = self._undo[project_id][-self.undo_depth :]
        self._redo.pop(project_id, None)
        return result

    def preview_change_set(
        self, project_id: str, change_set: Mapping[str, Any]
    ) -> ChangeSetResult:
        """Run the exact mutation path against an isolated project copy."""
        source = self._project_dir(project_id)
        if not source.is_dir():
            raise WorkbenchV2Error(
                ERR_PROJECT_NOT_FOUND,
                f"project {project_id!r} does not exist",
                data={"projectId": project_id},
            )
        symlink = next((path for path in source.rglob("*") if path.is_symlink()), None)
        if symlink is not None:
            raise WorkbenchV2Error(
                ERR_CHANGESET_VALIDATION,
                "project preview refuses symlinked content",
                data={"path": str(symlink.relative_to(source))},
            )
        with TemporaryDirectory(prefix="ltagent-preview-") as temp_dir:
            root = Path(temp_dir)
            shutil.copytree(source, root / project_id)
            preview = DesignService(projects_root=str(root), undo_depth=1)
            return preview.apply_change_set(project_id, change_set)

    def undo(self, project_id: str) -> ChangeSetResult | None:
        undo_stack = self._undo.get(project_id, [])
        if not undo_stack:
            return None
        last = undo_stack.pop()
        # The undo is implemented as a structural rewind via the
        # snapshot directory: when Phase 1.5 migrated a project the
        # pre-migration backup is at .workbench/migration-backup-*;
        # for ordinary undo the v2 service keeps the prior manifest
        # in the changes.jsonl history and rolls the document back
        # to that revision.

        project_dir = self._project_dir(project_id)
        history_path = project_dir / ".workbench" / "history" / "changes.jsonl"
        if not history_path.is_file():
            return None
        target = last.revision - 1
        rolled = self._rewind_to_revision(project_dir, target)
        self._redo.setdefault(project_id, []).append(last)
        return rolled

    def redo(self, project_id: str) -> ChangeSetResult | None:
        redo_stack = self._redo.get(project_id, [])
        if not redo_stack:
            return None
        next_result = redo_stack.pop()

        project_dir = self._project_dir(project_id)
        history_path = project_dir / ".workbench" / "history" / "changes.jsonl"
        if not history_path.is_file():
            return None
        self._undo.setdefault(project_id, []).append(next_result)
        return self._rewind_to_revision(project_dir, next_result.revision)

    def _rewind_to_revision(self, project_dir: Any, target_revision: int) -> ChangeSetResult:
        """Re-apply every change-set line up to ``target_revision``.

        Used by :meth:`undo` and :meth:`redo`. Each line carries a
        snapshot of the post-state; the rewind replays the lines
        in order, taking the final state of the matching revision.
        """
        import json
        from datetime import UTC, datetime

        history_path = project_dir / ".workbench" / "history" / "changes.jsonl"
        if not history_path.is_file():
            raise WorkbenchV2Error(
                ERR_CHANGESET_IO,
                f"history not found for project at {project_dir}",
                data={"projectDir": str(project_dir)},
            )
        final_documents: set[str] = set()
        final_components: set[str] = set()
        final_nets: set[str] = set()
        final_symbols: set[str] = set()
        step = 0
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            revision = record.get("revision")
            if not isinstance(revision, int) or revision > target_revision:
                continue
            step = max(step, int(record.get("step", step)))
            final_documents = set(record.get("changedDocuments", []))
            final_components = set(record.get("affectedComponentIds", []))
            final_nets = set(record.get("affectedNetNames", []))
            final_symbols = set(record.get("affectedSymbolIds", []))

        # The simplest correct rewind: re-apply every change set in
        # order, starting from the project's default state. The
        # changes.jsonl is append-only so the read is cheap.
        state = self._default_state(self.open_project(project_dir.name))
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("revision", 0) > target_revision:
                break
            for raw_op in record.get("operations", []):
                op_type = raw_op.get("type")
                reducer = OP_REDUCERS.get(op_type or "")
                if reducer is None:
                    continue
                for model_cls in OP_TYPES:
                    if model_cls.model_fields["type"].default == op_type:
                        reducer(state, model_cls.model_validate(_as_op_dict(raw_op)))  # type: ignore[operator]
                        break

        for document in DOCUMENT_NAMES:
            _write_json_atomic(project_dir / DOCUMENT_FILE_PATHS[document], state[document])
        new_manifest = HardwareProject(
            schemaVersion=PROJECT_SCHEMA_VERSION_LITERAL,
            projectId=project_dir.name,
            displayName=project_dir.name,
            revision=target_revision,
            createdAt=self.open_project(project_dir.name).createdAt or "",
            updatedAt=datetime.now(UTC).isoformat(),
        )
        _write_json_atomic(project_dir / FILE_MANIFEST, new_manifest.model_dump(mode="json"))
        return ChangeSetResult(
            revision=target_revision,
            changed_documents=tuple(sorted(final_documents)),
            history_step=step,
            affected_component_ids=tuple(sorted(final_components)),
            affected_net_names=tuple(sorted(final_nets)),
            affected_symbol_ids=tuple(sorted(final_symbols)),
        )


__all__ = [
    "DOCUMENT_FILE_PATHS",
    "DOCUMENT_NAMES",
    "ERR_CHANGESET_CONFLICT",
    "ERR_CHANGESET_DOCUMENT_INVALID",
    "ERR_CHANGESET_INVALID",
    "ERR_CHANGESET_IO",
    "ERR_CHANGESET_OPERATION_INVALID",
    "ERR_CHANGESET_VALIDATION",
    "ERR_DOCUMENT_NOT_FOUND",
    "ERR_PROJECT_NOT_FOUND",
    "OP_REDUCERS",
    "OP_TYPES",
    "AddComponentOp",
    "AddDirectiveOp",
    "AddMeasurementOp",
    "ChangeSet",
    "ChangeSetResult",
    "ConnectPinOp",
    "DeleteNodeOp",
    "DesignService",
    "DisconnectPinOp",
    "MoveNodeOp",
    "PlaceNodeOp",
    "RemoveComponentOp",
    "RemoveWireOp",
    "RenameComponentOp",
    "RenameNetOp",
    "ReplaceDocumentOp",
    "RotateNodeOp",
    "SetComponentValueOp",
    "SetDigitalDesignOp",
    "SetGridSizeOp",
    "SetNetLabelOp",
    "SetNodePropertiesOp",
    "SetWireRouteOp",
    "WorkbenchV2Error",
]
