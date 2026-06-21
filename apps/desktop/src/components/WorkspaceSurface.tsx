import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Activity, Braces, CircuitBoard, Grid2X2 } from "lucide-react";

import { SchematicSymbol, symbolLabel } from "./SchematicSymbol";
import { type SchematicNode, type SchematicNodeKind } from "./componentRegistry";

export type Surface = "schematic" | "hdl" | "waveform" | "led";
export type BottomTab = "problems" | "jobs" | "console";
export type { SchematicNode, SchematicNodeKind };

type WorkspaceSurfaceProps = {
  activeSurface: Surface;
  ledFrameCount: number;
  ledPixels: boolean[] | null;
  onRunLedDemo(): void;
  onPlaceComponent(x: number, y: number): void;
  onMoveComponent(id: string, x: number, y: number): void;
  schematicNodes: SchematicNode[];
  selectedComponent: SchematicNodeKind | null;
};

type DragState = {
  id: string;
  offsetX: number;
  offsetY: number;
  pointerId: number;
  x: number;
  y: number;
};

const GRID_SIZE = 16;

export function WorkspaceSurface({ activeSurface, ledFrameCount, ledPixels, onMoveComponent, onPlaceComponent, onRunLedDemo, schematicNodes, selectedComponent }: WorkspaceSurfaceProps) {
  const gridRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [dragging, setDragging] = useState<DragState | null>(null);

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
  if (activeSurface === "hdl") {
    return (
      <section className="code-surface" aria-label="HDL editor">
        <header className="surface-header"><Braces size={16} /><h1>HDL</h1><span>No module loaded</span></header>
        <div className="surface-empty">HDL editing is not connected to the desktop engine yet.</div>
      </section>
    );
  }
  if (activeSurface === "waveform") {
    return (
      <section className="waveform-surface" aria-label="Waveform viewer">
        <header className="surface-header"><Activity size={16} /><h1>Waveform</h1><span>Run a simulation to populate signals</span></header>
        <div className="surface-empty">No waveform data is available.</div>
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
      <header className="surface-header"><CircuitBoard size={16} /><h1>Schematic</h1><span>{schematicNodes.length} components</span></header>
      <div
        aria-label="Schematic grid"
        className={selectedComponent ? "schematic-grid placement-mode" : "schematic-grid"}
        ref={gridRef}
        onClick={(event) => {
          if (!selectedComponent || event.target !== event.currentTarget) return;
          const position = pointerPosition(event.clientX, event.clientY);
          onPlaceComponent(position.x, position.y);
        }}
      >
        {schematicNodes.map((node) => {
          const preview = dragging?.id === node.id ? dragging : node;
          const label = symbolLabel(node.kind);
          return (
            <button
              aria-label={`${label} ${node.id}`}
              className={dragging?.id === node.id ? "schematic-node is-dragging" : "schematic-node"}
              key={node.id}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => moveWithKeyboard(event, node)}
              onPointerDown={(event) => beginDrag(event, node)}
              onPointerMove={continueDrag}
              onPointerUp={finishDrag}
              style={{ left: preview.x, top: preview.y }}
              title={`Drag ${label}; use arrow keys to move by one grid unit`}
              type="button"
            >
              <SchematicSymbol kind={node.kind} />
              <small>{node.id}</small>
            </button>
          );
        })}
        {!schematicNodes.length ? <div className="schematic-empty">{selectedComponent ? `Click to place ${selectedComponent}` : "Select a component from the library to place it on the schematic."}</div> : null}
      </div>
    </section>
  );
}
