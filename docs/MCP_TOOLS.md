# MCP Tool Policy and Surface

The authoritative runtime surface is produced by:

```bash
ltagent-mcp --list-tools
ltagent-mcp --list-resources
```

The current integrated server exports 24 tools and 14 resources. The complete
table and client configuration live in `mcp_setup.md`.

## Boundary

- MCP is a stdio adapter over Python core APIs.
- MCP does not own planning, generation, simulation, or formula logic.
- Every side effect is project-scoped and path-validated.
- Inputs and outputs use stable structured contracts.

## Forbidden Surface

- generic shell or Python execution,
- generic read/write/delete,
- arbitrary network access,
- arbitrary executable or simulator arguments,
- workspace escapes or symlink traversal,
- `.raw` resource exposure,
- automatic template promotion.

New core capabilities are exposed through MCP only after their schema, CLI/API,
security, and regression gates pass.
