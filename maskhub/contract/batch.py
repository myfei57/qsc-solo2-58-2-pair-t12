"""Ledger recording the contract version used to process each batch."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from maskhub.errors import Conflict, NotFound
from maskhub.store.kvstore import RecordStore
from maskhub.store.query import keys_with_prefix

PREFIX = "contract.batch."

PROCESSED = "processed"
HELD = "held"
REPLAYED = "replayed"
REJECTED = "rejected"


@dataclass(frozen=True)
class Batch:
    identifier: str
    source: str
    status: str
    contract_version: str
    received_at: str
    record_count: int
    accepted_count: int = 0
    rejected_count: int = 0
    columns: tuple[dict[str, object], ...] = field(default_factory=tuple)
    proposed_version: str | None = None
    severity: str | None = None
    changes: tuple[dict[str, object], ...] = field(default_factory=tuple)
    records: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    idempotency_key: str | None = None
    replayed_at: str | None = None
    resolved_at: str | None = None

    def as_dict(self, include_records: bool = True) -> dict[str, object]:
        payload = asdict(self)
        if not include_records:
            payload.pop("records")
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> "Batch | None":
        if not isinstance(payload, dict):
            return None
        string_fields = ("identifier", "source", "status", "contract_version", "received_at")
        if not all(isinstance(payload.get(name), str) for name in string_fields):
            return None
        int_fields = ("record_count", "accepted_count", "rejected_count")
        if not all(isinstance(payload.get(name, 0), int) for name in int_fields):
            return None
        tuple_fields = ("columns", "changes", "records", "warnings", "errors")
        for name in tuple_fields:
            value = payload.get(name, [])
            if not isinstance(value, list):
                return None
        nullable_strings = ("proposed_version", "severity", "idempotency_key", "replayed_at", "resolved_at")
        for name in nullable_strings:
            value = payload.get(name)
            if value is not None and not isinstance(value, str):
                return None
        return cls(
            identifier=payload["identifier"],
            source=payload["source"],
            status=payload["status"],
            contract_version=payload["contract_version"],
            received_at=payload["received_at"],
            record_count=payload["record_count"],
            accepted_count=payload.get("accepted_count", 0),
            rejected_count=payload.get("rejected_count", 0),
            columns=tuple(payload.get("columns", [])),
            proposed_version=payload.get("proposed_version"),
            severity=payload.get("severity"),
            changes=tuple(payload.get("changes", [])),
            records=tuple(payload.get("records", [])),
            warnings=tuple(payload.get("warnings", [])),
            errors=tuple(payload.get("errors", [])),
            idempotency_key=payload.get("idempotency_key"),
            replayed_at=payload.get("replayed_at"),
            resolved_at=payload.get("resolved_at"),
        )


def batch_key(identifier: str) -> str:
    return PREFIX + identifier


def new_batch_id() -> str:
    return uuid4().hex[:16]


class BatchLedger:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def save(self, batch: Batch) -> Batch:
        self._store.put_json(batch_key(batch.identifier), batch.as_dict())
        return batch

    def get(self, identifier: str) -> Batch:
        batch = Batch.from_dict(self._store.get_json(batch_key(identifier)))
        if batch is None:
            raise NotFound(f"unknown contract batch {identifier}")
        return batch

    def find_idempotency_key(self, source: str, key: str) -> Batch | None:
        for batch in self.for_source(source):
            if batch.idempotency_key == key:
                return batch
        return None

    def all(self) -> list[Batch]:
        batches = [
            Batch.from_dict(self._store.get_json(PREFIX + key[len(PREFIX) :]))
            for key in keys_with_prefix(self._store, PREFIX)
        ]
        return [batch for batch in batches if batch is not None]

    def for_source(self, source: str) -> list[Batch]:
        return [batch for batch in self.all() if batch.source == source]

    def held(self, source: str | None = None) -> list[Batch]:
        return [batch for batch in self.all() if batch.status == HELD and (source is None or batch.source == source)]

    def require_held(self, identifier: str) -> Batch:
        batch = self.get(identifier)
        if batch.status != HELD:
            raise Conflict(f"batch {identifier} is {batch.status}, not held")
        return batch
