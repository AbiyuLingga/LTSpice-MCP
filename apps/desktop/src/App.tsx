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
import { nextNodeId, type SchematicWire } from "./components/componentRegistry";

type AppProps = { bridge?: EngineBridge };
type LedEmulation = { led?: { frames: Array<{ pixels: boolean[] }> }; status: string };
type SchematicDocument = {
  gridSize: number;
  netLabels: unknown[];
  schemaVersion: "2.0";
  symbols: SchematicNode[];
  wires: SchematicWire[];
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
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
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
      setSelectedIds([nextNode.id]);
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

  async function addWire(points: Array<[number, number]>) {
    if (!project) return;
    const wire: SchematicWire = { id: `wire_${schematic.wires.length + 1}`, points };
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [{ document: "schematic", points, type: "set_wire_route", wireId: wire.id }],
          schemaVersion: "2.0",
        },
        projectId: project.projectId,
      });
      setSchematic({ ...schematic, wires: [...schematic.wires, wire] });
      setProject({ ...project, revision: result.revision });
      setJobMessage(`${wire.id} connected`);
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Unable to add wire");
    }
  }

  async function rotateSelection(ids: string[]) {
    if (!project || !ids.length) return;
    const rotations = new Map(
      schematic.symbols
        .filter((node) => ids.includes(node.id))
        .map((node) => [node.id, (((node.rotation ?? 0) + 90) % 360) as 0 | 90 | 180 | 270]),
    );
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: [...rotations].map(([symbolId, rotation]) => ({ document: "schematic", rotation, symbolId, type: "rotate_node" })),
        schemaVersion: "2.0",
      },
      projectId: project.projectId,
    });
    setSchematic({
      ...schematic,
      symbols: schematic.symbols.map((node) => rotations.has(node.id) ? { ...node, rotation: rotations.get(node.id) } : node),
    });
    setProject({ ...project, revision: result.revision });
  }

  async function deleteSelection(ids: string[]) {
    if (!project || !ids.length) return;
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: ids.map((symbolId) => ({ document: "schematic", symbolId, type: "delete_node" })),
        schemaVersion: "2.0",
      },
      projectId: project.projectId,
    });
    setSchematic({ ...schematic, symbols: schematic.symbols.filter((node) => !ids.includes(node.id)) });
    setProject({ ...project, revision: result.revision });
    setSelectedIds([]);
  }

  async function deleteWire(wireId: string) {
    if (!project) return;
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: [{ document: "schematic", type: "remove_wire", wireId }],
        schemaVersion: "2.0",
      },
      projectId: project.projectId,
    });
    setSchematic({ ...schematic, wires: schematic.wires.filter((wire) => wire.id !== wireId) });
    setProject({ ...project, revision: result.revision });
  }

  async function updateNode(id: string, label: string, value: string) {
    if (!project) return;
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: [{ document: "schematic", label, properties: { value }, symbolId: id, type: "set_node_properties" }],
        schemaVersion: "2.0",
      },
      projectId: project.projectId,
    });
    setSchematic({
      ...schematic,
      symbols: schematic.symbols.map((node) => node.id === id ? { ...node, label, properties: { ...node.properties, value } } : node),
    });
    setProject({ ...project, revision: result.revision });
    setJobMessage(`${id} properties updated`);
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
        schematicWires={schematic.wires}
        project={project}
        schematicNodes={schematic.symbols}
        selectedComponent={selectedComponent}
        selectedIds={selectedIds}
        surface={surface}
        onAdvancedToggle={setAdvanced}
        onBottomTabChange={setBottomTab}
        onAddWire={addWire}
        onCreateClick={() => setDialogMode("create")}
        onDeleteSelection={deleteSelection}
        onDeleteWire={deleteWire}
        onExitPlacement={() => setSelectedComponent(null)}
        onMoveComponent={moveComponent}
        onOpenClick={() => setDialogMode("open")}
        onPlaceComponent={placeComponent}
        onRedo={() => changeHistory("design.redo")}
        onRotateSelection={rotateSelection}
        onRunLedDemo={runLedDemo}
        onSelectComponent={setSelectedComponent}
        onSelectionChange={(ids) => { setSelectedIds(ids); if (ids.length) setSelectedComponent(null); }}
        onSurfaceChange={setSurface}
        onUndo={() => changeHistory("design.undo")}
        onUpdateNode={updateNode}
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
