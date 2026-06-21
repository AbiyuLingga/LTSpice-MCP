import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { App } from "./App";
import type { EngineBridge } from "./engine";

function fakeBridge(options: { ai?: boolean; analog?: Record<string, unknown>; schematic?: Record<string, unknown>; waveform?: boolean } = {}): EngineBridge {
  let revision = 0;
  const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
    if (method === "ai.provider.status") return { configured: Boolean(options.ai) };
    if (method === "ai.contextPreview") {
      return { documents: [{ document: "schematic", redacted: false, size: 40 }], estimatedBytes: 40, snapshotId: "snapshot-1" };
    }
    if (method === "ai.propose") {
      return {
        changeSet: { baseRevision: revision, operations: [{ document: "schematic", kind: "resistor", symbolId: "R1", type: "place_node", x: 96, y: 96 }], schemaVersion: "2.0" },
        proposal: { operations: [{ document: "schematic", payload: { symbolId: "R1" }, type: "place_node" }], rationale: "", warnings: [] },
        validation: { isValid: true, issues: [] },
      };
    }
    if (method === "codex.install") return { configPath: "/projects/analog_lab/.codex/config.toml" };
    if (method === "project.create" || method === "project.open") {
      return {
        displayName: "Analog Lab",
        projectDir: "/projects/analog_lab",
        projectId: "analog_lab",
        revision: 0,
        schemaVersion: "2.0",
      };
    }
    if (method === "design.get") {
      if (params?.document === "analog") return { document: options.analog ?? { components: {} } };
      if (params?.document === "digital") return { document: { design: {} } };
      return {
        document: options.schematic ?? { gridSize: 16, netLabels: [], schemaVersion: "2.0", symbols: [], wires: [] },
        project: {
          displayName: "Analog Lab",
          projectDir: "/projects/analog_lab",
          projectId: "analog_lab",
          revision: 0,
          schemaVersion: "2.0",
        },
      };
    }
    if (method === "digital.emulate") {
      return {
        led: {
          diagnostics: [],
          frames: [{ cycle: 7, height: 16, pixels: Array.from({ length: 128 }, (_, index) => index === 26), width: 8 }],
          height: 16,
          width: 8,
        },
        status: "halted",
      };
    }
    if (method === "simulation.start" || method === "synthesis.start") {
      return { jobId: "job_1", state: "pending" };
    }
    if (method === "job.status") {
      if (options.waveform) {
        return {
          jobId: "job_1",
          result: { run: { artifacts: { waveformIndex: "waveform/index.json" } }, status: "success" },
          state: "completed",
        };
      }
      return { jobId: "job_1", result: { status: "skipped" }, state: "skipped" };
    }
    if (method === "artifact.readSlice") {
      return {
        text: JSON.stringify(options.waveform
          ? { name: "v(out)", points: [[0, 0], [1, 1], [2, 0]] }
          : {}),
      };
    }
    if (method === "tool.doctor") {
      return {
        status: "pass",
        tools: ["ngspice", "iverilog", "vvp", "verilator", "yosys"].map((toolId) => ({ available: true, toolId })),
      };
    }
    if (method === "digital.render") {
      return { source: "module counter_top();\nendmodule\n" };
    }
    if (method === "design.applyChanges") {
      revision += 1;
      return { changedDocuments: ["schematic"], revision };
    }
    if (method === "project.refresh") {
      return { changed: false, project: { revision } };
    }
    if (method === "design.undo" || method === "design.redo") {
      return { changed: true, revision };
    }
    return {};
  });
  return {
    request: request as unknown as EngineBridge["request"],
  };
}

function schematicBounds() {
  return {
    bottom: 480,
    height: 480,
    left: 0,
    right: 640,
    toJSON: () => ({}),
    top: 0,
    width: 640,
    x: 0,
    y: 0,
  } as DOMRect;
}

function firePointerEvent(element: Element, type: string, clientX: number, clientY: number) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    clientX: { value: clientX },
    clientY: { value: clientY },
    pointerId: { value: 1 },
  });
  fireEvent(element, event);
}

async function createProject(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Create project" }));
  await user.type(screen.getByLabelText("Project name"), "Analog Lab");
  await user.click(screen.getByRole("button", { name: "Create" }));
}

describe("App", () => {
  it("creates a local project through the engine bridge", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);

    expect(await screen.findByText("Analog Lab")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith("project.create", {
      displayName: "Analog Lab",
      projectId: "analog_lab",
    });
  });

  it("snaps imported loose wire routes to actual analog pin endpoints", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge({
      analog: {
        components: {
          R1: { kind: "resistor", pins: { pins: { "1": "VIN", "2": "N1" } } },
          V1: { kind: "voltage_source", pins: { pins: { "1": "VIN", "2": "0" } } },
        },
      },
      schematic: {
        gridSize: 16,
        netLabels: [],
        schemaVersion: "2.0",
        symbols: [
          { id: "V1", kind: "voltage_source", rotation: 0, x: 272, y: 336 },
          { id: "R1", kind: "resistor", rotation: 0, x: 384, y: 304 },
        ],
        wires: [{ connections: [], id: "W_V1_R1", net: "VIN", points: [[272, 304], [352, 304]] }],
      },
    });
    render(<App bridge={bridge} />);

    await createProject(user);

    expect(await screen.findByLabelText("Wire W_V1_R1")).toHaveAttribute("points", "328,304 328,336");
  });

  it("auto-lays out imported RLC series circuits into a readable row", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge({
      analog: {
        components: {
          C1: { kind: "capacitor", pins: { pins: { "1": "N2", "2": "0" } } },
          L1: { kind: "inductor", pins: { pins: { "1": "N1", "2": "N2" } } },
          R1: { kind: "resistor", pins: { pins: { "1": "VIN", "2": "N1" } } },
          V1: { kind: "voltage_source", pins: { pins: { "1": "VIN", "2": "0" } } },
        },
      },
      schematic: {
        gridSize: 16,
        netLabels: [],
        schemaVersion: "2.0",
        symbols: [
          { id: "V1", kind: "voltage_source", rotation: 0, x: 272, y: 336 },
          { id: "R1", kind: "resistor", rotation: 0, x: 384, y: 304 },
          { id: "L1", kind: "inductor", rotation: 0, x: 496, y: 304 },
          { id: "C1", kind: "capacitor", rotation: 90, x: 688, y: 320 },
        ],
        wires: [
          { connections: [], id: "W_V1_R1", net: "VIN", points: [[272, 304], [352, 304]] },
          { connections: [], id: "W_R1_L1", net: "N1", points: [[416, 304], [464, 304]] },
          { connections: [], id: "W_L1_C1", net: "N2", points: [[528, 304], [640, 304]] },
          { connections: [], id: "W_C1_GND", net: "0", points: [[640, 368], [704, 384]] },
        ],
      },
    });
    render(<App bridge={bridge} />);

    await createProject(user);

    expect(await screen.findByLabelText("Voltage source V1")).toHaveStyle({ left: "128px", top: "240px" });
    expect(screen.getByLabelText("Resistor R1")).toHaveStyle({ left: "272px", top: "240px" });
    expect(screen.getByLabelText("Inductor L1")).toHaveStyle({ left: "416px", top: "240px" });
    expect(screen.getByLabelText("Capacitor C1")).toHaveStyle({ left: "560px", top: "240px" });
    expect(screen.getByLabelText("Wire W_V1_R1")).toHaveAttribute("points", "184,240 216,240");
    expect(screen.getByLabelText("Wire W_C1_GND")).toHaveAttribute("points", "72,240 72,336 616,336 616,240");
  });

  it("never applies an AI proposal until the user accepts it", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge({ ai: true });
    render(<App bridge={bridge} />);
    await createProject(user);
    await user.type(screen.getByLabelText("AI design request"), "Make an RC low-pass");
    await user.click(screen.getByRole("button", { name: "Preview context" }));
    await user.click(await screen.findByRole("button", { name: "Generate proposal" }));
    await screen.findByLabelText("AI proposal diff");
    expect(vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Reject AI proposal" }));
    expect(vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Generate proposal" }));
    await user.click(await screen.findByRole("button", { name: "Apply" }));
    expect(vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges")).toHaveLength(1);
  });

  it("connects Codex to the open project", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);
    await createProject(user);
    await user.click(screen.getByRole("button", { name: "Connect Codex" }));
    expect(bridge.request).toHaveBeenCalledWith("codex.install", { projectId: "analog_lab" });
    expect(await screen.findByText(/Codex connected/)).toBeInTheDocument();
  });

  it("opens an existing project by id", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: "Open project" }));
    await user.type(screen.getByLabelText("Project ID"), "analog_lab");
    await user.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByText("Analog Lab")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith("project.open", { projectId: "analog_lab" });
  });

  it("switches between work surfaces without leaving the workspace", async () => {
    const user = userEvent.setup();
    render(<App bridge={fakeBridge()} />);

    await user.click(screen.getByRole("tab", { name: "LED" }));

    expect(screen.getByRole("heading", { name: "LED matrix" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Schematic" })).toBeVisible();
  });

  it("renders a verified Tiny8 frame in the LED surface", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await user.click(screen.getByRole("tab", { name: "LED" }));
    await user.click(screen.getByRole("button", { name: "Run LED demo" }));

    expect(await screen.findByText("1 frame rendered")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith("digital.emulate", expect.any(Object));
  });

  it("starts and observes an analog simulation job", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "Waveform" }));

    await user.click(screen.getByRole("button", { name: "Run analog simulation" }));

    expect(await screen.findByText("Simulation skipped", {}, { timeout: 1000 })).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith("simulation.start", {
      domain: "analog",
      projectId: "analog_lab",
    });
  });

  it("reports installed local EDA tools", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: "Tool doctor" }));

    expect(await screen.findByText("Tool doctor pass: 5/5 available")).toBeVisible();
  });

  it("loads a downsampled waveform preview from job artifacts", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge({ waveform: true });
    const request = vi.mocked(bridge.request);
    request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
      if (method === "project.create") return {
        displayName: "Analog Lab", projectDir: "/projects/analog_lab", projectId: "analog_lab", revision: 0, schemaVersion: "2.0",
      };
      if (method === "design.get") return { document: { gridSize: 16, netLabels: [], schemaVersion: "2.0", symbols: [], wires: [] } };
      if (method === "simulation.start") return { jobId: "job_1", state: "pending" };
      if (method === "job.status") return { jobId: "job_1", result: { run: { artifacts: { waveformIndex: "waveform/index.json" } }, status: "success" }, state: "completed" };
      if (method === "artifact.readSlice" && params.artifact === "waveform/index.json") return { text: JSON.stringify({ signals: [{ name: "v(out)", preview: "waveform/signal_0.preview.json", sampleCount: 3 }] }) };
      if (method === "artifact.readSlice") return { text: JSON.stringify({ name: "v(out)", points: [[0, 0], [1, 1], [2, 0]] }) };
      return {};
    });
    render(<App bridge={bridge} />);
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "Waveform" }));

    await user.click(screen.getByRole("button", { name: "Run analog simulation" }));

    expect(await screen.findByLabelText("Waveform v(out)", {}, { timeout: 1000 })).toBeVisible();
  });

  it("creates a digital template through typed IR and renders engine HDL", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "HDL" }));

    await user.click(screen.getByRole("button", { name: "Counter" }));

    expect(await screen.findByText(/module counter_top/)).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: [expect.objectContaining({ type: "set_digital_design" })],
        }),
      }),
    );
  });

  it("places a selected component through a revision-guarded change set", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 96, clientY: 144 });

    expect(await screen.findByText(/1 components/)).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: expect.arrayContaining([
            expect.objectContaining({ componentId: "R1", type: "add_component" }),
            expect.objectContaining({ symbolId: "R1", type: "place_node" }),
          ]),
          schemaVersion: "2.0",
        }),
        projectId: "analog_lab",
      }),
    );
  });

  it("renders circuit symbols instead of generic component boxes", async () => {
    const user = userEvent.setup();
    render(<App bridge={fakeBridge()} />);

    await createProject(user);
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 96, clientY: 144 });
    await user.click(screen.getByRole("button", { name: "Place Op amp" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 256, clientY: 144 });
    await user.click(screen.getByRole("button", { name: "Place Inductor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 416, clientY: 144 });

    expect(await screen.findByLabelText("Resistor R1")).toBeVisible();
    expect(screen.getByLabelText("Op amp X1")).toBeVisible();
    expect(screen.getByLabelText("Inductor L1")).toBeVisible();
    expect(screen.getByTestId("symbol-opamp").querySelector("polygon")).not.toBeNull();
    expect(screen.getByTestId("symbol-resistor").querySelector("polyline")).not.toBeNull();
    expect(screen.getByTestId("symbol-inductor").querySelector("path")).not.toBeNull();
  });

  it("moves an installed component on the grid and persists the final position", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    const grid = screen.getByLabelText("Schematic grid");
    vi.spyOn(grid, "getBoundingClientRect").mockReturnValue(schematicBounds());
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(grid, { clientX: 96, clientY: 144 });

    const resistor = await screen.findByLabelText("Resistor R1");
    firePointerEvent(resistor, "pointerdown", 96, 144);
    firePointerEvent(resistor, "pointermove", 179, 211);
    firePointerEvent(resistor, "pointerup", 179, 211);

    await waitFor(() => expect(vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges")).toHaveLength(2));
    const changes = vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges");
    expect(changes).toHaveLength(2);
    expect(changes.at(-1)?.[1]).toEqual(expect.objectContaining({
      changeSet: expect.objectContaining({
        baseRevision: 1,
        operations: expect.arrayContaining([expect.objectContaining({
          symbolId: "R1",
          type: "move_node",
          x: 176,
          y: 208,
        })]),
      }),
    }));
  });

  it("does not place a second component when clicking an installed symbol", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 96, clientY: 144 });
    fireEvent.click(await screen.findByLabelText("Resistor R1"));

    const changes = vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges");
    expect(changes).toHaveLength(1);
    expect(screen.getByText(/1 components/)).toBeVisible();
  });

  it("selects, rotates, and deletes an installed component", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 96, clientY: 144 });
    await user.click(await screen.findByLabelText("Resistor R1"));
    await user.click(screen.getByRole("button", { name: "Rotate selection" }));

    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: expect.arrayContaining([expect.objectContaining({ rotation: 90, type: "rotate_node" })]),
        }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Delete selection" }));
    expect(screen.getByText(/0 components/)).toBeVisible();
  });

  it("connects two component pins with an electrical net", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    const grid = screen.getByLabelText("Schematic grid");
    vi.spyOn(grid, "getBoundingClientRect").mockReturnValue(schematicBounds());
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(grid, { clientX: 96, clientY: 144 });
    await user.click(screen.getByRole("button", { name: "Place Capacitor" }));
    fireEvent.click(grid, { clientX: 256, clientY: 144 });
    await user.click(screen.getByRole("button", { name: "Wire tool" }));
    await user.click(screen.getByRole("button", { name: "Pin p2 of R1" }));
    await user.click(screen.getByRole("button", { name: "Pin p1 of C1" }));

    expect(await screen.findByLabelText("Wire wire_1")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: expect.arrayContaining([
            expect.objectContaining({ connections: [{ pin: "p2", symbolId: "R1" }, { pin: "p1", symbolId: "C1" }], net: "net_1", type: "set_wire_route" }),
            expect.objectContaining({ componentId: "R1", pin: "p2", type: "connect_pin" }),
            expect.objectContaining({ componentId: "C1", pin: "p1", type: "connect_pin" }),
          ]),
        }),
      }),
    );
  });

  it("edits the selected component label and value in the inspector", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 96, clientY: 144 });
    await user.click(await screen.findByLabelText("Resistor R1"));
    await user.type(screen.getByLabelText("Component label"), "Rload");
    await user.clear(screen.getByLabelText("Component value"));
    await user.type(screen.getByLabelText("Component value"), "1k");
    await user.click(screen.getByRole("button", { name: "Apply properties" }));

    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: expect.arrayContaining([
            expect.objectContaining({ componentId: "R1", type: "set_component_value", value: "1k" }),
            expect.objectContaining({ label: "Rload", properties: { value: "1k" }, type: "set_node_properties" }),
          ]),
        }),
      }),
    );
  });
});
