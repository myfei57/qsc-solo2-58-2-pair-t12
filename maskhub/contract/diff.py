"""Detect and classify changes between two contract field sets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from maskhub.contract.contract import ContractField
from maskhub.contract.schema import SAFE_WIDENINGS, DataType


class ChangeSeverity(str, Enum):
    COMPATIBLE = "compatible"
    BREAKING = "breaking"


@dataclass(frozen=True)
class FieldChange:
    kind: str
    field: str
    severity: str
    detail: str = ""
    before: str | bool | None = None
    after: str | bool | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _compatible(kind: str, field: str, detail: str = "") -> FieldChange:
    return FieldChange(kind=kind, field=field, severity=ChangeSeverity.COMPATIBLE.value, detail=detail)


def _breaking(kind: str, field: str, detail: str = "") -> FieldChange:
    return FieldChange(kind=kind, field=field, severity=ChangeSeverity.BREAKING.value, detail=detail)


def diff_contracts(
    old_fields: tuple[ContractField, ...],
    new_fields: tuple[ContractField, ...],
) -> list[FieldChange]:
    old = {field.name: field for field in old_fields}
    new = {field.name: field for field in new_fields}
    changes: list[FieldChange] = []

    for name in sorted(set(new) - set(old)):
        field = new[name]
        if field.required:
            changes.append(_breaking("field.added", name, "new field is required"))
        else:
            changes.append(_compatible("field.added", name, "optional field can be absent upstream"))

    for name in sorted(set(old) - set(new)):
        field = old[name]
        if field.required:
            changes.append(_breaking("field.removed", name, "required field disappeared"))
        else:
            changes.append(_compatible("field.removed", name, "optional field disappeared"))

    for name in sorted(set(old) & set(new)):
        before = old[name]
        after = new[name]
        old_type = before.type_enum
        new_type = after.type_enum
        if old_type != new_type:
            if (old_type, new_type) in SAFE_WIDENINGS:
                changes.append(
                    _compatible(
                        "type.changed",
                        name,
                        f"{old_type.value} safely widens to {new_type.value}",
                    )
                )
            else:
                changes.append(
                    _breaking(
                        "type.changed",
                        name,
                        f"{old_type.value} cannot be used as {new_type.value}",
                    )
                )
        if before.required and not after.required:
            changes.append(_compatible("required.relaxed", name, "field is now optional"))
        elif not before.required and after.required:
            changes.append(_breaking("required.tightened", name, "optional field is now required"))
        if before.nullable and not after.nullable:
            changes.append(_breaking("nullable.tightened", name, "null values are no longer allowed"))
        elif not before.nullable and after.nullable:
            changes.append(_compatible("nullable.relaxed", name, "null values are now allowed"))

    return changes


def is_compatible(changes: list[FieldChange]) -> bool:
    return all(item.severity == ChangeSeverity.COMPATIBLE.value for item in changes)


def field_index(fields: tuple[ContractField, ...]) -> dict[str, ContractField]:
    return {field.name: field for field in fields}


def safe_widening(before: DataType, after: DataType) -> bool:
    return (before, after) in SAFE_WIDENINGS
