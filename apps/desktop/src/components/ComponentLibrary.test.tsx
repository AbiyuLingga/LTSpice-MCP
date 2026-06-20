import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ComponentLibrary } from "./ComponentLibrary";
import { COMPONENT_REGISTRY } from "./componentRegistry";

describe("ComponentLibrary", () => {
  it("renders a button per registered kind", () => {
    render(<ComponentLibrary disabled={false} selected={null} onSelect={() => {}} />);
    for (const desc of Object.values(COMPONENT_REGISTRY)) {
      expect(screen.getByRole("button", { name: `Place ${desc.label}` })).toBeTruthy();
    }
  });
});
