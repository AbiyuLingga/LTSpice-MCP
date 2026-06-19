"""Safe edit operations for the LTspice Circuit Graph (Phase 3, plan §8)."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .edit_result import EditError, EditResult, EditWarning
from .graph_schema import (
    DIRECTIVE_ALLOWLIST,
    GROUND_NODE,
    IDENTIFIER_PATTERN,
    KIND_MIN_ARITY,
    NODE_NAME_PATTERN,
    SCHEMA_VERSION,
    SUPPORTED_ANALYSIS_KINDS,
)

GRAPH_SCHEMA_VERSION = SCHEMA_VERSION
KIND_ARITY = KIND_MIN_ARITY
MEASUREMENT_ANALYSIS_KINDS = SUPPORTED_ANALYSIS_KINDS

KIND_TO_SPICE_PREFIX: dict[str, str] = {
    "voltage_source": "V", "current_source": "I", "resistor": "R",
    "capacitor": "C", "inductor": "L", "diode": "D",
    "npn": "Q", "pnp": "Q", "nmos": "M", "pmos": "M", "opamp": "X",
}

NET_TYPE_GROUND: str = "ground"
NET_TYPE_SIGNAL: str = "signal"

PIN_NAMES: dict[str, tuple[str, ...]] = {
    "resistor": ("p1", "p2"), "capacitor": ("p1", "p2"),
    "inductor": ("p1", "p2"), "voltage_source": ("p1", "p2"),
    "current_source": ("p1", "p2"), "diode": ("p1", "p2"),
    "npn": ("c", "b", "e"), "pnp": ("c", "b", "e"),
    "nmos": ("d", "g", "s", "b"), "pmos": ("d", "g", "s", "b"),
    "opamp": ("ip", "in", "vp", "vn", "out"),
}

_SOURCE_KINDS: frozenset[str] = frozenset({"voltage_source", "current_source"})
_SEMICON_KINDS: frozenset[str] = frozenset({"diode", "npn", "pnp", "nmos", "pmos"})
_SUBCKT_KINDS: frozenset[str] = frozenset({"opamp"})
_VALUE_OR_MODEL_KINDS: frozenset[str] = frozenset({"resistor", "capacitor", "inductor"})

ERR_GRAPH_TYPE = "GRAPH_TYPE_INVALID"
ERR_COMPONENT_ID_INVALID = "COMPONENT_ID_INVALID"
ERR_COMPONENT_ID_DUPLICATE = "COMPONENT_ID_DUPLICATE"
ERR_COMPONENT_NOT_FOUND = "COMPONENT_NOT_FOUND"
ERR_COMPONENT_MISSING = "COMPONENT_MISSING"
ERR_COMPONENT_KIND_UNKNOWN = "COMPONENT_KIND_UNKNOWN"
ERR_COMPONENT_ARITY = "COMPONENT_ARITY_MISMATCH"
ERR_COMPONENT_VALUE_REQUIRED = "COMPONENT_VALUE_REQUIRED"
ERR_COMPONENT_MODEL_REQUIRED = "COMPONENT_MODEL_REQUIRED"
ERR_COMPONENT_VALUE_INVALID = "COMPONENT_VALUE_INVALID"
ERR_COMPONENT_MODEL_INVALID = "COMPONENT_MODEL_INVALID"
ERR_COMPONENT_PIN_SHAPE = "COMPONENT_PIN_SHAPE_INVALID"
ERR_PIN_NOT_FOUND = "PIN_NOT_FOUND"
ERR_PIN_NAME_INVALID = "PIN_NAME_INVALID"
ERR_NET_NAME_INVALID = "NET_NAME_INVALID"
ERR_NET_NOT_FOUND = "NET_NOT_FOUND"
ERR_NET_EXISTS = "NET_EXISTS"
ERR_MEASUREMENT_NAME_INVALID = "MEASUREMENT_NAME_INVALID"
ERR_MEASUREMENT_EXISTS = "MEASUREMENT_EXISTS"
ERR_MEASUREMENT_ANALYSIS_INVALID = "MEASUREMENT_ANALYSIS_INVALID"
ERR_MEASUREMENT_EXPRESSION_EMPTY = "MEASUREMENT_EXPRESSION_EMPTY"
ERR_DIRECTIVE_EMPTY = "DIRECTIVE_EMPTY"
ERR_DIRECTIVE_NOT_ALLOWED = "DIRECTIVE_NOT_ALLOWED"

WARN_PIN_ALREADY_CONNECTED = "PIN_ALREADY_CONNECTED"
WARN_PIN_ALREADY_DISCONNECTED = "PIN_ALREADY_DISCONNECTED"
WARN_NET_AUTO_CREATED = "NET_AUTO_CREATED"
WARN_VALUE_UNCHANGED = "VALUE_UNCHANGED"


def clone_graph(graph: Any) -> Any:
    if graph is None:
        return _empty_graph()
    model_copy = getattr(graph, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(deep=True)
        except TypeError:
            return model_copy()
    if not isinstance(graph, Mapping):
        return _empty_graph()
    return copy.deepcopy(dict(graph))


def _empty_graph() -> dict[str, Any]:
    return {
        "schemaVersion": GRAPH_SCHEMA_VERSION, "projectId": "", "domain": "analog",
        "topology": "", "components": {}, "nets": {}, "analyses": [],
        "measurements": [], "directives": [],
    }


def _coerce_graph(graph: Any) -> tuple[Any, EditError | None]:
    if graph is None:
        return _empty_graph(), EditError(
            code=ERR_GRAPH_TYPE, path="<root>",
            detail="graph must be a Mapping or CircuitGraph, got None")
    model_dump = getattr(graph, "model_dump", None)
    if callable(model_dump):
        return clone_graph(graph), None
    if not isinstance(graph, Mapping):
        return _empty_graph(), EditError(
            code=ERR_GRAPH_TYPE, path="<root>",
            detail=f"graph must be a Mapping or CircuitGraph, got {type(graph).__name__}")
    return clone_graph(graph), None


def _validate_identifier(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return bool(IDENTIFIER_PATTERN.match(value))


def _validate_node_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return bool(NODE_NAME_PATTERN.match(value))


def _ensure_ground_net(graph: dict[str, Any]) -> None:
    nets = graph.setdefault("nets", {})
    if GROUND_NODE not in nets or not isinstance(nets[GROUND_NODE], Mapping):
        nets[GROUND_NODE] = {"name": GROUND_NODE, "type": NET_TYPE_GROUND}


def _register_net(graph: dict[str, Any], net_name: str, *, result: EditResult) -> None:
    nets = graph.setdefault("nets", {})
    if net_name in nets and isinstance(nets[net_name], Mapping):
        return
    if net_name == GROUND_NODE:
        nets[net_name] = {"name": GROUND_NODE, "type": NET_TYPE_GROUND}
        return
    nets[net_name] = {"name": net_name, "type": NET_TYPE_SIGNAL}
    result.add_warning(
        code=WARN_NET_AUTO_CREATED, path=f"nets.{net_name}",
        detail=f"net {net_name!r} was not declared; auto-created as {NET_TYPE_SIGNAL!r}",
        data={"netName": net_name, "type": NET_TYPE_SIGNAL})


def _net_referenced(components: Mapping[str, Any], net_name: str) -> bool:
    for comp in components.values():
        if not isinstance(comp, dict):
            continue
        pins_obj = comp.get("pins")
        if not isinstance(pins_obj, dict):
            continue
        for pin_net in pins_obj.values():
            if pin_net == net_name:
                return True
    return False


def _get_mapping(graph: Any, key: str) -> Mapping[str, Any] | None:
    if graph is None:
        return None
    if hasattr(graph, key):
        try:
            value = getattr(graph, key)
            if hasattr(value, "model_dump"):
                dumped = value.model_dump(mode="json", exclude_none=True)
                if isinstance(dumped, Mapping):
                    return dumped
                return None
            if isinstance(value, Mapping):
                return value
        except AttributeError:
            pass
    if isinstance(graph, Mapping):
        value = graph.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def add_component(
    graph: Any, component_id: str, kind: str, pins: Mapping[str, str | None],
    *, value: str | None = None, model: str | None = None, role: str | None = None,
) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_identifier(component_id):
        result.add_error(
            code=ERR_COMPONENT_ID_INVALID, path="components",
            detail=f"component id {component_id!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id})
    if kind not in KIND_ARITY:
        result.add_error(
            code=ERR_COMPONENT_KIND_UNKNOWN, path=f"components.{component_id}.kind",
            detail=f"component kind {kind!r} is not supported; allowed: {sorted(KIND_ARITY)}",
            data={"componentId": component_id, "kind": kind})
        return result
    components: dict[str, Any] = new_graph.setdefault("components", {})
    if component_id in components:
        result.add_error(
            code=ERR_COMPONENT_ID_DUPLICATE, path=f"components.{component_id}",
            detail=f"component id {component_id!r} already exists",
            data={"componentId": component_id})
        return result
    if not isinstance(pins, Mapping):
        result.add_error(
            code=ERR_COMPONENT_PIN_SHAPE, path=f"components.{component_id}.pins",
            detail=f"pins must be a mapping, got {type(pins).__name__}",
            data={"componentId": component_id})
        return result
    arity = KIND_ARITY[kind]
    pin_items: list[tuple[str, str | None]] = []
    for pin_name, net_name in pins.items():
        if not _validate_identifier(pin_name):
            result.add_error(
                code=ERR_PIN_NAME_INVALID, path=f"components.{component_id}.pins",
                detail=f"pin name {pin_name!r} must match {IDENTIFIER_PATTERN.pattern}",
                data={"componentId": component_id, "pin": str(pin_name)})
            continue
        if net_name is not None and not _validate_node_name(net_name):
            result.add_error(
                code=ERR_NET_NAME_INVALID, path=f"components.{component_id}.pins.{pin_name}",
                detail=f"net name {net_name!r} must match {NODE_NAME_PATTERN.pattern}",
                data={"componentId": component_id, "pin": pin_name, "net": str(net_name)})
            continue
        pin_items.append((pin_name, net_name))
    if len(pin_items) != arity:
        result.add_error(
            code=ERR_COMPONENT_ARITY, path=f"components.{component_id}.pins",
            detail=f"component {component_id!r} kind {kind!r} requires {arity} pins, got {len(pin_items)}",
            data={"componentId": component_id, "kind": kind, "expected": arity, "got": len(pin_items)})
        return result
    if kind in _SOURCE_KINDS and (not isinstance(value, str) or not value.strip()):
        result.add_error(
            code=ERR_COMPONENT_VALUE_REQUIRED, path=f"components.{component_id}.value",
            detail=f"source component {component_id!r} requires a non-empty value",
            data={"componentId": component_id, "kind": kind})
    if kind in _SEMICON_KINDS:
        chosen = model if (isinstance(model, str) and model.strip()) else value
        if not isinstance(chosen, str) or not chosen.strip():
            result.add_error(
                code=ERR_COMPONENT_MODEL_REQUIRED, path=f"components.{component_id}.model",
                detail=f"semiconductor component {component_id!r} requires a non-empty model name in `model` or `value`",
                data={"componentId": component_id, "kind": kind})
    if kind in _SUBCKT_KINDS and (not isinstance(value, str) or not value.strip()):
        result.add_error(
            code=ERR_COMPONENT_VALUE_REQUIRED, path=f"components.{component_id}.value",
            detail=f"opamp component {component_id!r} requires a non-empty subcircuit name in `value`",
            data={"componentId": component_id, "kind": kind})
    if kind in _VALUE_OR_MODEL_KINDS and (not isinstance(value, str) or not value.strip()):
        result.add_error(
            code=ERR_COMPONENT_VALUE_REQUIRED, path=f"components.{component_id}.value",
            detail=f"passive component {component_id!r} requires a non-empty value",
            data={"componentId": component_id, "kind": kind})
    if result.errors:
        return result
    pin_map: dict[str, str | None] = {pin: net for pin, net in pin_items}
    new_component: dict[str, Any] = {"id": component_id, "kind": kind, "pins": pin_map}
    if value is not None:
        new_component["value"] = value
    if model is not None:
        new_component["model"] = model
    if role is not None:
        new_component["role"] = role
    _ensure_ground_net(new_graph)
    for net_name in (n for n in pin_map.values() if n is not None):
        _register_net(new_graph, net_name, result=result)
    components[component_id] = new_component
    result.add_change(
        op="add_component", target=component_id, before=None, after=dict(new_component),
        data={"kind": kind, "value": value, "model": model, "role": role})
    return result


def remove_component(graph: Any, component_id: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_identifier(component_id):
        result.add_error(
            code=ERR_COMPONENT_ID_INVALID, path="components",
            detail=f"component id {component_id!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id})
        return result
    components: dict[str, Any] = new_graph.setdefault("components", {})
    if component_id not in components:
        result.add_error(
            code=ERR_COMPONENT_NOT_FOUND, path=f"components.{component_id}",
            detail=f"component id {component_id!r} does not exist",
            data={"componentId": component_id})
        return result
    removed = components.pop(component_id)
    result.add_change(op="remove_component", target=component_id, before=removed, after=None)
    return result


def set_component_value(graph: Any, component_id: str, value: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_identifier(component_id):
        result.add_error(
            code=ERR_COMPONENT_ID_INVALID, path="components",
            detail=f"component id {component_id!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id})
        return result
    if not isinstance(value, str):
        result.add_error(
            code=ERR_COMPONENT_VALUE_INVALID, path=f"components.{component_id}.value",
            detail=f"value must be a string, got {type(value).__name__}",
            data={"componentId": component_id, "type": type(value).__name__})
        return result
    if not value.strip():
        result.add_error(
            code=ERR_COMPONENT_VALUE_INVALID, path=f"components.{component_id}.value",
            detail="value must be a non-empty string",
            data={"componentId": component_id})
        return result
    components: dict[str, Any] = new_graph.setdefault("components", {})
    if component_id not in components:
        result.add_error(
            code=ERR_COMPONENT_NOT_FOUND, path=f"components.{component_id}",
            detail=f"component id {component_id!r} does not exist",
            data={"componentId": component_id})
        return result
    component = components[component_id]
    if not isinstance(component, dict):
        result.add_error(
            code=ERR_COMPONENT_MISSING, path=f"components.{component_id}",
            detail=f"component {component_id!r} is malformed: expected dict, got {type(component).__name__}",
            data={"componentId": component_id})
        return result
    before = component.get("value")
    if before == value:
        result.add_warning(
            code=WARN_VALUE_UNCHANGED, path=f"components.{component_id}.value",
            detail=f"value of {component_id!r} is already {value!r}; no change applied",
            data={"componentId": component_id, "value": value})
        return result
    component["value"] = value
    result.add_change(op="set_component_value", target=component_id, before=before, after=value)
    return result


def connect_pin(graph: Any, component_id: str, pin_name: str, net_name: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_identifier(component_id):
        result.add_error(
            code=ERR_COMPONENT_ID_INVALID, path="components",
            detail=f"component id {component_id!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id})
        return result
    if not _validate_identifier(pin_name):
        result.add_error(
            code=ERR_PIN_NAME_INVALID, path=f"components.{component_id}.pins",
            detail=f"pin name {pin_name!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id, "pin": str(pin_name)})
        return result
    if not _validate_node_name(net_name):
        result.add_error(
            code=ERR_NET_NAME_INVALID, path=f"components.{component_id}.pins.{pin_name}",
            detail=f"net name {net_name!r} must match {NODE_NAME_PATTERN.pattern}",
            data={"componentId": component_id, "pin": pin_name, "net": str(net_name)})
        return result
    components: dict[str, Any] = new_graph.setdefault("components", {})
    if component_id not in components:
        result.add_error(
            code=ERR_COMPONENT_NOT_FOUND, path=f"components.{component_id}",
            detail=f"component id {component_id!r} does not exist",
            data={"componentId": component_id})
        return result
    component = components[component_id]
    if not isinstance(component, dict):
        result.add_error(
            code=ERR_COMPONENT_MISSING, path=f"components.{component_id}",
            detail=f"component {component_id!r} is malformed: expected dict, got {type(component).__name__}",
            data={"componentId": component_id})
        return result
    pins_obj = component.get("pins")
    if not isinstance(pins_obj, dict):
        result.add_error(
            code=ERR_COMPONENT_PIN_SHAPE, path=f"components.{component_id}.pins",
            detail=f"component {component_id!r} pins must be a dict, got {type(pins_obj).__name__}",
            data={"componentId": component_id})
        return result
    if pin_name not in pins_obj:
        result.add_error(
            code=ERR_PIN_NOT_FOUND, path=f"components.{component_id}.pins.{pin_name}",
            detail=f"pin {pin_name!r} is not declared on component {component_id!r}",
            data={"componentId": component_id, "pin": pin_name})
        return result
    before = pins_obj[pin_name]
    if before == net_name:
        result.add_warning(
            code=WARN_PIN_ALREADY_CONNECTED, path=f"components.{component_id}.pins.{pin_name}",
            detail=f"pin {pin_name!r} of {component_id!r} is already connected to net {net_name!r}",
            data={"componentId": component_id, "pin": pin_name, "net": net_name})
        return result
    _ensure_ground_net(new_graph)
    _register_net(new_graph, net_name, result=result)
    pins_obj[pin_name] = net_name
    result.add_change(
        op="connect_pin", target=f"{component_id}.{pin_name}",
        before=before, after=net_name,
        data={"componentId": component_id, "pin": pin_name, "net": net_name})
    return result


def disconnect_pin(graph: Any, component_id: str, pin_name: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_identifier(component_id):
        result.add_error(
            code=ERR_COMPONENT_ID_INVALID, path="components",
            detail=f"component id {component_id!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id})
        return result
    if not _validate_identifier(pin_name):
        result.add_error(
            code=ERR_PIN_NAME_INVALID, path=f"components.{component_id}.pins",
            detail=f"pin name {pin_name!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"componentId": component_id, "pin": str(pin_name)})
        return result
    components: dict[str, Any] = new_graph.setdefault("components", {})
    if component_id not in components:
        result.add_error(
            code=ERR_COMPONENT_NOT_FOUND, path=f"components.{component_id}",
            detail=f"component id {component_id!r} does not exist",
            data={"componentId": component_id})
        return result
    component = components[component_id]
    if not isinstance(component, dict):
        result.add_error(
            code=ERR_COMPONENT_MISSING, path=f"components.{component_id}",
            detail=f"component {component_id!r} is malformed: expected dict, got {type(component).__name__}",
            data={"componentId": component_id})
        return result
    pins_obj = component.get("pins")
    if not isinstance(pins_obj, dict):
        result.add_error(
            code=ERR_COMPONENT_PIN_SHAPE, path=f"components.{component_id}.pins",
            detail=f"component {component_id!r} pins must be a dict, got {type(pins_obj).__name__}",
            data={"componentId": component_id})
        return result
    if pin_name not in pins_obj:
        result.add_error(
            code=ERR_PIN_NOT_FOUND, path=f"components.{component_id}.pins.{pin_name}",
            detail=f"pin {pin_name!r} is not declared on component {component_id!r}",
            data={"componentId": component_id, "pin": pin_name})
        return result
    before = pins_obj[pin_name]
    if before is None:
        result.add_warning(
            code=WARN_PIN_ALREADY_DISCONNECTED, path=f"components.{component_id}.pins.{pin_name}",
            detail=f"pin {pin_name!r} of {component_id!r} is already disconnected",
            data={"componentId": component_id, "pin": pin_name})
        return result
    pins_obj[pin_name] = None
    result.add_change(
        op="disconnect_pin", target=f"{component_id}.{pin_name}",
        before=before, after=None,
        data={"componentId": component_id, "pin": pin_name})
    return result


def rename_net(graph: Any, old_name: str, new_name: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_node_name(old_name):
        result.add_error(
            code=ERR_NET_NAME_INVALID, path="nets",
            detail=f"net name {old_name!r} must match {NODE_NAME_PATTERN.pattern}",
            data={"net": str(old_name)})
        return result
    if not _validate_node_name(new_name):
        result.add_error(
            code=ERR_NET_NAME_INVALID, path="nets",
            detail=f"net name {new_name!r} must match {NODE_NAME_PATTERN.pattern}",
            data={"net": str(new_name)})
        return result
    if old_name == new_name:
        result.add_warning(
            code=WARN_VALUE_UNCHANGED, path="nets",
            detail=f"net {old_name!r} is already named {new_name!r}; no change applied",
            data={"oldName": old_name, "newName": new_name})
        return result
    nets: dict[str, Any] = new_graph.setdefault("nets", {})
    components: dict[str, Any] = new_graph.setdefault("components", {})
    if old_name not in nets and not _net_referenced(components, old_name):
        result.add_error(
            code=ERR_NET_NOT_FOUND, path=f"nets.{old_name}",
            detail=f"net {old_name!r} is not declared and not referenced by any component pin",
            data={"net": old_name})
        return result
    if new_name in nets:
        result.add_error(
            code=ERR_NET_EXISTS, path=f"nets.{new_name}",
            detail=f"net {new_name!r} already exists; refusing to overwrite",
            data={"oldName": old_name, "newName": new_name})
        return result
    net_type = NET_TYPE_SIGNAL
    if old_name in nets and isinstance(nets[old_name], Mapping):
        existing_type = nets[old_name].get("type")
        if isinstance(existing_type, str) and existing_type:
            net_type = existing_type
    elif new_name == GROUND_NODE:
        net_type = NET_TYPE_GROUND
    nets.pop(old_name, None)
    nets[new_name] = {"name": new_name, "type": net_type}
    pin_updates: list[dict[str, Any]] = []
    for comp_id, comp in components.items():
        if not isinstance(comp, dict):
            continue
        pins_obj = comp.get("pins")
        if not isinstance(pins_obj, dict):
            continue
        for pin_name, pin_net in pins_obj.items():
            if pin_net == old_name:
                pins_obj[pin_name] = new_name
                pin_updates.append({"componentId": comp_id, "pin": pin_name, "from": old_name, "to": new_name})
    result.add_change(
        op="rename_net", target=old_name, before=old_name, after=new_name,
        data={"oldName": old_name, "newName": new_name, "type": net_type, "pinUpdates": pin_updates})
    return result


def add_directive(graph: Any, directive_text: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not isinstance(directive_text, str):
        result.add_error(
            code=ERR_DIRECTIVE_EMPTY, path="directives",
            detail=f"directive must be a string, got {type(directive_text).__name__}",
            data={"type": type(directive_text).__name__})
        return result
    stripped = directive_text.strip()
    if not stripped:
        result.add_error(code=ERR_DIRECTIVE_EMPTY, path="directives", detail="directive must be a non-empty string")
        return result
    first_token = stripped.split()[0]
    if first_token not in DIRECTIVE_ALLOWLIST:
        result.add_error(
            code=ERR_DIRECTIVE_NOT_ALLOWED, path="directives",
            detail=f"directive {first_token!r} is not in allowlist; allowed: {sorted(DIRECTIVE_ALLOWLIST)}",
            data={"directive": stripped, "firstToken": first_token})
        return result
    directives: list[dict[str, Any]] = new_graph.setdefault("directives", [])
    directive_entry: dict[str, Any] = {"name": first_token}
    args = stripped[len(first_token):].strip()
    if args:
        directive_entry["args"] = args
    directives.append(directive_entry)
    result.add_change(
        op="add_directive", target=f"directives[{len(directives) - 1}]",
        before=None, after=dict(directive_entry))
    return result


def add_measurement(graph: Any, name: str, analysis: str, expression: str) -> EditResult:
    new_graph, graph_error = _coerce_graph(graph)
    result = EditResult(graph=new_graph)
    if graph_error is not None:
        result.add_error(graph_error.code, graph_error.path, graph_error.detail)
        return result
    if not _validate_identifier(name):
        result.add_error(
            code=ERR_MEASUREMENT_NAME_INVALID, path="measurements",
            detail=f"measurement name {name!r} must match {IDENTIFIER_PATTERN.pattern}",
            data={"measurementName": name})
        return result
    if not isinstance(analysis, str) or analysis not in MEASUREMENT_ANALYSIS_KINDS:
        result.add_error(
            code=ERR_MEASUREMENT_ANALYSIS_INVALID, path=f"measurements.{name}.analysis",
            detail=f"measurement analysis {analysis!r} must be one of {sorted(MEASUREMENT_ANALYSIS_KINDS)}",
            data={"measurementName": name, "analysis": str(analysis)})
        return result
    if not isinstance(expression, str) or not expression.strip():
        result.add_error(
            code=ERR_MEASUREMENT_EXPRESSION_EMPTY, path=f"measurements.{name}.expression",
            detail="measurement expression must be a non-empty string",
            data={"measurementName": name})
        return result
    measurements: list[dict[str, Any]] = new_graph.setdefault("measurements", [])
    for existing in measurements:
        if isinstance(existing, dict) and existing.get("name") == name:
            result.add_error(
                code=ERR_MEASUREMENT_EXISTS, path=f"measurements.{name}",
                detail=f"measurement {name!r} already exists",
                data={"measurementName": name})
            return result
    new_measurement: dict[str, Any] = {"name": name, "analysis": analysis, "expression": expression.strip()}
    measurements.append(new_measurement)
    result.add_change(
        op="add_measurement", target=name, before=None, after=dict(new_measurement),
        data={"analysis": analysis})
    return result


def list_component_ids(graph: Any) -> list[str]:
    components = _get_mapping(graph, "components")
    if components is None:
        return []
    return sorted(str(k) for k in components)


def list_net_names(graph: Any) -> list[str]:
    nets = _get_mapping(graph, "nets")
    if nets is None:
        return []
    return sorted(str(k) for k in nets)


def get_component(graph: Any, component_id: str) -> dict[str, Any] | None:
    components = _get_mapping(graph, "components")
    if components is None:
        return None
    value = components.get(component_id)
    if isinstance(value, dict):
        return dict(value)
    return None


def get_pin_net(graph: Any, component_id: str, pin_name: str) -> str | None:
    component = get_component(graph, component_id)
    if component is None:
        return None
    pins = component.get("pins")
    if not isinstance(pins, dict):
        return None
    value = pins.get(pin_name)
    return str(value) if isinstance(value, str) else None


__all__ = [
    "DIRECTIVE_ALLOWLIST",
    "GRAPH_SCHEMA_VERSION",
    "GROUND_NODE",
    "IDENTIFIER_PATTERN",
    "KIND_ARITY",
    "KIND_TO_SPICE_PREFIX",
    "MEASUREMENT_ANALYSIS_KINDS",
    "NET_TYPE_GROUND",
    "NET_TYPE_SIGNAL",
    "NODE_NAME_PATTERN",
    "PIN_NAMES",
    "EditError",
    "EditResult",
    "EditWarning",
    "add_component",
    "add_directive",
    "add_measurement",
    "clone_graph",
    "connect_pin",
    "disconnect_pin",
    "get_component",
    "get_pin_net",
    "list_component_ids",
    "list_net_names",
    "remove_component",
    "rename_net",
    "set_component_value",
]
