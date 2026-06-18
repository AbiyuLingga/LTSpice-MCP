# Runner reference (Phase 3)

> Companion to [`runner_troubleshooting.md`](runner_troubleshooting.md).
> That page is a field guide for *what to do when the runner breaks*.
> This page is the **contract**: inputs, outputs, error codes, and the
> rules the Python runner promises to obey.

The runner is the only code path in `ltagent` that actually launches
LTspice. It is implemented in `src/ltagent/runner.py` and exposed both
as a Python API and as the `ltagent run` CLI subcommand.

## 1. CLI

```bash
ltagent run <cir> [--workdir DIR] [--timeout SECONDS] \
                  [--ltspice-arg ARG ...] [--json|--text]
```

* `<cir>` — path to the `.cir` netlist to simulate. Relative paths are
  resolved against the current working directory. The path is rejected
  if it does not exist or is not a regular file.
* `--workdir DIR` — working directory for the simulation. Default:
  the directory containing the `.cir` file. The runner writes
  `<stem>.log` and `<stem>.raw` next to the `.cir` file by default.
* `--timeout SECONDS` — wall-clock timeout. Default: `runner.timeout_seconds`
  in `config.toml` (30s). The runner enforces a floor of 5 seconds;
  values below that are clamped, not respected literally.
* `--ltspice-arg ARG` — extra argument to pass to LTspice. Repeatable.
  Common uses: `-ascii` (text raw), `-netlist` (write netlist only).

**Example:**

```bash
ltagent run projects/demo/circuit.cir --json
```

```json
{
  "success": true,
  "command": "run",
  "message": "LTspice run completed in 812ms",
  "data": {
    "mode": "wine",
    "workdir": "/home/me/projects/demo",
    "argv": ["/opt/wine-stable/bin/wine", "XVIIx64.exe", "-b", "circuit.cir"],
    "exitCode": 0,
    "durationMs": 812,
    "timeoutSeconds": 30,
    "logPath": "/home/me/projects/demo/circuit.log",
    "rawPath": null,
    "logBytes": 1024,
    "rawBytes": null
  },
  "warnings": [],
  "errors": []
}
```

## 2. Python API

```python
from pathlib import Path
from ltagent.runner import RunRequest, run_simulation

request = RunRequest(
    cir_path=Path("circuit.cir"),
    workdir=Path(".").resolve(),
    timeout_seconds=30,
    mode="wine",                 # or "native"
    executable="/home/me/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe",
    wine_command="/opt/wine-stable/bin/wine",
    extra_args=(),               # e.g. ("-ascii",)
    expected_log_name=None,     # default: <stem>.log
    require_cir_under_workdir=True,
)
result = run_simulation(request)
```

`RunResult` is a frozen dataclass with the JSON output contract:

| Field | Type | Notes |
|---|---|---|
| `success` | `bool` | `True` iff `errors` is empty. |
| `command` | `str` | Always `"run"`. |
| `message` | `str` | Short human summary. |
| `data` | `dict` | Machine-readable details (see below). |
| `warnings` | `list[dict]` | Non-fatal issues (e.g. nonzero exit). |
| `errors` | `list[dict]` | Structured failures with `code`, `detail`, `data`. |

`data` fields:

| Key | Type | Notes |
|---|---|---|
| `mode` | `"wine"` \| `"native"` | Effective mode. |
| `workdir` | `str` | Resolved working directory. |
| `argv` | `list[str]` | Exact argv passed to `subprocess.run`. |
| `exitCode` | `int \| None` | LTspice exit code. |
| `durationMs` | `int \| None` | Wall-clock duration. |
| `timeoutSeconds` | `int` | Effective timeout (after clamping). |
| `logPath` | `str \| None` | Path of the produced `.log`, or `null` if missing. |
| `rawPath` | `str \| None` | Path of the produced `.raw`, or `null`. |
| `logBytes` | `int \| None` | Size of the `.log`, or `null`. |
| `rawBytes` | `int \| None` | Size of the `.raw`, or `null`. |

## 3. Error codes

| Code | When | Recovery |
|---|---|---|
| `LTSPICE_EXECUTABLE_NOT_SET` | `ltspice.executable` is empty in `config.toml`. | Set `ltspice.executable` in `config.toml`. |
| `LTSPICE_EXECUTABLE_MISSING` | The configured executable does not exist. | Fix the path or reinstall LTspice. |
| `LTSPICE_MODE_INVALID` | `ltspice.mode` is not `"wine"` or `"native"`. | Fix the config. |
| `WINE_NOT_FOUND` | `mode="wine"` and no `wine` is on PATH or in well-known locations. | Install wine or set `ltspice.wine_command`. |
| `LTSPICE_CIR_MISSING` | The `.cir` path does not exist. | Check the path. |
| `LTSPICE_CIR_NOT_FILE` | The `.cir` path is a directory or symlink to one. | Pass a regular file. |
| `LTSPICE_LAUNCH_ERROR` | `subprocess.run` raised `FileNotFoundError` or `OSError`. | Verify argv by running it manually. |
| `LTSPICE_TIMEOUT` | The simulation did not produce a `.log` before the timeout. | See [`runner_troubleshooting.md`](runner_troubleshooting.md). |
| `LTSPICE_NO_LOG` | The subprocess exited but no `.log` was produced. | Check the working directory permissions and the netlist. |

## 4. Hard rules (verified by tests)

1. **No shell.** `subprocess.run` is called with a list, never
   `shell=True`. `build_argv` always returns a list of strings.
2. **Single binary.** The runner launches only the configured LTspice
   executable (optionally prefixed by the configured `wine` binary).
   It does not invoke any other program.
3. **Path safety.** The runner refuses to run a `.cir` that is not
   inside the configured working directory unless the caller sets
   `require_cir_under_workdir=False`.
4. **Timeout floor.** `timeout_seconds` is clamped to a minimum of 5
   seconds to avoid pathological instant-fail behavior on cold Wine
   prefixes. Higher values pass through unchanged.
5. **Structured output only.** `run_simulation` never raises. Every
   failure is encoded as `success=False` with a stable error code.
6. **Testability.** The subprocess layer is injected as
   `run_subprocess`, and the wall clock as `clock`, so unit tests can
   exercise every error path without a real LTspice install.

## 5. Integration testing

A single test, `tests/test_runner.py::test_run_simulation_real_smoke_circuit`,
is decorated with `@pytest.mark.integration`. It auto-skips when
`ltspice.executable` is not configured or `wine` cannot be resolved.
Enable it locally with:

```bash
pytest -m integration
```

It is **not** run in CI until LTspice is available in the CI
environment.
