# Analog Engine

## Current Supported Foundation

- Validated `CircuitIR` with allowlisted analyses/directives.
- Deterministic SPICE netlist and LTspice ASC generation.
- Unit parsing, formula calculations, E-series selection, and reports.
- Bounded LTspice native/Wine runner with structured timeout/failure states.
- Log and measurement parsing, layout checks, and template promotion gates.

## Data Flow

```text
prompt or CircuitIR
-> deterministic planner/validator
-> Math Core sizing
-> netlist
-> optional LTspice run
-> measurements and verification
-> deterministic ASC
-> project report/template candidate
```

ASC coordinates are always produced by deterministic topology placers. An AI
may select topology and values but may not free-write production coordinates.

## Current Limits

- Real LTspice batch execution may time out on this host.
- The roadmap's 20-case real-simulation matrix is not yet proven.
- E-series selection and formulas exist, but a general bounded parameter sweep
  and ranking engine does not.
- Power-electronics output remains simulation-oriented and not build-ready.

The next acceptance work is Milestones 1-3 in
`SINGLE_AGENT_EXECUTION_PLAN.md`.
