import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { App } from "./App";
import type { EngineBridge } from "./engine";

function fakeBridge(): EngineBridge {
  const request = vi.fn(async (method: string) => {
    if (method === "project.create") {
      return {
        displayName: "Analog Lab",
        projectDir: "/projects/analog_lab",
        projectId: "analog_lab",
        revision: 0,
        schemaVersion: "1.0",
      };
    }
    if (method === "design.get") {
      return {
        document: { gridSize: 16, nodes: [], schemaVersion: "1.0", wires: [] },
        project: {
          displayName: "Analog Lab",
          projectDir: "/projects/analog_lab",
          projectId: "analog_lab",
          revision: 0,
          schemaVersion: "1.0",
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
      return { changedDocuments: ["schematic"], revision: 1 };
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

    expect(await screen.findByText("1 components")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({ projectDir: "/projects/analog_lab" }),
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
          value: expect.objectContaining({
            nodes: [expect.objectContaining({ id: "resistor_1", x: 176, y: 208 })],
          }),
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
    expect(screen.getByText("1 components")).toBeVisible();
  });
});
