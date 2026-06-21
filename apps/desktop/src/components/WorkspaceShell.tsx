import { type ReactNode } from "react";

import { type BottomTab, type Surface, WorkspaceSurface } from "./WorkspaceSurface";
import { type SchematicNode, type SchematicNodeKind, type SchematicPinConnection, type SchematicWire } from "./componentRegistry";
import { ComponentLibrary } from "./ComponentLibrary";
import { Explorer } from "./Explorer";
import { Inspector } from "./Inspector";
import { AIPanel } from "./AIPanel";
import { BottomPanel } from "./BottomPanel";
import { AppHeader } from "./AppHeader";
import { EngineBridge, EngineProject } from "../engine";

export interface WorkspaceShellProps {
  project: EngineProject | null;
  bridge: EngineBridge;
  surface: Surface;
  bottomTab: BottomTab;
  advanced: boolean;
  busy: boolean;
  error: string | null;
  digitalSource: string;
  jobMessage: string;
  activeJobId: string | null;
  schematicNodes: SchematicNode[];
  schematicWires: SchematicWire[];
  selectedIds: string[];
  selectedComponent: SchematicNodeKind | null;
  ledPixels: boolean[] | null;
  ledFrameCount: number;
  waveformSignals: Array<{ name: string; points: Array<[number, number]> }>;
  measurements: Array<{ name: string; value: number }>;
  onAdvancedToggle: (next: boolean) => void;
  onAiApplied: (project: EngineProject) => void;
  onCreateClick: () => void;
  onConnectCodex: () => void;
  onOpenClick: () => void;
  onRedo: () => void;
  onUndo: () => void;
  onValidate: () => void;
  onToolDoctor: () => void;
  onSurfaceChange: (next: Surface) => void;
  onBottomTabChange: (next: BottomTab) => void;
  onSelectComponent: (next: SchematicNodeKind | null) => void;
  onPlaceComponent: (x: number, y: number) => void;
  onMoveComponent: (id: string, x: number, y: number) => void;
  onAddWire: (points: Array<[number, number]>, connections: SchematicPinConnection[]) => void;
  onDeleteSelection: (ids: string[]) => void;
  onDeleteWire: (id: string) => void;
  onDigitalTemplate: (kind: "counter" | "fsm" | "pwm") => void;
  onExitPlacement: () => void;
  onRotateSelection: (ids: string[]) => void;
  onSelectionChange: (ids: string[]) => void;
  onUpdateNode: (id: string, label: string, value: string) => void;
  onRunLedDemo: () => void;
  onRunSimulation: (domain: "analog" | "digital") => void;
  onRunSynthesis: () => void;
  onCancelJob: () => void;
}

export function WorkspaceShell(props: WorkspaceShellProps): ReactNode {
  return (
    <main className="app-shell">
      <AppHeader
        advanced={props.advanced}
        busy={props.busy}
        project={props.project}
        onAdvancedToggle={props.onAdvancedToggle}
        onCreateClick={props.onCreateClick}
        onConnectCodex={props.onConnectCodex}
        onOpenClick={props.onOpenClick}
        onRedo={props.onRedo}
        onUndo={props.onUndo}
        onValidate={props.onValidate}
        onToolDoctor={props.onToolDoctor}
      />
      <Explorer
        project={props.project}
        selectedComponent={props.selectedComponent}
        onSelectComponent={props.onSelectComponent}
      />
      <section className="workspace" aria-label="Design workspace">
        <SurfaceTabs surface={props.surface} onSurfaceChange={props.onSurfaceChange} />
        <WorkspaceSurface
          activeSurface={props.surface}
          digitalSource={props.digitalSource}
          ledFrameCount={props.ledFrameCount}
          ledPixels={props.ledPixels}
          measurements={props.measurements}
          onAddWire={props.onAddWire}
          onDeleteSelection={props.onDeleteSelection}
          onDeleteWire={props.onDeleteWire}
          onDigitalTemplate={props.onDigitalTemplate}
          onExitPlacement={props.onExitPlacement}
          onMoveComponent={props.onMoveComponent}
          onPlaceComponent={props.onPlaceComponent}
          onRotateSelection={props.onRotateSelection}
          onRunLedDemo={props.onRunLedDemo}
          onRunSimulation={props.onRunSimulation}
          onRunSynthesis={props.onRunSynthesis}
          schematicNodes={props.schematicNodes}
          schematicWires={props.schematicWires}
          selectedComponent={props.selectedComponent}
          selectedIds={props.selectedIds}
          waveformSignals={props.waveformSignals}
          onSelectionChange={props.onSelectionChange}
        />
      </section>
      <Inspector
        advanced={props.advanced}
        bridge={props.bridge}
        onAiApplied={props.onAiApplied}
        onApplyProperties={props.onUpdateNode}
        project={props.project}
        selectedComponent={props.selectedComponent}
        selectedNode={props.schematicNodes.find((node) => node.id === props.selectedIds[0]) ?? null}
      />
      <BottomPanel activeJobId={props.activeJobId} bottomTab={props.bottomTab} jobMessage={props.jobMessage} onBottomTabChange={props.onBottomTabChange} onCancelJob={props.onCancelJob} />
    </main>
  );
}

function SurfaceTabs({ surface, onSurfaceChange }: { surface: Surface; onSurfaceChange: (next: Surface) => void }) {
  const surfaces: Array<{ id: Surface; label: string }> = [
    { id: "schematic", label: "Schematic" },
    { id: "hdl", label: "HDL" },
    { id: "waveform", label: "Waveform" },
    { id: "led", label: "LED" },
  ];
  return (
    <div aria-label="Design views" className="surface-tabs" role="tablist">
      {surfaces.map(({ id, label }) => (
        <button aria-selected={surface === id} key={id} onClick={() => onSurfaceChange(id)} role="tab" type="button">
          {label}
        </button>
      ))}
    </div>
  );
}

export { ComponentLibrary, Explorer, Inspector, AIPanel, BottomPanel, AppHeader };
export type { BottomTab, Surface, SchematicNode, SchematicNodeKind, EngineProject };
