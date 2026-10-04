"""Validate collected records against a frozen contract version."""

from __future__ import annotations

from typing import Any

from maskhub.contract.column import ContractColumn, value_matches


class RecordValidator:
    def validate(
        self,
        record: dict[str, Any],
        columns: tuple[ContractColumn, ...],
        strict_extra: bool = True,
    ) -> list[str]:
        errors: list[str] = []
        for column in columns:
            if column.name not in record:
                if column.required:
                    errors.append(f"missing required column {column.name}")
                continue
            value = record[column.name]
            if value is None and column.required:
                errors.append(f"column {column.name} must not be null")
                continue
            if not value_matches(value, column.type):
                errors.append(f"column {column.name} expected {column.type}")

        known = {column.name for column in columns}
        extras = sorted(name for name in record if name not in known)
        if strict_extra:
            errors.extend(f"unexpected column {name}" for name in extras)
        return errors
