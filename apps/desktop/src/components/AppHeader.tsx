import { type ReactNode } from "react";
import { CircuitBoard, Plus, Save, ShieldCheck } from "lucide-react";

import { EngineProject } from "../engine";

export interface AppHeaderProps {
  project: EngineProject | null;
  advanced: boolean;
  busy: boolean;
  onAdvancedToggle: (next: boolean) => void;
  onCreateClick: () => void;
  onValidate: () => void;
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
        <button aria-label="Save project" className="icon-button" disabled={!props.project} title="Save project" type="button">
          <Save size={17} />
        </button>
        <button className="command-button" disabled={!props.project || props.busy} onClick={props.onValidate} type="button">
          <ShieldCheck size={16} />Validate
        </button>
      </div>
    </header>
  );
}
