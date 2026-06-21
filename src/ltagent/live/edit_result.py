"""Structured result type for live circuit edit operations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..serialization import to_jsonable


@dataclass(frozen=True)
class EditError:
    code: str
    path: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "path": self.path, "detail": self.detail}
        if self.data:
            out["data"] = to_jsonable(self.data)
        return out


@dataclass(frozen=True)
class EditWarning:
    code: str
    path: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "path": self.path, "detail": self.detail}
        if self.data:
            out["data"] = to_jsonable(self.data)
        return out


@dataclass(frozen=True)
class EditChange:
    op: str
    target: str
    before: Any
    after: Any
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "op": self.op,
            "target": self.target,
            "before": to_jsonable(self.before),
            "after": to_jsonable(self.after),
        }
        if self.data:
            out["data"] = to_jsonable(self.data)
        return out


@dataclass
class EditResult:
    graph: Any
    errors: list[EditError] = field(default_factory=list)
    warnings: list[EditWarning] = field(default_factory=list)
    changes: list[EditChange] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors

    def add_error(
        self, code: str, path: str, detail: str, data: dict[str, Any] | None = None
    ) -> None:
        self.errors.append(EditError(code=code, path=path, detail=detail, data=data or {}))

    def add_warning(
        self, code: str, path: str, detail: str, data: dict[str, Any] | None = None
    ) -> None:
        self.warnings.append(EditWarning(code=code, path=path, detail=detail, data=data or {}))

    def add_change(
        self, op: str, target: str, before: Any, after: Any, data: dict[str, Any] | None = None
    ) -> None:
        self.changes.append(
            EditChange(op=op, target=target, before=before, after=after, data=data or {})
        )

    def extend(self, other: EditResult) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.changes.extend(other.changes)

    def to_dict(self) -> dict[str, Any]:
        graph_payload = _graph_to_dict(self.graph)
        return {
            "success": self.success,
            "graph": graph_payload,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "changes": [c.to_dict() for c in self.changes],
        }

    @classmethod
    def from_graph(
        cls,
        graph: Mapping[str, Any] | Any,
        *,
        errors: Iterable[EditError] = (),
        warnings: Iterable[EditWarning] = (),
        changes: Iterable[EditChange] = (),
    ) -> EditResult:
        from .edit_ops import clone_graph

        cloned = clone_graph(graph)
        return cls(
            graph=cloned, errors=list(errors), warnings=list(warnings), changes=list(changes)
        )


def _graph_to_dict(graph: Any) -> Any:
    if graph is None:
        return None
    model_dump = getattr(graph, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json", exclude_none=True)
        except TypeError:
            return model_dump()
    return to_jsonable(graph)


__all__ = ["EditChange", "EditError", "EditResult", "EditWarning"]
