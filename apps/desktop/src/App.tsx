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
import {
  defaultComponentValue,
  isAnalogKind,
  nextNodeId,
  pinPosition,
  type SchematicPinConnection,
  type SchematicWire,
} from "./components/componentRegistry";

type AppProps = { bridge?: EngineBridge };
type LedEmulation = { led?: { frames: Array<{ pixels: boolean[] }> }; status: string };
type EngineJob = {
  errorMessage?: string | null;
  jobId: string;
  result?: {
    measurements?: Array<{ name: string; value: number }>;
    run?: { artifacts?: Record<string, string> };
    status?: string;
  };
  state: string;
};
type WaveformSignal = { name: string; points: Array<[number, number]> };
type WaveformIndex = {
  signals: Array<{ name: string; preview: string; sampleCount: number }>;
};
type DigitalTemplate = "counter" | "fsm" | "pwm";
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

function digitalTemplate(kind: DigitalTemplate): Record<string, unknown> {
  const common = {
    schemaVersion: "2.0",
    clock: { signal: "clk", periodNs: 10 },
    reset: { signal: "rst_n", active: "low" },
    signals: [],
    testGoals: [],
  };
  if (kind === "fsm") return {
    ...common,
    topModule: "fsm_top",
    ports: [
      { name: "clk", direction: "input", width: 1 }, { name: "rst_n", direction: "input", width: 1 },
      { name: "toggle", direction: "input", width: 1 }, { name: "state", direction: "output", width: 1 },
    ],
    instances: [{ id: "fsm0", kind: "fsm", parameters: {} }],
    connections: ["clk", "reset", "toggle", "state"].map((pin, index) => ({ instanceId: "fsm0", pin, signal: ["clk", "rst_n", "toggle", "state"][index] })),
  };
  if (kind === "pwm") return {
    ...common,
    topModule: "pwm_top",
    ports: [
      { name: "clk", direction: "input", width: 1 }, { name: "rst_n", direction: "input", width: 1 },
      { name: "duty", direction: "input", width: 8 }, { name: "pwm_out", direction: "output", width: 1 },
    ],
    instances: [{ id: "pwm0", kind: "pwm", parameters: { width: 8 } }],
    connections: ["clk", "reset", "duty", "out"].map((pin, index) => ({ instanceId: "pwm0", pin, signal: ["clk", "rst_n", "duty", "pwm_out"][index] })),
  };
  return {
    ...common,
    topModule: "counter_top",
    ports: [
      { name: "clk", direction: "input", width: 1 }, { name: "rst_n", direction: "input", width: 1 },
      { name: "q", direction: "output", width: 8 },
    ],
    instances: [{ id: "counter0", kind: "counter", parameters: { width: 8 } }],
    connections: ["clk", "reset", "q"].map((pin, index) => ({ instanceId: "counter0", pin, signal: ["clk", "rst_n", "q"][index] })),
  };
}

function routeWire(wire: SchematicWire, symbols: SchematicNode[]): Array<[number, number]> {
  if (wire.connections?.length !== 2) return wire.points;
  const endpoints = wire.connections.map((connection) => {
    const symbol = symbols.find((item) => item.id === connection.symbolId);
    return symbol ? pinPosition(symbol, connection.pin) : null;
  });
  if (!endpoints[0] || !endpoints[1]) return wire.points;
  return [endpoints[0], [endpoints[1][0], endpoints[0][1]], endpoints[1]];
}

export function App({ bridge = desktopBridge }: AppProps) {
  const [project, setProject] = useState<EngineProject | null>(null);
  const [surface, setSurface] = useState<Surface>("schematic");
  const [bottomTab, setBottomTab] = useState<BottomTab>("problems");
  const [advanced, setAdvanced] = useState(false);
  const [dialogMode, setDialogMode] = useState<"create" | "open" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobMessage, setJobMessage] = useState("No jobs running");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [schematic, setSchematic] = useState<SchematicDocument>(INITIAL_SCHEMATIC);
  const [selectedComponent, setSelectedComponent] = useState<SchematicNodeKind | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [ledPixels, setLedPixels] = useState<boolean[] | null>(null);
  const [ledFrameCount, setLedFrameCount] = useState(0);
  const [waveformSignals, setWaveformSignals] = useState<WaveformSignal[]>([]);
  const [measurements, setMeasurements] = useState<Array<{ name: string; value: number }>>([]);
  const [digitalSource, setDigitalSource] = useState("");

  async function loadProject(nextProject: EngineProject) {
    const [loaded, digital] = await Promise.all([
      bridge.request<{ document: SchematicDocument }>("design.get", {
        document: "schematic",
        projectId: nextProject.projectId,
      }),
      bridge.request<{ document: { design?: { instances?: unknown[] } } }>("design.get", {
        document: "digital",
        projectId: nextProject.projectId,
      }),
    ]);
    setProject(nextProject);
    setSchematic(loaded.document);
    if (digital.document.design?.instances?.length) {
      const rendered = await bridge.request<{ source: string }>("digital.render", {
        projectId: nextProject.projectId,
      });
      setDigitalSource(rendered.source);
    } else {
      setDigitalSource("");
    }
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

  async function runToolDoctor() {
    setBottomTab("jobs");
    setJobMessage("Checking local EDA tools…");
    try {
      const result = await bridge.request<{
        status: string;
        tools: Array<{ available: boolean; toolId: string }>;
      }>("tool.doctor", {});
      const available = result.tools.filter((tool) => tool.available).length;
      setJobMessage(`Tool doctor ${result.status}: ${available}/${result.tools.length} available`);
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Tool doctor failed");
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

  async function runJob(method: "simulation.start" | "synthesis.start", params: Record<string, unknown>) {
    if (!project || activeJobId) return;
    setBottomTab("jobs");
    setJobMessage(method === "synthesis.start" ? "Starting synthesis…" : "Starting simulation…");
    try {
      const started = await bridge.request<EngineJob>(method, { ...params, projectId: project.projectId });
      setActiveJobId(started.jobId);
      let status = started;
      while (!["cancelled", "completed", "failed", "skipped", "timed_out", "unsupported"].includes(status.state)) {
        await new Promise((resolve) => window.setTimeout(resolve, 150));
        status = await bridge.request<EngineJob>("job.status", { jobId: started.jobId });
        setJobMessage(`${status.state}: ${status.jobId}`);
      }
      const outcome = status.result?.status ?? status.state;
      const indexPath = status.result?.run?.artifacts?.waveformIndex;
      if (indexPath) {
        const index = await readJsonArtifact<WaveformIndex>(started.jobId, indexPath);
        const previews = await Promise.all(
          index.signals.slice(0, 8).map(async (signal) => {
            const preview = await readJsonArtifact<{ points: Array<[number, number]> }>(started.jobId, signal.preview);
            return { name: signal.name, points: preview.points };
          }),
        );
        setWaveformSignals(previews);
        setMeasurements(status.result?.measurements ?? []);
        setSurface("waveform");
      }
      setJobMessage(status.errorMessage || `${method === "synthesis.start" ? "Synthesis" : "Simulation"} ${outcome}`);
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Job failed");
    } finally {
      setActiveJobId(null);
    }
  }

  async function readJsonArtifact<T>(jobId: string, artifact: string): Promise<T> {
    const slice = await bridge.request<{ text: string }>("artifact.readSlice", {
      artifact,
      jobId,
      limit: 256 * 1024,
      offset: 0,
    });
    return JSON.parse(slice.text) as T;
  }

  async function cancelJob() {
    if (!activeJobId) return;
    try {
      await bridge.request<EngineJob>("job.cancel", { jobId: activeJobId });
      setJobMessage("Job cancelled");
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Unable to cancel job");
    }
  }

  async function applyDigitalTemplate(kind: DigitalTemplate) {
    if (!project) return;
    setJobMessage(`Creating ${kind} design…`);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [{ design: digitalTemplate(kind), document: "digital", type: "set_digital_design" }],
          schemaVersion: "2.0",
        },
        projectId: project.projectId,
      });
      const rendered = await bridge.request<{ source: string }>("digital.render", { projectId: project.projectId });
      setProject({ ...project, revision: result.revision });
      setDigitalSource(rendered.source);
      setSurface("hdl");
      setJobMessage(`${kind} design ready`);
    } catch (caught) {
      setJobMessage(caught instanceof Error ? caught.message : "Unable to create digital design");
    }
  }

  async function placeComponent(x: number, y: number) {
    if (!project || !selectedComponent) return;
    const nextNode: SchematicNode = {
      id: nextNodeId(
        selectedComponent,
        schematic.symbols.filter((node) => node.kind === selectedComponent).length,
      ),
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
          operations: [
            ...(isAnalogKind(selectedComponent) ? [{
              componentId: nextNode.id,
              document: "analog",
              kind: selectedComponent,
              pins: {},
              type: "add_component",
              value: defaultComponentValue(selectedComponent),
            }] : []),
            {
              document: "schematic",
              kind: selectedComponent,
              properties: { value: defaultComponentValue(selectedComponent) },
              rotation: 0,
              symbolId: nextNode.id,
              type: "place_node",
              x,
              y,
            },
          ],
          schemaVersion: "2.0",
        },
        projectId: project.projectId,
      });
      setSchematic({
        ...schematic,
        symbols: [...schematic.symbols, {
          ...nextNode,
          properties: { value: defaultComponentValue(selectedComponent) },
        }],
      });
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
    nextSchematic.wires = nextSchematic.wires.map((wire) => ({
      ...wire,
      points: routeWire(wire, nextSchematic.symbols),
    }));
    if (nextSchematic.symbols.every((node, index) => node === schematic.symbols[index])) return;
    setSchematic(nextSchematic);
    setJobMessage(`Moving ${componentId}…`);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [
            { document: "schematic", symbolId: componentId, type: "move_node", x, y },
            ...nextSchematic.wires
              .filter((wire) => wire.connections?.some((item) => item.symbolId === componentId))
              .map((wire) => ({
                connections: wire.connections,
                document: "schematic",
                net: wire.net,
                points: wire.points,
                type: "set_wire_route",
                wireId: wire.id,
              })),
          ],
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

  async function addWire(points: Array<[number, number]>, connections: SchematicPinConnection[]) {
    if (!project) return;
    const endpointNodes = connections.map((item) => schematic.symbols.find((node) => node.id === item.symbolId));
    const net = endpointNodes.some((node) => node?.kind === "gnd")
      ? "0"
      : `net_${schematic.wires.length + 1}`;
    const wire: SchematicWire = { connections, id: `wire_${schematic.wires.length + 1}`, net, points };
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: {
          baseRevision: project.revision,
          operations: [
            { connections, document: "schematic", net, points, type: "set_wire_route", wireId: wire.id },
            ...connections.flatMap((connection, index) => {
              const node = endpointNodes[index];
              if (!node || !isAnalogKind(node.kind)) return [];
              return [{
                componentId: connection.symbolId,
                document: "analog",
                net,
                pin: connection.pin,
                type: "connect_pin",
              }];
            }),
          ],
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
    const nextSymbols = schematic.symbols.map((node) => rotations.has(node.id) ? { ...node, rotation: rotations.get(node.id) } : node);
    const nextWires = schematic.wires.map((wire) => ({ ...wire, points: routeWire(wire, nextSymbols) }));
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: [
          ...[...rotations].map(([symbolId, rotation]) => ({ document: "schematic", rotation, symbolId, type: "rotate_node" })),
          ...nextWires
            .filter((wire) => wire.connections?.some((item) => rotations.has(item.symbolId)))
            .map((wire) => ({ connections: wire.connections, document: "schematic", net: wire.net, points: wire.points, type: "set_wire_route", wireId: wire.id })),
        ],
        schemaVersion: "2.0",
      },
      projectId: project.projectId,
    });
    setSchematic({
      ...schematic,
      symbols: nextSymbols,
      wires: nextWires,
    });
    setProject({ ...project, revision: result.revision });
  }

  async function deleteSelection(ids: string[]) {
    if (!project || !ids.length) return;
    const deletedNodes = schematic.symbols.filter((node) => ids.includes(node.id));
    const deletedWires = schematic.wires.filter((wire) => wire.connections?.some((item) => ids.includes(item.symbolId)));
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: [
          ...deletedWires.map((wire) => ({ document: "schematic", type: "remove_wire", wireId: wire.id })),
          ...deletedWires.flatMap((wire) => (wire.connections ?? []).flatMap((connection) => {
            if (ids.includes(connection.symbolId)) return [];
            const node = schematic.symbols.find((item) => item.id === connection.symbolId);
            if (!node || !isAnalogKind(node.kind)) return [];
            return [{ componentId: connection.symbolId, document: "analog", pin: connection.pin, type: "disconnect_pin" }];
          })),
          ...deletedNodes.flatMap((node) => isAnalogKind(node.kind) ? [{ componentId: node.id, document: "analog", type: "remove_component" }] : []),
          ...ids.map((symbolId) => ({ document: "schematic", symbolId, type: "delete_node" })),
        ],
        schemaVersion: "2.0",
      },
      projectId: project.projectId,
    });
    setSchematic({
      ...schematic,
      symbols: schematic.symbols.filter((node) => !ids.includes(node.id)),
      wires: schematic.wires.filter((wire) => !deletedWires.some((deleted) => deleted.id === wire.id)),
    });
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
    const node = schematic.symbols.find((item) => item.id === id);
    const result = await bridge.request<{ revision: number }>("design.applyChanges", {
      changeSet: {
        baseRevision: project.revision,
        operations: [
          ...(node && isAnalogKind(node.kind) ? [{ componentId: id, document: "analog", type: "set_component_value", value }] : []),
          { document: "schematic", label, properties: { value }, symbolId: id, type: "set_node_properties" },
        ],
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
        activeJobId={activeJobId}
        bottomTab={bottomTab}
        bridge={bridge}
        busy={busy}
        error={error}
        digitalSource={digitalSource}
        jobMessage={jobMessage}
        ledFrameCount={ledFrameCount}
        ledPixels={ledPixels}
        measurements={measurements}
        schematicWires={schematic.wires}
        project={project}
        schematicNodes={schematic.symbols}
        selectedComponent={selectedComponent}
        selectedIds={selectedIds}
        surface={surface}
        waveformSignals={waveformSignals}
        onAdvancedToggle={setAdvanced}
        onAiApplied={(nextProject) => { void loadProject(nextProject); }}
        onBottomTabChange={setBottomTab}
        onCancelJob={cancelJob}
        onAddWire={addWire}
        onCreateClick={() => setDialogMode("create")}
        onDeleteSelection={deleteSelection}
        onDeleteWire={deleteWire}
        onDigitalTemplate={applyDigitalTemplate}
        onExitPlacement={() => setSelectedComponent(null)}
        onMoveComponent={moveComponent}
        onOpenClick={() => setDialogMode("open")}
        onPlaceComponent={placeComponent}
        onRedo={() => changeHistory("design.redo")}
        onRotateSelection={rotateSelection}
        onRunLedDemo={runLedDemo}
        onRunSimulation={(domain) => runJob("simulation.start", { domain })}
        onRunSynthesis={() => runJob("synthesis.start", {})}
        onSelectComponent={setSelectedComponent}
        onSelectionChange={(ids) => { setSelectedIds(ids); if (ids.length) setSelectedComponent(null); }}
        onSurfaceChange={setSurface}
        onUndo={() => changeHistory("design.undo")}
        onUpdateNode={updateNode}
        onValidate={validateProject}
        onToolDoctor={runToolDoctor}
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
