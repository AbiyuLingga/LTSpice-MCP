import { type ReactNode } from "react";

import { type SchematicNodeKind, COMPONENT_REGISTRY } from "./componentRegistry";

export interface ComponentLibraryProps {
  disabled: boolean;
  selected: SchematicNodeKind | null;
  onSelect: (next: SchematicNodeKind | null) => void;
}

export function ComponentLibrary(props: ComponentLibraryProps): ReactNode {
  return (
    <div className="component-list" role="listbox">
      {Object.values(COMPONENT_REGISTRY).map((desc) => (
        <button
          aria-label={`Place ${desc.label}`}
          aria-pressed={props.selected === desc.kind}
          disabled={props.disabled}
          key={desc.kind}
          onClick={() => props.onSelect(props.selected === desc.kind ? null : desc.kind)}
          title={`Place ${desc.label}`}
          type="button"
        >
          <span className="component-glyph">{desc.label.slice(0, 1)}</span>
          {desc.label}
        </button>
      ))}
    </div>
  );
}
