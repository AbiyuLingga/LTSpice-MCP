"""Unit tests for the live edit operations (Phase 3, plan §8)."""

from __future__ import annotations

from typing import Any

import pytest

from ltagent.live.edit_ops import (
    DIRECTIVE_ALLOWLIST,
    ERR_COMPONENT_ARITY,
    ERR_COMPONENT_ID_DUPLICATE,
    ERR_COMPONENT_ID_INVALID,
    ERR_COMPONENT_KIND_UNKNOWN,
    ERR_COMPONENT_MODEL_REQUIRED,
    ERR_COMPONENT_NOT_FOUND,
    ERR_COMPONENT_PIN_SHAPE,
    ERR_COMPONENT_VALUE_INVALID,
    ERR_COMPONENT_VALUE_REQUIRED,
    ERR_DIRECTIVE_EMPTY,
    ERR_DIRECTIVE_NOT_ALLOWED,
    ERR_GRAPH_TYPE,
    ERR_MEASUREMENT_ANALYSIS_INVALID,
    ERR_MEASUREMENT_EXISTS,
    ERR_MEASUREMENT_EXPRESSION_EMPTY,
    ERR_MEASUREMENT_NAME_INVALID,
    ERR_NET_EXISTS,
    ERR_NET_NAME_INVALID,
    ERR_NET_NOT_FOUND,
    ERR_PIN_NAME_INVALID,
    ERR_PIN_NOT_FOUND,
    GROUND_NODE,
    IDENTIFIER_PATTERN,
    KIND_ARITY,
    PIN_NAMES,
    WARN_NET_AUTO_CREATED,
    WARN_PIN_ALREADY_CONNECTED,
    WARN_PIN_ALREADY_DISCONNECTED,
    WARN_VALUE_UNCHANGED,
    EditError,
    EditResult,
    add_component,
    add_directive,
    add_measurement,
    clone_graph,
    connect_pin,
    disconnect_pin,
    get_component,
    get_pin_net,
    list_component_ids,
    list_net_names,
    remove_component,
    rename_net,
    set_component_value,
)


def _starter_graph() -> dict[str, Any]:
    return {
        "schemaVersion": "0.2", "projectId": "rc_lowpass_1khz", "domain": "analog",
        "topology": "rc_lowpass", "components": {},
        "nets": {
            "in": {"name": "in", "type": "signal"},
            "out": {"name": "out", "type": "signal"},
            GROUND_NODE: {"name": GROUND_NODE, "type": "ground"},
        },
        "analyses": [{"kind": "ac", "startFreq": "10", "stopFreq": "100k", "pointsPerDecade": 100}],
        "measurements": [], "directives": [],
    }


def _add_resistor(graph: dict[str, Any], value: str = "1k") -> dict[str, Any]:
    r = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"}, value=value)
    assert r.success, r.errors
    return r.graph  # type: ignore[return-value]


@pytest.fixture()
def graph() -> dict[str, Any]:
    return _starter_graph()


class TestModuleSurface:
    def test_directive_allowlist_excludes_path_bearing(self) -> None:
        for forbidden in (".include", ".lib", ".model"):
            assert forbidden not in DIRECTIVE_ALLOWLIST

    def test_pin_names_are_well_formed(self) -> None:
        for kind, pins in PIN_NAMES.items():
            assert len(pins) == KIND_ARITY[kind], kind
            for pin in pins:
                assert IDENTIFIER_PATTERN.match(pin), (kind, pin)


class TestEditResultShape:
    def test_success_is_false_when_errors_present(self) -> None:
        result = EditResult(graph={})
        result.add_error("X", "<root>", "boom")
        assert result.success is False

    def test_success_is_true_when_no_errors(self) -> None:
        result = EditResult(graph={})
        result.add_change("noop", "R1", None, None)
        result.add_warning("W", "<root>", "heads up")
        assert result.success is True

    def test_to_dict_has_required_keys(self) -> None:
        result = EditResult(graph={"a": 1})
        result.add_error("E", "<root>", "detail", {"k": 1})
        result.add_warning("W", "<root>", "heads up", {"k": 2})
        result.add_change("op", "target", "before", "after", {"k": 3})
        payload = result.to_dict()
        assert payload["success"] is False
        assert payload["graph"] == {"a": 1}
        assert payload["errors"] == [{"code": "E", "path": "<root>", "detail": "detail", "data": {"k": 1}}]
        assert payload["warnings"] == [{"code": "W", "path": "<root>", "detail": "heads up", "data": {"k": 2}}]
        assert payload["changes"] == [{"op": "op", "target": "target", "before": "before", "after": "after", "data": {"k": 3}}]

    def test_to_dict_drops_empty_data_payloads(self) -> None:
        result = EditResult(graph={})
        result.add_warning("W", "<root>", "heads up")
        assert result.to_dict()["warnings"] == [{"code": "W", "path": "<root>", "detail": "heads up"}]

    def test_add_helpers_accept_none_data(self) -> None:
        result = EditResult(graph={})
        result.add_error("E", "<root>", "x", None)
        result.add_warning("W", "<root>", "x", None)
        result.add_change("op", "t", None, None, None)
        assert result.errors[0].data == {}
        assert result.warnings[0].data == {}
        assert result.changes[0].data == {}

    def test_extend_merges_errors_warnings_changes(self) -> None:
        a = EditResult(graph={})
        a.add_error("E1", "p1", "d1")
        a.add_change("op", "t1", None, 1)
        b = EditResult(graph={})
        b.add_warning("W1", "p2", "d2")
        b.add_change("op", "t2", 1, 2)
        a.extend(b)
        assert [e.code for e in a.errors] == ["E1"]
        assert [w.code for w in a.warnings] == ["W1"]
        assert [c.target for c in a.changes] == ["t1", "t2"]

    def test_from_graph_deep_copies_input(self) -> None:
        original: dict[str, Any] = {"components": {"R1": {"kind": "resistor"}}}
        result = EditResult.from_graph(original)
        assert result.graph is not original
        result.graph["components"]["R1"]["value"] = "1k"  # type: ignore[index]
        assert original["components"]["R1"] == {"kind": "resistor"}

    def test_records_are_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            EditError(code="E", path="p", detail="d").code = "F"  # type: ignore[misc]


class TestCloneGraph:
    def test_returns_a_deep_copy(self) -> None:
        g: dict[str, Any] = {"components": {"R1": {"pins": {"p1": "in"}}}}
        c = clone_graph(g)
        assert c is not g
        c["components"]["R1"]["pins"]["p1"] = "out"
        assert g["components"]["R1"]["pins"]["p1"] == "in"

    def test_handles_none(self) -> None:
        c = clone_graph(None)
        assert isinstance(c, dict)
        assert "components" in c and "nets" in c


class TestAddComponent:
    def test_add_resistor_succeeds(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1.6k")
        assert result.success is True
        comp = result.graph["components"]["R1"]
        assert comp["kind"] == "resistor"
        assert comp["value"] == "1.6k"
        assert comp["pins"] == {"p1": "in", "p2": "out"}
        assert comp["id"] == "R1"
        assert len(result.changes) == 1
        assert result.changes[0].op == "add_component"
        assert result.changes[0].target == "R1"

    def test_input_graph_is_not_mutated(self, graph: dict[str, Any]) -> None:
        snapshot = clone_graph(graph)
        add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1.6k")
        assert graph == snapshot

    def test_duplicate_id_returns_structured_error(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        second = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"}, value="2k")
        assert second.success is False
        assert second.errors[0].code == ERR_COMPONENT_ID_DUPLICATE

    def test_invalid_identifier_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "1bad", "resistor", {"p1": "in", "p2": "out"}, value="1k")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_ID_INVALID

    def test_unknown_kind_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "X1", "flux_capacitor", {"p1": "in", "p2": "out"}, value="1")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_KIND_UNKNOWN

    def test_arity_mismatch_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out", "p3": "extra"}, value="1k")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_ARITY

    def test_numeric_pin_names_rejected(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", {"1": "in", "2": "out"}, value="1k")
        assert result.success is False
        assert result.errors[0].code == ERR_PIN_NAME_INVALID

    def test_source_requires_value(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "Vin", "voltage_source", {"p1": "in", "p2": GROUND_NODE})
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_VALUE_REQUIRED

    def test_voltage_source_with_value_succeeds(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "Vin", "voltage_source", {"p1": "in", "p2": GROUND_NODE}, value="SINE(0 1 1k)")
        assert result.success is True

    def test_passive_requires_value(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"})
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_VALUE_REQUIRED

    def test_diode_requires_model_name(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "D1", "diode", {"p1": "in", "p2": "out"})
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_MODEL_REQUIRED

    def test_diode_with_model_succeeds(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "D1", "diode", {"p1": "in", "p2": "out"}, value="1N4148")
        assert result.success is True

    def test_opamp_uses_value_as_subcircuit(self, graph: dict[str, Any]) -> None:
        result = add_component(
            graph, "U1", "opamp",
            {"ip": "in", "in": "fb", "vp": GROUND_NODE, "vn": GROUND_NODE, "out": "out"},
            value="UniversalOpamp",
        )
        assert result.success is True, result.errors
        assert result.graph["components"]["U1"]["value"] == "UniversalOpamp"

    def test_new_net_is_auto_registered(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", {"p1": "fb", "p2": "out"}, value="10k")
        assert result.success is True
        assert "fb" in result.graph["nets"]
        assert WARN_NET_AUTO_CREATED in [w.code for w in result.warnings]

    def test_ground_is_always_registered(self, graph: dict[str, Any]) -> None:
        del graph["nets"][GROUND_NODE]
        result = add_component(graph, "Vin", "voltage_source", {"p1": "in", "p2": GROUND_NODE}, value="SINE(0 1 1k)")
        assert result.success is True
        assert GROUND_NODE in result.graph["nets"]

    def test_none_graph_returns_structured_error(self) -> None:
        result = add_component(None, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1k")
        assert result.success is False
        assert result.errors[0].code == ERR_GRAPH_TYPE

    def test_non_mapping_graph_returns_structured_error(self) -> None:
        result = add_component("not a mapping", "R1", "resistor", {"p1": "in", "p2": "out"}, value="1k")  # type: ignore[arg-type]
        assert result.success is False
        assert result.errors[0].code == ERR_GRAPH_TYPE

    def test_role_is_preserved(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1.6k", role="series_resistor")
        assert result.success is True
        assert result.graph["components"]["R1"]["role"] == "series_resistor"

    def test_atomicity_arity_mismatch_leaves_graph_unchanged(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        before = clone_graph(graph)
        bad = add_component(graph, "R2", "resistor", {"p1": "in"}, value="2k")
        assert bad.success is False
        assert bad.graph == before

    def test_non_mapping_pins_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_component(graph, "R1", "resistor", ["p1", "p2"], value="1k")  # type: ignore[arg-type]
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_PIN_SHAPE


class TestSetComponentValue:
    def test_set_value_succeeds(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph, value="1k")
        result = set_component_value(graph, "R1", "1.6k")
        assert result.success is True
        assert result.graph["components"]["R1"]["value"] == "1.6k"
        assert result.changes[0].before == "1k"
        assert result.changes[0].after == "1.6k"

    def test_input_not_mutated(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        snapshot = clone_graph(graph)
        set_component_value(graph, "R1", "2k")
        assert graph == snapshot

    def test_unknown_component_returns_error(self, graph: dict[str, Any]) -> None:
        result = set_component_value(graph, "R99", "1k")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_NOT_FOUND

    def test_empty_value_rejected(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = set_component_value(graph, "R1", "   ")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_VALUE_INVALID

    def test_non_string_value_rejected(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = set_component_value(graph, "R1", 1000)  # type: ignore[arg-type]
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_VALUE_INVALID

    def test_idempotent_value_emits_warning(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph, value="1k")
        result = set_component_value(graph, "R1", "1k")
        assert result.success is True
        assert result.warnings[0].code == WARN_VALUE_UNCHANGED


class TestConnectPin:
    def test_connect_pin_succeeds(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = connect_pin(graph, "R1", "p1", "fb")
        assert result.success is True
        assert get_pin_net(result.graph, "R1", "p1") == "fb"

    def test_connect_ground_succeeds(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = connect_pin(graph, "R1", "p1", GROUND_NODE)
        assert result.success is True
        assert get_pin_net(result.graph, "R1", "p1") == GROUND_NODE

    def test_connect_unknown_pin_returns_error(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = connect_pin(graph, "R1", "p9", "in")
        assert result.success is False
        assert result.errors[0].code == ERR_PIN_NOT_FOUND

    def test_connect_unknown_component_returns_error(self, graph: dict[str, Any]) -> None:
        result = connect_pin(graph, "R99", "p1", "in")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_NOT_FOUND

    def test_connect_invalid_net_returns_error(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = connect_pin(graph, "R1", "p1", "1bad")
        assert result.success is False
        assert result.errors[0].code == ERR_NET_NAME_INVALID

    def test_idempotent_connect_emits_warning(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = connect_pin(graph, "R1", "p1", "in")
        assert result.success is True
        assert result.warnings[0].code == WARN_PIN_ALREADY_CONNECTED


class TestDisconnectPin:
    def test_disconnect_pin_succeeds(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = disconnect_pin(graph, "R1", "p1")
        assert result.success is True
        assert get_pin_net(result.graph, "R1", "p1") is None

    def test_disconnect_unknown_pin_returns_error(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = disconnect_pin(graph, "R1", "p9")
        assert result.success is False
        assert result.errors[0].code == ERR_PIN_NOT_FOUND

    def test_idempotent_disconnect_emits_warning(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        graph = disconnect_pin(graph, "R1", "p1").graph  # type: ignore[attr-defined]
        result = disconnect_pin(graph, "R1", "p1")
        assert result.success is True
        assert result.warnings[0].code == WARN_PIN_ALREADY_DISCONNECTED

    def test_disconnect_unknown_component_returns_error(self, graph: dict[str, Any]) -> None:
        result = disconnect_pin(graph, "R99", "p1")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_NOT_FOUND


class TestRemoveComponent:
    def test_remove_succeeds(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        result = remove_component(graph, "R1")
        assert result.success is True
        assert "R1" not in result.graph["components"]

    def test_input_not_mutated(self, graph: dict[str, Any]) -> None:
        graph = _add_resistor(graph)
        snapshot = clone_graph(graph)
        remove_component(graph, "R1")
        assert graph == snapshot

    def test_remove_unknown_returns_error(self, graph: dict[str, Any]) -> None:
        result = remove_component(graph, "R99")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_NOT_FOUND

    def test_invalid_identifier_returns_error(self, graph: dict[str, Any]) -> None:
        result = remove_component(graph, "1bad")
        assert result.success is False
        assert result.errors[0].code == ERR_COMPONENT_ID_INVALID


class TestRenameNet:
    def _seed_two(self, graph: dict[str, Any]) -> dict[str, Any]:
        graph = _add_resistor(graph, value="1k")
        r = add_component(graph, "R2", "resistor", {"p1": "out", "p2": GROUND_NODE}, value="1k")
        assert r.success, r.errors
        return r.graph  # type: ignore[return-value]

    def test_rename_updates_all_pins(self, graph: dict[str, Any]) -> None:
        graph = self._seed_two(graph)
        result = rename_net(graph, "out", "vout")
        assert result.success is True
        assert get_pin_net(result.graph, "R1", "p2") == "vout"
        assert get_pin_net(result.graph, "R2", "p1") == "vout"
        assert "out" not in result.graph["nets"]
        assert "vout" in result.graph["nets"]
        pin_updates = result.changes[0].data["pinUpdates"]
        assert {u["componentId"] for u in pin_updates} == {"R1", "R2"}

    def test_rename_refuses_to_overwrite_existing_net(self, graph: dict[str, Any]) -> None:
        graph = self._seed_two(graph)
        result = rename_net(graph, "out", "in")
        assert result.success is False
        assert result.errors[0].code == ERR_NET_EXISTS

    def test_rename_unknown_net_returns_error(self, graph: dict[str, Any]) -> None:
        result = rename_net(graph, "nope", "new")
        assert result.success is False
        assert result.errors[0].code == ERR_NET_NOT_FOUND

    def test_rename_same_name_emits_warning(self, graph: dict[str, Any]) -> None:
        graph = self._seed_two(graph)
        result = rename_net(graph, "in", "in")
        assert result.success is True
        assert result.warnings[0].code == WARN_VALUE_UNCHANGED

    def test_rename_input_not_mutated(self, graph: dict[str, Any]) -> None:
        graph = self._seed_two(graph)
        snapshot = clone_graph(graph)
        rename_net(graph, "out", "vout")
        assert graph == snapshot


class TestAddDirective:
    def test_allowlisted_directive_succeeds(self, graph: dict[str, Any]) -> None:
        result = add_directive(graph, ".tran 1m 5m 1u")
        assert result.success is True
        assert result.graph["directives"][0]["name"] == ".tran"
        assert result.graph["directives"][0]["args"] == "1m 5m 1u"

    def test_directive_without_args(self, graph: dict[str, Any]) -> None:
        result = add_directive(graph, ".op")
        assert result.success is True
        assert result.graph["directives"][0] == {"name": ".op"}

    def test_include_directive_rejected(self, graph: dict[str, Any]) -> None:
        result = add_directive(graph, ".include /etc/passwd")
        assert result.success is False
        assert result.errors[0].code == ERR_DIRECTIVE_NOT_ALLOWED

    def test_lib_directive_rejected(self, graph: dict[str, Any]) -> None:
        result = add_directive(graph, ".lib secrets.lib")
        assert result.success is False
        assert result.errors[0].code == ERR_DIRECTIVE_NOT_ALLOWED

    def test_empty_directive_rejected(self, graph: dict[str, Any]) -> None:
        result = add_directive(graph, "   ")
        assert result.success is False
        assert result.errors[0].code == ERR_DIRECTIVE_EMPTY

    def test_non_string_directive_rejected(self, graph: dict[str, Any]) -> None:
        result = add_directive(graph, 1234)  # type: ignore[arg-type]
        assert result.success is False
        assert result.errors[0].code == ERR_DIRECTIVE_EMPTY

    def test_input_not_mutated(self, graph: dict[str, Any]) -> None:
        snapshot = clone_graph(graph)
        add_directive(graph, ".tran 1m 5m 1u")
        assert graph == snapshot


class TestAddMeasurement:
    def test_add_measurement_succeeds(self, graph: dict[str, Any]) -> None:
        result = add_measurement(graph, "GAIN_1K", "ac", "FIND mag(V(out)/V(in)) AT=1k")
        assert result.success is True
        assert result.graph["measurements"] == [{"name": "GAIN_1K", "analysis": "ac", "expression": "FIND mag(V(out)/V(in)) AT=1k"}]

    def test_duplicate_measurement_returns_error(self, graph: dict[str, Any]) -> None:
        r1 = add_measurement(graph, "GAIN_1K", "ac", "FIND mag(V(out)/V(in)) AT=1k")
        assert r1.success
        r2 = add_measurement(r1.graph, "GAIN_1K", "ac", "FIND mag(V(out)/V(in)) AT=2k")
        assert r2.success is False
        assert r2.errors[0].code == ERR_MEASUREMENT_EXISTS

    def test_invalid_name_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_measurement(graph, "1bad", "ac", "V(out)")
        assert result.success is False
        assert result.errors[0].code == ERR_MEASUREMENT_NAME_INVALID

    def test_unknown_analysis_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_measurement(graph, "M1", "fft", "V(out)")
        assert result.success is False
        assert result.errors[0].code == ERR_MEASUREMENT_ANALYSIS_INVALID

    def test_empty_expression_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_measurement(graph, "M1", "ac", "   ")
        assert result.success is False
        assert result.errors[0].code == ERR_MEASUREMENT_EXPRESSION_EMPTY

    def test_non_string_expression_returns_error(self, graph: dict[str, Any]) -> None:
        result = add_measurement(graph, "M1", "ac", 1234)  # type: ignore[arg-type]
        assert result.success is False
        assert result.errors[0].code == ERR_MEASUREMENT_EXPRESSION_EMPTY

    def test_input_not_mutated(self, graph: dict[str, Any]) -> None:
        snapshot = clone_graph(graph)
        add_measurement(graph, "GAIN_1K", "ac", "V(out)")
        assert graph == snapshot


class TestEndToEndScenarios:
    def test_full_edit_session(self) -> None:
        graph: dict[str, Any] = {
            "schemaVersion": "0.2", "projectId": "rc_lowpass_1khz", "domain": "analog",
            "topology": "rc_lowpass", "components": {},
            "nets": {GROUND_NODE: {"name": GROUND_NODE, "type": "ground"}},
            "analyses": [], "measurements": [], "directives": [],
        }
        r1 = add_component(graph, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1k")
        assert r1.success
        graph = r1.graph  # type: ignore[assignment]
        r2 = add_component(graph, "C1", "capacitor", {"p1": "out", "p2": GROUND_NODE}, value="100n")
        assert r2.success
        graph = r2.graph  # type: ignore[assignment]
        r3 = set_component_value(graph, "R1", "1.6k")
        assert r3.success
        graph = r3.graph  # type: ignore[assignment]
        r4 = rename_net(graph, "in", "v_in")
        assert r4.success
        graph = r4.graph  # type: ignore[assignment]
        r5 = connect_pin(graph, "R1", "p1", "v_in")
        assert r5.success
        graph = r5.graph  # type: ignore[assignment]
        r6 = add_measurement(graph, "GAIN_1K", "ac", "V(out)")
        assert r6.success
        graph = r6.graph  # type: ignore[assignment]
        r7 = add_directive(graph, ".tran 1m 5m 1u")
        assert r7.success
        graph = r7.graph  # type: ignore[assignment]
        r8 = remove_component(graph, "C1")
        assert r8.success
        graph = r8.graph  # type: ignore[assignment]
        assert get_component(graph, "R1") is not None
        assert get_component(graph, "C1") is None
        assert get_pin_net(graph, "R1", "p1") == "v_in"
        assert get_pin_net(graph, "R1", "p2") == "out"
        assert graph["measurements"] == [{"name": "GAIN_1K", "analysis": "ac", "expression": "V(out)"}]
        assert graph["directives"] == [{"name": ".tran", "args": "1m 5m 1u"}]


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        a = _starter_graph()
        b = _starter_graph()
        ra = add_component(a, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1.6k")
        rb = add_component(b, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1.6k")
        assert ra.graph == rb.graph
        assert len(ra.changes) == len(rb.changes)

    def test_warning_order_is_stable(self) -> None:
        a = _starter_graph()
        result = add_component(a, "R1", "resistor", {"p1": "fb_a", "p2": "fb_b"}, value="1k")
        assert [w.data["netName"] for w in result.warnings] == ["fb_a", "fb_b"]

    def test_to_dict_is_idempotent(self) -> None:
        a = _starter_graph()
        r = add_component(a, "R1", "resistor", {"p1": "in", "p2": "out"}, value="1.6k")
        assert r.to_dict() == r.to_dict()


class TestListAndLookupHelpers:
    def test_list_component_ids_sorted(self) -> None:
        a = _starter_graph()
        a = _add_resistor(a)
        a = add_component(a, "R2", "resistor", {"p1": "in", "p2": "out"}, value="1k").graph  # type: ignore[attr-defined]
        assert list_component_ids(a) == ["R1", "R2"]

    def test_list_net_names_includes_ground(self) -> None:
        a = _starter_graph()
        assert "in" in list_net_names(a)
        assert GROUND_NODE in list_net_names(a)

    def test_get_component_returns_copy(self) -> None:
        a = _starter_graph()
        a = _add_resistor(a)
        comp = get_component(a, "R1")
        assert comp is not None
        comp["value"] = "999"  # type: ignore[index]
        assert a["components"]["R1"]["value"] == "1k"

    def test_get_component_missing(self) -> None:
        assert get_component(_starter_graph(), "R99") is None

    def test_get_pin_net_returns_str(self) -> None:
        a = _starter_graph()
        a = _add_resistor(a)
        assert get_pin_net(a, "R1", "p1") == "in"
        assert get_pin_net(a, "R1", "p9") is None
        assert get_pin_net(a, "R99", "p1") is None
