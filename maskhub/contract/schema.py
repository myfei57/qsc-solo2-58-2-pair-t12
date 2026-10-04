"""Explicit column-level contract schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class DataType(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    DATE = "date"
    DATETIME = "datetime"

    @classmethod
    def coerce(cls, value: str) -> "DataType":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"unsupported data type {value!r}") from exc


# Widening is the only automatic type compatibility: integer values can be
# represented as numbers without losing information.
SAFE_WIDENINGS: frozenset[tuple[DataType, DataType]] = frozenset(
    {(DataType.INTEGER, DataType.NUMBER)}
)


@dataclass(frozen=True)
class ContractField:
    name: str
    data_type: str
    required: bool = True
    nullable: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("field name is required")
        DataType.coerce(self.data_type)
        if not self.required and not self.nullable:
            raise ValueError("optional field must be nullable")

    @property
    def type_enum(self) -> DataType:
        return DataType(self.data_type)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "required": self.required,
            "nullable": self.nullable,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ContractField | None":
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        data_type = payload.get("data_type")
        required = payload.get("required", True)
        nullable = payload.get("nullable", False)
        if not isinstance(name, str) or not isinstance(data_type, str):
            return None
        if not isinstance(required, bool) or not isinstance(nullable, bool):
            return None
        try:
            return cls(name=name, data_type=data_type, required=required, nullable=nullable)
        except ValueError:
            return None


def value_matches_type(value: Any, data_type: DataType) -> bool:
    """Match transport-level values without performing an implicit coercion."""

    if value is None:
        return True
    if data_type is DataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type is DataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type is DataType.NUMBER:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == value
            and value not in (float("inf"), float("-inf"))
        )
    if data_type is DataType.STRING:
        return isinstance(value, str)
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        if data_type is DataType.DATE:
            parsed = date.fromisoformat(value)
            return parsed.isoformat() == value
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False
