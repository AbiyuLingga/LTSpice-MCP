import { useState } from "react";

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
type SchematicDocument = { gridSize: number; nodes: SchematicNode[]; schemaVersion: "1.0"; wires: unknown[] };

const LED_DEMO_ROM = [0x1002, 0xC0F0, 0x1003, 0xC0F1, 0x1001, 0xC0F2, 0xC0F4, 0xF000];
const INITIAL_SCHEMATIC: SchematicDocument = { gridSize: 16, nodes: [], schemaVersion: "1.0", wires: [] };

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
  const [selectedComponent, setSelectedComponent] = useState<SchematicNodeKind | null>(null);
  const [ledPixels, setLedPixels] = useState<boolean[] | null>(null);
  const [ledFrameCount, setLedFrameCount] = useState(0);
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
      id: nextNodeId(selectedComponent, schematic.nodes.length),
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
        schematicNodes={schematic.nodes}
        selectedComponent={selectedComponent}
        surface={surface}
        onAdvancedToggle={setAdvanced}
        onBottomTabChange={setBottomTab}
        onCreateClick={() => setShowDialog(true)}
        onMoveComponent={moveComponent}
        onPlaceComponent={placeComponent}
        onRunLedDemo={runLedDemo}
        onSelectComponent={setSelectedComponent}
        onSurfaceChange={setSurface}
        onValidate={validateProject}
      />
      {showDialog ? <ProjectDialog busy={busy} error={error} onClose={() => setShowDialog(false)} onCreate={createProject} /> : null}
    </>
  );
}

export { WorkspaceSurface };
