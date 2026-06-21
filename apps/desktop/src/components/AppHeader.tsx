import { type ReactNode } from "react";
import { CircuitBoard, FolderOpen, Plug, Plus, Redo2, ShieldCheck, Undo2, Wrench } from "lucide-react";

import { EngineProject } from "../engine";

export interface AppHeaderProps {
  project: EngineProject | null;
  advanced: boolean;
  busy: boolean;
  onAdvancedToggle: (next: boolean) => void;
  onCreateClick: () => void;
  onConnectCodex: () => void;
  onOpenClick: () => void;
  onRedo: () => void;
  onUndo: () => void;
  onValidate: () => void;
  onToolDoctor: () => void;
}

export function AppHeader(props: AppHeaderProps): ReactNode {
  const projectLabel = props.project?.displayName ?? "No project open";
  const statusLabel = props.project ? `Revision ${props.project.revision}` : "Local-first";
  return (
    <header className="app-bar">
      <div className="brand-mark">
        <CircuitBoard size={18} />
        <span>Hardware Design Workbench</span>
      </div>
      <div className="project-identity">
        <span>{projectLabel}</span>
        <small>{statusLabel}</small>
      </div>
      <div className="app-actions">
        <div aria-label="Workbench mode" className="mode-switch" role="group">
          <button aria-pressed={!props.advanced} onClick={() => props.onAdvancedToggle(false)} type="button">Basic</button>
          <button aria-pressed={props.advanced} onClick={() => props.onAdvancedToggle(true)} type="button">Advanced</button>
        </div>
        <button aria-label="Create project" className="icon-button" disabled={props.busy} onClick={props.onCreateClick} title="Create project" type="button">
          <Plus size={17} />
        </button>
        <button aria-label="Open project" className="icon-button" disabled={props.busy} onClick={props.onOpenClick} title="Open project" type="button">
          <FolderOpen size={17} />
        </button>
        <button aria-label="Undo" className="icon-button" disabled={!props.project || props.busy} onClick={props.onUndo} title="Undo" type="button">
          <Undo2 size={17} />
        </button>
        <button aria-label="Redo" className="icon-button" disabled={!props.project || props.busy} onClick={props.onRedo} title="Redo" type="button">
          <Redo2 size={17} />
        </button>
        <button className="command-button" disabled={!props.project || props.busy} onClick={props.onValidate} type="button">
          <ShieldCheck size={16} />Validate
        </button>
        <button aria-label="Connect Codex" className="icon-button" disabled={!props.project || props.busy} onClick={props.onConnectCodex} title="Connect Codex" type="button">
          <Plug size={16} />
        </button>
        <button aria-label="Tool doctor" className="icon-button" disabled={props.busy} onClick={props.onToolDoctor} title="Tool doctor" type="button">
          <Wrench size={16} />
        </button>
      </div>
    </header>
  );
}
