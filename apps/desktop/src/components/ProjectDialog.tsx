import { useMemo, useState } from "react";
import { X } from "lucide-react";

type ProjectDialogProps = {
  busy: boolean;
  error: string | null;
  mode: "create" | "open";
  onClose(): void;
  onCreate(input: { displayName: string; projectId: string }): void;
  onOpen(projectId: string): void;
};

function toProjectId(displayName: string): string {
  const normalized = displayName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 63);
  return /^[a-z]/.test(normalized) ? normalized : `project_${normalized || "design"}`;
}

export function ProjectDialog({ busy, error, mode, onClose, onCreate, onOpen }: ProjectDialogProps) {
  const [name, setName] = useState("");
  const projectId = useMemo(() => toProjectId(name), [name]);
  const isCreate = mode === "create";

  return (
    <div className="dialog-backdrop" role="presentation">
      <section aria-labelledby="project-dialog-title" aria-modal="true" className="project-dialog" role="dialog">
        <header className="dialog-header">
          <h2 id="project-dialog-title">{isCreate ? "New local project" : "Open local project"}</h2>
          <button aria-label="Close project dialog" className="icon-button" onClick={onClose} title="Close">
            <X size={17} />
          </button>
        </header>
        <form
          className="dialog-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (isCreate) onCreate({ displayName: name.trim(), projectId });
            else onOpen(name.trim());
          }}
        >
          <label>
            <span>{isCreate ? "Project name" : "Project ID"}</span>
            <input
              aria-label={isCreate ? "Project name" : "Project ID"}
              autoFocus
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
              placeholder={isCreate ? "Analog Lab" : "analog_lab"}
              required
              value={name}
            />
          </label>
          {isCreate ? <label>
            <span>Project ID</span>
            <input aria-label="Project ID" disabled readOnly value={projectId} />
          </label> : null}
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <footer className="dialog-actions">
            <button className="text-button" disabled={busy} onClick={onClose} type="button">Cancel</button>
            <button className="primary-button" disabled={busy || !name.trim()} type="submit">{isCreate ? "Create" : "Open"}</button>
          </footer>
        </form>
      </section>
    </div>
  );
}
