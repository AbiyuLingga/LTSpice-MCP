"""Generate circuit_ir.schema.json from the Pydantic models.

Standalone script: run from the repo root as

    PYTHONPATH=src .venv/bin/python tools/generate_schema.py

It writes a JSON Schema 2020-12 document derived from the Pydantic
``CircuitIR`` model. The schema is written to two locations and they
must stay byte-identical:

* ``schemas/circuit_ir.schema.json`` — the public, repo-rooted copy
  consumed by external tools (IDE plugins, third-party agents).
* ``src/ltagent/resources/circuit_ir.schema.json`` — the package
  resource that ships inside the wheel and is read by the runtime
  ``ltagent ir schema`` command via :mod:`importlib.resources`.

Note: pydantic's JSON Schema export encodes its own validation, but the
*strict* rules enforced by `ir.py` field_validators (e.g. component
arity vs kind, ground node presence, measurement/analysis consistency)
are not always expressible in JSON Schema. Therefore the JSON Schema
file is a useful first-pass contract; `load_ir` remains the source of
truth and the only thing that should be trusted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running without installing the package.
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ltagent.ir import (  # noqa: E402  (import after path tweak)
    SCHEMA_VERSION,
    CircuitIR,
)


def _build_schema() -> dict:
    schema = CircuitIR.model_json_schema()
    schema["$id"] = (
        f"https://ltspice-ai-agent.local/schemas/circuit_ir.v{SCHEMA_VERSION}.schema.json"
    )
    schema["title"] = "CircuitIR"
    schema["description"] = (
        f"LTspice AI Agent Circuit IR v{SCHEMA_VERSION}. Stable contract "
        "between AI intent and generated LTspice files. Validation rules "
        "in ir.py are the source of truth; this schema is a first-pass "
        "contract only."
    )
    return schema


def _write(path: Path, schema: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    schema = _build_schema()
    rendered = json.dumps(schema, indent=2) + "\n"

    repo_path = HERE.parent / "schemas" / "circuit_ir.schema.json"
    pkg_path = SRC / "ltagent" / "resources" / "circuit_ir.schema.json"

    for path in (repo_path, pkg_path):
        _write(path, schema)

    assert (
        repo_path.read_text(encoding="utf-8")
        == pkg_path.read_text(encoding="utf-8")
    ), "repo schema and packaged resource diverged"

    print(f"wrote {repo_path} ({repo_path.stat().st_size} bytes)")
    print(f"wrote {pkg_path} ({pkg_path.stat().st_size} bytes)")
    print(f"identical: {len(rendered)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
