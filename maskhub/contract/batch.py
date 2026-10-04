"""Records describing how each observed batch was handled."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from maskhub.store.kvstore import RecordStore

BATCH_PREFIX = "contract.batch."


class BatchState(str, Enum):
    PROCESSED = "processed"
    HELD = "held"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProcessingMode(str, Enum):
    EXACT = "exact"
    COMPATIBLE = "compatible"
    LEGACY = "legacy"
    REPLAY = "replay"


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    source: str
    state: str
    observed_version: int
    contract_version: int
    mode: str
    rows_received: int
    rows_processed: int
    held_reason: str = ""
    changes: tuple[dict[str, object], ...] = ()
    detail: str = ""
    raw_records: tuple[dict[str, Any], ...] = ()
    processed_at: str = ""
    decided_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "source": self.source,
            "state": self.state,
            "observed_version": self.observed_version,
            "contract_version": self.contract_version,
            "mode": self.mode,
            "rows_received": self.rows_received,
            "rows_processed": self.rows_processed,
            "held_reason": self.held_reason,
            "changes": list(self.changes),
            "detail": self.detail,
            "raw_records": list(self.raw_records),
            "processed_at": self.processed_at,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "BatchRecord | None":
        if not isinstance(payload, dict):
            return None
        batch_id = payload.get("batch_id")
        source = payload.get("source")
        state = payload.get("state")
        observed_version = payload.get("observed_version")
        contract_version = payload.get("contract_version")
        mode = payload.get("mode")
        rows_received = payload.get("rows_received")
        rows_processed = payload.get("rows_processed")
        held_reason = payload.get("held_reason", "")
        changes = payload.get("changes", [])
        detail = payload.get("detail", "")
        raw_records = payload.get("raw_records", [])
        processed_at = payload.get("processed_at", "")
        decided_at = payload.get("decided_at", "")
        strings = (batch_id, source, state, mode, held_reason, detail, processed_at, decided_at)
        if not all(isinstance(item, str) for item in strings):
            return None
        ints = (observed_version, contract_version, rows_received, rows_processed)
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in ints):
            return None
        if not isinstance(changes, list) or not all(isinstance(item, dict) for item in changes):
            return None
        if not isinstance(raw_records, list) or not all(isinstance(item, dict) for item in raw_records):
            return None
        return cls(
            batch_id=batch_id,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            observed_version=observed_version,  # type: ignore[arg-type]
            contract_version=contract_version,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            rows_received=rows_received,  # type: ignore[arg-type]
            rows_processed=rows_processed,  # type: ignore[arg-type]
            held_reason=held_reason,  # type: ignore[arg-type]
            changes=tuple(changes),
            detail=detail,  # type: ignore[arg-type]
            raw_records=tuple(raw_records),
            processed_at=processed_at,  # type: ignore[arg-type]
            decided_at=decided_at,  # type: ignore[arg-type]
        )


class BatchRegistry:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def save(self, record: BatchRecord) -> BatchRecord:
        self._store.put_json(BATCH_PREFIX + record.batch_id, record.as_dict())
        return record

    def get(self, batch_id: str) -> BatchRecord:
        record = self.find(batch_id)
        if record is None:
            from maskhub.errors import NotFound

            raise NotFound(f"unknown contract batch {batch_id}")
        return record

    def find(self, batch_id: str) -> BatchRecord | None:
        return BatchRecord.from_dict(self._store.get_json(BATCH_PREFIX + batch_id))

    def for_source(self, source: str) -> list[BatchRecord]:
        from maskhub.store.query import keys_with_prefix

        records = []
        for key in keys_with_prefix(self._store, BATCH_PREFIX):
            record = BatchRecord.from_dict(self._store.get_json(key))
            if record is not None and record.source == source:
                records.append(record)
        return records

    def held_for(self, source: str, observed_version: int) -> list[BatchRecord]:
        return [
            record
            for record in self.for_source(source)
            if record.state == BatchState.HELD.value
            and record.observed_version == observed_version
        ]
