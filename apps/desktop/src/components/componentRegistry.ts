export type SchematicNodeKind =
  | "resistor"
  | "capacitor"
  | "inductor"
  | "diode"
  | "opamp"
  | "voltage_source"
  | "gnd"
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
  connections?: SchematicPinConnection[];
  points: Array<[number, number]>;
}

export interface SchematicPinConnection {
  pin: string;
  symbolId: string;
}

export interface ComponentDescriptor {
  kind: SchematicNodeKind;
  label: string;
  /** Pin names in canonical order for future electrical connections. */
  pins: string[];
  pinOffsets: Record<string, [number, number]>;
  /** Approximate bounding box in grid units. */
  width: number;
  height: number;
}

export const COMPONENT_REGISTRY: Record<SchematicNodeKind, ComponentDescriptor> = {
  resistor: { kind: "resistor", label: "Resistor", pins: ["p1", "p2"], pinOffsets: { p1: [-56, 0], p2: [56, 0] }, width: 6, height: 1 },
  capacitor: { kind: "capacitor", label: "Capacitor", pins: ["p1", "p2"], pinOffsets: { p1: [-56, 0], p2: [56, 0] }, width: 2, height: 2 },
  inductor: { kind: "inductor", label: "Inductor", pins: ["p1", "p2"], pinOffsets: { p1: [-56, 0], p2: [56, 0] }, width: 5, height: 2 },
  diode: { kind: "diode", label: "Diode", pins: ["a", "k"], pinOffsets: { a: [-56, 0], k: [56, 0] }, width: 3, height: 2 },
  opamp: { kind: "opamp", label: "Op amp", pins: ["in+", "in-", "v+", "v-", "out"], pinOffsets: { "in+": [-40, -12], "in-": [-40, 12], "v+": [0, -36], "v-": [0, 36], out: [48, 0] }, width: 4, height: 4 },
  voltage_source: { kind: "voltage_source", label: "Voltage source", pins: ["p1", "p2"], pinOffsets: { p1: [56, 0], p2: [-56, 0] }, width: 3, height: 2 },
  gnd: { kind: "gnd", label: "Ground", pins: ["0"], pinOffsets: { "0": [0, -28] }, width: 2, height: 2 },
  counter: { kind: "counter", label: "Counter", pins: ["clk", "rst", "q0", "q1", "q2", "q3"], pinOffsets: { clk: [-56, -12], rst: [-56, 12], q0: [56, -24], q1: [56, -8], q2: [56, 8], q3: [56, 24] }, width: 4, height: 2 },
  led_matrix: { kind: "led_matrix", label: "LED matrix", pins: ["row0", "row1", "col0", "col1", "col2"], pinOffsets: { row0: [-56, -16], row1: [-56, 16], col0: [56, -24], col1: [56, 0], col2: [56, 24] }, width: 6, height: 6 },
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
  const prefix: Record<SchematicNodeKind, string> = {
    capacitor: "C",
    counter: "U",
    diode: "D",
    gnd: "GND",
    inductor: "L",
    led_matrix: "LED",
    opamp: "X",
    resistor: "R",
    voltage_source: "V",
  };
  return `${prefix[kind]}${count + 1}`;
}

export function pinPosition(node: SchematicNode, pin: string): [number, number] {
  const [dx, dy] = componentDescriptor(node.kind).pinOffsets[pin];
  const rotation = node.rotation ?? 0;
  if (rotation === 90) return [node.x - dy, node.y + dx];
  if (rotation === 180) return [node.x - dx, node.y - dy];
  if (rotation === 270) return [node.x + dy, node.y - dx];
  return [node.x + dx, node.y + dy];
}

export function isAnalogKind(kind: SchematicNodeKind): boolean {
  return !["counter", "gnd", "led_matrix"].includes(kind);
}

export function defaultComponentValue(kind: SchematicNodeKind): string {
  return {
    capacitor: "100n",
    counter: "",
    diode: "1N4148",
    gnd: "",
    inductor: "10m",
    led_matrix: "",
    opamp: "UniversalOpamp",
    resistor: "1k",
    voltage_source: "5",
  }[kind];
}
