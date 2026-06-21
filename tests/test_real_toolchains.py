from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from ltagent.analog_workbench import run_analog_simulation
from ltagent.digital_workbench import (
    VERILATOR_TOOL_ID,
    ClockSpec,
    DigitalDesignIR,
    DigitalModule,
    Port,
    ResetSpec,
    run_simulation,
    run_synthesis,
)
from ltagent.engine_server import EngineService
from ltagent.live.graph_schema import (
    Analysis,
    AnalysisKind,
    CircuitGraph,
    Component,
    ComponentKind,
    Net,
    NetType,
    PinMap,
)


def _rc_graph() -> CircuitGraph:
    return CircuitGraph(
        schemaVersion="0.2",
        projectId="rc_lab",
        topology="rc_lowpass",
        components={
            "V1": Component(
                id="V1",
                kind=ComponentKind.VOLTAGE_SOURCE,
                value="PULSE(0 1 0 1n 1n 5m 10m)",
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
            "0": Net(name="0", type=NetType.GROUND),
            "vin": Net(name="vin"),
            "vout": Net(name="vout"),
        },
        analyses=[Analysis(kind=AnalysisKind.TRAN, stopTime="1m", stepTime="1u")],
    )


def _counter() -> DigitalDesignIR:
    return DigitalDesignIR(
        schemaVersion="1.0",
        topModule="counter8",
        clock=ClockSpec(name="clk", periodNs=10),
        reset=ResetSpec(name="rst_n", active="low"),
        modules=[
            DigitalModule(
                name="counter8",
                kind="counter",
                ports=[
                    Port(name="clk", direction="in", width=1),
                    Port(name="rst_n", direction="in", width=1),
                    Port(name="q", direction="out", width=8),
                ],
                body="reg [7:0] q; always @(posedge clk) q <= !rst_n ? 0 : q + 1;",
            )
        ],
    )


def _fsm() -> DigitalDesignIR:
    return DigitalDesignIR(
        schemaVersion="1.0",
        topModule="toggle_fsm",
        clock=ClockSpec(name="clk", periodNs=10),
        reset=ResetSpec(name="rst_n", active="low"),
        modules=[
            DigitalModule(
                name="toggle_fsm",
                kind="fsm",
                ports=[
                    Port(name="clk", direction="in", width=1),
                    Port(name="rst_n", direction="in", width=1),
                    Port(name="state", direction="out", width=1),
                ],
                body="reg state; always @(posedge clk) state <= !rst_n ? 0 : ~state;",
            )
        ],
    )


def _moving_pixel() -> DigitalDesignIR:
    return DigitalDesignIR(
        schemaVersion="1.0",
        topModule="moving_pixel",
        clock=ClockSpec(name="clk", periodNs=10),
        reset=ResetSpec(name="rst_n", active="low"),
        modules=[
            DigitalModule(
                name="moving_pixel",
                kind="led_blinker",
                ports=[
                    Port(name="clk", direction="in", width=1),
                    Port(name="rst_n", direction="in", width=1),
                    Port(name="led", direction="out", width=16),
                ],
                body=(
                    "reg [15:0] led; always @(posedge clk) "
                    "led <= !rst_n ? 16'h0001 : {led[14:0], led[15]};"
                ),
            )
        ],
    )


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_real_ngspice_rc_transient(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "rc_lab").mkdir(parents=True)

    result = run_analog_simulation("rc_lab", root, _rc_graph())

    assert result.bundle.status == "success", result.bundle.errors


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_real_ngspice_noninverting_opamp(tmp_path: Path) -> None:
    def component(
        component_id: str,
        kind: ComponentKind,
        value: str,
        pins: dict[str, str],
    ) -> Component:
        return Component(id=component_id, kind=kind, value=value, pins=PinMap(pins=pins))

    graph = CircuitGraph(
        schemaVersion="0.2",
        projectId="opamp_lab",
        topology="noninv_opamp",
        components={
            "VCC": component("VCC", ComponentKind.VOLTAGE_SOURCE, "5", {"p1": "vcc", "p2": "0"}),
            "VEE": component("VEE", ComponentKind.VOLTAGE_SOURCE, "-5", {"p1": "vee", "p2": "0"}),
            "VIN": component("VIN", ComponentKind.VOLTAGE_SOURCE, "1", {"p1": "inp", "p2": "0"}),
            "X1": component(
                "X1",
                ComponentKind.OPAMP,
                "UniversalOpamp",
                {"in+": "inp", "in-": "fb", "v+": "vcc", "v-": "vee", "out": "out"},
            ),
            "R1": component("R1", ComponentKind.RESISTOR, "10k", {"p1": "out", "p2": "fb"}),
            "R2": component("R2", ComponentKind.RESISTOR, "10k", {"p1": "fb", "p2": "0"}),
        },
        nets={
            name: Net(name=name, type=NetType.GROUND if name == "0" else NetType.SIGNAL)
            for name in ("0", "vcc", "vee", "inp", "fb", "out")
        },
        analyses=[Analysis(kind=AnalysisKind.OP)],
    )
    root = tmp_path / "projects"
    (root / "opamp_lab").mkdir(parents=True)

    result = run_analog_simulation("opamp_lab", root, graph)

    assert result.bundle.status == "success", result.bundle.errors


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_editor_changeset_builds_and_runs_generic_rc_project(tmp_path: Path) -> None:
    service = EngineService(tmp_path / "projects")

    def request(request_id: int, method: str, params: dict[str, object]):  # type: ignore[no-untyped-def]
        return service.handle(
            {"id": request_id, "jsonrpc": "2.0", "method": method, "params": params}
        )

    request(1, "project.create", {"projectId": "editor_rc"})
    operations: list[dict[str, object]] = []
    for component_id, kind, value, x in (
        ("V1", "voltage_source", "5", 80),
        ("R1", "resistor", "1k", 240),
        ("C1", "capacitor", "100n", 400),
    ):
        operations.extend(
            [
                {
                    "componentId": component_id,
                    "document": "analog",
                    "kind": kind,
                    "pins": {},
                    "type": "add_component",
                    "value": value,
                },
                {
                    "document": "schematic",
                    "kind": kind,
                    "symbolId": component_id,
                    "type": "place_node",
                    "x": x,
                    "y": 160,
                },
            ]
        )
    for component_id, pin, net in (
        ("V1", "p1", "vin"),
        ("V1", "p2", "0"),
        ("R1", "p1", "vin"),
        ("R1", "p2", "vout"),
        ("C1", "p1", "vout"),
        ("C1", "p2", "0"),
    ):
        operations.append(
            {
                "componentId": component_id,
                "document": "analog",
                "net": net,
                "pin": pin,
                "type": "connect_pin",
            }
        )
    applied = request(
        2,
        "design.applyChanges",
        {
            "changeSet": {
                "baseRevision": 0,
                "operations": operations,
                "schemaVersion": "2.0",
            },
            "projectId": "editor_rc",
        },
    )
    assert applied["result"]["revision"] == 1

    started = request(
        3,
        "simulation.start",
        {"domain": "analog", "projectId": "editor_rc"},
    )
    job_id = started["result"]["jobId"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = request(4, "job.status", {"jobId": job_id})["result"]
        if status["state"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("editor RC simulation did not finish")
    assert status["state"] == "completed", status
    assert status["result"]["status"] == "success"
    service.close()


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus toolchain not installed",
)
def test_real_iverilog_counter_produces_vcd(tmp_path: Path) -> None:
    project = tmp_path / "counter"
    project.mkdir()

    result = run_simulation("counter", project, _counter())

    assert result.bundle.status == "success", result.bundle.errors
    artifact = result.bundle.run.artifacts.get("waveform")
    assert artifact is not None
    assert (project / artifact).is_file()


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus toolchain not installed",
)
@pytest.mark.parametrize("design", [_fsm(), _moving_pixel()], ids=["fsm", "moving-pixel"])
def test_real_iverilog_additional_digital_golden(tmp_path: Path, design: DigitalDesignIR) -> None:
    project = tmp_path / design.topModule
    project.mkdir()

    result = run_simulation(design.topModule, project, design)

    assert result.bundle.status == "success", result.bundle.errors
    assert "waveform" in result.bundle.run.artifacts


@pytest.mark.skipif(shutil.which("verilator") is None, reason="Verilator not installed")
def test_real_verilator_counter_lint(tmp_path: Path) -> None:
    project = tmp_path / "counter"
    project.mkdir()

    result = run_simulation("counter", project, _counter(), tool_id=VERILATOR_TOOL_ID)

    assert result.bundle.status == "success", result.bundle.errors


@pytest.mark.skipif(shutil.which("yosys") is None, reason="yosys not installed")
def test_real_yosys_counter_synthesis(tmp_path: Path) -> None:
    project = tmp_path / "counter"
    project.mkdir()

    result = run_synthesis("counter", project, _counter())

    assert result.bundle.status == "success", result.bundle.errors
    assert any(item["name"] == "cell_count" for item in result.bundle.measurements)
