"""Pending destructive contract changes."""

from __future__ import annotations

from dataclasses import dataclass

from maskhub.contract.column import ContractColumn
from maskhub.contract.version import Change, ContractVersion


@dataclass(frozen=True)
class PendingContract:
    source: str
    proposed_version: str
    base_version: str
    columns: tuple[ContractColumn, ...]
    changes: tuple[Change, ...]
    created_at: str
    proposed_by: str = "gateway"
    reason: str = "destructive contract change observed"

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "proposed_version": self.proposed_version,
            "base_version": self.base_version,
            "columns": [column.as_dict() for column in self.columns],
            "changes": [change.as_dict() for change in self.changes],
            "created_at": self.created_at,
            "proposed_by": self.proposed_by,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PendingContract | None":
        if not isinstance(payload, dict):
            return None
        source = payload.get("source")
        proposed_version = payload.get("proposed_version")
        base_version = payload.get("base_version")
        raw_columns = payload.get("columns")
        raw_changes = payload.get("changes")
        created_at = payload.get("created_at")
        proposed_by = payload.get("proposed_by", "gateway")
        reason = payload.get("reason", "destructive contract change observed")
        values = (source, proposed_version, base_version, created_at, proposed_by, reason)
        if not all(isinstance(value, str) for value in values):
            return None
        if not isinstance(raw_columns, list) or not isinstance(raw_changes, list):
            return None
        columns = tuple(
            item for item in (ContractColumn.from_dict(raw) for raw in raw_columns) if item is not None
        )
        changes = tuple(item for item in (Change.from_dict(raw) for raw in raw_changes) if item is not None)
        if len(columns) != len(raw_columns) or len(changes) != len(raw_changes):
            return None
        return cls(
            source=source,
            proposed_version=proposed_version,
            base_version=base_version,
            columns=columns,
            changes=changes,
            created_at=created_at,
            proposed_by=proposed_by,
            reason=reason,
        )

    @classmethod
    def from_version(cls, version: ContractVersion, source: str, base_version: str) -> "PendingContract":
        return cls(
            source=source,
            proposed_version=version.version,
            base_version=base_version,
            columns=version.columns,
            changes=version.changes,
            created_at=version.created_at,
            proposed_by=version.created_by,
            reason=version.reason,
        )
