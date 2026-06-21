"""Unit tests for the live-editing Circuit Graph (Agent 1 scope).

Covers the acceptance criteria from the Agent 1 task brief:

* Empty graph is valid (with a ``GRAPH_GROUND_MISSING`` warning).
* A simple R + C graph passes validation.
* Duplicate component ids are flagged with the stable code
  ``GRAPH_COMPONENT_ID_DUPLICATE``.
* A pin that references an undeclared net is flagged with
  ``GRAPH_COMPONENT_PIN_UNKNOWN_NET``.
* Serialisation round-trips: ``graph_from_dict(graph_to_dict(g))``
  produces an equivalent graph.
* Source components with empty values and semiconductor components
  missing their model / subcircuit name are flagged.
* The ground net ``0`` is recognised specially: the only net name
  allowed for a ``ground``-typed net, and floating other nets emit
  ``GRAPH_NET_FLOATING`` warnings.

The test file uses a small loader to side-step the package-level
``__init__.py`` while Agent 2 and Agent 0 are still landing the other
``ltagent.live`` modules. The Agent 1 source modules are pure
Python and import cleanly in isolation; once Agent 0's package
``__init__.py`` is stable, this loader can be replaced by a normal
``from ltagent.live.graph_schema import ...`` import.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

# ---------------------------------------------------------------------------
# Isolated module loader
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LIVE_DIR = _PROJECT_ROOT / "src" / "ltagent" / "live"


def _load_isolated(name: str) -> object:
    """Load a module from ``src/ltagent/live/<name>.py`` without
    running the (possibly half-written) package ``__init__.py``.

    Registers the module under a private top-level name so the
    relative imports inside ``graph_validation`` and ``graph``
    resolve to the freshly loaded ``graph_schema`` instance.
    """
    target = _LIVE_DIR / f"{name}.py"
    assert target.exists(), f"missing source: {target}"
    private_name = f"ltagent_live_iso.{name}"
    spec = importlib.util.spec_from_file_location(private_name, target)
    assert spec is not None and spec.loader is not None, f"cannot load {target}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[private_name] = module
    spec.loader.exec_module(module)
    return module


_ISO_NAME = "ltagent_live_iso"
if _ISO_NAME not in sys.modules:
    import types

    sys.modules[_ISO_NAME] = types.ModuleType(_ISO_NAME)

# Load graph_schema first (others depend on it via relative imports).
schema_mod = _load_isolated("graph_schema")
sys.modules[f"{_ISO_NAME}.graph_schema"] = schema_mod
sys.modules[_ISO_NAME].graph_schema = schema_mod
gv_mod = _load_isolated("graph_validation")
sys.modules[f"{_ISO_NAME}.graph_validation"] = gv_mod
sys.modules[_ISO_NAME].graph_validation = gv_mod
g_mod = _load_isolated("graph")
sys.modules[f"{_ISO_NAME}.graph"] = g_mod
sys.modules[_ISO_NAME].graph = g_mod

# Re-export the names the tests reference. The tests below can be
# migrated to ``from ltagent.live.graph import ...`` once Agent 0
# stabilises the package ``__init__.py``.
CircuitGraph = schema_mod.CircuitGraph
Component = schema_mod.Component
ComponentKind = schema_mod.ComponentKind
PinMap = schema_mod.PinMap
Net = schema_mod.Net
NetType = schema_mod.NetType
Analysis = schema_mod.Analysis
AnalysisKind = schema_mod.AnalysisKind
Measurement = schema_mod.Measurement
Directive = schema_mod.Directive
LayoutHint = schema_mod.LayoutHint
Constraints = schema_mod.Constraints
GROUND_NODE = schema_mod.GROUND_NODE
SCHEMA_VERSION = schema_mod.SCHEMA_VERSION
DIRECTIVE_ALLOWLIST = schema_mod.DIRECTIVE_ALLOWLIST
PROJECT_ID_PATTERN = schema_mod.PROJECT_ID_PATTERN
KIND_MIN_ARITY = schema_mod.KIND_MIN_ARITY
COMPONENT_KINDS_REQUIRING_MODEL = schema_mod.COMPONENT_KINDS_REQUIRING_MODEL

Severity = gv_mod.Severity
ValidationIssue = gv_mod.ValidationIssue
ValidationResult = gv_mod.ValidationResult
validate_graph = gv_mod.validate_graph
CODE_GROUND_MISSING = gv_mod.CODE_GROUND_MISSING
CODE_COMPONENT_ID_DUPLICATE = gv_mod.CODE_COMPONENT_ID_DUPLICATE
CODE_COMPONENT_PIN_UNKNOWN_NET = gv_mod.CODE_COMPONENT_PIN_UNKNOWN_NET
CODE_COMPONENT_MISSING_VALUE = gv_mod.CODE_COMPONENT_MISSING_VALUE
CODE_COMPONENT_MISSING_MODEL = gv_mod.CODE_COMPONENT_MISSING_MODEL
CODE_COMPONENT_INSUFFICIENT_PINS = gv_mod.CODE_COMPONENT_INSUFFICIENT_PINS
CODE_NET_FLOATING = gv_mod.CODE_NET_FLOATING
CODE_MEAS_UNKNOWN_ANALYSIS = gv_mod.CODE_MEAS_UNKNOWN_ANALYSIS
CODE_DIRECTIVE_DISALLOWED = gv_mod.CODE_DIRECTIVE_DISALLOWED

create_empty_graph = g_mod.create_empty_graph
graph_to_dict = g_mod.graph_to_dict
graph_from_dict = g_mod.graph_from_dict
graph_from_dict_safe = g_mod.graph_from_dict_safe
list_components = g_mod.list_components
list_nets = g_mod.list_nets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resistor(comp_id: str, value: str, n1: str, n2: str) -> Component:
    return Component(
        id=comp_id,
        kind=ComponentKind.RESISTOR,
        value=value,
        pins=PinMap(pins={"1": n1, "2": n2}),
    )


def _make_capacitor(comp_id: str, value: str, n1: str, n2: str) -> Component:
    return Component(
        id=comp_id,
        kind=ComponentKind.CAPACITOR,
        value=value,
        pins=PinMap(pins={"1": n1, "2": n2}),
    )


def _make_grounded_rc(
    project_id: str = "rc_lowpass_1khz",
    r_value: str = "1.6k",
    c_value: str = "100n",
) -> CircuitGraph:
    """Build a minimal R + C low-pass graph with a ground net.

    The structure mirrors the example in plan section 7.2::

        in --R1-- out --C1-- 0
    """
    g = create_empty_graph(project_id=project_id, topology="rc_lowpass")
    g.components["R1"] = _make_resistor("R1", r_value, "in", "out")
    g.components["C1"] = _make_capacitor("C1", c_value, "out", GROUND_NODE)
    g.nets["in"] = Net(name="in")
    g.nets["out"] = Net(name="out")
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    return g


# ---------------------------------------------------------------------------
# Schema: basic construction
# ---------------------------------------------------------------------------


def test_create_empty_graph_minimum_fields() -> None:
    g = create_empty_graph(project_id="demo_proj")
    assert g.schemaVersion == SCHEMA_VERSION
    assert g.projectId == "demo_proj"
    assert g.domain == "analog"
    assert g.topology == ""
    assert g.description is None
    assert g.components == {}
    assert g.nets == {}
    assert g.analyses == []
    assert g.measurements == []
    assert g.directives == []
    assert g.constraints is None
    assert g.layoutHints is None


def test_create_empty_graph_rejects_bad_project_id() -> None:
    with pytest.raises(PydanticValidationError):
        create_empty_graph(project_id="")
    with pytest.raises(PydanticValidationError):
        create_empty_graph(project_id="9bad")
    with pytest.raises(PydanticValidationError):
        create_empty_graph(project_id="bad id")
    with pytest.raises(PydanticValidationError):
        create_empty_graph(project_id="a" * 65)


def test_create_empty_graph_rejects_unknown_domain() -> None:
    with pytest.raises(PydanticValidationError):
        create_empty_graph(project_id="demo", domain="made_up_domain")


def test_create_empty_graph_accepts_known_domains() -> None:
    for domain in ("analog", "sensor", "power", "system"):
        g = create_empty_graph(project_id=f"demo_{domain}", domain=domain)
        assert g.domain == domain


def test_component_id_must_match_pattern() -> None:
    with pytest.raises(PydanticValidationError):
        Component(id="1bad", kind=ComponentKind.RESISTOR, value="1k")
    with pytest.raises(PydanticValidationError):
        Component(id="has space", kind=ComponentKind.RESISTOR, value="1k")
    with pytest.raises(PydanticValidationError):
        Component(id="", kind=ComponentKind.RESISTOR, value="1k")


def test_pinmap_accepts_permissive_pin_shapes() -> None:
    p1 = PinMap(pins={"1": "in", "2": "out"})
    assert p1.net_for("1") == "in"
    p2 = PinMap(pins={"+": "in", "-": GROUND_NODE})
    assert p2.net_for("-") == GROUND_NODE
    p3 = PinMap(pins={"in+": "inp", "in-": "inn", "v+": "vp", "v-": "vn", "out": "out"})
    assert p3.net_for("out") == "out"
    assert p3.nets() == sorted({"inp", "inn", "vp", "vn", "out"})


def test_pinmap_rejects_unsafe_pin_or_net_names() -> None:
    with pytest.raises(PydanticValidationError):
        PinMap(pins={"1 2": "in"})
    with pytest.raises(PydanticValidationError):
        PinMap(pins={"a.b": "in"})
    with pytest.raises(PydanticValidationError):
        PinMap(pins={"1": "has space"})


def test_net_ground_must_be_zero() -> None:
    with pytest.raises(PydanticValidationError):
        Net(name="gnd", type=NetType.GROUND)
    g = Net(name=GROUND_NODE, type=NetType.GROUND)
    assert g.name == GROUND_NODE


def test_circuit_graph_rejects_duplicate_ground() -> None:
    with pytest.raises(PydanticValidationError):
        CircuitGraph(
            schemaVersion=SCHEMA_VERSION,
            projectId="two_ground",
            nets={
                GROUND_NODE: Net(name=GROUND_NODE, type=NetType.GROUND),
                "alt_ground": Net(name="alt_ground", type=NetType.GROUND),
            },
        )


def test_circuit_graph_components_keys_must_match_ids() -> None:
    with pytest.raises(PydanticValidationError):
        CircuitGraph(
            schemaVersion=SCHEMA_VERSION,
            projectId="key_mismatch",
            components={
                "R1": Component(id="R1", kind=ComponentKind.RESISTOR, value="1k"),
                "R2": Component(id="R2", kind=ComponentKind.RESISTOR, value="2k"),
                "WRONG": Component(id="R2", kind=ComponentKind.RESISTOR, value="3k"),
            },
        )


def test_directive_rejects_non_allowlisted() -> None:
    with pytest.raises(PydanticValidationError):
        Directive(name=".include", args="foo.lib")
    with pytest.raises(PydanticValidationError):
        Directive(name=".random_thing")
    d = Directive(name=".tran", args="5m")
    assert d.render() == ".tran 5m"
    d2 = Directive(name=".op")
    assert d2.render() == ".op"


def test_directive_must_start_with_dot() -> None:
    with pytest.raises(PydanticValidationError):
        Directive(name="tran")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_empty_graph_is_ok_with_ground_missing_warning() -> None:
    g = create_empty_graph(project_id="empty_proj")
    result = validate_graph(g)
    assert result.ok is True
    assert len(result.errors) == 0
    assert len(result.warnings) == 1
    assert result.warnings[0].code == CODE_GROUND_MISSING
    assert result.warnings[0].severity is Severity.WARNING
    blob = result.to_dict()
    assert blob["ok"] is True
    assert blob["warningCount"] == 1
    assert blob["issueCount"] == 1


def test_resistor_capacitor_graph_is_valid() -> None:
    g = _make_grounded_rc()
    result = validate_graph(g)
    assert result.ok is True
    assert result.errors == []
    assert result.warnings == []


def test_ground_only_graph_no_components_does_not_warn_floating() -> None:
    g = create_empty_graph(project_id="ground_only")
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    result = validate_graph(g)
    assert result.ok is True
    assert [w.code for w in result.warnings] == []


def test_duplicate_component_id_detected() -> None:
    g = _make_grounded_rc()
    g.components["R1_dup"] = Component(
        id="R1",
        kind=ComponentKind.RESISTOR,
        value="2k",
        pins=PinMap(pins={"1": "out", "2": GROUND_NODE}),
    )
    result = validate_graph(g)
    assert result.ok is False
    assert any(issue.code == CODE_COMPONENT_ID_DUPLICATE for issue in result.errors)


def test_floating_or_missing_net_detected() -> None:
    g = create_empty_graph(project_id="bad_net")
    g.components["R1"] = _make_resistor("R1", "1k", "in", "nowhere")
    g.nets["in"] = Net(name="in")
    result = validate_graph(g)
    assert result.ok is False
    assert any(issue.code == CODE_COMPONENT_PIN_UNKNOWN_NET for issue in result.errors)


def test_floating_declared_net_emits_warning() -> None:
    g = create_empty_graph(project_id="floating_net")
    g.components["R1"] = _make_resistor("R1", "1k", "in", GROUND_NODE)
    g.nets["in"] = Net(name="in")
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    g.nets["unused"] = Net(name="unused")
    result = validate_graph(g)
    assert result.ok is True
    assert any(
        issue.code == CODE_NET_FLOATING and issue.target == "unused" for issue in result.warnings
    )


def test_source_component_missing_value() -> None:
    g = create_empty_graph(project_id="src_no_val")
    g.components["Vin"] = Component(
        id="Vin",
        kind=ComponentKind.VOLTAGE_SOURCE,
        value=None,
        pins=PinMap(pins={"+": "in", "-": GROUND_NODE}),
    )
    g.nets["in"] = Net(name="in")
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    result = validate_graph(g)
    assert result.ok is False
    codes = [issue.code for issue in result.errors]
    assert CODE_COMPONENT_MISSING_VALUE in codes


def test_semiconductor_component_missing_model() -> None:
    g = create_empty_graph(project_id="no_model")
    g.components["D1"] = Component(
        id="D1",
        kind=ComponentKind.DIODE,
        pins=PinMap(pins={"A": "in", "K": GROUND_NODE}),
    )
    g.nets["in"] = Net(name="in")
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    result = validate_graph(g)
    assert result.ok is False
    assert any(issue.code == CODE_COMPONENT_MISSING_MODEL for issue in result.errors)


def test_opamp_with_subckt_value_passes() -> None:
    g = create_empty_graph(project_id="opamp_ok")
    g.components["U1"] = Component(
        id="U1",
        kind=ComponentKind.OPAMP,
        value="UniversalOpamp",
        pins=PinMap(
            pins={
                "in+": "inp",
                "in-": "inn",
                "v+": "vp",
                "v-": "vn",
                "out": "out",
            }
        ),
    )
    for n in ("inp", "inn", "vp", "vn", "out"):
        g.nets[n] = Net(name=n)
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    result = validate_graph(g)
    assert result.ok is True
    assert result.errors == []


def test_opamp_with_insufficient_pins() -> None:
    g = create_empty_graph(project_id="opamp_short")
    g.components["U1"] = Component(
        id="U1",
        kind=ComponentKind.OPAMP,
        value="UniversalOpamp",
        pins=PinMap(pins={"in+": "inp", "in-": "inn"}),
    )
    g.nets["inp"] = Net(name="inp")
    g.nets["inn"] = Net(name="inn")
    result = validate_graph(g)
    assert result.ok is False
    assert any(issue.code == CODE_COMPONENT_INSUFFICIENT_PINS for issue in result.errors)


def test_measurement_referencing_unknown_analysis() -> None:
    g = _make_grounded_rc()
    g.measurements.append(
        Measurement(
            name="VOUT_MAX",
            analysis=AnalysisKind.TRAN,
            expression="MAX V(out)",
        )
    )
    result = validate_graph(g)
    assert result.ok is False
    assert any(issue.code == CODE_MEAS_UNKNOWN_ANALYSIS for issue in result.errors)


def test_measurement_with_declared_analysis_passes() -> None:
    g = _make_grounded_rc()
    g.analyses.append(Analysis(kind=AnalysisKind.TRAN, stopTime="5m"))
    g.measurements.append(
        Measurement(
            name="VOUT_MAX",
            analysis=AnalysisKind.TRAN,
            expression="MAX V(out)",
        )
    )
    result = validate_graph(g)
    assert result.ok is True


def test_invalid_directive_flagged() -> None:
    g = _make_grounded_rc()
    g.directives.append(Directive.model_construct(name=".include", args="x"))
    result = validate_graph(g)
    assert any(issue.code == CODE_DIRECTIVE_DISALLOWED for issue in result.errors)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_serialization_round_trip_preserves_data() -> None:
    g = _make_grounded_rc()
    blob = graph_to_dict(g)
    g2 = graph_from_dict(blob)
    assert graph_to_dict(g) == graph_to_dict(g2)


def test_graph_to_dict_rejects_non_graph() -> None:
    with pytest.raises(TypeError):
        graph_to_dict({"not": "a graph"})  # type: ignore[arg-type]


def test_graph_from_dict_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        graph_from_dict("not a dict")  # type: ignore[arg-type]


def test_graph_from_dict_safe_surfaces_pydantic_errors() -> None:
    g, issues = graph_from_dict_safe({"projectId": "demo"})
    assert g is None
    assert issues
    assert all(issue.severity is Severity.ERROR for issue in issues)
    g2, issues2 = graph_from_dict_safe("not a dict")  # type: ignore[arg-type]
    assert g2 is None
    assert issues2[0].code == "GRAPH_INPUT_INVALID"


def test_graph_from_dict_rejects_unknown_field() -> None:
    with pytest.raises(PydanticValidationError):
        graph_from_dict(
            {
                "schemaVersion": SCHEMA_VERSION,
                "projectId": "demo",
                "sneaky": "field",
            }
        )


def test_graph_to_dict_is_json_safe() -> None:
    g = _make_grounded_rc()
    g.directives.append(Directive(name=".tran", args="5m"))
    blob = graph_to_dict(g)
    encoded = json.dumps(blob)
    assert isinstance(encoded, str)
    assert json.loads(encoded) == blob


# ---------------------------------------------------------------------------
# Read-only inspection
# ---------------------------------------------------------------------------


def test_list_components_returns_sorted() -> None:
    g = _make_grounded_rc()
    ids = [c.id for c in list_components(g)]
    assert ids == ["C1", "R1"]


def test_list_components_on_empty_graph() -> None:
    g = create_empty_graph(project_id="empty")
    assert list_components(g) == []


def test_list_nets_returns_sorted_with_ground_first() -> None:
    g = _make_grounded_rc()
    g.nets["aux"] = Net(name="aux")
    names = [n.name for n in list_nets(g)]
    assert names == [GROUND_NODE, "aux", "in", "out"]


def test_list_components_rejects_non_graph() -> None:
    with pytest.raises(TypeError):
        list_components({"not": "a graph"})  # type: ignore[arg-type]


def test_list_nets_rejects_non_graph() -> None:
    with pytest.raises(TypeError):
        list_nets("not a graph")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End-to-end golden: a realistic RC low-pass graph
# ---------------------------------------------------------------------------


def test_rc_lowpass_end_to_end() -> None:
    g = create_empty_graph(
        project_id="rc_lowpass_1khz",
        topology="rc_lowpass",
        description="First-order RC low-pass filter, cutoff ~1 kHz",
    )
    g.components["Vin"] = Component(
        id="Vin",
        kind=ComponentKind.VOLTAGE_SOURCE,
        value="SINE(0 1 1k)",
        role="input_source",
        pins=PinMap(pins={"+": "in", "-": GROUND_NODE}),
    )
    g.components["R1"] = _make_resistor("R1", "1.6k", "in", "out")
    g.components["C1"] = _make_capacitor("C1", "100n", "out", GROUND_NODE)
    g.nets["in"] = Net(name="in")
    g.nets["out"] = Net(name="out")
    g.nets[GROUND_NODE] = Net(name=GROUND_NODE, type=NetType.GROUND)
    g.analyses.append(Analysis(kind=AnalysisKind.TRAN, stopTime="5m"))
    g.measurements.append(
        Measurement(
            name="VOUT_MAX",
            analysis=AnalysisKind.TRAN,
            expression="MAX V(out)",
        )
    )
    g.layoutHints = LayoutHint(flow="left_to_right", inputNode="in", outputNode="out")
    g.constraints = Constraints.model_validate({"targetCutoffHz": 1000})

    result = validate_graph(g)
    assert result.ok is True, [i.to_dict() for i in result.errors]
    assert result.warnings == []

    blob = graph_to_dict(g)
    assert blob["projectId"] == "rc_lowpass_1khz"
    assert blob["components"]["R1"]["value"] == "1.6k"
    assert blob["nets"][GROUND_NODE]["type"] == "ground"
    g2 = graph_from_dict(blob)
    assert graph_to_dict(g2) == blob
    assert [c.id for c in list_components(g)] == ["C1", "R1", "Vin"]
    assert [n.name for n in list_nets(g)] == [GROUND_NODE, "in", "out"]


# ---------------------------------------------------------------------------
# Constants are exposed and stable
# ---------------------------------------------------------------------------


def test_constants_exposed() -> None:
    assert SCHEMA_VERSION == "0.2"
    assert GROUND_NODE == "0"
    assert ".include" not in DIRECTIVE_ALLOWLIST
    assert ".lib" not in DIRECTIVE_ALLOWLIST
    assert ".tran" in DIRECTIVE_ALLOWLIST
    for kind, arity in KIND_MIN_ARITY.items():
        assert arity >= 2, kind
    assert PROJECT_ID_PATTERN.match("rc_lowpass_1khz")
    assert not PROJECT_ID_PATTERN.match("BadProject")


def test_component_kinds_requiring_model_is_stable() -> None:
    assert (
        frozenset({"diode", "npn", "pnp", "nmos", "pmos", "opamp"})
        == COMPONENT_KINDS_REQUIRING_MODEL
    )
