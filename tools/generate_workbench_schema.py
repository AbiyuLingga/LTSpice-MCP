"""Generate JSON Schema documents for the Workbench v2 contracts.

Mirrors :mod:`tools.generate_schema` (the Circuit IR generator). Run
from the repo root as::

    PYTHONPATH=src .venv/bin/python tools/generate_workbench_schema.py

Writes two byte-identical copies of each contract's JSON Schema to:

* ``schemas/workbench_v2/<name>.schema.json`` -- the public, repo-rooted
  copy consumed by external tools (IDE plugins, third-party agents).
* ``src/ltagent/resources/workbench_v2/<name>.schema.json`` -- the
  package resource that ships inside the wheel.

The byte-identical invariant is asserted at the end of the run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ltagent.workbench_v2 import (  # noqa: E402  (import after path tweak)
    AnalogGraph,
    DigitalDesignDocument,
    HardwareProject,
    Requirements,
    SchematicView,
    SystemSpec,
)

CONTRACTS: tuple[tuple[str, type], ...] = (
    ("HardwareProject", HardwareProject),
    ("Requirements", Requirements),
    ("AnalogGraph", AnalogGraph),
    ("SchematicView", SchematicView),
    ("DigitalDesignDocument", DigitalDesignDocument),
    ("SystemSpec", SystemSpec),
)


def _build_schema(name: str, model: type) -> dict:
    schema = model.model_json_schema()
    schema["$id"] = (
        f"https://ltspice-ai-agent.local/schemas/workbench_v2/{name}.schema.json"
    )
    schema["title"] = name
    schema["description"] = (
        f"Workbench v2 contract for {name}. Generated from the Pydantic "
        "model in src/ltagent/workbench_v2.py. The Python validators "
        "remain the source of truth; this JSON Schema is a first-pass "
        "machine-readable contract."
    )
    return schema


def _write(path: Path, schema: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    repo_dir = HERE.parent / "schemas" / "workbench_v2"
    pkg_dir = SRC / "ltagent" / "resources" / "workbench_v2"
    total_bytes = 0
    for name, model in CONTRACTS:
        schema = _build_schema(name, model)
        rendered = json.dumps(schema, indent=2) + "\n"
        repo_path = repo_dir / f"{name}.schema.json"
        pkg_path = pkg_dir / f"{name}.schema.json"
        for path in (repo_path, pkg_path):
            _write(path, schema)
        assert (
            repo_path.read_text(encoding="utf-8")
            == pkg_path.read_text(encoding="utf-8")
        ), f"repo and packaged schema diverged for {name}"
        total_bytes += len(rendered)
        print(f"wrote {name}: {repo_path} + {pkg_path} ({len(rendered)} bytes)")
    print(f"total: {len(CONTRACTS)} contracts, {total_bytes} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
