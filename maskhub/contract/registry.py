"""Registry and version history for explicit data-source contracts."""

from __future__ import annotations

from maskhub.contract.column import ContractColumn
from maskhub.contract.pending import PendingContract
from maskhub.contract.version import (
    BREAKING,
    COMPATIBLE,
    ContractVersion,
    columns_match,
    diff_contract,
    next_version,
    severity,
    utc_now,
)
from maskhub.errors import Conflict, NotFound
from maskhub.store.kvstore import RecordStore
from maskhub.store.query import keys_with_prefix

CURRENT_PREFIX = "contract.current."
HISTORY_PREFIX = "contract.history."
PENDING_PREFIX = "contract.pending."


class ContractRegistry:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def register(
        self,
        source: str,
        columns: tuple[ContractColumn, ...],
        created_by: str,
        reason: str = "initial registration",
    ) -> ContractVersion:
        if self.has(source):
            raise Conflict(f"contract already registered for source {source}")
        version = ContractVersion(
            version="1.0.0",
            columns=columns,
            created_at=utc_now(),
            created_by=created_by,
            reason=reason,
        )
        self._put_current(source, version)
        self._put_history(source, [version])
        return version

    def has(self, source: str) -> bool:
        return self._store.has(CURRENT_PREFIX + source)

    def current(self, source: str) -> ContractVersion:
        version = ContractVersion.from_dict(self._store.get_json(CURRENT_PREFIX + source))
        if version is None:
            raise NotFound(f"no contract registered for source {source}")
        return version

    def history(self, source: str) -> list[ContractVersion]:
        raw = self._store.get_json(HISTORY_PREFIX + source)
        if not isinstance(raw, list):
            raise NotFound(f"no contract registered for source {source}")
        versions = [ContractVersion.from_dict(item) for item in raw]
        if any(item is None for item in versions):
            return []
        return [item for item in versions if item is not None]

    def get_version(self, source: str, version: str) -> ContractVersion:
        for item in self.history(source):
            if item.version == version:
                return item
        raise NotFound(f"unknown contract version {version} for source {source}")

    def propose(
        self,
        source: str,
        columns: tuple[ContractColumn, ...],
        proposed_by: str = "gateway",
        reason: str = "contract change observed",
    ) -> tuple[ContractVersion, str, PendingContract | None]:
        current = self.current(source)
        changes = diff_contract(current.columns, columns)
        if not changes:
            return current, COMPATIBLE, None
        change_severity = severity(changes)
        version = ContractVersion(
            version=next_version(current.version, change_severity),
            columns=columns,
            created_at=utc_now(),
            created_by=proposed_by,
            reason=reason,
            changes=changes,
        )
        if change_severity == COMPATIBLE:
            self._append_version(source, version)
            return version, COMPATIBLE, None
        pending = PendingContract.from_version(version, source, current.version)
        self._put_pending(source, pending)
        return version, BREAKING, pending

    def pending(self, source: str) -> PendingContract | None:
        return PendingContract.from_dict(self._store.get_json(PENDING_PREFIX + source))

    def require_pending(self, source: str) -> PendingContract:
        pending = self.pending(source)
        if pending is None:
            raise Conflict(f"no pending contract change for source {source}")
        return pending

    def approve(
        self,
        source: str,
        approved_by: str,
        columns: tuple[ContractColumn, ...] | None = None,
        reason: str = "destructive change approved",
    ) -> ContractVersion:
        pending = self.require_pending(source)
        final_columns = columns or pending.columns
        current = self.current(source)
        changes = diff_contract(current.columns, final_columns)
        if not changes or severity(changes) == COMPATIBLE:
            self._store.delete(PENDING_PREFIX + source)
            if changes:
                version = ContractVersion(
                    version=next_version(current.version, COMPATIBLE),
                    columns=final_columns,
                    created_at=utc_now(),
                    created_by=approved_by,
                    reason=reason,
                    changes=changes,
                )
                self._append_version(source, version)
                return version
            return current

        version = ContractVersion(
            version=pending.proposed_version,
            columns=final_columns,
            created_at=utc_now(),
            created_by=approved_by,
            reason=reason,
            changes=changes,
        )
        self._append_version(source, version)
        self._store.delete(PENDING_PREFIX + source)
        return version

    def reject(self, source: str, rejected_by: str, reason: str = "destructive change rejected") -> PendingContract:
        pending = self.require_pending(source)
        self._store.delete(PENDING_PREFIX + source)
        return PendingContract(
            source=pending.source,
            proposed_version=pending.proposed_version,
            base_version=pending.base_version,
            columns=pending.columns,
            changes=pending.changes,
            created_at=pending.created_at,
            proposed_by=rejected_by,
            reason=reason,
        )

    def matches_pending(self, source: str, columns: tuple[ContractColumn, ...]) -> bool:
        pending = self.pending(source)
        return pending is not None and columns_match(pending.columns, columns)

    def identifiers(self) -> list[str]:
        return [key[len(CURRENT_PREFIX) :] for key in keys_with_prefix(self._store, CURRENT_PREFIX)]

    def _put_current(self, source: str, version: ContractVersion) -> None:
        self._store.put_json(CURRENT_PREFIX + source, version.as_dict())

    def _put_history(self, source: str, versions: list[ContractVersion]) -> None:
        self._store.put_json(
            HISTORY_PREFIX + source,
            [version.as_dict() for version in versions],
        )

    def _append_version(self, source: str, version: ContractVersion) -> None:
        versions = self.history(source)
        versions.append(version)
        self._put_history(source, versions)
        self._put_current(source, version)

    def _put_pending(self, source: str, pending: PendingContract) -> None:
        self._store.put_json(PENDING_PREFIX + source, pending.as_dict())
