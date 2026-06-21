"""Safe instance-based Digital IR 2.0 and deterministic Verilog generator."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INSTANCE_PINS: dict[str, frozenset[str]] = {
    "and_gate": frozenset({"a", "b", "y"}),
    "or_gate": frozenset({"a", "b", "y"}),
    "xor_gate": frozenset({"a", "b", "y"}),
    "not_gate": frozenset({"a", "y"}),
    "mux2": frozenset({"a", "b", "sel", "y"}),
    "adder": frozenset({"a", "b", "y"}),
    "counter": frozenset({"clk", "reset", "q"}),
    "fsm": frozenset({"clk", "reset", "toggle", "state"}),
    "pwm": frozenset({"clk", "reset", "duty", "out"}),
}


class DigitalPortV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=IDENTIFIER.pattern)
    direction: Literal["input", "output", "inout"]
    width: int = Field(default=1, ge=1, le=256)


class DigitalSignalV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=IDENTIFIER.pattern)
    width: int = Field(default=1, ge=1, le=256)


class DigitalInstanceV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=IDENTIFIER.pattern)
    kind: Literal[
        "and_gate", "or_gate", "xor_gate", "not_gate", "mux2", "adder", "counter", "fsm", "pwm"
    ]
    parameters: dict[str, int | str | bool] = Field(default_factory=dict)


class DigitalConnectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instanceId: str = Field(pattern=IDENTIFIER.pattern)
    pin: str = Field(pattern=IDENTIFIER.pattern)
    signal: str = Field(pattern=IDENTIFIER.pattern)


class DigitalClockV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: str = Field(pattern=IDENTIFIER.pattern)
    periodNs: int = Field(ge=1, le=1_000_000)


class DigitalResetV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: str = Field(pattern=IDENTIFIER.pattern)
    active: Literal["low", "high"] = "low"


class DigitalTestGoalV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=IDENTIFIER.pattern)
    signal: str = Field(pattern=IDENTIFIER.pattern)
    expected: int = Field(ge=0)
    afterCycles: int = Field(ge=1, le=1_000_000)


class DigitalDesignIRV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal["2.0"] = "2.0"
    topModule: str = Field(pattern=IDENTIFIER.pattern)
    ports: list[DigitalPortV2]
    signals: list[DigitalSignalV2] = Field(default_factory=list)
    instances: list[DigitalInstanceV2]
    connections: list[DigitalConnectionV2]
    clock: DigitalClockV2 | None = None
    reset: DigitalResetV2 | None = None
    testGoals: list[DigitalTestGoalV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_are_complete(self) -> DigitalDesignIRV2:
        signal_names = [item.name for item in self.ports] + [item.name for item in self.signals]
        if len(signal_names) != len(set(signal_names)):
            raise ValueError("port and signal names must be unique")
        instance_ids = [item.id for item in self.instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("instance ids must be unique")
        known_signals = set(signal_names)
        known_instances = {item.id: item for item in self.instances}
        connected: dict[str, set[str]] = {item.id: set() for item in self.instances}
        for connection in self.connections:
            instance = known_instances.get(connection.instanceId)
            if instance is None:
                raise ValueError(
                    f"connection references unknown instance {connection.instanceId!r}"
                )
            if connection.signal not in known_signals:
                raise ValueError(f"connection references unknown signal {connection.signal!r}")
            if connection.pin not in INSTANCE_PINS[instance.kind]:
                raise ValueError(f"pin {connection.pin!r} is invalid for {instance.kind!r}")
            if connection.pin in connected[instance.id]:
                raise ValueError(f"pin {instance.id}.{connection.pin} is connected more than once")
            connected[instance.id].add(connection.pin)
        for instance in self.instances:
            missing = INSTANCE_PINS[instance.kind] - connected[instance.id]
            if missing:
                raise ValueError(f"instance {instance.id!r} is missing pins {sorted(missing)}")
        for reference in [
            self.clock.signal if self.clock else None,
            self.reset.signal if self.reset else None,
        ]:
            if reference is not None and reference not in known_signals:
                raise ValueError(f"clock/reset references unknown signal {reference!r}")
        for goal in self.testGoals:
            if goal.signal not in known_signals:
                raise ValueError(f"test goal references unknown signal {goal.signal!r}")
        return self


def _width(width: int) -> str:
    return "" if width == 1 else f" [{width - 1}:0]"


def _connections(design: DigitalDesignIRV2, instance_id: str) -> dict[str, str]:
    return {item.pin: item.signal for item in design.connections if item.instanceId == instance_id}


def render_verilog_v2(design: DigitalDesignIRV2, *, include_testbench: bool = True) -> str:
    sequential_outputs = {
        _connections(design, instance.id)[
            "q" if instance.kind == "counter" else "state" if instance.kind == "fsm" else "out"
        ]
        for instance in design.instances
        if instance.kind in {"counter", "fsm", "pwm"}
    }
    port_lines = []
    for port in design.ports:
        storage = " reg" if port.direction == "output" and port.name in sequential_outputs else ""
        port_lines.append(f"  {port.direction}{storage}{_width(port.width)} {port.name}")
    declarations = [
        f"  {'reg' if signal.name in sequential_outputs else 'wire'}{_width(signal.width)} {signal.name};"
        for signal in design.signals
    ]
    logic: list[str] = []
    for instance in design.instances:
        pins = _connections(design, instance.id)
        if instance.kind in {"and_gate", "or_gate", "xor_gate"}:
            operator = {"and_gate": "&", "or_gate": "|", "xor_gate": "^"}[instance.kind]
            logic.append(f"  assign {pins['y']} = {pins['a']} {operator} {pins['b']};")
        elif instance.kind == "not_gate":
            logic.append(f"  assign {pins['y']} = ~{pins['a']};")
        elif instance.kind == "mux2":
            logic.append(f"  assign {pins['y']} = {pins['sel']} ? {pins['b']} : {pins['a']};")
        elif instance.kind == "adder":
            logic.append(f"  assign {pins['y']} = {pins['a']} + {pins['b']};")
        elif instance.kind == "counter":
            reset = (
                f"!{pins['reset']}"
                if design.reset and design.reset.active == "low"
                else pins["reset"]
            )
            logic.append(
                f"  always @(posedge {pins['clk']}) {pins['q']} <= {reset} ? 0 : {pins['q']} + 1'b1;"
            )
        elif instance.kind == "fsm":
            reset = (
                f"!{pins['reset']}"
                if design.reset and design.reset.active == "low"
                else pins["reset"]
            )
            logic.append(
                f"  always @(posedge {pins['clk']}) {pins['state']} <= {reset} ? 0 : ({pins['toggle']} ? ~{pins['state']} : {pins['state']});"
            )
        else:
            width = int(instance.parameters.get("width", 8))
            counter = f"{instance.id}_counter"
            declarations.append(f"  reg{_width(width)} {counter};")
            reset = (
                f"!{pins['reset']}"
                if design.reset and design.reset.active == "low"
                else pins["reset"]
            )
            logic.append(
                f"  always @(posedge {pins['clk']}) {counter} <= {reset} ? 0 : {counter} + 1'b1;"
            )
            logic.append(f"  always @* {pins['out']} = ({counter} < {pins['duty']});")
    module = (
        "\n".join(
            [
                f"module {design.topModule}(\n" + ",\n".join(port_lines) + "\n);",
                *declarations,
                *logic,
                "endmodule",
            ]
        )
        + "\n"
    )
    return module + (_render_testbench(design) if include_testbench else "")


def _render_testbench(design: DigitalDesignIRV2) -> str:
    declarations = []
    connections = []
    for port in design.ports:
        storage = "reg" if port.direction == "input" else "wire"
        initial = " = 0" if storage == "reg" else ""
        declarations.append(f"  {storage}{_width(port.width)} {port.name}{initial};")
        connections.append(f"    .{port.name}({port.name})")
    clock_line = ""
    if design.clock:
        clock_line = f"  always #({max(1, design.clock.periodNs // 2)}) {design.clock.signal} = ~{design.clock.signal};\n"
    reset_lines = ""
    if design.reset:
        active, inactive = ("0", "1") if design.reset.active == "low" else ("1", "0")
        period = design.clock.periodNs if design.clock else 10
        reset_lines = f"    {design.reset.signal} = {active};\n    #({period * 2}) {design.reset.signal} = {inactive};\n"
    period = design.clock.periodNs if design.clock else 10
    goals = []
    for goal in design.testGoals:
        goals.append(f"    #({period * goal.afterCycles});")
        goals.append(
            f'    if ({goal.signal} !== {goal.expected}) $fatal(1, "goal {goal.name} failed");'
        )
    return "".join(
        [
            f"module tb_{design.topModule};\n",
            "\n".join(declarations),
            "\n",
            f"  {design.topModule} dut(\n",
            ",\n".join(connections),
            "\n  );\n",
            clock_line,
            "  initial begin\n",
            '    $dumpfile("waveform.vcd");\n',
            "    $dumpvars(0, dut);\n",
            reset_lines,
            "\n".join(goals),
            f"\n    #({period * 2});\n",
            "    $finish;\n  end\nendmodule\n",
        ]
    )


__all__ = ["DigitalDesignIRV2", "render_verilog_v2"]
