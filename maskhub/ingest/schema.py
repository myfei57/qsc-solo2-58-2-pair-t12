"""Infer candidate contract columns from a collected batch."""

from __future__ import annotations

from typing import Any

from maskhub.contract.column import ContractColumn


def infer_columns(
    records: list[dict[str, Any]], known_names: frozenset[str] | None = None
) -> tuple[ContractColumn, ...]:
    known = known_names or frozenset()
    names = sorted({name for record in records for name in record})
    return tuple(
        ContractColumn.infer_from_values(
            name,
            [record.get(name) for record in records],
            observed=name in known,
        )
        for name in names
    )
