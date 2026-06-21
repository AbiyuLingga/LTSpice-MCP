import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { App } from "./App";
import type { EngineBridge } from "./engine";

function fakeBridge(): EngineBridge {
  let revision = 0;
  const request = vi.fn(async (method: string) => {
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
      return {
        document: { gridSize: 16, netLabels: [], schemaVersion: "2.0", symbols: [], wires: [] },
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
          operations: [expect.objectContaining({ type: "place_node" })],
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

    expect(await screen.findByLabelText("Resistor resistor_1")).toBeVisible();
    expect(screen.getByLabelText("Op amp opamp_2")).toBeVisible();
    expect(screen.getByTestId("symbol-opamp").querySelector("polygon")).not.toBeNull();
    expect(screen.getByTestId("symbol-resistor").querySelector("polyline")).not.toBeNull();
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

    const resistor = await screen.findByLabelText("Resistor resistor_1");
    firePointerEvent(resistor, "pointerdown", 96, 144);
    firePointerEvent(resistor, "pointermove", 179, 211);
    firePointerEvent(resistor, "pointerup", 179, 211);

    await waitFor(() => expect(vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges")).toHaveLength(2));
    const changes = vi.mocked(bridge.request).mock.calls.filter(([method]) => method === "design.applyChanges");
    expect(changes).toHaveLength(2);
    expect(changes.at(-1)?.[1]).toEqual(expect.objectContaining({
      changeSet: expect.objectContaining({
        baseRevision: 1,
        operations: [expect.objectContaining({
          symbolId: "resistor_1",
          type: "move_node",
          x: 176,
          y: 208,
        })],
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
    fireEvent.click(await screen.findByLabelText("Resistor resistor_1"));

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
    await user.click(await screen.findByLabelText("Resistor resistor_1"));
    await user.click(screen.getByRole("button", { name: "Rotate selection" }));

    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: [expect.objectContaining({ rotation: 90, type: "rotate_node" })],
        }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Delete selection" }));
    expect(screen.getByText(/0 components/)).toBeVisible();
  });

  it("draws an orthogonal wire with two grid clicks", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await createProject(user);
    const grid = screen.getByLabelText("Schematic grid");
    vi.spyOn(grid, "getBoundingClientRect").mockReturnValue(schematicBounds());
    await user.click(screen.getByRole("button", { name: "Wire tool" }));
    fireEvent.click(grid, { clientX: 32, clientY: 48 });
    fireEvent.click(grid, { clientX: 160, clientY: 112 });

    expect(await screen.findByLabelText("Wire wire_1")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: [expect.objectContaining({ type: "set_wire_route" })],
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
    await user.click(await screen.findByLabelText("Resistor resistor_1"));
    await user.type(screen.getByLabelText("Component label"), "Rload");
    await user.type(screen.getByLabelText("Component value"), "1k");
    await user.click(screen.getByRole("button", { name: "Apply properties" }));

    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({
        changeSet: expect.objectContaining({
          operations: [expect.objectContaining({ label: "Rload", properties: { value: "1k" }, type: "set_node_properties" })],
        }),
      }),
    );
  });
});
