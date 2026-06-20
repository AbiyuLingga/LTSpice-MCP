import { useMemo, useState } from "react";
import {
  Bot,
  CircuitBoard,
  FileCode2,
  FolderTree,
  Gauge,
  Grid2X2,
  Plus,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Waves,
} from "lucide-react";

import { ProjectDialog } from "./components/ProjectDialog";
import { type SchematicNode, type Surface, WorkspaceSurface } from "./components/WorkspaceSurface";
import { desktopBridge, type EngineBridge, type EngineProject } from "./engine";

type AppProps = { bridge?: EngineBridge };
type BottomTab = "problems" | "jobs" | "console";
type LedEmulation = { led?: { frames: Array<{ pixels: boolean[] }> }; status: string };
type SchematicDocument = { gridSize: number; nodes: SchematicNode[]; schemaVersion: "1.0"; wires: unknown[] };

const LED_DEMO_ROM = [0x1002, 0xC0F0, 0x1003, 0xC0F1, 0x1001, 0xC0F2, 0xC0F4, 0xF000];
const INITIAL_SCHEMATIC: SchematicDocument = { gridSize: 16, nodes: [], schemaVersion: "1.0", wires: [] };
const COMPONENTS = [
  { kind: "resistor", label: "Resistor" },
  { kind: "capacitor", label: "Capacitor" },
  { kind: "diode", label: "Diode" },
  { kind: "opamp", label: "Op amp" },
  { kind: "counter", label: "Counter" },
  { kind: "led_matrix", label: "LED matrix" },
];

const surfaces: Array<{ id: Surface; icon: typeof CircuitBoard; label: string }> = [
  { id: "schematic", icon: CircuitBoard, label: "Schematic" },
  { id: "hdl", icon: FileCode2, label: "HDL" },
  { id: "waveform", icon: Waves, label: "Waveform" },
  { id: "led", icon: Grid2X2, label: "LED" },
];

export function App({ bridge = desktopBridge }: AppProps) {
  const [project, setProject] = useState<EngineProject | null>(null);
  const [surface, setSurface] = useState<Surface>("schematic");
  const [bottomTab, setBottomTab] = useState<BottomTab>("problems");
  const [advanced, setAdvanced] = useState(false);
  const [showDialog, setShowDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobMessage, setJobMessage] = useState("No jobs running");
  const [schematic, setSchematic] = useState<SchematicDocument>(INITIAL_SCHEMATIC);
  const [selectedComponent, setSelectedComponent] = useState<string | null>(null);
  const [ledPixels, setLedPixels] = useState<boolean[] | null>(null);
  const [ledFrameCount, setLedFrameCount] = useState(0);

  const projectLabel = project?.displayName ?? "No project open";
  const inspectorTitle = advanced ? "Properties & constraints" : "Properties";
  const statusLabel = useMemo(() => project ? `Revision ${project.revision}` : "Local-first", [project]);

  async function createProject(input: { displayName: string; projectId: string }) {
    setBusy(true);
    setError(null);
    try {
      const created = await bridge.request<EngineProject>("project.create", input);
      const loadedSchematic = await bridge.request<{ document: SchematicDocument }>("design.get", {
        document: "schematic",
        projectDir: created.projectDir,
      });
      setProject(created);
      setSchematic(loadedSchematic.document);
      setShowDialog(false);
      setBottomTab("jobs");
      setJobMessage("Project created and schematic loaded");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create project");
    } finally {
      setBusy(false);
    }
  }

  async function validateProject() {
    if (!project) return;
    setBottomTab("jobs");
    setJobMessage("Validating project…");
    try {
      const result = await bridge.request<{ status: string }>("project.validate", {
        projectDir: project.projectDir,
      });
      setJobMessage(result.status === "pass" ? "Project validation passed" : "Project validation finished");
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Project validation failed");
    }
  }

  async function runLedDemo() {
    setBottomTab("jobs");
    setJobMessage("Running Tiny8 LED demo…");
    try {
      const result = await bridge.request<LedEmulation>("digital.emulate", {
        maxCycles: 16,
        renderLed: true,
        rom: LED_DEMO_ROM,
      });
      const frames = result.led?.frames ?? [];
      setLedFrameCount(frames.length);
      setLedPixels(frames.at(-1)?.pixels ?? null);
      setJobMessage(result.status === "halted" ? "Tiny8 LED demo halted cleanly" : `Tiny8 demo ${result.status}`);
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Tiny8 LED demo failed");
    }
  }

  async function placeComponent(x: number, y: number) {
    if (!project || !selectedComponent) return;
    const nextNode: SchematicNode = {
      id: `${selectedComponent}_${schematic.nodes.length + 1}`,
      kind: selectedComponent,
      rotation: 0,
      x,
      y,
    };
    const nextSchematic: SchematicDocument = { ...schematic, nodes: [...schematic.nodes, nextNode] };
    setJobMessage(`Placing ${selectedComponent}…`);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [{ document: "schematic", type: "replace_document", value: nextSchematic }],
          schemaVersion: "1.0",
        },
        projectDir: project.projectDir,
      });
      setSchematic(nextSchematic);
      setProject({ ...project, revision: result.revision });
      setJobMessage(`${nextNode.id} placed`);
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Unable to place component");
    }
  }

  async function moveComponent(componentId: string, x: number, y: number) {
    if (!project) return;
    const previousSchematic = schematic;
    const nextSchematic: SchematicDocument = {
      ...schematic,
      nodes: schematic.nodes.map((node) => node.id === componentId ? { ...node, x, y } : node),
    };
    if (nextSchematic.nodes.every((node, index) => node === schematic.nodes[index])) return;
    setSchematic(nextSchematic);
    setJobMessage(`Moving ${componentId}…`);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [{ document: "schematic", type: "replace_document", value: nextSchematic }],
          schemaVersion: "1.0",
        },
        projectDir: project.projectDir,
      });
      setProject({ ...project, revision: result.revision });
      setJobMessage(`${componentId} moved`);
    } catch (caught) {
      setSchematic(previousSchematic);
      setJobMessage(caught instanceof Error ? caught.message : "Unable to move component");
    }
  }

  return (
    <main className="app-shell">
      <header className="app-bar">
        <div className="brand-mark"><CircuitBoard size={18} /><span>Hardware Design Workbench</span></div>
        <div className="project-identity"><span>{projectLabel}</span><small>{statusLabel}</small></div>
        <div className="app-actions">
          <div aria-label="Workbench mode" className="mode-switch" role="group">
            <button aria-pressed={!advanced} onClick={() => setAdvanced(false)}>Basic</button>
            <button aria-pressed={advanced} onClick={() => setAdvanced(true)}>Advanced</button>
          </div>
          <button aria-label="Create project" className="icon-button" onClick={() => setShowDialog(true)} title="Create project"><Plus size={17} /></button>
          <button aria-label="Save project" className="icon-button" disabled={!project} title="Save project"><Save size={17} /></button>
          <button className="command-button" disabled={!project} onClick={validateProject}><ShieldCheck size={16} />Validate</button>
        </div>
      </header>

      <aside className="left-panel panel" aria-label="Project and components">
        <section>
          <header className="panel-heading"><FolderTree size={15} /><h2>Project</h2></header>
          {project ? (
            <ul className="project-tree">
              <li><span>design</span><ul><li>analog</li><li>schematic</li><li>digital</li><li>system</li></ul></li>
              <li>firmware</li><li>verification</li><li>runs</li>
            </ul>
          ) : <p className="muted">Create a local project to begin.</p>}
        </section>
        <section>
          <header className="panel-heading"><CircuitBoard size={15} /><h2>Components</h2></header>
          <div className="component-list">
            {COMPONENTS.map(({ kind, label }) => (
              <button aria-label={`Place ${label}`} aria-pressed={selectedComponent === kind} disabled={!project} key={kind} onClick={() => setSelectedComponent(kind)} title={`Place ${label}`} type="button"><span className="component-glyph">{label.slice(0, 1)}</span>{label}</button>
            ))}
          </div>
        </section>
      </aside>

      <section className="workspace" aria-label="Design workspace">
        <div aria-label="Design views" className="surface-tabs" role="tablist">
          {surfaces.map(({ id, icon: Icon, label }) => (
            <button aria-selected={surface === id} key={id} onClick={() => setSurface(id)} role="tab">
              <Icon size={15} />{label}
            </button>
          ))}
        </div>
        <WorkspaceSurface activeSurface={surface} ledFrameCount={ledFrameCount} ledPixels={ledPixels} onMoveComponent={moveComponent} onPlaceComponent={placeComponent} onRunLedDemo={runLedDemo} schematicNodes={schematic.nodes} selectedComponent={selectedComponent} />
      </section>

      <aside className="right-panel panel" aria-label="Inspector and AI">
        <section>
          <header className="panel-heading"><Settings2 size={15} /><h2>{inspectorTitle}</h2></header>
          <dl className="inspector-list">
            <div><dt>Selection</dt><dd>None</dd></div>
            <div><dt>Grid</dt><dd>16 units</dd></div>
            {advanced ? <div><dt>Constraints</dt><dd>Not configured</dd></div> : null}
          </dl>
        </section>
        <section className="ai-panel">
          <header className="panel-heading"><Bot size={15} /><h2>AI proposal</h2></header>
          <p className="muted">AI remains off until a provider and a validated change proposal are configured.</p>
          <button className="text-button" disabled><Sparkles size={15} />Generate proposal</button>
        </section>
      </aside>

      <section className="bottom-panel panel" aria-label="Problems jobs and console">
        <div className="bottom-tabs" role="tablist">
          <button aria-selected={bottomTab === "problems"} onClick={() => setBottomTab("problems")} role="tab">Problems <span>0</span></button>
          <button aria-selected={bottomTab === "jobs"} onClick={() => setBottomTab("jobs")} role="tab">Jobs</button>
          <button aria-selected={bottomTab === "console"} onClick={() => setBottomTab("console")} role="tab"><TerminalSquare size={14} />Console</button>
        </div>
        <div aria-live="polite" className="bottom-content">
          {bottomTab === "problems" ? "No validation problems" : null}
          {bottomTab === "jobs" ? <span className="job-line"><Gauge size={15} />{jobMessage}</span> : null}
          {bottomTab === "console" ? <code>Engine bridge ready. Simulator jobs will appear here.</code> : null}
        </div>
      </section>

      {showDialog ? <ProjectDialog busy={busy} error={error} onClose={() => setShowDialog(false)} onCreate={createProject} /> : null}
    </main>
  );
}
