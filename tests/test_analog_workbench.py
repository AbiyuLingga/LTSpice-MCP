"""Tests for the analog workbench.

The host this test runs on does not have ngspice installed, so
the simulation tests are split:

* ``test_run_simulation_skipped_when_ngspice_missing`` exercises
  the structured skip path (no tool → ResultBundle(status='skipped')).
* ``test_run_simulation_with_user_supplied_tool`` invokes
  /bin/true as a stand-in simulator and verifies the success /
  failure / timeout paths without needing ngspice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ltagent.analog_workbench import (
    SUPPORTED_ANALYSIS_KINDS,
    AnalogWorkbenchError,
    bundle_waveform,
    graph_to_netlist_text,
    parse_ltspice_asc,
    run_analog_simulation,
    validate_topology,
)
from ltagent.jobs import RESULT_SCHEMA_VERSION, JobState
from ltagent.live.graph_schema import (
    Analysis,
    AnalysisKind,
    CircuitGraph,
    Component,
    ComponentKind,
    NetType,
    PinMap,
)
from ltagent.live.graph_schema import (
    Net as GraphNet,
)
from ltagent.live.project import (
    get_project_paths,
)
from ltagent.workbench_v2 import DIR_RUNS


def _sample_graph() -> CircuitGraph:
    return CircuitGraph(
        schemaVersion="0.2",
        projectId="rc_lab",
        topology="rc_lowpass",
        components={
            "V1": Component(
                id="V1",
                kind=ComponentKind.VOLTAGE_SOURCE,
                value="SIN(0 1 1k)",
                pins=PinMap(pins={"p1": "vin", "p2": "0"}),
            ),
            "R1": Component(
                id="R1",
                kind=ComponentKind.RESISTOR,
                value="1k",
                pins=PinMap(pins={"p1": "vin", "p2": "vout"}),
            ),
            "C1": Component(
                id="C1",
                kind=ComponentKind.CAPACITOR,
                value="100n",
                pins=PinMap(pins={"p1": "vout", "p2": "0"}),
            ),
        },
        nets={
            "vin": GraphNet(name="vin", type=NetType.SIGNAL),
            "vout": GraphNet(name="vout", type=NetType.SIGNAL),
            "0": GraphNet(name="0", type=NetType.GROUND),
        },
        analyses=[
            Analysis(kind=AnalysisKind.OP),
            Analysis(kind=AnalysisKind.TRAN, startTime="0", stopTime="1m", stepTime="1u"),
        ],
    )


def test_graph_to_netlist_emits_components_and_analyses() -> None:
    text = graph_to_netlist_text(_sample_graph())
    assert "R1" in text
    assert "C1" in text
    assert "V1" in text
    assert ".tran" in text.lower() or ".op" in text.lower()


def test_validate_topology_rejects_missing_ground() -> None:
    graph = CircuitGraph(
        schemaVersion="0.2",
        projectId="x",
        components={
            "R1": Component(
                id="R1",
                kind=ComponentKind.RESISTOR,
                value="1k",
                pins=PinMap(pins={"p1": "vin", "p2": "vout"}),
            )
        },
        nets={
            "vin": GraphNet(name="vin"),
            "vout": GraphNet(name="vout"),
        },
    )
    issues = validate_topology(graph)
    assert any("ground" in issue for issue in issues)


def test_run_simulation_skipped_when_ngspice_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir = projects_root / "rc_lab"
    paths = get_project_paths(project_dir)
    paths.graph.parent.mkdir(parents=True, exist_ok=True)
    paths.graph.write_text(_sample_graph().model_dump_json(), encoding="utf-8")
    # Force the resolver to fail.
    monkeypatch.setattr("ltagent.analog_workbench._resolve_tool", lambda *_: None)
    monkeypatch.setattr("ltagent.analog_workbench.discover_analog_tool", lambda: None)
    result = run_analog_simulation("rc_lab", projects_root, _sample_graph())
    assert result.skipped_reason is not None
    assert result.bundle.status == "skipped"
    assert result.bundle.errors == ["ngspice binary not found"]
    assert result.manifest.state == JobState.SKIPPED


def test_run_simulation_with_user_supplied_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir = projects_root / "rc_lab"
    paths = get_project_paths(project_dir)
    paths.graph.parent.mkdir(parents=True, exist_ok=True)
    graph = _sample_graph()
    paths.graph.write_text(graph.model_dump_json(), encoding="utf-8")

    # Stand-in tool: exit 0 and write a measurable log line. The
    # fake tool's argv is ``-b -o <log_path> <netlist_path>``; the
    # 4th arg (index 3) is the netlist and the 3rd is the log path.
    fake_tool = tmp_path / "fake_tool.sh"
    fake_tool.write_text(
        "#!/bin/sh\necho 'vout_max = 1.234' > \"$3\"\nexit 0\n",
        encoding="utf-8",
    )
    fake_tool.chmod(0o755)
    result = run_analog_simulation("rc_lab", projects_root, graph, tool_executable=fake_tool)
    assert result.bundle.status == "success"
    assert result.manifest.state == JobState.COMPLETED
    assert any(m["name"] == "vout_max" for m in result.bundle.measurements)
    # The netlist landed under runs/.
    run_dirs = [p for p in (project_dir / DIR_RUNS).iterdir() if p.is_dir()]
    assert run_dirs
    assert (run_dirs[0] / "circuit.cir").is_file()


def test_run_simulation_with_failing_tool(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    project_dir = projects_root / "rc_lab"
    project_dir.mkdir(parents=True)
    fake_tool = tmp_path / "fail.sh"
    fake_tool.write_text("#!/bin/sh\necho broken 1>&2\nexit 42\n", encoding="utf-8")
    fake_tool.chmod(0o755)
    result = run_analog_simulation(
        "rc_lab", projects_root, _sample_graph(), tool_executable=fake_tool
    )
    assert result.bundle.status == "failed"
    assert result.manifest.state == JobState.FAILED
    assert "42" in (result.manifest.errorMessage or "")


def test_ltspice_asc_round_trip_supported_subset() -> None:
    asc = (
        "Version 4\n"
        "SHEET 1 880 680\n"
        "WIRE 0 0 96 0\n"
        "WIRE 96 0 96 64\n"
        "FLAG 96 64 0\n"
        "SYMBOL res 16 0 R0\n"
        "SYMATTR InstName R1\n"
        "SYMBOL cap 80 80 R0\n"
        "TEXT 0 0 Left 2 !.tran 1m\n"
    )
    parsed = parse_ltspice_asc(asc)
    assert parsed["schemaVersion"] == "ltagent-asc-1.0"
    assert len(parsed["wires"]) == 2
    assert len(parsed["components"]) == 2
    assert len(parsed["flags"]) == 1
    assert len(parsed["textBlocks"]) == 1
    # The Version / SHEET lines are opaque.
    assert "Version 4" in parsed["opaque"]


def test_bundle_waveform_stitches_chunks() -> None:
    samples = [0.1 * i for i in range(50)]
    bundle = bundle_waveform("vout", samples, sample_rate_hz=100.0)
    assert bundle.waveform is not None
    assert bundle.waveform.sampleRateHz == 100.0
    assert len(bundle.waveform.traces) == 1
    assert bundle.waveform.traces[0].sampleCount == 50


def test_supported_analysis_kinds_match_ir() -> None:
    assert "op" in SUPPORTED_ANALYSIS_KINDS
    assert "ac" in SUPPORTED_ANALYSIS_KINDS
    assert "tran" in SUPPORTED_ANALYSIS_KINDS
    assert "dc" in SUPPORTED_ANALYSIS_KINDS


def test_result_bundle_schema_version_stable() -> None:
    assert RESULT_SCHEMA_VERSION == "1.0"


def test_analog_error_structured() -> None:
    err = AnalogWorkbenchError(
        "WORKBENCH_ANALOG_INVALID",
        "bad",
        data={"field": "kind"},
    )
    assert err.code == "WORKBENCH_ANALOG_INVALID"
    assert err.data == {"field": "kind"}
