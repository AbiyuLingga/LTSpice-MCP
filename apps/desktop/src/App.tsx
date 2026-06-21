import { useEffect, useState } from "react";

import { desktopBridge, EngineBridge, EngineProject } from "./engine";
import { ProjectDialog } from "./components/ProjectDialog";
import {
  type BottomTab,
  type SchematicNode,
  type SchematicNodeKind,
  type Surface,
  WorkspaceSurface,
} from "./components/WorkspaceSurface";
import { WorkspaceShell } from "./components/WorkspaceShell";
import { nextNodeId } from "./components/componentRegistry";

type AppProps = { bridge?: EngineBridge };
type LedEmulation = { led?: { frames: Array<{ pixels: boolean[] }> }; status: string };
type SchematicDocument = {
  gridSize: number;
  netLabels: unknown[];
  schemaVersion: "2.0";
  symbols: SchematicNode[];
  wires: unknown[];
};

const LED_DEMO_ROM = [0x1002, 0xC0F0, 0x1003, 0xC0F1, 0x1001, 0xC0F2, 0xC0F4, 0xF000];
const INITIAL_SCHEMATIC: SchematicDocument = {
  gridSize: 16,
  netLabels: [],
  schemaVersion: "2.0",
  symbols: [],
  wires: [],
};

export function App({ bridge = desktopBridge }: AppProps) {
  const [project, setProject] = useState<EngineProject | null>(null);
  const [surface, setSurface] = useState<Surface>("schematic");
  const [bottomTab, setBottomTab] = useState<BottomTab>("problems");
  const [advanced, setAdvanced] = useState(false);
  const [dialogMode, setDialogMode] = useState<"create" | "open" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobMessage, setJobMessage] = useState("No jobs running");
  const [schematic, setSchematic] = useState<SchematicDocument>(INITIAL_SCHEMATIC);
  const [selectedComponent, setSelectedComponent] = useState<SchematicNodeKind | null>(null);
  const [ledPixels, setLedPixels] = useState<boolean[] | null>(null);
  const [ledFrameCount, setLedFrameCount] = useState(0);

  async function loadProject(nextProject: EngineProject) {
    const loaded = await bridge.request<{ document: SchematicDocument }>("design.get", {
      document: "schematic",
      projectId: nextProject.projectId,
    });
    setProject(nextProject);
    setSchematic(loaded.document);
  }

  useEffect(() => {
    if (!project) return;
    async function refresh() {
      const result = await bridge.request<{ changed: boolean; project: EngineProject }>("project.refresh", {
        knownRevision: project?.revision,
        projectId: project?.projectId,
      });
      if (result.changed) await loadProject(result.project);
    }
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [bridge, project]);

  async function createProject(input: { displayName: string; projectId: string }) {
    setBusy(true);
    setError(null);
    try {
      const created = await bridge.request<EngineProject>("project.create", input);
      await loadProject(created);
      setDialogMode(null);
      setBottomTab("jobs");
      setJobMessage("Project created and schematic loaded");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create project");
    } finally {
      setBusy(false);
    }
  }

  async function openProject(projectId: string) {
    setBusy(true);
    setError(null);
    try {
      const opened = await bridge.request<EngineProject>("project.open", { projectId });
      await loadProject(opened);
      setDialogMode(null);
      setJobMessage("Project opened");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to open project");
    } finally {
      setBusy(false);
    }
  }

  async function changeHistory(method: "design.undo" | "design.redo") {
    if (!project) return;
    setBusy(true);
    try {
      const result = await bridge.request<{ changed: boolean; revision?: number }>(method, {
        projectId: project.projectId,
      });
      if (result.changed && result.revision !== undefined) {
        await loadProject({ ...project, revision: result.revision });
      }
      setJobMessage(result.changed ? (method.endsWith("undo") ? "Undo complete" : "Redo complete") : "Nothing to change");
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "History operation failed");
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
        projectId: project.projectId,
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
      id: nextNodeId(selectedComponent, schematic.symbols.length),
      kind: selectedComponent,
      rotation: 0,
      x,
      y,
    };
    setJobMessage(`Placing ${selectedComponent}…`);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [{
            document: "schematic",
            kind: selectedComponent,
            rotation: 0,
            symbolId: nextNode.id,
            type: "place_node",
            x,
            y,
          }],
          schemaVersion: "2.0",
        },
        projectId: project.projectId,
      });
      setSchematic({ ...schematic, symbols: [...schematic.symbols, nextNode] });
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
      symbols: schematic.symbols.map((node) => node.id === componentId ? { ...node, x, y } : node),
    };
    if (nextSchematic.symbols.every((node, index) => node === schematic.symbols[index])) return;
    setSchematic(nextSchematic);
    setJobMessage(`Moving ${componentId}…`);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [{ document: "schematic", symbolId: componentId, type: "move_node", x, y }],
          schemaVersion: "2.0",
        },
        projectId: project.projectId,
      });
      setProject({ ...project, revision: result.revision });
      setJobMessage(`${componentId} moved`);
    } catch (caught) {
      setSchematic(previousSchematic);
      setJobMessage(caught instanceof Error ? caught.message : "Unable to move component");
    }
  }

  return (
    <>
      <WorkspaceShell
        advanced={advanced}
        bottomTab={bottomTab}
        busy={busy}
        error={error}
        jobMessage={jobMessage}
        ledFrameCount={ledFrameCount}
        ledPixels={ledPixels}
        project={project}
        schematicNodes={schematic.symbols}
        selectedComponent={selectedComponent}
        surface={surface}
        onAdvancedToggle={setAdvanced}
        onBottomTabChange={setBottomTab}
        onCreateClick={() => setDialogMode("create")}
        onMoveComponent={moveComponent}
        onOpenClick={() => setDialogMode("open")}
        onPlaceComponent={placeComponent}
        onRedo={() => changeHistory("design.redo")}
        onRunLedDemo={runLedDemo}
        onSelectComponent={setSelectedComponent}
        onSurfaceChange={setSurface}
        onUndo={() => changeHistory("design.undo")}
        onValidate={validateProject}
      />
      {dialogMode ? (
        <ProjectDialog
          busy={busy}
          error={error}
          mode={dialogMode}
          onClose={() => setDialogMode(null)}
          onCreate={createProject}
          onOpen={openProject}
        />
      ) : null}
    </>
  );
}

export { WorkspaceSurface };
