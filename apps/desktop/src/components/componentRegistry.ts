export type SchematicNodeKind =
  | "resistor"
  | "capacitor"
  | "diode"
  | "opamp"
  | "counter"
  | "led_matrix";

export interface SchematicNode {
  id: string;
  kind: SchematicNodeKind;
  x: number;
  y: number;
  rotation?: 0 | 90 | 180 | 270;
  mirror?: boolean;
  label?: string;
  properties?: Record<string, unknown>;
}

export interface SchematicWire {
  id: string;
  net?: string;
  points: Array<[number, number]>;
}

export interface ComponentDescriptor {
  kind: SchematicNodeKind;
  label: string;
  /** Pin names in canonical order for future electrical connections. */
  pins: string[];
  /** Approximate bounding box in grid units. */
  width: number;
  height: number;
}

export const COMPONENT_REGISTRY: Record<SchematicNodeKind, ComponentDescriptor> = {
  resistor: { kind: "resistor", label: "Resistor", pins: ["p1", "p2"], width: 6, height: 1 },
  capacitor: { kind: "capacitor", label: "Capacitor", pins: ["p1", "p2"], width: 2, height: 2 },
  diode: { kind: "diode", label: "Diode", pins: ["a", "k"], width: 3, height: 2 },
  opamp: { kind: "opamp", label: "Op amp", pins: ["in+", "in-", "v+", "v-", "out"], width: 4, height: 4 },
  counter: { kind: "counter", label: "Counter", pins: ["clk", "rst", "q0", "q1", "q2", "q3"], width: 4, height: 2 },
  led_matrix: { kind: "led_matrix", label: "LED matrix", pins: ["row0", "row1", "col0", "col1", "col2"], width: 6, height: 6 },
};

export function componentDescriptor(kind: SchematicNodeKind): ComponentDescriptor {
  const desc = COMPONENT_REGISTRY[kind];
  if (!desc) throw new Error(`unknown component kind: ${kind}`);
  return desc;
}

export function snap(value: number, grid: number): number {
  const snapped = Math.floor(value / grid) * grid;
  return Math.max(0, snapped);
}

export function nextNodeId(kind: SchematicNodeKind, count: number): string {
  return `${kind}_${count + 1}`;
}
