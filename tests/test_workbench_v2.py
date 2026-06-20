"""Tests for the Workbench v2 Pydantic contracts.

The contracts in :mod:`ltagent.workbench_v2` are additive to the 1.0
project layout. These tests focus on:

* Each contract round-trips through Pydantic + JSON.
* Field-level validators reject malformed input.
* The :class:`AnalogGraph` alias points at the existing
  :class:`ltagent.live.graph_schema.CircuitGraph` so Phase 1.2 is a
  no-op for the graph layer.
* Generated JSON Schemas are present and loadable.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from ltagent.live.graph_schema import CircuitGraph
from ltagent.workbench_v2 import (
    ANALOG_GRAPH_SCHEMA_VERSION,
    DOCUMENT_PATHS,
    FILE_MANIFEST,
    LEGACY_PROJECT_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION,
    AnalogGraph,
    DigitalDesignDocument,
    HardwareProject,
    Requirements,
    SchematicNetLabel,
    SchematicSymbol,
    SchematicView,
    SchematicWire,
    SystemSpec,
)

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_hardware_project_round_trip() -> None:
    project = HardwareProject(
        projectId="analog_lab",
        displayName="Analog Lab",
        revision=0,
    )
    payload = project.model_dump(mode="json")
    assert payload["schemaVersion"] == "2.0"
    assert payload["projectId"] == "analog_lab"
    assert HardwareProject.model_validate(payload) == project


def test_hardware_project_rejects_invalid_project_id() -> None:
    with pytest.raises(ValueError):
        HardwareProject(projectId="Bad ID", displayName="x", revision=0)


def test_hardware_project_rejects_negative_revision() -> None:
    with pytest.raises(ValueError):
        HardwareProject(projectId="analog_lab", displayName="x", revision=-1)


def test_hardware_project_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        HardwareProject.model_validate(
            {
                "schemaVersion": "2.0",
                "projectId": "analog_lab",
                "displayName": "x",
                "revision": 0,
                "extra": "nope",
            }
        )


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


def test_requirements_rejects_nested_constraint() -> None:
    with pytest.raises(ValueError):
        Requirements(constraints={"nested": {"a": 1}})


def test_requirements_accepts_scalar_constraints() -> None:
    requirements = Requirements(
        text="RC low-pass",
        constraints={"cutoffHz": 1000, "voltage": 5.0},
        goals=["attenuate 40 dB/dec"],
        safetyClass="simulation_only",
    )
    assert requirements.constraints["cutoffHz"] == 1000


def test_requirements_rejects_unknown_safety_class() -> None:
    with pytest.raises(ValueError):
        Requirements(safetyClass="toaster")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AnalogGraph alias
# ---------------------------------------------------------------------------


def test_analog_graph_alias_is_circuit_graph() -> None:
    assert AnalogGraph is CircuitGraph


def test_analog_graph_schema_version_matches() -> None:
    assert ANALOG_GRAPH_SCHEMA_VERSION == "0.2"
    # The CircuitGraph requires the caller to stamp schemaVersion; the
    # contract therefore advertises the version through the module
    # constant rather than a Pydantic default.
    assert "schemaVersion" in CircuitGraph.model_fields


# ---------------------------------------------------------------------------
# SchematicView
# ---------------------------------------------------------------------------


def _sample_schematic() -> dict:
    return {
        "schemaVersion": "2.0",
        "gridSize": 16,
        "symbols": [
            {
                "id": "r1",
                "kind": "resistor",
                "x": 96,
                "y": 144,
                "rotation": 0,
                "properties": {"value": "1k"},
            },
            {
                "id": "u1",
                "kind": "opamp",
                "x": 256,
                "y": 144,
                "rotation": 90,
                "mirror": False,
                "properties": {"model": "UniversalOpamp"},
            },
        ],
        "wires": [
            {"id": "w1", "points": [[0, 0], [96, 0]], "net": "vin"},
            {"id": "w2", "points": [[96, 0], [256, 0]], "net": "vout"},
        ],
        "netLabels": [
            {"id": "l1", "x": 0, "y": 0, "net": "vin"},
        ],
    }


def test_schematic_view_round_trip() -> None:
    view = SchematicView.model_validate(_sample_schematic())
    payload = view.model_dump(mode="json")
    assert SchematicView.model_validate(payload) == view


def test_schematic_view_rejects_unknown_kind() -> None:
    payload = _sample_schematic()
    payload["symbols"][0]["kind"] = "nonsense"
    with pytest.raises(ValueError):
        SchematicView.model_validate(payload)


def test_schematic_view_rejects_bad_rotation() -> None:
    payload = _sample_schematic()
    payload["symbols"][1]["rotation"] = 45
    with pytest.raises(ValueError):
        SchematicView.model_validate(payload)


def test_schematic_view_rejects_duplicate_symbol_ids() -> None:
    payload = _sample_schematic()
    payload["symbols"][1]["id"] = "r1"
    with pytest.raises(ValueError):
        SchematicView.model_validate(payload)


def test_schematic_view_rejects_short_wire() -> None:
    payload = _sample_schematic()
    payload["wires"][0]["points"] = [[0, 0]]
    with pytest.raises(ValueError):
        SchematicView.model_validate(payload)


def test_schematic_symbol_alias_round_trip() -> None:
    symbol = SchematicSymbol(id="r1", kind="resistor", x=16, y=16, properties={"value": "1k"})
    assert symbol.rotation == 0
    assert symbol.mirror is False
    payload = symbol.model_dump(mode="json")
    assert payload["properties"] == {"value": "1k"}
    assert SchematicSymbol.model_validate(payload) == symbol


def test_schematic_wire_accepts_two_points() -> None:
    wire = SchematicWire(id="w1", points=[(0, 0), (10, 0)])
    assert wire.net is None
    assert wire.points == [(0, 0), (10, 0)]


def test_schematic_net_label_round_trip() -> None:
    label = SchematicNetLabel(id="l1", x=5, y=5, net="vin")
    assert label.net == "vin"
    payload = label.model_dump(mode="json")
    assert SchematicNetLabel.model_validate(payload) == label


# ---------------------------------------------------------------------------
# Digital / System
# ---------------------------------------------------------------------------


def test_digital_document_round_trip() -> None:
    doc = DigitalDesignDocument(
        design={"top": "tiny8_cpu"},
        notes="Tiny8 baseline",
    )
    payload = doc.model_validate(doc.model_dump(mode="json"))
    assert payload.notes == "Tiny8 baseline"
    assert payload.design == {"top": "tiny8_cpu"}


def test_system_spec_accepts_empty_lists() -> None:
    spec = SystemSpec(blocks=[], connections=[], clockHz=1_000_000)
    assert spec.clockHz == 1_000_000


def test_system_spec_rejects_zero_clock() -> None:
    with pytest.raises(ValueError):
        SystemSpec(clockHz=0)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_manifest_path_constant() -> None:
    assert FILE_MANIFEST == "hardware.project.json"


def test_schema_version_constants() -> None:
    assert PROJECT_SCHEMA_VERSION == "2.0"
    assert LEGACY_PROJECT_SCHEMA_VERSION == "1.0"


def test_document_paths_match_v2_layout() -> None:
    assert DOCUMENT_PATHS["analog"] == "design/analog/main.graph.json"
    assert DOCUMENT_PATHS["schematic"] == "design/schematic/main.view.json"
    assert DOCUMENT_PATHS["digital"] == "design/digital/main.digital.json"


# ---------------------------------------------------------------------------
# Generated JSON Schemas
# ---------------------------------------------------------------------------


def test_generated_schemas_present_and_identical(tmp_path: Path) -> None:
    repo_dir = Path("schemas/workbench_v2")
    pkg_files = resources.files("ltagent.resources.workbench_v2")
    for name in (
        "HardwareProject",
        "Requirements",
        "AnalogGraph",
        "SchematicView",
        "DigitalDesignDocument",
        "SystemSpec",
    ):
        repo_path = repo_dir / f"{name}.schema.json"
        pkg_path = pkg_files / f"{name}.schema.json"
        assert repo_path.is_file(), f"missing {repo_path}"
        repo_text = repo_path.read_text(encoding="utf-8")
        pkg_text = pkg_path.read_text(encoding="utf-8")
        assert repo_text == pkg_text, f"diverged for {name}"
        schema = json.loads(repo_text)
        assert schema["title"] == name
        assert schema["$id"].endswith(f"/{name}.schema.json")
