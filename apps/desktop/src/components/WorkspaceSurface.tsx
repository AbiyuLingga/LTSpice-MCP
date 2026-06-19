import { Activity, Braces, CircuitBoard, Grid2X2 } from "lucide-react";

export type Surface = "schematic" | "hdl" | "waveform" | "led";
export type SchematicNode = {
  id: string;
  kind: string;
  rotation: number;
  x: number;
  y: number;
};

type WorkspaceSurfaceProps = {
  activeSurface: Surface;
  ledFrameCount: number;
  ledPixels: boolean[] | null;
  onRunLedDemo(): void;
  onPlaceComponent(x: number, y: number): void;
  schematicNodes: SchematicNode[];
  selectedComponent: string | null;
};

const hdlLines = [
  "module counter(input clk, input rst, output reg [7:0] led);",
  "  always @(posedge clk) begin",
  "    if (rst) led <= 8'b00000001;",
  "    else     led <= {led[6:0], led[7]};",
  "  end",
  "endmodule",
];

export function WorkspaceSurface({ activeSurface, ledFrameCount, ledPixels, onPlaceComponent, onRunLedDemo, schematicNodes, selectedComponent }: WorkspaceSurfaceProps) {
  if (activeSurface === "hdl") {
    return (
      <section className="code-surface" aria-label="HDL editor">
        <header className="surface-header"><Braces size={16} /><h1>HDL</h1><span>counter.v</span></header>
        <pre>{hdlLines.map((line, index) => <code key={line}><span>{index + 1}</span>{line}{"\n"}</code>)}</pre>
      </section>
    );
  }
  if (activeSurface === "waveform") {
    return (
      <section className="waveform-surface" aria-label="Waveform viewer">
        <header className="surface-header"><Activity size={16} /><h1>Waveform</h1><span>Run a simulation to populate signals</span></header>
        <div className="wave-grid">
          <span>clk</span><div className="signal signal-clock" />
          <span>reset</span><div className="signal signal-reset" />
          <span>led[7:0]</span><div className="signal signal-led" />
        </div>
      </section>
    );
  }
  if (activeSurface === "led") {
    return (
      <section className="led-surface" aria-label="LED matrix simulator">
        <header className="surface-header"><Grid2X2 size={16} /><h1>LED matrix</h1><span>{ledFrameCount ? `${ledFrameCount} frame rendered` : "8 × 16 framebuffer preview"}</span><button className="surface-run" onClick={onRunLedDemo}>Run LED demo</button></header>
        <div aria-label="8 by 16 LED matrix" className="led-matrix" role="img">
          {Array.from({ length: 128 }, (_, index) => (
            <span className={ledPixels?.[index] ?? ((index + Math.floor(index / 8)) % 11 === 0) ? "led-on" : "led-off"} key={index} />
          ))}
        </div>
      </section>
    );
  }
  return (
    <section className="schematic-surface" aria-label="Schematic editor work area">
      <header className="surface-header"><CircuitBoard size={16} /><h1>Schematic</h1><span>{schematicNodes.length} components</span></header>
      <div
        aria-label="Schematic grid"
        className={selectedComponent ? "schematic-grid placement-mode" : "schematic-grid"}
        onClick={(event) => {
          if (!selectedComponent) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const x = Math.max(0, Math.floor((event.clientX - rect.left) / 16) * 16);
          const y = Math.max(0, Math.floor((event.clientY - rect.top) / 16) * 16);
          onPlaceComponent(x, y);
        }}
      >
        {schematicNodes.map((node) => (
          <span className="schematic-node" key={node.id} style={{ left: node.x, top: node.y }}>
            <b>{node.kind.slice(0, 1).toUpperCase()}</b><small>{node.id}</small>
          </span>
        ))}
        {!schematicNodes.length ? <div className="schematic-empty">{selectedComponent ? `Click to place ${selectedComponent}` : "Select a component from the library to place it on the schematic."}</div> : null}
      </div>
    </section>
  );
}
