type SchematicSymbolProps = {
  kind: string;
};

function TerminalLeads() {
  return <><path d="M4 36h16M100 36h16" /></>;
}

function ResistorSymbol() {
  return <>
    <TerminalLeads />
    <polyline points="20,36 28,22 38,50 48,22 58,50 68,22 78,50 88,22 100,36" />
  </>;
}

function CapacitorSymbol() {
  return <>
    <TerminalLeads />
    <path d="M44 16v40M76 16v40" />
    <path d="M20 36h24M76 36h24" />
  </>;
}

function InductorSymbol() {
  return <>
    <TerminalLeads />
    <path d="M20 36h12" />
    <path d="M32 36c0-18 16-18 16 0s16 18 16 0s16-18 16 0s16 18 16 0" />
    <path d="M96 36h4" />
  </>;
}

function DiodeSymbol() {
  return <>
    <TerminalLeads />
    <path d="M20 36h24" />
    <polygon points="44,16 44,56 76,36" />
    <path d="M82 16v40M76 36h24" />
  </>;
}

function OpAmpSymbol() {
  return <>
    <TerminalLeads />
    <path d="M20 24h24M20 48h24" />
    <polygon points="44,12 44,60 92,36" />
    <path d="M92 36h16M28 24h8M32 20v8M28 48h8" />
    <path d="M60 12V0M60 60v12" />
  </>;
}

function VoltageSourceSymbol() {
  return <>
    <TerminalLeads />
    <path d="M46 18v36M62 26v20" />
    <path d="M20 36h26M62 36h38" />
    <path d="M38 22h10M43 17v10M68 50h10" />
  </>;
}

function GroundSymbol() {
  return <>
    <path d="M60 8v28M36 36h48M44 46h32M52 56h16" />
  </>;
}

function CounterSymbol() {
  return <>
    <path d="M4 24h22M4 48h22M94 24h22M94 48h22" />
    <rect height="48" width="68" x="26" y="12" />
    <path d="M26 24h8M26 48h8M86 24h8M86 48h8" />
    <text x="60" y="40">CTR</text>
  </>;
}

function LedMatrixSymbol() {
  return <>
    <path d="M4 24h16M4 48h16M100 24h16M100 48h16" />
    <rect height="48" width="80" x="20" y="12" />
    {Array.from({ length: 12 }, (_, index) => {
      const column = index % 4;
      const row = Math.floor(index / 4);
      return <circle cx={38 + column * 15} cy={24 + row * 14} key={index} r="3" />;
    })}
  </>;
}

export function symbolLabel(kind: string): string {
  const labels: Record<string, string> = {
    capacitor: "Capacitor",
    counter: "Counter",
    diode: "Diode",
    inductor: "Inductor",
    led_matrix: "LED matrix",
    opamp: "Op amp",
    resistor: "Resistor",
    voltage_source: "Voltage source",
    gnd: "Ground",
  };
  return labels[kind] ?? kind;
}

export function SchematicSymbol({ kind }: SchematicSymbolProps) {
  let content = <CounterSymbol />;
  if (kind === "resistor") content = <ResistorSymbol />;
  if (kind === "capacitor") content = <CapacitorSymbol />;
  if (kind === "inductor") content = <InductorSymbol />;
  if (kind === "diode") content = <DiodeSymbol />;
  if (kind === "opamp") content = <OpAmpSymbol />;
  if (kind === "voltage_source") content = <VoltageSourceSymbol />;
  if (kind === "gnd") content = <GroundSymbol />;
  if (kind === "led_matrix") content = <LedMatrixSymbol />;

  return (
    <svg aria-hidden="true" className="schematic-symbol" data-testid={`symbol-${kind}`} viewBox="0 0 120 72">
      {content}
    </svg>
  );
}
