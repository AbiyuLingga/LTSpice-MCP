# Tiny8 digital toolchain

Phase 12 uses three optional external tools. None are
required for the test suite to pass; their absence produces
structured warnings, not failures (except in `--strict`
mode on `simulate` / `synth-check`).

| Tool | Purpose | Required? |
|---|---|---|
| [Icarus Verilog](https://steveicarus.github.io/iverilog/) (`iverilog` + `vvp`) | compile + simulate generated HDL + testbench | optional; primary v1 simulator |
| [Verilator](https://verilator.org/guide/latest/) | optional lint of generated HDL | optional |
| [Yosys](https://yosyshq.readthedocs.io/projects/yosys/en/latest/) | synthesis sanity check (`hierarchy`, `proc`, `opt`, `stat`) | optional |
| [GTKWave](https://gtkwave.sourceforge.net/) | view `.vcd` waveforms | optional (manual only) |

The LTspice analog companion continues to use the existing
`runner.py` + Wine setup. See [`../ltspice_setup.md`](../ltspice_setup.md)
for the analog toolchain.

## Install

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y iverilog verilator yosys gtkwave
```

Verify:

```bash
iverilog -V 2>&1 | head -1
vvp -V 2>&1 | head -1
verilator --version 2>&1 | head -1
yosys -V 2>&1 | head -1
gtkwave --version 2>&1 | head -1
```

### Fedora

```bash
sudo dnf install -y iverilog verilator yosys gtkwave
```

### macOS (Homebrew)

```bash
brew install icarus-verilog verilator yosys gtkwave
```

`iverilog` ships as `icarus-verilog` on Homebrew; the
`ltagent digital doctor` check accepts the renamed binary.

### Windows

Install MSYS2 / WSL and use the Debian instructions.
Native Windows binaries are available from each project's
site but are not part of the v1 acceptance matrix.

## Tool gating policy

- `ltagent digital doctor` always reports the presence
  and version of every tool, with a `recommendedInstall`
  hint per platform.
- `ltagent digital simulate` is `skipped` (not failed)
  if `iverilog` or `vvp` is missing, unless `--strict` is
  set. In `--strict` mode the command returns
  `success=false` with `code: "DIGITAL_TOOL_MISSING"`.
- `ltagent digital synth-check` follows the same rule for
  `yosys`.
- `ltagent digital create` does not require any tool. It
  writes artefacts and runs static validation only.

## Output caps

- Captured stdout/stderr per tool is capped at 64 KiB
  per stream. The `result.json` carries the tail (last
  4 KiB) and the full length.
- Generated project files are written under
  `projects/<project_id>/` and stay within the workspace
  (`ltagent.security.safe_resolve_under`).
