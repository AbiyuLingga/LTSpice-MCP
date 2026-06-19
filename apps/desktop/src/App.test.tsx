import { fireEvent, render, screen } from "@testing-library/react";
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

describe("App", () => {
  it("creates a local project through the engine bridge", async () => {
    const user = userEvent.setup();
    const bridge = fakeBridge();
    render(<App bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: "Create project" }));
    await user.type(screen.getByLabelText("Project name"), "Analog Lab");
    await user.click(screen.getByRole("button", { name: "Create" }));

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

    await user.click(screen.getByRole("button", { name: "Create project" }));
    await user.type(screen.getByLabelText("Project name"), "Analog Lab");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await user.click(screen.getByRole("button", { name: "Place Resistor" }));
    fireEvent.click(screen.getByLabelText("Schematic grid"), { clientX: 96, clientY: 144 });

    expect(await screen.findByText("1 components")).toBeVisible();
    expect(bridge.request).toHaveBeenCalledWith(
      "design.applyChanges",
      expect.objectContaining({ projectDir: "/projects/analog_lab" }),
    );
  });
});
