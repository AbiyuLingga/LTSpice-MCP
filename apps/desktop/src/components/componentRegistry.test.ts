import { describe, expect, it } from "vitest";

import { COMPONENT_REGISTRY, componentDescriptor, nextNodeId, snap } from "./componentRegistry";

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
    expect(nextNodeId("resistor", 0)).toBe("resistor_1");
    expect(nextNodeId("resistor", 4)).toBe("resistor_5");
  });
});
