import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Activity, Braces, Cable, CircuitBoard, Grid2X2, MousePointer2, RotateCw, Trash2 } from "lucide-react";

import { SchematicSymbol, symbolLabel } from "./SchematicSymbol";
import { componentDescriptor, pinPosition, type SchematicNode, type SchematicNodeKind, type SchematicPinConnection, type SchematicWire } from "./componentRegistry";

export type Surface = "schematic" | "hdl" | "waveform" | "led";
export type BottomTab = "problems" | "jobs" | "console";
export type { SchematicNode, SchematicNodeKind };

type WorkspaceSurfaceProps = {
  activeSurface: Surface;
  digitalSource: string;
  ledFrameCount: number;
  ledPixels: boolean[] | null;
  waveformSignals: Array<{ name: string; points: Array<[number, number]> }>;
  measurements: Array<{ name: string; value: number }>;
  onRunLedDemo(): void;
  onRunSimulation(domain: "analog" | "digital"): void;
  onRunSynthesis(): void;
  onAddWire(points: Array<[number, number]>, connections: SchematicPinConnection[]): void;
  onDeleteSelection(ids: string[]): void;
  onDeleteWire(id: string): void;
  onDigitalTemplate(kind: "counter" | "fsm" | "pwm"): void;
  onExitPlacement(): void;
  onPlaceComponent(x: number, y: number): void;
  onMoveComponent(id: string, x: number, y: number): void;
  onRotateSelection(ids: string[]): void;
  onSelectionChange(ids: string[]): void;
  schematicNodes: SchematicNode[];
  schematicWires: SchematicWire[];
  selectedComponent: SchematicNodeKind | null;
  selectedIds: string[];
};

type DragState = {
  id: string;
  offsetX: number;
  offsetY: number;
  pointerId: number;
  x: number;
  y: number;
};

type WireEndpoint = SchematicPinConnection & { x: number; y: number };

const GRID_SIZE = 16;

export function WorkspaceSurface({ activeSurface, digitalSource, ledFrameCount, ledPixels, measurements, onAddWire, onDeleteSelection, onDeleteWire, onDigitalTemplate, onExitPlacement, onMoveComponent, onPlaceComponent, onRotateSelection, onRunLedDemo, onRunSimulation, onRunSynthesis, onSelectionChange, schematicNodes, schematicWires, selectedComponent, selectedIds, waveformSignals }: WorkspaceSurfaceProps) {
  const gridRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [dragging, setDragging] = useState<DragState | null>(null);
  const [tool, setTool] = useState<"select" | "wire">("select");
  const [wireStart, setWireStart] = useState<WireEndpoint | null>(null);
  const [selectedWireId, setSelectedWireId] = useState<string | null>(null);
  const overlapIds = new Set(
    schematicNodes
      .filter((node, index) => schematicNodes.some((other, otherIndex) => otherIndex !== index && other.x === node.x && other.y === node.y))
      .map((node) => node.id),
  );

  function setDragState(next: DragState | null) {
    dragRef.current = next;
    setDragging(next);
  }

  function snapCoordinate(value: number, maximum: number): number {
    return Math.max(0, Math.min(maximum, Math.floor(value / GRID_SIZE) * GRID_SIZE));
  }

  function pointerPosition(clientX: number, clientY: number, offsetX = 0, offsetY = 0) {
    const rect = gridRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: snapCoordinate(clientX - rect.left - offsetX, Math.floor(rect.width / GRID_SIZE) * GRID_SIZE),
      y: snapCoordinate(clientY - rect.top - offsetY, Math.floor(rect.height / GRID_SIZE) * GRID_SIZE),
    };
  }

  function beginDrag(event: PointerEvent<HTMLButtonElement>, node: SchematicNode) {
    event.preventDefault();
    event.stopPropagation();
    const rect = gridRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragState({
      id: node.id,
      offsetX: event.clientX - rect.left - node.x,
      offsetY: event.clientY - rect.top - node.y,
      pointerId: event.pointerId,
      x: node.x,
      y: node.y,
    });
  }

  function continueDrag(event: PointerEvent<HTMLButtonElement>) {
    const current = dragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const position = pointerPosition(event.clientX, event.clientY, current.offsetX, current.offsetY);
    setDragState({ ...current, ...position });
  }

  function finishDrag(event: PointerEvent<HTMLButtonElement>) {
    const current = dragRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragState(null);
    const node = schematicNodes.find((candidate) => candidate.id === current.id);
    if (node && (node.x !== current.x || node.y !== current.y)) {
      onMoveComponent(current.id, current.x, current.y);
    }
  }

  function moveWithKeyboard(event: KeyboardEvent<HTMLButtonElement>, node: SchematicNode) {
    const deltaByKey: Record<string, { x: number; y: number }> = {
      ArrowDown: { x: 0, y: GRID_SIZE },
      ArrowLeft: { x: -GRID_SIZE, y: 0 },
      ArrowRight: { x: GRID_SIZE, y: 0 },
      ArrowUp: { x: 0, y: -GRID_SIZE },
    };
    const delta = deltaByKey[event.key];
    if (!delta) return;
    event.preventDefault();
    onMoveComponent(node.id, Math.max(0, node.x + delta.x), Math.max(0, node.y + delta.y));
  }

  function connectPin(endpoint: WireEndpoint) {
    if (!wireStart) {
      setWireStart(endpoint);
      return;
    }
    if (wireStart.symbolId === endpoint.symbolId && wireStart.pin === endpoint.pin) {
      setWireStart(null);
      return;
    }
    onAddWire(
      [[wireStart.x, wireStart.y], [endpoint.x, wireStart.y], [endpoint.x, endpoint.y]],
      [
        { pin: wireStart.pin, symbolId: wireStart.symbolId },
        { pin: endpoint.pin, symbolId: endpoint.symbolId },
      ],
    );
    setWireStart(null);
  }
  if (activeSurface === "hdl") {
    return (
      <section className="code-surface" aria-label="HDL editor">
        <header className="surface-header"><Braces size={16} /><h1>HDL</h1><span>{digitalSource ? "Generated from Digital IR 2.0" : "Choose a safe template"}</span><button className="surface-run" onClick={() => onDigitalTemplate("counter")}>Counter</button><button className="surface-run" onClick={() => onDigitalTemplate("fsm")}>FSM</button><button className="surface-run" onClick={() => onDigitalTemplate("pwm")}>PWM</button><button className="surface-run" onClick={() => onRunSimulation("digital")}>Simulate</button><button className="surface-run" onClick={onRunSynthesis}>Synthesize</button></header>
        {digitalSource ? <pre className="hdl-source">{digitalSource}</pre> : <div className="surface-empty">No digital design is loaded.</div>}
      </section>
    );
  }
  if (activeSurface === "waveform") {
    return (
      <section className="waveform-surface" aria-label="Waveform viewer">
        <header className="surface-header"><Activity size={16} /><h1>Waveform</h1><span>{waveformSignals.length ? `${waveformSignals.length} signals` : "Run a simulation to populate signals"}</span><button className="surface-run" onClick={() => onRunSimulation("analog")}>Run analog simulation</button></header>
        {waveformSignals.length ? (
          <div className="waveform-content">
            <div className="waveform-traces">
              {waveformSignals.map((signal) => <WaveformTrace key={signal.name} signal={signal} />)}
            </div>
            {measurements.length ? <table className="measurement-table"><thead><tr><th>Measurement</th><th>Value</th></tr></thead><tbody>{measurements.map((item) => <tr key={item.name}><td>{item.name}</td><td>{item.value}</td></tr>)}</tbody></table> : null}
          </div>
        ) : <div className="surface-empty">No waveform data is available.</div>}
      </section>
    );
  }
  if (activeSurface === "led") {
    return (
      <section className="led-surface" aria-label="LED matrix simulator">
        <header className="surface-header"><Grid2X2 size={16} /><h1>LED matrix</h1><span>{ledFrameCount ? `${ledFrameCount} frame rendered` : "8 × 16 framebuffer preview"}</span><button className="surface-run" onClick={onRunLedDemo}>Run LED demo</button></header>
        <div aria-label="8 by 16 LED matrix" className="led-matrix" role="img">
          {Array.from({ length: 128 }, (_, index) => (
            <span className={ledPixels?.[index] ?? ((index + Math.floor(index / 8)) % 11 === 0) ? "led-on" : "led-off"} key={index} />
          ))}
        </div>
      </section>
    );
  }
  return (
    <section className="schematic-surface" aria-label="Schematic editor work area">
      <header className="surface-header">
        <CircuitBoard size={16} /><h1>Schematic</h1>
        <span>{schematicNodes.length} components · {overlapIds.size} DRC issues</span>
        <div aria-label="Schematic tools" className="surface-tools" role="toolbar">
          <button aria-label="Select tool" aria-pressed={tool === "select"} onClick={() => { setTool("select"); setWireStart(null); onExitPlacement(); }} title="Select" type="button"><MousePointer2 size={15} /></button>
          <button aria-label="Wire tool" aria-pressed={tool === "wire"} onClick={() => { setTool("wire"); setWireStart(null); onExitPlacement(); }} title="Wire" type="button"><Cable size={15} /></button>
          <button aria-label="Rotate selection" disabled={!selectedIds.length} onClick={() => onRotateSelection(selectedIds)} title="Rotate" type="button"><RotateCw size={15} /></button>
          <button
            aria-label="Delete selection"
            disabled={!selectedIds.length && !selectedWireId}
            onClick={() => {
              if (selectedWireId) {
                onDeleteWire(selectedWireId);
                setSelectedWireId(null);
              } else onDeleteSelection(selectedIds);
            }}
            title="Delete"
            type="button"
          ><Trash2 size={15} /></button>
        </div>
      </header>
      <div
        aria-label="Schematic grid"
        className={selectedComponent ? "schematic-grid placement-mode" : "schematic-grid"}
        ref={gridRef}
        onClick={(event) => {
          if (event.target !== event.currentTarget) return;
          const position = pointerPosition(event.clientX, event.clientY);
          if (selectedComponent) {
            onPlaceComponent(position.x, position.y);
            return;
          }
          if (tool === "wire") { setWireStart(null); return; }
          onSelectionChange([]);
          setSelectedWireId(null);
        }}
      >
        <svg aria-label="Schematic wires" className="wire-layer">
          {schematicWires.map((wire) => (
            <polyline
              aria-label={`Wire ${wire.id}`}
              className={selectedWireId === wire.id ? "wire is-selected" : "wire"}
              key={wire.id}
              onClick={(event) => { event.stopPropagation(); setSelectedWireId(wire.id); onSelectionChange([]); }}
              points={wire.points.map(([x, y]) => `${x},${y}`).join(" ")}
              role="button"
              tabIndex={0}
            />
          ))}
          {wireStart ? <circle className="wire-start" cx={wireStart.x} cy={wireStart.y} r={4} /> : null}
        </svg>
        {schematicNodes.map((node) => {
          const preview = dragging?.id === node.id ? dragging : node;
          const label = symbolLabel(node.kind);
          return (
            <button
              aria-label={`${label} ${node.id}`}
              aria-pressed={selectedIds.includes(node.id)}
              className={[
                "schematic-node",
                dragging?.id === node.id ? "is-dragging" : "",
                selectedIds.includes(node.id) ? "is-selected" : "",
                overlapIds.has(node.id) ? "has-drc" : "",
              ].filter(Boolean).join(" ")}
              key={node.id}
              onClick={(event) => {
                event.stopPropagation();
                const next = event.shiftKey
                  ? (selectedIds.includes(node.id) ? selectedIds.filter((id) => id !== node.id) : [...selectedIds, node.id])
                  : [node.id];
                onSelectionChange(next);
                setSelectedWireId(null);
              }}
              onKeyDown={(event) => moveWithKeyboard(event, node)}
              onPointerDown={(event) => beginDrag(event, node)}
              onPointerMove={continueDrag}
              onPointerUp={finishDrag}
              style={{ left: preview.x, top: preview.y, transform: `translate(-50%, -50%) rotate(${node.rotation ?? 0}deg)` }}
              title={`Drag ${label}; use arrow keys to move by one grid unit`}
              type="button"
            >
              <SchematicSymbol kind={node.kind} />
              <small>{node.id}</small>
            </button>
          );
        })}
        {tool === "wire" ? schematicNodes.flatMap((node) =>
          componentDescriptor(node.kind).pins.map((pin) => {
            const [x, y] = pinPosition(node, pin);
            return (
              <button
                aria-label={`Pin ${pin} of ${node.id}`}
                className="pin-handle"
                key={`${node.id}:${pin}`}
                onClick={(event) => { event.stopPropagation(); connectPin({ pin, symbolId: node.id, x, y }); }}
                style={{ left: x, top: y }}
                title={`${node.id}.${pin}`}
                type="button"
              />
            );
          }),
        ) : null}
        {!schematicNodes.length ? <div className="schematic-empty">{selectedComponent ? `Click to place ${selectedComponent}` : "Select a component from the library to place it on the schematic."}</div> : null}
      </div>
    </section>
  );
}

function WaveformTrace({ signal }: { signal: { name: string; points: Array<[number, number]> } }) {
  if (!signal.points.length) return null;
  const times = signal.points.map((point) => point[0]);
  const values = signal.points.map((point) => point[1]);
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const xRange = maxTime - minTime || 1;
  const yRange = maxValue - minValue || 1;
  const points = signal.points.map(([time, value]) => `${((time - minTime) / xRange) * 760 + 20},${90 - ((value - minValue) / yRange) * 70}`).join(" ");
  return (
    <figure className="waveform-trace">
      <figcaption>{signal.name}</figcaption>
      <svg aria-label={`Waveform ${signal.name}`} preserveAspectRatio="none" role="img" viewBox="0 0 800 100">
        <path d="M20 90H780M20 10V90" />
        <polyline points={points} />
      </svg>
    </figure>
  );
}
