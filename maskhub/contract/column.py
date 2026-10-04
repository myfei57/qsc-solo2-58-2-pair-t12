"""Explicit data-source contract columns."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from maskhub.errors import InvalidRequest

KNOWN_TYPES = frozenset({"string", "integer", "number", "boolean", "date-time"})
DATE_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


@dataclass(frozen=True)
class ContractColumn:
    name: str
    type: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("column name is required")
        if self.type not in KNOWN_TYPES:
            raise ValueError(f"unknown contract type {self.type!r}")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "ContractColumn | None":
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        column_type = payload.get("type")
        required = payload.get("required", True)
        if not isinstance(name, str) or not isinstance(column_type, str):
            return None
        if not isinstance(required, bool):
            return None
        try:
            return cls(name=name, type=column_type, required=required)
        except ValueError:
            return None

    @classmethod
    def parse(cls, payload: object) -> "ContractColumn":
        column = cls.from_dict(payload)
        if column is None:
            raise InvalidRequest("each column must include name, type, and an optional required boolean")
        return column

    @classmethod
    def infer_from_values(cls, name: str, values: list[Any], observed: bool = True) -> "ContractColumn":
        present = [value for value in values if value is not None]
        inferred_type = infer_type(present)
        return cls(name=name, type=inferred_type, required=observed and len(present) == len(values))


def infer_type(values: list[Any]) -> str:
    types = {type_of(value) for value in values if value is not None}
    types.discard(None)
    if not types:
        return "string"
    if types == {"integer"}:
        return "integer"
    if types <= {"integer", "number"}:
        return "number"
    if len(types) == 1:
        return next(iter(types))
    return "variant"


def type_of(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "date-time" if DATE_TIME_RE.fullmatch(value) else "string"
    return "variant"


def value_matches(value: Any, column_type: str) -> bool:
    if value is None:
        return True
    actual = type_of(value)
    if column_type == "variant":
        return actual is not None
    if actual == column_type:
        return True
    return column_type == "number" and actual == "integer"
