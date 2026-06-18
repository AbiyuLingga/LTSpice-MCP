"""Recursively normalise Python objects into JSON-friendly primitives.

The CLI and the MCP server share one JSON contract (see
``docs/SPEC.md`` section 2). Both layers have to be defensive against
payloads that include domain objects — for example
:class:`ltagent.layout.Point` instances inside a layout warning's
``data`` field. Without a normalisation step, ``json.dump`` raises
``TypeError: Object of type Point is not JSON serialisable`` and the
whole subcommand fails.

:func:`to_jsonable` walks any input and converts the values that
``json.dump`` does not understand:

* ``None``, ``bool``, ``int``, ``float``, ``str`` pass through.
* :class:`enum.Enum` (including the project's ``(str, Enum)`` members
  used for :class:`ltagent.ir.ComponentKind`,
  :class:`ltagent.ir.AnalysisKind`, :class:`ltagent.templates.TemplateStatus`)
  become their ``.value`` (or ``.name`` when there is no value).
* :class:`pathlib.Path` becomes ``str(path)``.
* dataclasses become ``asdict(...)`` recursively.
* mappings, sequences, and tuples become their JSON-native equivalents.
* anything else falls back to ``str(obj)`` so the CLI never crashes on
  an unexpected type. Callers that need stricter behaviour can inspect
  the fallback branch — the conversion is a defensive last resort, not
  a contract change.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from enum import Enum
from pathlib import Path
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Recursively normalise ``obj`` so ``json.dump`` accepts it."""
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, Enum):
        # ``str, Enum`` members carry the serialised value in ``.value``;
        # a plain ``Enum`` has no ``.value`` and we fall back to ``.name``.
        value = getattr(obj, "value", None)
        if value is not None and not isinstance(value, type(obj)):
            return value
        return obj.name
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, type):
        return obj.__name__
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Iterable):
        # Generators, custom iterables.
        return [to_jsonable(v) for v in obj]
    return str(obj)


__all__ = ["to_jsonable"]
