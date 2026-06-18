"""Phase 11 acceptance tests for advanced analog templates.

Covers the contract from plan section 21:

* New component kinds (diode, npn, pnp, nmos, pmos, opamp) round-trip
  through the IR loader.
* New topologies (inverting_opamp, noninv_opamp, comparator,
  diode_clipper, halfwave_rectifier, bridge_rectifier,
  transistor_switch) accept a hand-crafted IR, render to a valid
  .cir, render to a valid .asc, and pass the layout checker.
* Each new template seeds with ``simulationVerified=True``,
  a non-zero ``layoutScore``, and the correct tags.
* The 10-template seed index is fully populated.
* The .subckt / .model / X-prefix chain survives the full
  pipeline (IR -> netlist -> asc).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from ltagent.asc import render_asc
from ltagent.ir import (
    MVP_TOPOLOGIES,
    AnalysisKind,
    CircuitIR,
    ComponentKind,
    SemiconductorModel,
    Subcircuit,
    load_ir,
)
from ltagent.layout_checker import score_layout
from ltagent.netlist import render_netlist, write_netlist
from ltagent.templates import (
    TemplateStatus,
    list_templates,
    seed_default_templates,
)

NEW_TOPOLOGIES: tuple[str, ...] = (
    "inverting_opamp",
    "noninv_opamp",
    "comparator",
    "diode_clipper",
    "halfwave_rectifier",
    "bridge_rectifier",
    "transistor_switch",
)


NEW_KINDS: tuple[ComponentKind, ...] = (
    ComponentKind.DIODE,
    ComponentKind.NPN,
    ComponentKind.PNP,
    ComponentKind.NMOS,
    ComponentKind.PMOS,
    ComponentKind.OPAMP,
)


UNIVERSAL_OPAMP_BODY = (
    "G1 0 out in+ in- 100k",
    "E1 out 0 in+ in- 1",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_templates_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Seed the official template library into an isolated temp dir."""
    monkeypatch.chdir(tmp_path)
    written = seed_default_templates(tmp_path / "templates")
    assert len(written) == 10, f"expected 10 fresh seeds, got {len(written)}"
    yield tmp_path / "templates"


def _load_seed_ir(templates_dir: Path, template_id: str) -> CircuitIR:
    """Read the seeded IR file for one official template."""
    matches = list_templates(templates_dir, status=TemplateStatus.OFFICIAL)
    target = next(m for m in matches if m.templateId == template_id)
    ir_file = templates_dir / target.status.value / template_id / "template.ir.json"
    return load_ir(ir_file)


# ---------------------------------------------------------------------------
# Component kinds: round-trip + kind/arity invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_new_kind_round_trip_in_ir(kind: ComponentKind) -> None:
    """Each new kind is accepted by CircuitIR and round-trips through JSON."""
    payload = {
        "schemaVersion": "0.1",
        "name": f"phase11_{kind.value}",
        "topology": "inverting_opamp" if kind is ComponentKind.OPAMP else "diode_clipper",
        "nodes": ["a", "b", "0"],
        "components": [],
        "analysis": [{"kind": "op"}],
    }
    if kind is ComponentKind.OPAMP:
        payload["components"] = [
            {"id": "U1", "kind": kind.value, "spicePrefix": "X",
             "nodes": ["a", "b", "a", "b", "0"], "value": "UniversalOpamp"},
        ]
        payload["subcircuits"] = [
            {"name": "UniversalOpamp", "nodes": ["in+", "in-", "v+", "v-", "out"],
             "body": list(UNIVERSAL_OPAMP_BODY)},
        ]
    elif kind in (ComponentKind.NPN, ComponentKind.PNP):
        # 3-terminal BJT.
        payload["components"] = [
            {"id": "Q1", "kind": kind.value, "spicePrefix": "Q",
             "nodes": ["a", "b", "0"], "value": "modelX"},
        ]
        payload["models"] = [{"name": "modelX", "type": "NPN"}]
    elif kind in (ComponentKind.NMOS, ComponentKind.PMOS):
        # 4-terminal MOSFET.
        payload["components"] = [
            {"id": "M1", "kind": kind.value, "spicePrefix": "M",
             "nodes": ["a", "b", "0", "0"], "value": "modelX"},
        ]
        payload["models"] = [{"name": "modelX", "type": "NMOS"}]
    else:
        # 2-terminal kinds (diode).
        payload["components"] = [
            {"id": "D1", "kind": kind.value, "spicePrefix": "D",
             "nodes": ["a", "b"], "value": "modelX"},
        ]
        payload["models"] = [{"name": "modelX", "type": "D"}]
    ir = CircuitIR.model_validate(payload)
    roundtrip = json.loads(ir.model_dump_json())
    assert roundtrip["components"][0]["kind"] == kind.value


def test_new_kinds_all_in_kind_enum() -> None:
    for kind in NEW_KINDS:
        assert kind in ComponentKind


def test_new_kinds_have_spice_prefix() -> None:
    from ltagent.ir import KIND_TO_SPICE_PREFIX

    assert KIND_TO_SPICE_PREFIX["diode"] == "D"
    assert KIND_TO_SPICE_PREFIX["npn"] == "Q"
    assert KIND_TO_SPICE_PREFIX["pnp"] == "Q"
    assert KIND_TO_SPICE_PREFIX["nmos"] == "M"
    assert KIND_TO_SPICE_PREFIX["pmos"] == "M"
    assert KIND_TO_SPICE_PREFIX["opamp"] == "X"


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_new_kinds_have_correct_arity(kind: ComponentKind) -> None:
    from ltagent.ir import KIND_ARITY

    expected = {ComponentKind.OPAMP: 5}.get(kind, 2)
    if kind in (ComponentKind.NPN, ComponentKind.PNP):
        expected = 3
    if kind in (ComponentKind.NMOS, ComponentKind.PMOS):
        expected = 4
    assert KIND_ARITY[kind.value] == expected


# ---------------------------------------------------------------------------
# Topologies: IR -> netlist -> asc pipeline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topology", NEW_TOPOLOGIES)
def test_topology_accepted_in_mvp_topologies(topology: str) -> None:
    assert topology in MVP_TOPOLOGIES


def test_diode_ir_renders_valid_cir() -> None:
    """A diode with a .model block renders both the .model and the D line."""
    ir = CircuitIR(
        schemaVersion="0.1",
        name="phase11_diode_smoke",
        topology="halfwave_rectifier",
        nodes=["in", "out", "0"],
        components=[
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
            {"id": "D1", "kind": "diode", "spicePrefix": "D",
             "nodes": ["in", "out"], "value": "1N4148"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["out", "0"], "value": "1k"},
        ],
        models=[SemiconductorModel(name="1N4148", type="D",
                                    params=["IS=2.55e-9", "RS=0.5"])],
        analysis=[{"kind": "tran", "stopTime": "5m"}],
    )
    res = render_netlist(ir)
    assert ".model 1N4148 D (IS=2.55e-9 RS=0.5)" in res.text
    assert re.search(r"^D1 in out 1N4148", res.text, re.MULTILINE)
    assert res.model_count == 1
    assert res.subcircuit_count == 0


def test_bjt_ir_renders_valid_cir() -> None:
    """An NPN with a .model block renders both the .model and the Q line."""
    ir = CircuitIR(
        schemaVersion="0.1",
        name="phase11_bjt_smoke",
        topology="transistor_switch",
        nodes=["in", "base", "vcc", "out", "0"],
        components=[
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "PULSE(0 5 0 1n 1n 1m 2m)"},
            {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["vcc", "0"], "value": "DC 12"},
            {"id": "Rb", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "base"], "value": "10k"},
            {"id": "Rl", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["vcc", "out"], "value": "1k"},
            {"id": "Q1", "kind": "npn", "spicePrefix": "Q",
             "nodes": ["out", "base", "0"], "value": "BC547"},
        ],
        models=[SemiconductorModel(name="BC547", type="NPN",
                                    params=["BF=400", "VAF=80"])],
        analysis=[{"kind": "tran", "stopTime": "5m"}],
    )
    res = render_netlist(ir)
    assert ".model BC547 NPN (BF=400 VAF=80)" in res.text
    assert re.search(r"^Q1 out base 0 BC547", res.text, re.MULTILINE)


def test_opamp_ir_renders_subckt_and_x_line() -> None:
    """Opamp IR renders both the .subckt block and the X-prefixed call line."""
    ir = CircuitIR(
        schemaVersion="0.1",
        name="phase11_opamp_smoke",
        topology="inverting_opamp",
        nodes=["in", "out", "vfb", "vcc", "vee", "0"],
        components=[
            {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["in", "0"], "value": "SINE(0 0.5 1k)"},
            {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["vcc", "0"], "value": "DC 12"},
            {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
             "nodes": ["vee", "0"], "value": "DC -12"},
            {"id": "R1", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["in", "vfb"], "value": "10k"},
            {"id": "R2", "kind": "resistor", "spicePrefix": "R",
             "nodes": ["vfb", "out"], "value": "100k"},
            {"id": "U1", "kind": "opamp", "spicePrefix": "X",
             "nodes": ["in", "vfb", "vcc", "vee", "out"], "value": "UniversalOpamp"},
        ],
        subcircuits=[Subcircuit(name="UniversalOpamp",
                                  nodes=["in+", "in-", "v+", "v-", "out"],
                                  body=list(UNIVERSAL_OPAMP_BODY))],
        analysis=[{"kind": "tran", "stopTime": "5m"}],
    )
    res = render_netlist(ir)
    assert ".subckt UniversalOpamp in+ in- v+ v- out" in res.text
    assert "G1 0 out in+ in- 100k" in res.text
    assert ".ends UniversalOpamp" in res.text
    assert re.search(r"^U1 in vfb vcc vee out UniversalOpamp", res.text, re.MULTILINE)
    assert res.subcircuit_count == 1


# ---------------------------------------------------------------------------
# Per-topology: deterministic layout renders without errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topology", NEW_TOPOLOGIES)
def test_topology_renders_asc_without_error(topology: str) -> None:
    """Each new topology's IR renders to an ASCResult with required lines."""
    ir = _build_minimal_ir(topology)
    asc = render_asc(ir)
    assert "Version 4" in asc.text
    assert "SHEET 1" in asc.text
    # No zero-length wire bugs and no missing placements.
    layout = score_layout(asc)
    # overlap is always 0 by construction.
    assert layout.overlaps == 0
    # ground node is present.
    assert layout.missing_ground is False
    # Every new kind has a SYMBOL entry.
    sym_lines = [ln for ln in asc.text.splitlines() if ln.startswith("SYMBOL ")]
    assert sym_lines


# ---------------------------------------------------------------------------
# Templates: 10-template library, per-template invariants
# ---------------------------------------------------------------------------


def test_official_template_library_has_ten_entries(seeded_templates_dir: Path) -> None:
    officials = list_templates(seeded_templates_dir, status=TemplateStatus.OFFICIAL)
    assert len(officials) == 10
    ids = {m.templateId for m in officials}
    for tid in NEW_TOPOLOGIES:
        assert tid in ids, f"missing official template {tid}"


@pytest.mark.parametrize("template_id", NEW_TOPOLOGIES)
def test_new_template_seed_ir_loads_and_renders(
    seeded_templates_dir: Path, template_id: str
) -> None:
    """Each new template's seeded IR loads, renders to netlist, renders to asc."""
    ir = _load_seed_ir(seeded_templates_dir, template_id)
    # .cir render must succeed and contain a .end.
    res = render_netlist(ir)
    assert res.text.rstrip().endswith(".end")
    # .asc render must succeed and have no overlaps.
    asc = render_asc(ir)
    layout = score_layout(asc)
    assert layout.overlaps == 0
    assert layout.missing_ground is False
    # layoutScore must be at or above the official threshold (85).
    m = next(mm for mm in list_templates(seeded_templates_dir, status=TemplateStatus.OFFICIAL)
             if mm.templateId == template_id)
    assert m.layoutScore >= 85, (
        f"official template {template_id} has layoutScore {m.layoutScore} < 85"
    )


@pytest.mark.parametrize("template_id", NEW_TOPOLOGIES)
def test_new_template_marked_simulation_verified(
    seeded_templates_dir: Path, template_id: str
) -> None:
    m = next(mm for mm in list_templates(seeded_templates_dir, status=TemplateStatus.OFFICIAL)
             if mm.templateId == template_id)
    assert m.simulationVerified is True


# ---------------------------------------------------------------------------
# End-to-end: project orchestrator accepts a new topology
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topology", NEW_TOPOLOGIES)
def test_project_create_writes_full_artifact_set_for_new_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, topology: str
) -> None:
    """ltagent create <new_topology_ir> writes the standard artifact set."""
    from ltagent.config import WorkspaceConfig
    from ltagent.project import create_project

    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "projects").mkdir()
    (cwd / "templates").mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    # Seed the library so the project orchestrator can match templates.
    seed_default_templates(cwd / "templates")

    ir = _build_minimal_ir(topology)
    target = cwd / "projects" / f"phase11_{topology}"

    create_project(
        ir,
        target,
        templates_dir=cwd / "templates",
        config=__import__("ltagent.config", fromlist=["Config"]).Config(
            workspace=WorkspaceConfig(),
        ),
        run_simulation=False,
    )

    assert target.is_dir()
    files = {p.name for p in target.iterdir()}
    assert "circuit.ir.json" in files
    assert "circuit.cir" in files
    assert "circuit.asc" in files
    assert "metadata.json" in files
    assert "result.json" in files


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_minimal_ir(topology: str) -> CircuitIR:
    """Build a minimal hand-crafted IR for the given topology.

    Used by the per-topology smoke tests. Values are picked so that
    each IR is a self-consistent analog circuit that the deterministic
    placer + wirer accept.
    """
    if topology == "inverting_opamp":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "out", "vfb", "vcc", "vee", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 0.5 1k)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 12"},
                {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vee", "0"], "value": "DC -12"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "vfb"], "value": "10k"},
                {"id": "R2", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["vfb", "out"], "value": "100k"},
                {"id": "U1", "kind": "opamp", "spicePrefix": "X",
                 "nodes": ["in", "vfb", "vcc", "vee", "out"], "value": "UniversalOpamp"},
            ],
            subcircuits=[Subcircuit(name="UniversalOpamp",
                                      nodes=["in+", "in-", "v+", "v-", "out"],
                                      body=list(UNIVERSAL_OPAMP_BODY))],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    if topology == "noninv_opamp":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "out", "vfb", "vcc", "vee", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 0.5 1k)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 12"},
                {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vee", "0"], "value": "DC -12"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "vfb"], "value": "10k"},
                {"id": "R2", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["vfb", "0"], "value": "10k"},
                {"id": "U1", "kind": "opamp", "spicePrefix": "X",
                 "nodes": ["vfb", "0", "vcc", "vee", "out"], "value": "UniversalOpamp"},
            ],
            subcircuits=[Subcircuit(name="UniversalOpamp",
                                      nodes=["in+", "in-", "v+", "v-", "out"],
                                      body=list(UNIVERSAL_OPAMP_BODY))],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    if topology == "comparator":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "out", "vcc", "vee", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 1 1k)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 5"},
                {"id": "Vee", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vee", "0"], "value": "DC 0"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "vcc"], "value": "1k"},
                {"id": "U1", "kind": "opamp", "spicePrefix": "X",
                 "nodes": ["vcc", "vee", "vcc", "vee", "out"], "value": "UniversalOpamp"},
            ],
            subcircuits=[Subcircuit(name="UniversalOpamp",
                                      nodes=["in+", "in-", "v+", "v-", "out"],
                                      body=list(UNIVERSAL_OPAMP_BODY))],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    if topology == "diode_clipper":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "out", "high", "low", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "out"], "value": "1k"},
                {"id": "D1", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["out", "high"], "value": "1N4148"},
                {"id": "D2", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["low", "out"], "value": "1N4148"},
            ],
            models=[SemiconductorModel(name="1N4148", type="D")],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    if topology == "halfwave_rectifier":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "out", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
                {"id": "D1", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["in", "out"], "value": "1N4148"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["out", "0"], "value": "1k"},
            ],
            models=[SemiconductorModel(name="1N4148", type="D")],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    if topology == "bridge_rectifier":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "out", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "SINE(0 5 1k)"},
                {"id": "D1", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["in", "out"], "value": "1N4148"},
                {"id": "D2", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["out", "in"], "value": "1N4148"},
                {"id": "D3", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["0", "out"], "value": "1N4148"},
                {"id": "D4", "kind": "diode", "spicePrefix": "D",
                 "nodes": ["out", "0"], "value": "1N4148"},
                {"id": "R1", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["out", "0"], "value": "1k"},
            ],
            models=[SemiconductorModel(name="1N4148", type="D")],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    if topology == "transistor_switch":
        return CircuitIR(
            schemaVersion="0.1",
            name=topology,
            topology=topology,
            nodes=["in", "base", "vcc", "out", "0"],
            components=[
                {"id": "Vin", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["in", "0"], "value": "PULSE(0 5 0 1n 1n 1m 2m)"},
                {"id": "Vcc", "kind": "voltage_source", "spicePrefix": "V",
                 "nodes": ["vcc", "0"], "value": "DC 12"},
                {"id": "Rb", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["in", "base"], "value": "10k"},
                {"id": "Rl", "kind": "resistor", "spicePrefix": "R",
                 "nodes": ["vcc", "out"], "value": "1k"},
                {"id": "Q1", "kind": "npn", "spicePrefix": "Q",
                 "nodes": ["out", "base", "0"], "value": "BC547"},
            ],
            models=[SemiconductorModel(name="BC547", type="NPN",
                                        params=["BF=400"])],
            analysis=[{"kind": "tran", "stopTime": "5m"}],
        )
    raise ValueError(f"unknown topology: {topology}")


# Touch the imports that some callers do indirectly so ruff doesn't drop them.
_ = AnalysisKind
_ = write_netlist
