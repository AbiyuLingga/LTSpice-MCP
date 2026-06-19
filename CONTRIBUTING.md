# Contributing

This repository follows the single-agent, deterministic-tool model described
in `docs/AI_HARDWARE_AGENT_ROADMAP.md`.

## Before Changing Code

1. Read `AGENTS.md` and the active milestone in
   `docs/SINGLE_AGENT_EXECUTION_PLAN.md`.
2. Keep one serialized editing owner. Do not run competing AI file editors.
3. Confirm the change closes a stated acceptance gap.
4. For new behavior, write a failing test before implementation.

## Change Rules

- Keep CLI/core logic independent from MCP.
- Generate netlists, ASC, HDL, and reports deterministically.
- Never add generic shell, Python execution, unrestricted file access, or
  unrestricted delete tools.
- Resolve paths under configured workspace roots and reject symlink escapes.
- Treat optional EDA tools honestly: pass, fail, timeout, and unavailable are
  distinct structured outcomes.
- Do not promote templates automatically or weaken tests to make a change pass.
- Keep commits focused and rollback-friendly.

## Required Checks

```bash
python -m pytest -q
python -m ruff check .
python -m mypy src
python -m build
git diff --check
```

Changes to MCP must also verify `ltagent-mcp --list-tools` and
`ltagent-mcp --list-resources`. Changes requiring LTspice or digital tools must
record tool versions and exact skip/failure reasons.

## Documentation

Label capabilities as supported, experimental, planned, or unavailable on the
current host. Do not turn mocked runner coverage into a real-simulation claim.
