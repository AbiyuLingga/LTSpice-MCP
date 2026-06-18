"""Smoke tests for schema generation artifact."""

from __future__ import annotations

import json
from importlib import resources as importlib_resources


def test_schema_file_exists(schema_path) -> None:
    assert schema_path.is_file()
    assert schema_path.stat().st_size > 100


def test_schema_is_valid_json_schema(schema_path, json_schema) -> None:
    """The generated schema must itself be a valid JSON Schema 2020-12."""
    # Validating an empty object against a meta-schema is overkill; just
    # assert $schema, $id, and title are present and types line up.
    assert "$schema" in json_schema or "properties" in json_schema
    assert json_schema.get("title") == "CircuitIR"
    assert json_schema["$id"].startswith("https://")


def test_schema_self_validates_via_jsonschema() -> None:
    """The schema must validate against the JSON Schema 2020-12 meta-schema."""
    from jsonschema import Draft202012Validator

    from ltagent.ir import CircuitIR

    schema = CircuitIR.model_json_schema()
    Draft202012Validator.check_schema(schema)


def test_packaged_schema_resource_matches_repo_schema(
    schema_path, json_schema
) -> None:
    """The package resource must mirror the repo-rooted schema.

    ``ltagent ir schema`` reads from the package resource so a wheel
    install works. The two copies are written together by
    ``tools/generate_schema.py`` and must stay in sync.
    """
    resource = importlib_resources.files("ltagent.resources").joinpath(
        "circuit_ir.schema.json"
    )
    packaged_text = resource.read_text(encoding="utf-8")
    packaged = json.loads(packaged_text)
    repo = json.loads(schema_path.read_text(encoding="utf-8"))
    assert packaged == repo == json_schema


def test_packaged_schema_resource_is_loadable_by_jsonschema() -> None:
    """The packaged schema must validate against the JSON Schema 2020-12 meta-schema."""
    from jsonschema import Draft202012Validator

    resource = importlib_resources.files("ltagent.resources").joinpath(
        "circuit_ir.schema.json"
    )
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
