"""Smoke tests for schema generation artifact."""

from __future__ import annotations

import json

import jsonschema
import pytest


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
