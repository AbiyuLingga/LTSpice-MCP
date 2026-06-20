import { type ReactNode } from "react";
import { Bot, Settings2, Sparkles } from "lucide-react";

import { type SchematicNodeKind } from "./componentRegistry";
import { COMPONENT_REGISTRY } from "./componentRegistry";

export interface InspectorProps {
  advanced: boolean;
  selectedComponent: SchematicNodeKind | null;
}

export function Inspector(props: InspectorProps): ReactNode {
  const inspectorTitle = props.advanced ? "Properties & constraints" : "Properties";
  const selected = props.selectedComponent ? COMPONENT_REGISTRY[props.selectedComponent] : null;
  return (
    <aside className="right-panel panel" aria-label="Inspector and AI">
      <section>
        <header className="panel-heading">
          <Settings2 size={15} />
          <h2>{inspectorTitle}</h2>
        </header>
        <dl className="inspector-list">
          <div>
            <dt>Selection</dt>
            <dd>{selected ? selected.label : "None"}</dd>
          </div>
          {selected ? (
            <div>
              <dt>Pins</dt>
              <dd>{selected.pins.join(", ")}</dd>
            </div>
          ) : null}
          <div>
            <dt>Grid</dt>
            <dd>16 units</dd>
          </div>
          {props.advanced ? (
            <div>
              <dt>Constraints</dt>
              <dd>Not configured</dd>
            </div>
          ) : null}
        </dl>
      </section>
      <AIPanel />
    </aside>
  );
}

function AIPanel(): ReactNode {
  return (
    <section className="ai-panel">
      <header className="panel-heading">
        <Bot size={15} />
        <h2>AI proposal</h2>
      </header>
      <p className="muted">
        AI remains off until a provider and a validated change proposal are configured.
      </p>
      <button className="text-button" disabled type="button">
        <Sparkles size={15} />Generate proposal
      </button>
    </section>
  );
}
