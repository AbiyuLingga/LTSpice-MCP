# LTspice AI Agent — File-Based Live Editing + Mathematical Accuracy Plan

**Project target:** `LTSpice-MCP` / `ltagent`
**Plan scope:** Pilihan 1 — File-based live editing, ditambah sistem perhitungan matematis yang akurat.
**Language:** Indonesian.
**Prepared date:** 2026-06-19.

---

## 0. Executive Summary

Tujuan project ini adalah membuat AI agent yang dapat menerima prompt user, membuat atau mengedit rangkaian LTspice secara bertahap melalui file, menjalankan simulasi, membaca hasil, menghitung nilai komponen secara matematis, melakukan optimasi, dan menyimpan hasil sebagai project atau template yang reusable.

Target pengalaman user:

```text
User:
"buat RC low-pass 1 kHz dengan C 100 nF, simulasikan, dan jelaskan perhitungannya"

AI Agent:
1. Mengekstrak spesifikasi.
2. Menghitung R ideal dari rumus.
3. Memilih nilai standar E24.
4. Membuat circuit graph, IR, .cir, .asc, .plt.
5. Menjalankan LTspice.
6. Membandingkan hasil formula vs simulasi.
7. Membuat calculation.md dan result.json.
8. Memberi laporan final.
```
Untuk rangkaian yang sudah ada:

```text
User:
"buka project amplifier_01, cari kenapa output clipping, lalu perbaiki"

AI Agent:
1. Membuka project.
2. Membaca graph/IR/netlist/result.
3. Membuat snapshot.
4. Mengidentifikasi gain network.
5. Menghitung nilai resistor feedback baru.
6. Mengedit file project.
7. Simulasi ulang.
8. Verifikasi no-clipping.
9. Menyimpan versi final.
```

Prinsip utama:

```text
AI bebas berpikir di level desain,
tetapi eksekusi desain dibatasi oleh operasi aman,
perhitungan dilakukan oleh math engine,
dan kebenaran akhir diverifikasi oleh LTspice/SPICE.
```

Yang **tidak** disarankan:

```text
Prompt -> LLM langsung tulis .asc koordinat mentah -> selesai
```

Yang disarankan:

```text
Prompt -> Requirement -> Circuit Graph -> Math Core -> IR -> .cir/.asc -> LTspice -> Verification -> Optimization -> Report
```

---

## 1. Research Summary

### 1.1 Pola dari Blender/3D AI agent

Beberapa Blender MCP server memakai pola seperti ini:

```text
AI Client -> MCP Server -> Blender Add-on -> Blender Python API
```

Pelajaran utama untuk LTspice:

- AI terasa seperti mengedit software langsung karena memiliki tool kecil seperti inspect, create, move, assign material, render, export, undo/redo.
- AI tidak sekadar menulis file akhir, tetapi melakukan operasi bertahap pada state software.
- Untuk LTspice, pola yang ekuivalen adalah inspect circuit, add component, set value, connect node, generate schematic, run simulation, read measurements, undo.

Sumber:

- https://github.com/djeada/blender-mcp-server
- https://github.com/youichi-uda/blender-mcp-pro

### 1.2 Pola dari LTspice MCP yang sudah ada

Repo `xuio/ltspice-mcp` menunjukkan bahwa MCP untuk LTspice dapat mencakup:

- simulation tools,
- queueing,
- schematic creation/refinement,
- lint/debug tools,
- RAW vector querying,
- `.meas` automation,
- sweep/Monte Carlo studies,
- render schematic/plot/symbol.

Pelajaran untuk project ini:

```text
Buat workflow lengkap:
create/edit -> lint -> clean -> simulate -> measure -> verify -> render -> debug.
```

Sumber:

- https://github.com/xuio/ltspice-mcp
- https://github.com/luc-me/ltspiceMCP
- https://github.com/Cognitohazard/ltspice-mcp

### 1.3 Pola dari KiCad MCP dan schematic manipulation

`mcp-kicad-sch-api` mengekspos tool seperti:

- create schematic,
- add component,
- search component,
- add wire,
- add hierarchical sheet,
- list components,
- get schematic info.

Pelajaran:

```text
AI lebih akurat ketika operasi schematic eksplisit dan tervalidasi,
bukan ketika AI bebas menulis format file schematic mentah.
```

Sumber:

- https://github.com/circuit-synth/mcp-kicad-sch-api
- https://github.com/circuit-synth/kicad-sch-api
- https://github.com/circuit-synth/circuit-synth

### 1.4 Pola dari tscircuit / electronics-as-code

Tscircuit memakai pendekatan hardware-as-code dengan React/TypeScript. Desain dapat dirender menjadi schematic, PCB, 3D view, netlist, KiCad, DSN, JSON, dan format lain. Tscircuit juga memakai Circuit JSON sebagai intermediate representation.

Pelajaran:

- Gunakan source-of-truth yang terstruktur, bukan file visual akhir.
- Buat desain diffable, versionable, dan dapat di-review.
- Gunakan intermediate representation seperti `Circuit Graph` / `Circuit IR` / `Circuit JSON`.

Sumber:

- https://tscircuit.com/
- https://github.com/tscircuit
- https://docs.tscircuit.com/

### 1.5 Pola dari SKiDL

SKiDL memungkinkan rangkaian dibuat dalam Python, melakukan electrical rules checking, menghasilkan netlist, mendukung hierarchical design, reuse, dan smart parametric modules.

Pelajaran:

```text
Untuk membuat AI lebih bebas,
bangun library block/topology yang reusable dan parametrik.
```

Contoh block:

```text
rc_lowpass(cutoff=1kHz, C=100nF)
noninv_opamp(gain=10, supply=12V)
buck_converter(vin=12V, vout=5V, iout=1A)
```

Sumber:

- https://devbisme.github.io/skidl/
- https://github.com/devbisme/skidl

### 1.6 Pola dari LTspice dan Analog Devices design tools

LTspice adalah simulator SPICE, schematic capture, dan waveform viewer untuk simulasi analog. Analog Devices juga menyediakan tool seperti Signal Chain Designer, Analog Filter Wizard, Photodiode Wizard, Precision ADC Driver Tool, dan LTpowerCAD yang menghitung, mengevaluasi trade-off, lalu mengekspor ke LTspice untuk verifikasi.

Pelajaran:

```text
Rumus ideal saja tidak cukup.
Workflow terbaik:
requirement -> formula/design assistant -> real component trade-off -> LTspice verification.
```

Sumber:

- https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html
- https://tools.analog.com/

### 1.7 Pola dari SPICE/ngspice internals

Ngspice menjelaskan flow simulasi tipikal:

```text
read netlist -> pre-process netlist -> create circuit structure -> create/fill matrix -> run simulation -> process data
```

Pelajaran:

- SPICE adalah ground truth numerik untuk rangkaian analog.
- Perhitungan AI harus diverifikasi oleh simulator berbasis matrix/circuit equations.

Sumber:

- https://ngspice.sourceforge.io/docs.html

### 1.8 Pola dari PyLTSpice

PyLTSpice menyediakan kemampuan untuk:

- membaca dan memanipulasi netlist,
- menjalankan simulasi,
- membaca RAW files,
- membaca log information,
- menggunakan editor untuk `.asc` dan netlist.

Pelajaran:

```text
PyLTSpice/spicelib bisa menjadi referensi atau optional backend,
tetapi core project harus tetap independen sampai keputusan lisensi jelas.
```

Sumber:

- https://pyltspice.readthedocs.io/en/latest/
- https://github.com/nunobrum/PyLTSpice
- https://github.com/nunobrum/spicelib

### 1.9 Pola dari riset AI circuit design

#### Schemato

Schemato membahas netlist-to-schematic conversion untuk LTspice `.asc` dan CircuiTikZ. Masalah utama: netlist hasil ML sering tidak mudah dibaca manusia sehingga perlu layout schematic yang interpretable.

Sumber:

- https://arxiv.org/abs/2411.13899

#### CircuitLM

CircuitLM memakai multi-agent pipeline:

1. component identification,
2. canonical pinout retrieval,
3. electronics expert reasoning,
4. JSON schematic synthesis,
5. SVG visualization.

Pelajaran:

```text
LLM harus digrounding dengan component knowledge base dan validator.
```

Sumber:

- https://arxiv.org/abs/2601.04505

#### AutoCkt

AutoCkt memakai reinforcement learning untuk analog circuit sizing. Paper ini menunjukkan automated sizing yang closed-loop dengan simulasi dapat mencapai target spesifikasi pada banyak topologi.

Sumber:

- https://arxiv.org/abs/2001.01808

#### EEsizer

EEsizer adalah LLM-based AI agent untuk sizing analog/mixed-signal yang menghubungkan LLM dengan simulator dan data analysis functions secara closed-loop.

Sumber:

- https://arxiv.org/abs/2509.25510

### 1.10 Pola dari MCP specification

MCP tools harus memiliki schema input, error handling, output schema jika perlu, validasi input, access control, rate limiting, sanitasi output, user confirmation untuk operasi sensitif, timeout, dan audit logging.

Pelajaran untuk project ini:

```text
MCP tools untuk live editing harus kecil, eksplisit, schema-driven, dan path-safe.
```

Sumber:

- https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- https://modelcontextprotocol.io/specification/2025-11-25/server/resources

---

## 2. Product Vision

### 2.1 Nama fitur

Nama fitur yang disarankan:

```text
LTspice Live Agent
```

atau:

```text
Circuit Copilot Mode
```

### 2.2 User experience target

User dapat memberi prompt natural language:

```text
buat rangkaian sensor cahaya yang LED menyala saat gelap
```

Sistem menghasilkan:

```text
projects/ldr_dark_led_001/
  circuit.graph.json
  circuit.ir.json
  circuit.cir
  circuit.asc
  circuit.plt
  calculation.md
  calculation.json
  metadata.json
  result.json
  edit_history.jsonl
  preview.svg
  .snapshots/
```

User dapat melakukan edit lanjutan:

```text
ubah threshold comparator jadi 2.5V
buat output LED 20mA
simulasikan saat terang dan gelap
jelaskan rumus pembagi tegangannya
```

### 2.3 Definisi sukses

Project dianggap berhasil jika:

1. AI bisa membuat rangkaian sederhana dari prompt.
2. AI bisa mengedit project yang sudah ada melalui file-based workflow.
3. Setiap edit punya snapshot dan bisa undo.
4. Setiap desain punya perhitungan matematis yang eksplisit.
5. Setiap desain yang diklaim berhasil sudah lolos simulasi/verification.
6. `.asc` yang dihasilkan bisa dibuka di LTspice.
7. `.cir` bisa dijalankan dengan LTspice runner.
8. Result tidak hanya berupa prose, tetapi `result.json`, `calculation.json`, dan `verification.json`.
9. AI tidak dapat menulis file di luar workspace.
10. Rangkaian baru yang reusable masuk `templates/candidates`, bukan langsung `official`.

---

## 3. Non-Goals

Untuk menjaga scope, hal berikut tidak masuk MVP:

- AI mengontrol mouse/keyboard GUI LTspice secara langsung.
- AI bebas menulis `.asc` koordinat mentah tanpa generator.
- Remote MCP server publik.
- Arbitrary shell execution.
- Arbitrary file read/write.
- Automatic official template promotion tanpa audit.
- Full PCB layout dan routing.
- Mini PC siap produksi dari satu prompt.
- High-speed signal integrity untuk DDR/PCIe/USB/HDMI dalam MVP.
- Fine-tuned model.
- LLM sebagai kalkulator utama.

---

## 4. System Architecture

### 4.1 High-level architecture

```text
AI Agent / Codex / Claude Code / OpenCode
        |
        v
LTspice MCP Server
        |
        v
Live Editing Core
        |
        +--> Circuit Graph Engine
        +--> Math Core
        +--> Topology Library
        +--> Edit Operations
        +--> File Writers
        +--> Snapshot Manager
        +--> LTspice Runner
        +--> Log/Raw Parser
        +--> Verification Engine
        +--> Optimizer
        +--> Template Memory
```

### 4.2 File-based live editing architecture

```text
Prompt
  -> requirement.json
  -> circuit.graph.json
  -> circuit.ir.json
  -> circuit.cir
  -> circuit.asc
  -> circuit.plt
  -> LTspice run
  -> circuit.log / circuit.raw
  -> result.json
  -> calculation.md/json
  -> verification.json
```

### 4.3 Source of truth

Urutan source-of-truth:

```text
1. circuit.graph.json        primary editing model
2. circuit.ir.json           validated generation contract
3. circuit.cir               simulation netlist
4. circuit.asc               human-viewable schematic
5. result.json               simulation result
6. calculation.json/md       math report
```

`.asc` bukan source-of-truth utama karena `.asc` berisi koordinat/layout dan lebih rapuh untuk diedit langsung.

---

## 5. Repository Structure

Struktur final yang disarankan:

```text
LTSpice-MCP/
  README.md
  AGENTS.md
  MCP.md
  pyproject.toml
  config.example.toml

  docs/
    PROJECT_PLAN.md
    LIVE_EDITING_PLAN.md
    MATH_ACCURACY_PLAN.md
    SPEC.md
    architecture.md
    security.md
    troubleshooting.md
    sources.md
    adr/
      0001-cli-core-before-mcp.md
      0002-circuit-graph.md
      0003-file-based-live-editing.md
      0004-math-core-and-verification.md
      0005-template-memory.md

  examples/
    rc_lowpass.ir.json
    noninv_opamp.ir.json
    ldr_dark_led.prompt.txt
    buck_converter.prompt.txt

  schemas/
    circuit_graph.schema.json
    circuit_ir.schema.json
    calculation.schema.json
    verification.schema.json
    edit_operation.schema.json

  src/ltagent/
    __init__.py
    cli.py
    config.py
    security.py
    mcp_server.py

    graph/
      __init__.py
      circuit_graph.py
      graph_validation.py
      graph_to_ir.py
      ir_to_graph.py
      graph_diff.py

    live/
      __init__.py
      live_project.py
      edit_ops.py
      snapshot.py
      edit_history.py
      file_watcher.py
      preview.py

    math_core/
      __init__.py
      units.py
      formulas.py
      formula_registry.py
      standard_values.py
      symbolic.py
      mna.py
      specs.py
      optimizer.py
      tolerance.py
      verification_math.py
      calculation_report.py

    topology/
      __init__.py
      topology_library.py
      topology_matcher.py
      block_composer.py
      design_modes.py

    generation/
      __init__.py
      netlist.py
      asc.py
      plt.py
      layout.py
      layout_checker.py

    simulation/
      __init__.py
      runner.py
      log_parser.py
      raw_parser.py
      measurement.py
      verification.py
      result.py

    templates/
      __init__.py
      library.py
      evaluator.py
      promoter.py
      audit.py

  topologies/
    analog/
      voltage_divider.json
      rc_lowpass.json
      rc_highpass.json
      noninv_opamp.json
      inverting_opamp.json
      comparator.json
      buck_converter_basic.json
    sensor/
      ldr_dark_detector.json
      photodiode_tia.json
    power/
      zener_regulator.json
      buck_converter_basic.json
      boost_converter_basic.json
    system/
      mini_pc_architecture.json

  templates/
    official/
    candidates/
    rejected/

  projects/
    .gitkeep

  tests/
    test_circuit_graph.py
    test_edit_ops.py
    test_snapshot.py
    test_math_units.py
    test_formulas.py
    test_standard_values.py
    test_calculation_report.py
    test_verification.py
    test_optimizer.py
    test_live_cli.py
    test_live_mcp.py
```

---

## 6. Project Directory Format

Setiap project harus punya struktur standar:

```text
projects/{project_id}/
  circuit.graph.json
  circuit.ir.json
  circuit.cir
  circuit.asc
  circuit.plt
  metadata.json
  calculation.md
  calculation.json
  result.json
  verification.json
  edit_history.jsonl
  preview.svg
  preview.png
  circuit.log          generated after simulation
  circuit.raw          optional, ignored by git
  .snapshots/
    001_initial/
    002_before_set_r1/
    003_before_add_opamp/
```

### 6.1 `metadata.json`

```json
{
  "projectId": "rc_lowpass_1khz",
  "name": "RC Low-pass 1 kHz",
  "createdAt": "2026-06-19T12:00:00+07:00",
  "updatedAt": "2026-06-19T12:10:00+07:00",
  "createdBy": "ltagent",
  "supportLevel": "official_template",
  "mode": "reliable",
  "topology": "rc_lowpass",
  "status": "verified",
  "files": {
    "graph": "circuit.graph.json",
    "ir": "circuit.ir.json",
    "netlist": "circuit.cir",
    "schematic": "circuit.asc",
    "plot": "circuit.plt",
    "calculation": "calculation.md",
    "result": "result.json"
  }
}
```

### 6.2 `edit_history.jsonl`

```jsonl
{"step":1,"time":"2026-06-19T12:00:00+07:00","op":"create_project","reason":"initial prompt","prompt":"buat RC low-pass 1kHz"}
{"step":2,"time":"2026-06-19T12:02:00+07:00","op":"set_component_value","target":"R1","old":"1.59k","new":"1.6k","reason":"select E24 standard value"}
{"step":3,"time":"2026-06-19T12:03:00+07:00","op":"run_simulation","success":true,"measurements":{"fc":"994.7Hz"}}
```

---

## 7. Circuit Graph Model

### 7.1 Tujuan

`Circuit Graph` adalah representasi utama untuk live editing. Semua edit operasi dilakukan pada graph, bukan langsung pada `.asc`.

### 7.2 Schema awal

```json
{
  "schemaVersion": "0.2",
  "projectId": "rc_lowpass_1khz",
  "domain": "analog",
  "topology": "rc_lowpass",
  "components": {
    "Vin": {
      "kind": "voltage_source",
      "value": "SINE(0 1 1k)",
      "pins": {
        "+": "in",
        "-": "0"
      },
      "role": "input_source"
    },
    "R1": {
      "kind": "resistor",
      "value": "1.6k",
      "pins": {
        "1": "in",
        "2": "out"
      },
      "role": "series_resistor"
    },
    "C1": {
      "kind": "capacitor",
      "value": "100n",
      "pins": {
        "1": "out",
        "2": "0"
      },
      "role": "shunt_capacitor"
    }
  },
  "nets": {
    "in": {"type": "signal"},
    "out": {"type": "signal"},
    "0": {"type": "ground"}
  },
  "analyses": [
    {"kind": "ac", "points": 100, "start": "10", "stop": "100k"}
  ],
  "measurements": [
    {"name": "GAIN_1K", "analysis": "ac", "expression": "FIND mag(V(out)/V(in)) AT=1k"}
  ],
  "constraints": {
    "targetCutoffHz": 1000,
    "tolerancePercent": 2
  },
  "layoutHints": {
    "flow": "left_to_right",
    "inputNode": "in",
    "outputNode": "out"
  }
}
```

### 7.3 Validation rules

Wajib:

1. `schemaVersion` valid.
2. `projectId` slug-safe.
3. Ground harus `0`.
4. Setiap component id unik.
5. Setiap pin terhubung ke net yang terdaftar.
6. Arity component benar.
7. Nilai komponen valid secara unit.
8. Tidak ada duplicate node invalid.
9. Tidak ada raw directive berbahaya.
10. Tidak ada `.include` di luar allowlist.
11. Semua measurement punya nama aman.
12. Semua analysis valid.
13. Floating critical node diberi error atau warning.

---

## 8. Edit Operation API

### 8.1 Tujuan

AI tidak boleh langsung mengubah file mentah. AI memanggil `edit_ops`.

### 8.2 Edit operation schema

```json
{
  "op": "set_component_value",
  "projectId": "rc_lowpass_1khz",
  "args": {
    "componentId": "R1",
    "value": "1.6k"
  },
  "reason": "Adjust cutoff frequency to 1kHz using E24 value"
}
```

### 8.3 Operasi MVP

```text
open_project
inspect_circuit
add_component
remove_component
replace_component
set_component_value
rename_component
connect
 disconnect
rename_net
add_ground
add_label
add_directive
add_measurement
add_probe
generate_files
run_simulation
verify_project
```

### 8.4 Contoh operasi

#### Add component

```json
{
  "op": "add_component",
  "args": {
    "id": "R3",
    "kind": "resistor",
    "value": "10k",
    "pins": {"1": "out", "2": "fb"},
    "role": "feedback_resistor"
  }
}
```

#### Connect nodes

```json
{
  "op": "connect",
  "args": {
    "componentId": "U1",
    "pin": "OUT",
    "net": "vout"
  }
}
```

#### Add measurement

```json
{
  "op": "add_measurement",
  "args": {
    "name": "VOUT_MAX",
    "analysis": "tran",
    "expression": "MAX V(out)"
  }
}
```

### 8.5 Apply workflow

```text
1. Validate edit op schema.
2. Load project.
3. Create snapshot.
4. Apply edit to graph.
5. Validate graph.
6. Convert graph -> IR.
7. Generate .cir.
8. Generate .asc.
9. Lint schematic/layout.
10. Update edit_history.jsonl.
11. Return structured result.
```

---

## 9. Snapshot and Undo System

### 9.1 Tujuan

Setiap edit dapat di-rollback.

### 9.2 Snapshot structure

```text
.snapshots/
  001_initial/
    circuit.graph.json
    circuit.ir.json
    circuit.cir
    circuit.asc
    metadata.json
    calculation.json
    result.json
    manifest.json
```

### 9.3 Snapshot manifest

```json
{
  "snapshotId": "001_initial",
  "createdAt": "2026-06-19T12:00:00+07:00",
  "reason": "before edit: set_component_value R1",
  "files": [
    "circuit.graph.json",
    "circuit.ir.json",
    "circuit.cir",
    "circuit.asc",
    "metadata.json"
  ]
}
```

### 9.4 Commands

```bash
ltagent live snapshot projects/demo --reason "before add opamp" --json
ltagent live snapshots projects/demo --json
ltagent live restore projects/demo 002_before_add_opamp --json
ltagent live diff projects/demo 001_initial 003_final --json
```

---

## 10. File-Based Live Editing CLI

### 10.1 Commands

```bash
ltagent live open projects/demo --json
ltagent live inspect projects/demo --json
ltagent live apply projects/demo edit.json --json
ltagent live apply-many projects/demo edits.jsonl --json
ltagent live snapshot projects/demo --reason "manual checkpoint" --json
ltagent live restore projects/demo 003_before_optimizer --json
ltagent live generate projects/demo --json
ltagent live run projects/demo --json
ltagent live verify projects/demo --json
ltagent live optimize projects/demo --json
ltagent live explain projects/demo --json
```

### 10.2 Example edit file

```json
{
  "operations": [
    {
      "op": "set_component_value",
      "args": {"componentId": "R1", "value": "1.6k"},
      "reason": "Use standard E24 value"
    },
    {
      "op": "add_measurement",
      "args": {"name": "GAIN_1K", "analysis": "ac", "expression": "FIND mag(V(out)/V(in)) AT=1k"},
      "reason": "Verify gain at target frequency"
    }
  ]
}
```

### 10.3 Output contract

Semua command wajib:

```json
{
  "success": true,
  "command": "live.apply",
  "message": "Applied 2 operations",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Failure:

```json
{
  "success": false,
  "command": "live.apply",
  "message": "Graph validation failed",
  "data": {"projectId": "demo"},
  "warnings": [],
  "errors": [
    {
      "code": "FLOATING_NODE",
      "detail": "Node fb has only one connected pin",
      "data": {"node": "fb"}
    }
  ]
}
```

---

## 11. MCP Live Editing Tools

### 11.1 Design principle

MCP tools harus:

- kecil,
- eksplisit,
- schema-driven,
- path-safe,
- tidak memberi arbitrary shell,
- tidak memberi arbitrary file read/write,
- return structured JSON,
- snapshot before modification.

### 11.2 Tool groups

#### Project tools

```text
live_open_project
live_create_project
live_get_project_state
live_list_files
live_snapshot
live_restore_snapshot
live_diff
```

#### Circuit edit tools

```text
live_add_component
live_remove_component
live_set_value
live_connect
live_disconnect
live_rename_net
live_add_stage
live_replace_stage
live_add_measurement
live_add_directive
```

#### Generation tools

```text
live_generate_netlist
live_generate_schematic
live_generate_plot_settings
live_write_all
```

#### Simulation tools

```text
live_run_simulation
live_parse_log
live_read_measurements
live_verify_targets
live_optimize
```

#### Agent-level tools

```text
design_circuit
edit_circuit
fix_circuit
explain_circuit
compare_versions
promote_to_template
```

### 11.3 Recommended primary tool: `design_circuit`

Input:

```json
{
  "prompt": "buat non-inverting amplifier gain 10 untuk input sinus 100mV 1kHz",
  "mode": "reliable",
  "runSimulation": true,
  "optimize": true,
  "maxAttempts": 5
}
```

Output:

```json
{
  "success": true,
  "projectId": "noninv_amp_gain_10",
  "selectedTopology": "noninv_opamp",
  "supportLevel": "official_template",
  "confidence": 0.91,
  "files": {
    "graph": "circuit.graph.json",
    "ir": "circuit.ir.json",
    "cir": "circuit.cir",
    "asc": "circuit.asc",
    "calculation": "calculation.md",
    "result": "result.json"
  },
  "warnings": [
    "uses ideal op-amp model unless a real model is selected"
  ]
}
```

### 11.4 Recommended primary tool: `edit_circuit`

Input:

```json
{
  "projectId": "amplifier_01",
  "instruction": "kurangi gain supaya output tidak clipping",
  "runSimulation": true,
  "maxAttempts": 5
}
```

Output:

```json
{
  "success": true,
  "projectId": "amplifier_01",
  "changes": [
    {"componentId": "Rf", "old": "100k", "new": "47k"}
  ],
  "verification": {
    "noClipping": true,
    "gain": 5.7
  }
}
```

---

## 12. Subagent Architecture

### 12.1 Recommended subagents

```text
Supervisor Agent
├── Requirement Agent
├── Circuit Research Agent
├── Topology Agent
├── Constraint Agent
├── Math Agent
├── Component Agent
├── Netlist Agent
├── Layout Agent
├── Simulation Agent
├── Analyzer Agent
├── Optimizer Agent
├── Reviewer Agent
└── Memory Curator Agent
```

### 12.2 Responsibilities

| Subagent | Responsibility |
|---|---|
| Requirement Agent | Ekstrak input, output, supply, load, priority, tolerance. |
| Circuit Research Agent | Cari topologi dari library, docs, template lama. |
| Topology Agent | Pilih atau susun topology candidate. |
| Constraint Agent | Cek batasan fisik: tegangan, arus, power, frekuensi. |
| Math Agent | Memanggil math_core, bukan menghitung bebas. |
| Component Agent | Pilih nilai dan model komponen. |
| Netlist Agent | Membuat graph, IR, `.cir`. |
| Layout Agent | Membuat `.asc` rapi. |
| Simulation Agent | Menjalankan LTspice. |
| Analyzer Agent | Membaca `.log`, `.meas`, `.raw`. |
| Optimizer Agent | Mengubah nilai komponen dan loop ulang. |
| Reviewer Agent | Cek desain aman, masuk akal, dan tidak overstated. |
| Memory Curator Agent | Memutuskan apakah desain masuk candidates/official/rejected. |

### 12.3 Rule utama

```text
LLM boleh memilih arah desain,
tetapi tidak boleh menjadi kalkulator utama,
tidak boleh menulis file arbitrary,
dan tidak boleh mengklaim sukses tanpa verification pass.
```

---

## 13. Mathematical Accuracy System

## 13.1 Tujuan

Membuat perhitungan rangkaian sangat akurat, transparan, dan bisa diverifikasi.

### 13.2 Prinsip

```text
AI = planner + explainer
Math Core = calculator
SPICE/LTspice = numerical verifier
Optimizer = search/refinement engine
Verification Engine = pass/fail authority
```

### 13.3 Math pipeline

```text
Prompt
  -> requirement extraction
  -> unit normalization
  -> topology formula selection
  -> ideal calculation
  -> standard value selection
  -> symbolic/MNA sanity check
  -> SPICE netlist + .meas generation
  -> LTspice simulation
  -> measurement parsing
  -> formula-vs-simulation comparison
  -> optimizer loop if needed
  -> tolerance/worst-case analysis
  -> calculation report
```

---

## 14. Math Core Modules

### 14.1 Directory

```text
src/ltagent/math_core/
  __init__.py
  units.py
  formulas.py
  formula_registry.py
  standard_values.py
  symbolic.py
  mna.py
  specs.py
  optimizer.py
  tolerance.py
  verification_math.py
  calculation_report.py
```

### 14.2 `units.py`

Tugas:

```text
Parse and normalize values:
10k        -> 10000 ohm
100nF      -> 1e-7 F
3.3V       -> 3.3 V
1mA        -> 0.001 A
1kHz       -> 1000 Hz
```

Rules:

- Support SPICE suffix: `f`, `p`, `n`, `u`, `m`, `k`, `meg`, `g`, `t`.
- Normalize unicode `µ` to `u`.
- Reject ambiguous suffix if context requires unit.
- Reject invalid combination: e.g. `1kHz` as resistance.
- Store both raw and SI value.

Example output:

```json
{
  "raw": "100nF",
  "quantity": "capacitance",
  "siValue": 1e-7,
  "unit": "F"
}
```

### 14.3 `formulas.py`

Topologi awal:

```text
voltage_divider
rc_lowpass
rc_highpass
rl_lowpass
rl_highpass
rlc_resonance
inverting_opamp
noninv_opamp
led_resistor
bjt_switch_basic
mosfet_switch_basic
buck_ideal
boost_ideal
```

### 14.4 `standard_values.py`

Support:

```text
E6, E12, E24, E48, E96
common capacitor values
common inductor values
preferred voltage/current ratings
```

Example:

```json
{
  "ideal": 1591.55,
  "series": "E24",
  "selected": 1600,
  "errorPercent": 0.53
}
```

### 14.5 `symbolic.py`

Tugas:

- Generate symbolic transfer function for simple circuits.
- Use SymPy optional dependency.
- Produce human-readable derivation.

Example:

```text
H(s) = Vout/Vin = 1 / (1 + sRC)
fc = 1 / (2πRC)
```

### 14.6 `mna.py`

Tugas:

- Build basic Modified Nodal Analysis for linear circuits.
- Support R, C, L, independent sources for sanity check.
- Not a replacement for LTspice.

MNA equation:

```text
Gx = b          for DC / operating point linear circuits
C dx/dt + Gx = b(t)    for dynamic circuits
```

### 14.7 `optimizer.py`

Modes:

```text
formula_only
standard_value_search
grid_search
spice_sweep
differential_evolution
bayesian_later
```

Initial optimizer priority:

1. Exact formula if available.
2. Standard value nearest search.
3. Local sweep around selected values.
4. LTspice `.step` sweep.
5. SciPy differential evolution for multi-variable circuits.

### 14.8 `tolerance.py`

Support:

```text
worst_case
monte_carlo
input_sweep
load_sweep
temperature_sweep_later
```

Output:

```json
{
  "nominal": {"fc": 994.7},
  "worstCase": {"min": 890.0, "max": 1110.0},
  "monteCarlo": {"samples": 200, "mean": 998.0, "std": 42.0}
}
```

### 14.9 `calculation_report.py`

Generate:

```text
calculation.md
calculation.json
```

---

## 15. Formula Library

### 15.1 Formula JSON structure

```json
{
  "topology": "rc_lowpass",
  "description": "First-order passive RC low-pass filter",
  "variables": {
    "R": {"quantity": "resistance", "unit": "ohm"},
    "C": {"quantity": "capacitance", "unit": "F"},
    "fc": {"quantity": "frequency", "unit": "Hz"}
  },
  "formulas": [
    {
      "name": "cutoff_frequency",
      "expression": "fc = 1 / (2*pi*R*C)",
      "solveFor": ["fc", "R", "C"]
    }
  ],
  "verification": [
    {
      "name": "gain_at_cutoff",
      "expectedMagnitude": 0.707,
      "tolerancePercent": 5
    }
  ]
}
```

### 15.2 Formula examples

#### Voltage divider

```text
Vout = Vin * R2 / (R1 + R2)
R1/R2 = (Vin - Vout) / Vout
```

#### RC low-pass

```text
fc = 1 / (2πRC)
H(s) = 1 / (1 + sRC)
```

#### RC high-pass

```text
fc = 1 / (2πRC)
H(s) = sRC / (1 + sRC)
```

#### Non-inverting op-amp

```text
Av = 1 + Rf/Rg
Rf = (Av - 1)Rg
```

#### Inverting op-amp

```text
Av = -Rf/Rin
Rf = |Av| Rin
```

#### LED resistor

```text
R = (Vsupply - Vf) / Iled
P_R = Iled^2 R
```

#### Ideal buck converter

```text
D = Vout / Vin
Rload = Vout / Iout
Pout = Vout * Iout
```

#### Ideal boost converter

```text
D = 1 - Vin / Vout
Rload = Vout / Iout
```

---

## 16. Calculation Report Format

### 16.1 `calculation.md`

Example:

```md
# Calculation Report

## User Target
- Circuit: RC low-pass filter
- Target cutoff: 1 kHz
- Fixed capacitor: 100 nF
- Component series: E24

## Formula
fc = 1 / (2πRC)

## Solve for R
R = 1 / (2πfcC)

## Substitution
R = 1 / (2π × 1000 × 100nF)
R = 1591.55 Ω

## Standard Value Selection
Selected R = 1.6 kΩ
Selected C = 100 nF

## Predicted Result
fc = 994.7 Hz
error = 0.53%

## LTspice Verification
Measured gain at 1 kHz = ...
Passed: true

## Assumptions
- Ideal capacitor unless tolerance analysis is enabled.
- No ESR/ESL modeled in MVP.
- Source impedance is assumed ideal.
```

### 16.2 `calculation.json`

```json
{
  "success": true,
  "topology": "rc_lowpass",
  "formulas": [
    {
      "name": "cutoff_frequency",
      "expression": "fc = 1 / (2*pi*R*C)"
    }
  ],
  "idealValues": {
    "R": {"value": 1591.55, "unit": "ohm"},
    "C": {"value": 1e-7, "unit": "F"}
  },
  "selectedValues": {
    "R": {"value": 1600, "unit": "ohm", "display": "1.6k"},
    "C": {"value": 1e-7, "unit": "F", "display": "100n"}
  },
  "predicted": {
    "fc": {"value": 994.718, "unit": "Hz"},
    "errorPercent": 0.528
  },
  "simulation": {
    "attempted": true,
    "passed": true,
    "measurements": {}
  },
  "assumptions": [
    "ideal capacitor",
    "no parasitic ESR/ESL"
  ]
}
```

---

## 17. Verification System

### 17.1 Verification schema

```json
{
  "checks": [
    {
      "name": "cutoff_frequency",
      "target": 1000,
      "actual": 994.7,
      "unit": "Hz",
      "tolerancePercent": 2,
      "errorPercent": 0.53,
      "passed": true
    }
  ],
  "overallPassed": true,
  "confidence": 0.94
}
```

### 17.2 Verification levels

```text
Level 0: graph validation only
Level 1: formula calculation only
Level 2: formula + generated netlist
Level 3: LTspice simulation pass
Level 4: simulation + target measurement pass
Level 5: simulation + tolerance/worst-case pass
```

### 17.3 Confidence scoring

```text
+25 formula available and validated
+20 unit/dimension check passed
+25 LTspice simulation passed
+10 target error below tolerance
+10 standard component selection available
+10 tolerance analysis passed

-30 simulation failed
-20 formula unavailable
-20 unsupported topology
-15 ideal model only
-10 tolerance not tested
```

Example:

```json
{
  "mathConfidence": 0.94,
  "simulationConfidence": 0.91,
  "overallConfidence": 0.89,
  "reasons": [
    "formula available",
    "unit check passed",
    "LTspice simulation passed",
    "standard component error below 1%"
  ],
  "warnings": [
    "component tolerance not simulated",
    "real op-amp model not selected"
  ]
}
```

---

## 18. Optimizer Design

### 18.1 Optimizer loop

```python
for attempt in range(max_attempts):
    values = propose_component_values()
    write_netlist(values)
    run_ltspice()
    result = parse_measurements()
    score = objective(result, target)

    if score.passed:
        return values, result

    update_search_space(result)
```

### 18.2 Objective function

```text
score =
  w1 * target_error
+ w2 * ripple_penalty
+ w3 * clipping_penalty
+ w4 * power_penalty
+ w5 * instability_penalty
+ w6 * component_cost_penalty
```

### 18.3 Optimizer progression

MVP:

```text
formula -> standard value search -> local sweep
```

Next:

```text
LTspice .step -> grid search -> SciPy differential_evolution
```

Future:

```text
Bayesian optimization -> surrogate model -> reinforcement learning
```

### 18.4 When to use optimizer

Use optimizer when:

- formula unavailable,
- formula exists but simulation fails target,
- multi-variable trade-off,
- topology has nonideal components,
- user requests high accuracy,
- tolerance analysis fails.

Do not use optimizer when:

- circuit unsafe/high-voltage and model is insufficient,
- target is underspecified,
- no valid simulation model exists,
- topology is outside supported domain.

---

## 19. Design Modes

### 19.1 Reliable mode

Only official templates and known formulas.

Use for:

```text
voltage divider, RC filter, op-amp gain, comparator, rectifier, transistor switch
```

Output support level:

```text
official_template
```

### 19.2 Exploratory mode

AI can propose new candidate topologies, but all candidates must be linted and simulated.

Use for:

```text
sensor interface, oscillator, active filter, simple power converter
```

Output support level:

```text
generated_candidate
```

### 19.3 System architecture mode

For large requests like:

```text
buat mini PC
buat laptop mini
buat motherboard
buat robot controller lengkap
```

Output:

```text
architecture.md
block_diagram.json
subsystem_plan.md
power_tree.md
ltspice_simulation_plan.md
```

Important:

```text
Do not claim manufacturing-ready schematic.
```

### 19.4 Learning mode

When a candidate works repeatedly:

```text
projects -> candidates -> official after audit
```

---

## 20. Topology Library

### 20.1 Initial topology list

```text
1. voltage_divider
2. rc_lowpass
3. rc_highpass
4. rl_lowpass
5. rl_highpass
6. rlc_bandpass
7. rlc_notch
8. opamp_buffer
9. inverting_opamp
10. noninv_opamp
11. comparator
12. schmitt_trigger
13. diode_clipper
14. diode_clamper
15. halfwave_rectifier
16. bridge_rectifier
17. zener_regulator
18. bjt_switch
19. mosfet_switch
20. current_source_basic
21. 555_astable
22. 555_monostable
23. photodiode_tia
24. ldr_dark_detector
25. buck_converter_basic
26. boost_converter_basic
27. linear_regulator_basic
28. active_lowpass_filter
29. active_highpass_filter
30. instrumentation_amplifier_basic
```

### 20.2 Topology metadata

```json
{
  "id": "noninv_opamp",
  "description": "Non-inverting op-amp amplifier",
  "complexity": "simple",
  "domain": "analog",
  "useWhen": [
    "positive gain",
    "high input impedance",
    "small signal amplification"
  ],
  "avoidWhen": [
    "negative gain required",
    "single transistor only"
  ],
  "requiredParams": [
    "targetGain",
    "supplyVoltage",
    "inputAmplitude"
  ],
  "formulas": ["opamp_noninv_gain"],
  "verification": ["gain", "no_clipping", "output_range"],
  "templateStatus": "official"
}
```

---

## 21. Handling Complex Requests: Mini PC Example

### 21.1 User prompt

```text
buat mini PC
```

### 21.2 Classification

```json
{
  "mode": "system_architecture",
  "reason": "request exceeds single LTspice circuit scope",
  "requires": [
    "SoC selection",
    "RAM interface",
    "power tree",
    "USB/HDMI/Ethernet",
    "clock/reset",
    "PCB stackup",
    "firmware boot chain",
    "thermal design"
  ]
}
```

### 21.3 Output files

```text
projects/mini_pc_architecture_001/
  architecture.md
  block_diagram.json
  power_tree.md
  ltspice_power_section_plan.md
  regulator_5v/circuit.asc
  regulator_3v3/circuit.asc
  regulator_1v8/circuit.asc
  assumptions.md
  risk_register.md
```

### 21.4 Allowed claim

Allowed:

```text
This is an architecture draft and simulation plan.
Power sections can be simulated in LTspice.
```

Not allowed:

```text
This mini PC is ready to fabricate.
```

---

## 22. Security Model

### 22.1 Hard rules

1. No arbitrary shell.
2. No arbitrary file read/write.
3. All writes under configured workspace.
4. All path inputs resolved and checked.
5. MCP tools cannot escape workspace.
6. AI cannot write `.asc` coordinate lines directly in production path.
7. No arbitrary `.include` or `.lib` outside allowlist.
8. Official templates require audit and snapshot before modification.
9. `.raw` files not exposed by default.
10. Tool calls have timeout.
11. Sensitive operations require explicit confirmation.
12. Logs and edit history are always written.

### 22.2 Dangerous request handling

Requests involving:

```text
mains AC
high voltage
battery charger
high current power electronics
RF transmitter
medical electronics
safety-critical control
```

must include:

```text
expert_review_required = true
hardware_safe_to_build = false unless explicitly verified by qualified engineer
```

### 22.3 MCP security

Follow MCP guidance:

- validate all tool inputs,
- access control,
- rate limiting,
- output sanitization,
- user confirmation on sensitive operations,
- timeout,
- audit logging.

---

## 23. Testing Strategy

### 23.1 Unit tests

```text
test_units.py
test_formulas.py
test_standard_values.py
test_circuit_graph.py
test_graph_validation.py
test_edit_ops.py
test_snapshot.py
test_netlist_generation.py
test_asc_generation.py
test_calculation_report.py
test_verification.py
test_optimizer.py
```

### 23.2 Integration tests

```text
test_live_apply_to_project.py
test_live_run_simulation.py
test_formula_vs_ltspice.py
test_optimizer_with_ltspice.py
test_mcp_live_tools.py
```

Integration tests requiring LTspice should be marked:

```bash
pytest -m integration
```

### 23.3 Golden tests

Golden outputs for:

```text
rc_lowpass
voltage_divider
noninv_opamp
inverting_opamp
comparator
bridge_rectifier
bjt_switch
buck_converter_basic
```

### 23.4 Failure tests

Ensure structured error for:

- missing ground,
- duplicate component id,
- floating critical node,
- invalid unit,
- unsafe directive,
- path traversal,
- simulation timeout,
- no `.log` produced,
- formula unavailable,
- target impossible.

---

## 24. Implementation Roadmap

## Phase 0 — Repo cleanup and planning docs

Goal:

```text
Make repository consistent and ready for live editing.
```

Tasks:

- Fix repo/package naming inconsistency.
- Update README tool/resource counts if Phase 12 remains.
- Add `docs/LIVE_EDITING_PLAN.md`.
- Add `docs/MATH_ACCURACY_PLAN.md`.
- Add ADR for Circuit Graph.
- Remove public `allow_outside_workspace` escape hatch from MCP tools.
- Ensure all generated outputs are `.gitignore`d.

Acceptance:

- README matches actual package and repo.
- `pytest` passes.
- `ruff` passes.
- `mypy` passes for touched modules.

---

## Phase 1 — Circuit Graph MVP

Goal:

```text
Create structured graph representation for live editing.
```

Tasks:

- Implement `graph/circuit_graph.py`.
- Define Pydantic models.
- Implement graph validation.
- Implement IR -> Graph converter.
- Implement Graph -> IR converter.
- Add JSON schema.

Acceptance:

- `rc_lowpass.ir.json` converts to graph and back.
- Graph validation catches missing ground, duplicate component IDs, invalid nets.

---

## Phase 2 — Math Core MVP

Goal:

```text
Add accurate formula-based calculation for simple circuits.
```

Tasks:

- Implement `math_core/units.py`.
- Implement formula registry.
- Add formulas for voltage divider, RC filters, op-amp gain, LED resistor.
- Add E-series selector.
- Add calculation JSON model.
- Generate `calculation.md`.

Acceptance:

- RC low-pass 1 kHz with C=100nF returns R ideal ≈ 1591.55Ω and E24 selected ≈ 1.6kΩ.
- Calculation report includes formula, substitution, ideal values, selected values, and predicted error.

---

## Phase 3 — Edit Operations MVP

Goal:

```text
Allow safe file-based edits through operations.
```

Tasks:

- Implement `live/edit_ops.py`.
- Add `set_component_value`.
- Add `add_component`.
- Add `remove_component`.
- Add `connect` / `disconnect`.
- Add `add_measurement`.
- Auto-generate IR/netlist/schematic after edit.

Acceptance:

- Edit R1 in RC project from 1.59k to 1.6k.
- Files update consistently.
- Invalid edit fails with structured error.

---

## Phase 4 — Snapshot and Undo

Goal:

```text
Every edit can be rolled back.
```

Tasks:

- Implement snapshot manager.
- Implement restore.
- Implement diff.
- Implement edit history.
- Make snapshot mandatory before modifying files.

Acceptance:

- After 3 edits, restore snapshot 1 produces the original file hashes.

---

## Phase 5 — Live CLI

Goal:

```text
Expose live editing through CLI.
```

Tasks:

- Add `ltagent live open`.
- Add `ltagent live inspect`.
- Add `ltagent live apply`.
- Add `ltagent live generate`.
- Add `ltagent live run`.
- Add `ltagent live verify`.
- Add `ltagent live optimize`.

Acceptance:

- A JSON edit file can modify a project, regenerate `.cir/.asc`, run simulation, and write result.

---

## Phase 6 — Formula vs Simulation Verification

Goal:

```text
Compare mathematical prediction against LTspice results.
```

Tasks:

- Generate `.meas` from verification targets.
- Parse measurement.
- Compare formula prediction vs simulation.
- Write `verification.json`.
- Update confidence score.

Acceptance:

- RC low-pass report includes predicted cutoff and simulated measurement.
- Failure target produces `success=false` or warning, never silent success.

---

## Phase 7 — MCP Live Tools

Goal:

```text
Allow AI agent to live-edit via MCP.
```

Tasks:

- Add `live_open_project`.
- Add `live_inspect_project`.
- Add `live_apply_edit`.
- Add `live_snapshot`.
- Add `live_restore_snapshot`.
- Add `live_run_and_verify`.
- Add tests for path safety and structured output.

Acceptance:

- AI client can change R1 and rerun simulation through MCP.
- No live MCP tool can write outside workspace.

---

## Phase 8 — Design Circuit Tool

Goal:

```text
One prompt can create a full project.
```

Tasks:

- Implement `design_circuit` orchestration.
- Add requirement extractor.
- Add topology matcher.
- Add math calculation.
- Add file generation.
- Add simulation and verification.
- Add final report.

Acceptance:

- Prompt `buat non-inverting amplifier gain 10` creates project and calculation report.

---

## Phase 9 — Topology Library Expansion

Goal:

```text
Increase range from simple to moderately complex circuits.
```

Tasks:

- Add 30 topology metadata files.
- Add formulas where possible.
- Add verification templates.
- Add official/candidate template mapping.
- Add `useWhen` / `avoidWhen` logic.

Acceptance:

- Agent chooses appropriate topology for at least 20 common prompt types.

---

## Phase 10 — Optimizer

Goal:

```text
Improve component values automatically.
```

Tasks:

- Add standard value local search.
- Add `.step` sweep generator.
- Add objective function.
- Add best candidate selector.
- Optional: SciPy differential evolution.

Acceptance:

- If initial value fails target, optimizer attempts better values and records attempts.

---

## Phase 11 — Tolerance and Robustness Analysis

Goal:

```text
Evaluate design beyond nominal values.
```

Tasks:

- Add component tolerance metadata.
- Add worst-case analysis.
- Add Monte Carlo generation.
- Add input/load sweep.
- Write tolerance report.

Acceptance:

- Calculation report includes nominal and worst-case for supported topologies.

---

## Phase 12 — Preview and Visual Diff

Goal:

```text
Make file-based live editing feel visual.
```

Tasks:

- Generate preview.svg from graph.
- Add before/after visual diff.
- Add layout score.
- Optional PNG rendering.

Acceptance:

- Each edit can produce before/after preview.

---

## Phase 13 — Exploratory Mode

Goal:

```text
AI can try new topologies safely.
```

Tasks:

- Generate multiple candidates.
- Lint all candidates.
- Simulate all candidates.
- Score candidates.
- Store best as candidate template.
- Reject failures with reason.

Acceptance:

- Prompt `buat sensor cahaya LED menyala saat gelap` produces a generated candidate project with verification notes.

---

## Phase 14 — System Architecture Mode

Goal:

```text
Handle large requests like mini PC honestly.
```

Tasks:

- Add complexity classifier.
- Generate block diagram.
- Generate power tree.
- Generate LTspice simulation plan for analog/power sections only.
- Add risk register.

Acceptance:

- Prompt `buat mini PC` produces architecture plan and subprojects, not false claim of finished manufacturable schematic.

---

## 25. Acceptance Criteria for MVP

MVP complete if:

1. `ltagent live apply` works on RC low-pass.
2. Snapshot/restore works.
3. `calculation.md` and `calculation.json` generated.
4. Math formula and selected values are correct for supported topologies.
5. `.cir` and `.asc` generated.
6. LTspice run attempted when configured.
7. Formula vs simulation verification works when simulation available.
8. MCP live tools expose safe edit operations.
9. All path traversal attempts rejected.
10. Unit tests pass without LTspice installed.
11. Integration tests skip cleanly if LTspice unavailable.

---

## 26. Example End-to-End Scenarios

### 26.1 RC low-pass

Prompt:

```text
buat RC low-pass cutoff 1kHz dengan C 100nF
```

Expected:

```text
R ideal = 1591.55 ohm
R selected = 1.6k
fc predicted = 994.7 Hz
simulation target = gain at 1 kHz near -3 dB
```

Files:

```text
circuit.graph.json
circuit.ir.json
circuit.cir
circuit.asc
calculation.md
result.json
verification.json
```

### 26.2 Non-inverting op-amp

Prompt:

```text
buat non-inverting amplifier gain 10, input 100mV 1kHz, supply ±12V
```

Expected:

```text
Av = 1 + Rf/Rg
Choose Rg = 10k
Rf = 90k ideal
Selected Rf = 91k E24 if desired
Check output amplitude ≈ 1V peak
Check no clipping under ±12V supply
```

### 26.3 LDR dark detector

Prompt:

```text
buat sensor cahaya LED menyala saat gelap
```

Expected topology:

```text
LDR divider -> comparator -> transistor switch -> LED load
```

Mode:

```text
exploratory or template if topology exists
```

Warnings:

```text
requires LDR model assumptions
threshold depends on LDR resistance range
real component values need calibration
```

### 26.4 Buck converter

Prompt:

```text
buat buck converter 12V ke 5V 1A ripple < 50mV
```

Expected:

```text
D ideal = 5/12
Rload = 5 ohm
Select switching frequency default if unspecified
Initial L/C from formula
Transient simulation
Measure Vout_avg and ripple
Optimize L/C/duty if necessary
```

### 26.5 Mini PC

Prompt:

```text
buat mini PC
```

Expected:

```text
system_architecture mode
block diagram
power tree
simulation plan for regulators
no manufacturing-ready claim
```

---

## 27. Source Index

### MCP and agent tooling

- Model Context Protocol — Tools specification: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Model Context Protocol — Resources specification: https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- Blender MCP server example: https://github.com/djeada/blender-mcp-server
- Blender MCP Pro: https://github.com/youichi-uda/blender-mcp-pro

### LTspice and SPICE automation

- Analog Devices LTspice official page: https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html
- PyLTSpice documentation: https://pyltspice.readthedocs.io/en/latest/
- PyLTSpice GitHub: https://github.com/nunobrum/PyLTSpice
- spicelib GitHub: https://github.com/nunobrum/spicelib
- ngspice documentation: https://ngspice.sourceforge.io/docs.html

### Circuit-as-code and schematic APIs

- SKiDL documentation: https://devbisme.github.io/skidl/
- SKiDL GitHub: https://github.com/devbisme/skidl
- tscircuit website: https://tscircuit.com/
- tscircuit GitHub org: https://github.com/tscircuit
- Circuit JSON: https://github.com/tscircuit/circuit-json
- KiCad schematic MCP API: https://github.com/circuit-synth/mcp-kicad-sch-api
- kicad-sch-api: https://github.com/circuit-synth/kicad-sch-api
- circuit-synth: https://github.com/circuit-synth/circuit-synth

### AI circuit design research

- Schemato — LLM for Netlist-to-Schematic Conversion: https://arxiv.org/abs/2411.13899
- CircuitLM — Multi-Agent LLM-Aided Circuit Schematic Generation: https://arxiv.org/abs/2601.04505
- AutoCkt — Deep Reinforcement Learning of Analog Circuit Designs: https://arxiv.org/abs/2001.01808
- EEsizer — LLM-Based AI Agent for Sizing Analog and Mixed Signal Circuit: https://arxiv.org/abs/2509.25510

### Optimization and numerical methods

- SciPy differential evolution: https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html

---

## 28. Final Recommendation

Bangun project ini dengan prinsip:

```text
File-based live editing untuk workflow,
Circuit Graph untuk source-of-truth,
Math Core untuk perhitungan,
LTspice untuk verifikasi,
Optimizer untuk perbaikan,
Snapshot untuk keamanan,
Template Memory untuk pembelajaran jangka panjang.
```

Target akhirnya bukan sekadar:

```text
AI membuat file LTspice
```

melainkan:

```text
AI menjadi engineering assistant yang dapat:
- memahami requirement,
- menghitung nilai komponen,
- membuat rangkaian,
- mengedit rangkaian lama,
- menjalankan simulasi,
- membandingkan hasil dengan target,
- memperbaiki desain,
- menjelaskan perhitungan,
- menyimpan desain reusable.
```
