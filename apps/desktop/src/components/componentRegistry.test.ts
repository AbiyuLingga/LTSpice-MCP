import { describe, expect, it } from "vitest";

import { COMPONENT_REGISTRY, componentDescriptor, nextNodeId, pinPosition, snap } from "./componentRegistry";

describe("componentRegistry", () => {
  it("returns a known descriptor for every kind", () => {
    for (const kind of Object.keys(COMPONENT_REGISTRY)) {
      const desc = componentDescriptor(kind as keyof typeof COMPONENT_REGISTRY);
      expect(desc.pins.length).toBeGreaterThan(0);
    }
  });

  it("rejects unknown kinds", () => {
    expect(() => componentDescriptor("rocket" as never)).toThrow();
  });

  it("snap floors to the grid (matches the existing drag handler)", () => {
    expect(snap(0, 16)).toBe(0);
    expect(snap(7, 16)).toBe(0);
    expect(snap(15, 16)).toBe(0);
    expect(snap(16, 16)).toBe(16);
    expect(snap(23, 16)).toBe(16);
    expect(snap(31, 16)).toBe(16);
    expect(snap(32, 16)).toBe(32);
    expect(snap(-3, 16)).toBe(0);
  });

  it("nextNodeId increments from the count", () => {
    expect(nextNodeId("resistor", 0)).toBe("R1");
    expect(nextNodeId("resistor", 4)).toBe("R5");
    expect(nextNodeId("inductor", 0)).toBe("L1");
  });

  it("rotates stable pin positions around the symbol center", () => {
    expect(pinPosition({ id: "R1", kind: "resistor", rotation: 0, x: 100, y: 100 }, "p1")).toEqual([44, 100]);
    expect(pinPosition({ id: "R1", kind: "resistor", rotation: 90, x: 100, y: 100 }, "p1")).toEqual([100, 44]);
    expect(pinPosition({ id: "V1", kind: "voltage_source", rotation: 0, x: 100, y: 100 }, "p1")).toEqual([44, 100]);
    expect(pinPosition({ id: "V1", kind: "voltage_source", rotation: 0, x: 100, y: 100 }, "p2")).toEqual([156, 100]);
  });
});
