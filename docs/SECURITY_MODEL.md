# Security Model

The canonical threat model and implemented controls are documented in
[`security.md`](security.md). This roadmap-facing entry point adds the
single-agent operating constraints:

1. One AI decision owner and one serialized edit queue.
2. Parallelism only for bounded deterministic jobs.
3. Generated circuits, HDL, logs, models, and metadata are untrusted inputs.
4. Every external tool has allowlisted arguments, timeouts, workspace roots,
   artifact limits, and structured outcomes.
5. High-voltage, mains, high-power, medical, automotive, and other
   safety-critical requests are simulation-only or refused for build-ready use.
6. Component/model provenance must be recorded before knowledge-base content
   becomes trusted.

No roadmap milestone may weaken the controls in `security.py`, `security.md`,
or MCP contract tests.
