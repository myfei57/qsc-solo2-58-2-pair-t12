"""Immutable versions in a data-source contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from maskhub.contract.column import ContractColumn

COMPATIBLE = "compatible"
BREAKING = "breaking"


@dataclass(frozen=True)
class Change:
    kind: str
    column: str
    compatible: bool
    detail: str = ""
    from_type: str | None = None
    to_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "Change | None":
        if not isinstance(payload, dict):
            return None
        kind = payload.get("kind")
        column = payload.get("column")
        compatible = payload.get("compatible")
        detail = payload.get("detail", "")
        from_type = payload.get("from_type")
        to_type = payload.get("to_type")
        if not isinstance(kind, str) or not isinstance(column, str):
            return None
        if not isinstance(compatible, bool) or not isinstance(detail, str):
            return None
        if from_type is not None and not isinstance(from_type, str):
            return None
        if to_type is not None and not isinstance(to_type, str):
            return None
        return cls(
            kind=kind,
            column=column,
            compatible=compatible,
            detail=detail,
            from_type=from_type,
            to_type=to_type,
        )


@dataclass(frozen=True)
class ContractVersion:
    version: str
    columns: tuple[ContractColumn, ...]
    created_at: str
    created_by: str = "gateway"
    reason: str = ""
    changes: tuple[Change, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("contract version is required")
        if not self.columns:
            raise ValueError("contract version must declare at least one column")
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("contract contains duplicate columns")

    def column(self, name: str) -> ContractColumn | None:
        for item in self.columns:
            if item.name == name:
                return item
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "columns": [column.as_dict() for column in self.columns],
            "created_at": self.created_at,
            "created_by": self.created_by,
            "reason": self.reason,
            "changes": [change.as_dict() for change in self.changes],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ContractVersion | None":
        if not isinstance(payload, dict):
            return None
        version = payload.get("version")
        raw_columns = payload.get("columns")
        created_at = payload.get("created_at")
        created_by = payload.get("created_by", "gateway")
        reason = payload.get("reason", "")
        raw_changes = payload.get("changes", [])
        if not all(isinstance(value, str) for value in (version, created_at, created_by, reason)):
            return None
        if not isinstance(raw_columns, list) or not isinstance(raw_changes, list):
            return None
        columns = tuple(
            item for item in (ContractColumn.from_dict(raw) for raw in raw_columns) if item is not None
        )
        changes = tuple(item for item in (Change.from_dict(raw) for raw in raw_changes) if item is not None)
        if len(columns) != len(raw_columns) or len(changes) != len(raw_changes):
            return None
        try:
            return cls(
                version=version,
                columns=columns,
                created_at=created_at,
                created_by=created_by,
                reason=reason,
                changes=changes,
            )
        except ValueError:
            return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def next_version(current: str | None, severity: str) -> str:
    if current is None:
        return "1.0.0"
    major, minor, patch = (int(part) for part in current.split("."))
    if severity == BREAKING:
        return f"{major + 1}.0.0"
    return f"{major}.{minor + 1}.0"


def diff_contract(
    current: tuple[ContractColumn, ...], candidate: tuple[ContractColumn, ...]
) -> tuple[Change, ...]:
    changes: list[Change] = []
    current_by_name = {column.name: column for column in current}
    candidate_by_name = {column.name: column for column in candidate}

    for column in candidate:
        existing = current_by_name.get(column.name)
        if existing is None:
            changes.append(
                Change(
                    kind="column_added",
                    column=column.name,
                    compatible=not column.required,
                    detail="optional column added" if not column.required else "required column added",
                    to_type=column.type,
                )
            )
            continue
        if existing.type != column.type:
            compatible = _type_is_widened(existing.type, column.type)
            changes.append(
                Change(
                    kind="type_changed",
                    column=column.name,
                    compatible=compatible,
                    detail=f"{existing.type} -> {column.type}",
                    from_type=existing.type,
                    to_type=column.type,
                )
            )
        elif not existing.required and column.required:
            changes.append(
                Change(
                    kind="required_tightened",
                    column=column.name,
                    compatible=False,
                    detail="optional -> required",
                    from_type=existing.type,
                    to_type=column.type,
                )
            )
        elif existing.required and not column.required:
            changes.append(
                Change(
                    kind="required_relaxed",
                    column=column.name,
                    compatible=True,
                    detail="required -> optional",
                    from_type=existing.type,
                    to_type=column.type,
                )
            )

    for column in current:
        if column.name not in candidate_by_name:
            changes.append(
                Change(
                    kind="column_removed",
                    column=column.name,
                    compatible=False,
                    detail="column removed",
                    from_type=column.type,
                )
            )
    return tuple(changes)


def _type_is_widened(old: str, new: str) -> bool:
    return (old, new) in {
        ("integer", "number"),
        ("date-time", "string"),
    }


def severity(changes: tuple[Change, ...]) -> str:
    return COMPATIBLE if all(change.compatible for change in changes) else BREAKING


def columns_match(
    left: tuple[ContractColumn, ...], right: tuple[ContractColumn, ...]
) -> bool:
    return [column.as_dict() for column in left] == [column.as_dict() for column in right]
