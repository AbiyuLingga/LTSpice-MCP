"""Generate circuit_ir.schema.json from the Pydantic models.

Standalone script: run from the phase-1 directory as

    PYTHONPATH=src .venv/bin/python tools/generate_schema.py

It writes a JSON Schema 2020-12 document derived from the Pydantic
``CircuitIR`` model. This is what other tools (IDE plugins, third-party
agents) can consume without depending on Python or pydantic.

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


def main() -> int:
    schema = CircuitIR.model_json_schema()
    # Pydantic v2 wraps with $defs and a top-level title. Add a stable
    # $id and document the contract clearly.
    schema["$id"] = f"https://ltspice-ai-agent.local/schemas/circuit_ir.v{SCHEMA_VERSION}.schema.json"
    schema["title"] = "CircuitIR"
    schema["description"] = (
        "LTspice AI Agent Circuit IR v"
        f"{SCHEMA_VERSION}. Stable contract between AI intent and "
        "generated LTspice files. Validation rules in ir.py are the "
        "source of truth; this schema is a first-pass contract only."
    )

    out_path = HERE.parent / "schemas" / "circuit_ir.schema.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
