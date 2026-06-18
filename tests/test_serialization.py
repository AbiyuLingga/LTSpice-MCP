"""Unit tests for :mod:`ltagent.serialization`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

import pytest

from ltagent.layout import Point
from ltagent.serialization import to_jsonable


class _Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class _PlainEnum(Enum):
    """A plain ``Enum`` whose ``.value`` is an int.

    The serializer should prefer ``.value`` when it is a useful
    primitive, not ``.name``.
    """

    ONE = 1
    TWO = 2


class _NoValueEnum(Enum):
    """An Enum where ``.value`` is itself an Enum instance; the
    serializer must fall back to ``.name`` to avoid infinite
    recursion.
    """

    A = "alpha"


@dataclass(frozen=True)
class _Box:
    x: int
    y: int


def test_passes_through_primitives() -> None:
    assert to_jsonable(None) is None
    assert to_jsonable(True) is True
    assert to_jsonable(42) == 42
    assert to_jsonable(3.14) == 3.14
    assert to_jsonable("hi") == "hi"


def test_enum_str_returns_value() -> None:
    assert to_jsonable(_Color.RED) == "red"
    assert to_jsonable(_Color.BLUE) == "blue"


def test_enum_plain_returns_value() -> None:
    # The (str, Enum) form is the project convention, but a plain
    # ``Enum`` still has to round-trip safely. ``.value`` wins when
    # it is a JSON-native primitive.
    assert to_jsonable(_PlainEnum.ONE) == 1


def test_path_becomes_string() -> None:
    p = Path("/tmp/opencode/x.json")
    assert to_jsonable(p) == str(p)


def test_dataclass_is_unpacked() -> None:
    assert to_jsonable(_Box(1, 2)) == {"x": 1, "y": 2}


def test_nested_dataclass_is_unpacked() -> None:
    box = _Box(1, 2)
    nested = {"box": box, "list": [box, box]}
    out = to_jsonable(nested)
    assert out == {
        "box": {"x": 1, "y": 2},
        "list": [{"x": 1, "y": 2}, {"x": 1, "y": 2}],
    }


def test_point_in_warning_data_is_serialisable() -> None:
    """Regression: layout warnings put ``Point`` instances in their
    ``data`` dict. ``to_jsonable`` must turn them into ``{x, y}``
    objects that ``json.dump`` accepts.
    """
    warning = {
        "code": "LAYOUT_OVERLAP",
        "detail": "symbols overlap",
        "data": {
            "a": {"anchor": Point(80, 160), "size": (32, 16)},
            "b": {"anchor": Point(200, 240), "size": (32, 16)},
        },
    }
    rendered = to_jsonable(warning)
    text = json.dumps(rendered)
    parsed = json.loads(text)
    assert parsed["data"]["a"]["anchor"] == {"x": 80, "y": 160}
    assert parsed["data"]["b"]["size"] == [32, 16]


def test_mapping_and_sequence_recursion() -> None:
    src = {"k": [1, 2, {"nested": True}], "tuple": (1, 2)}
    out = to_jsonable(src)
    assert out == {"k": [1, 2, {"nested": True}], "tuple": [1, 2]}


def test_generator_is_materialised() -> None:
    out = to_jsonable(x for x in (1, 2, 3))
    assert out == [1, 2, 3]


def test_frozenset_and_set_become_list() -> None:
    assert to_jsonable(frozenset({1, 2})) == [1, 2]
    assert to_jsonable({1, 2}) == [1, 2]


def test_unknown_falls_back_to_str() -> None:
    """The serializer's contract: never raise on a domain object.
    The fallback is a stable ``str(obj)`` so the CLI never crashes
    on a missing handler.
    """

    class _Opaque:
        def __str__(self) -> str:
            return "<opaque>"

    assert to_jsonable(_Opaque()) == "<opaque>"


def test_type_object_returns_name() -> None:
    assert to_jsonable(int) == "int"
    assert to_jsonable(Path) == "Path"


@pytest.mark.parametrize(
    "value, expected",
    [
        (Point(0, 0), {"x": 0, "y": 0}),
        (Point(-10, 32), {"x": -10, "y": 32}),
    ],
)
def test_point_shape(value: Point, expected: dict) -> None:
    assert to_jsonable(value) == expected
