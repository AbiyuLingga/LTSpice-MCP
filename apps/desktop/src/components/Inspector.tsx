import { type ReactNode, useEffect, useState } from "react";
import { Bot, Settings2, Sparkles } from "lucide-react";

import { type SchematicNode, type SchematicNodeKind } from "./componentRegistry";
import { COMPONENT_REGISTRY } from "./componentRegistry";

export interface InspectorProps {
  advanced: boolean;
  selectedComponent: SchematicNodeKind | null;
  selectedNode: SchematicNode | null;
  onApplyProperties(id: string, label: string, value: string): void;
}

export function Inspector(props: InspectorProps): ReactNode {
  const inspectorTitle = props.advanced ? "Properties & constraints" : "Properties";
  const selectedKind = props.selectedNode?.kind ?? props.selectedComponent;
  const selected = selectedKind ? COMPONENT_REGISTRY[selectedKind] : null;
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  useEffect(() => {
    setLabel(props.selectedNode?.label ?? "");
    setValue(String(props.selectedNode?.properties?.value ?? ""));
  }, [props.selectedNode]);
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
        {props.selectedNode ? (
          <form
            className="inspector-form"
            onSubmit={(event) => {
              event.preventDefault();
              props.onApplyProperties(props.selectedNode!.id, label, value);
            }}
          >
            <label>Component label<input aria-label="Component label" onChange={(event) => setLabel(event.target.value)} value={label} /></label>
            <label>Component value<input aria-label="Component value" onChange={(event) => setValue(event.target.value)} value={value} /></label>
            <button className="text-button" type="submit">Apply properties</button>
          </form>
        ) : null}
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
