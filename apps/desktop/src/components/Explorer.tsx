import { type ReactNode } from "react";
import { CircuitBoard, FolderTree } from "lucide-react";

import { EngineProject } from "../engine";
import { ComponentLibrary } from "./ComponentLibrary";
import { type SchematicNodeKind } from "./componentRegistry";

export interface ExplorerProps {
  project: EngineProject | null;
  selectedComponent?: SchematicNodeKind | null;
  onSelectComponent?: (next: SchematicNodeKind | null) => void;
}

export function Explorer(props: ExplorerProps): ReactNode {
  return (
    <aside className="left-panel panel" aria-label="Project and components">
      <section>
        <header className="panel-heading">
          <FolderTree size={15} />
          <h2>Project</h2>
        </header>
        {props.project ? (
          <ul className="project-tree">
            <li>
              <span>design</span>
              <ul>
                <li>analog</li>
                <li>schematic</li>
                <li>digital</li>
                <li>system</li>
              </ul>
            </li>
            <li>firmware</li>
            <li>verification</li>
            <li>runs</li>
          </ul>
        ) : (
          <p className="muted">Create a local project to begin.</p>
        )}
      </section>
      <section>
        <header className="panel-heading">
          <CircuitBoard size={15} />
          <h2>Components</h2>
        </header>
        {props.onSelectComponent ? (
          <ComponentLibrary
            disabled={!props.project}
            selected={props.selectedComponent ?? null}
            onSelect={props.onSelectComponent}
          />
        ) : (
          <p className="muted" data-testid="library-missing">Create a project to enable the component library.</p>
        )}
      </section>
    </aside>
  );
}
