# Runner troubleshooting

> Companion to [`ltspice_setup.md`](ltspice_setup.md). If you are
> setting up LTspice for the first time, read that document first.

`ltagent doctor --simulate` exists to expose runner problems as
**structured** output instead of cryptic crashes. This page is a
field guide to the most common failures seen on this host.

## How to read `ltagent doctor --simulate --json`

The output has a top-level `data.checks` array. Each check has:

```json
{
  "name": "lt_spice_smoke_simulate",
  "status": "fail",
  "detail": "Timed out after 20s with no .log produced",
  "data": {
    "timeoutSeconds": 20,
    "executable": "/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe",
    "argv": ["wine", "XVIIx64.exe", "-b", "smoke.cir"]
  }
}
```

`status` is one of `ok`, `warn`, `fail`, `skip`. Anything other than
`ok` should be investigated. The `data` field is check-specific and
designed to be machine-readable — feed it to your editor / scripts.

## Common failure modes

### 1. `LTSPICE_TIMEOUT` — "No .log file was produced before timeout"

**Symptom (observed on this host):** `XVIIx64.exe -b smoke.cir` runs
past the configured timeout and never writes `smoke.log`.

**Likely causes (in order of frequency on Linux/Wine):**

1. **The Wine prefix is in a broken state.** Stale lock files, missing
   `system.reg`, or an interrupted previous `wineboot`. Reset it:

   ```bash
   mv ~/.wine ~/.wine.broken
   /opt/wine-stable/bin/wineboot --init
   ```

2. **A stale LTspice GUI process is holding the working dir.** Kill
   anything resembling `XVIIx64.exe` in `ps` / `pgrep`.

3. **The path being passed to Wine is not Wine-visible.** Wine cannot
   reach Linux paths outside the prefix in the usual case. Always use
   the `C:\...` form inside the Wine prefix, or a Linux path that
   resolves inside `drive_c/...`.

4. **`Program Files` is not being quoted in the underlying argv.**
   `ltagent` quotes all paths with spaces; if you bypassed `ltagent`
   and ran Wine directly, you may have hit this.

5. **The current LTspice differs from XVII.** The bundled Wine
   commands in the plan's runner target the legacy `-b` switch. Newer
   LTspice releases may need a different invocation. This is the
   highest-risk item in the risk table in
   [`PROJECT_PLAN.md`](PROJECT_PLAN.md) section 25.

**Next step:** check `data.argv` in the doctor's output, verify each
piece manually, and try running the same command from a terminal:

```bash
/opt/wine-stable/bin/wine \
    "/home/abiyulinx/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe" \
    -b \
    /tmp/smoke.cir
```

If that also hangs, the problem is Wine/LTspice, not `ltagent`. See
[`ltspice_setup.md`](ltspice_setup.md).

### 2. `LTSPICE_LAUNCH_ERROR` — "Failed to start LTspice"

The runner could not even start the process. Most common cause: the
`executable` path in `config.toml` does not point at a real file, or
`wine_command` is wrong.

Verify with:

```bash
ls -l "$(ltagent config show --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["ltspice"]["executable"])')"
```

### 3. `LTSPICE_NO_LOG` — "Process exited but no .log was produced"

LTspice started and exited, but the `.log` file is missing. This is
usually a working-directory problem: LTspice writes `.log` next to
the input `.cir`. The runner copies both into the project's temp
directory before running, so verify that the configured `working_dir`
exists and is writable.

### 4. `WINE_NOT_FOUND` — "No wine binary detected"

`ltagent` searched `which wine` and the well-known fallback
`/opt/wine-stable/bin/wine`, and neither was present. Install Wine
(see [`ltspice_setup.md`](ltspice_setup.md)) or set
`ltspice.wine_command` to the absolute path of your `wine` binary.

### 5. `WORKSPACE_NOT_WRITABLE`

`projects_dir` (or its parent) is not writable by the current user.
Either change the config, fix the permissions, or run as a different
user. Never `chmod 777` the workspace to "fix" this.

## Reporting a new failure mode

If you hit a `fail` that is not on this list, capture:

- The full `ltagent doctor --simulate --json` output.
- The contents of `~/.cache/ltagent/doctor.log` if present.
- The output of running the `argv` from the failed check manually.

Then open an issue with that bundle. The `argv` field is the
single most useful piece of information — without it, debugging is
guesswork.
