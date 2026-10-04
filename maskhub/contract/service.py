"""Contract registration, schema enforcement, and replay workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from maskhub.audit.journal import Journal
from maskhub.contract.batch import BatchRecord, BatchRegistry, BatchState, ProcessingMode
from maskhub.contract.contract import Contract
from maskhub.contract.diff import FieldChange, diff_contracts, is_compatible
from maskhub.contract.registry import ContractRegistry, utc_now
from maskhub.contract.schema import ContractField, DataType, value_matches_type
from maskhub.errors import Conflict, InvalidRequest
from maskhub.source.registry import SourceRegistry


@dataclass(frozen=True)
class IngestionResult:
    batch: BatchRecord
    contract: Contract
    changes: tuple[FieldChange, ...]
    records: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "batch": self.batch.as_dict(),
            "contract": self.contract.as_dict(),
            "changes": [change.as_dict() for change in self.changes],
            "records": list(self.records),
        }


class ContractService:
    def __init__(
        self,
        sources: SourceRegistry,
        contracts: ContractRegistry,
        batches: BatchRegistry,
        journal: Journal,
    ) -> None:
        self._sources = sources
        self._contracts = contracts
        self._batches = batches
        self._journal = journal

    def current(self, source: str) -> Contract:
        return self._contracts.current(source)

    def history(self, source: str) -> list[Contract]:
        return self._contracts.history(source)

    def batches_for(self, source: str) -> list[BatchRecord]:
        return self._batches.for_source(source)

    def batch(self, batch_id: str) -> BatchRecord:
        return self._batches.get(batch_id)

    def register(
        self,
        source: str,
        fields: tuple[ContractField, ...],
        at: str | None = None,
    ) -> Contract:
        self._sources.get(source)
        contract = self._contracts.register(source, fields, at=at)
        self._journal.append("contract.register", source, f"version={contract.version}")
        return contract

    def ingest(
        self,
        source: str,
        batch_id: str,
        rows: list[dict[str, Any]],
        at: str | None = None,
    ) -> IngestionResult:
        self._sources.get(source)
        if self._batches.find(batch_id) is not None:
            raise Conflict(f"batch {batch_id} already recorded")
        active = self._contracts.current(source)
        pending = self._contracts.pending(source)
        observed = infer_observed_fields(rows, active)
        changes = diff_contracts(active.fields, observed)
        decided_at = at or utc_now()

        if not changes:
            records = project_rows(rows, active)
            batch = self._batches.save(
                BatchRecord(
                    batch_id=batch_id,
                    source=source,
                    state=BatchState.PROCESSED.value,
                    observed_version=active.version,
                    contract_version=active.version,
                    mode=ProcessingMode.EXACT.value,
                    rows_received=len(rows),
                    rows_processed=len(records),
                    raw_records=tuple(rows),
                    processed_at=decided_at,
                )
            )
            self._journal.append("contract.batch.exact", source, batch_id)
            return IngestionResult(batch, active, (), tuple(records))

        if pending is not None:
            if pending.fields == observed:
                return self._finish_legacy_batch(
                    batch_id=batch_id,
                    rows=rows,
                    active=active,
                    observed_version=pending.version,
                    changes=changes,
                    at=decided_at,
                    pending_action="contract.breaking.pending",
                )
            self._reject_pending(
                pending,
                "superseded by a newer observed schema",
                mark_held=True,
            )

        if is_compatible(changes):
            updated = self._contracts.activate_compatible(
                source,
                observed,
                reason="; ".join(item.detail or item.field for item in changes),
                at=decided_at,
            )
            records = project_rows(rows, updated)
            batch = self._batches.save(
                BatchRecord(
                    batch_id=batch_id,
                    source=source,
                    state=BatchState.PROCESSED.value,
                    observed_version=updated.version,
                    contract_version=updated.version,
                    mode=ProcessingMode.COMPATIBLE.value,
                    rows_received=len(rows),
                    rows_processed=len(records),
                    changes=tuple(change.as_dict() for change in changes),
                    detail="compatible change accepted automatically",
                    raw_records=tuple(rows),
                    processed_at=decided_at,
                )
            )
            self._journal.append("contract.compatible", source, f"version={updated.version}")
            return IngestionResult(batch, updated, tuple(changes), tuple(records))

        candidate = self._contracts.propose_breaking(
            source,
            observed,
            reason="; ".join(item.detail or item.field for item in changes),
            at=decided_at,
        )
        return self._finish_legacy_batch(
            batch_id=batch_id,
            rows=rows,
            active=active,
            observed_version=candidate.version,
            changes=changes,
            at=decided_at,
            pending_action="contract.breaking.proposed",
        )

    def approve(self, source: str, version: int, reason: str = "") -> dict[str, object]:
        contract = self._contracts.approve(source, version, reason)
        self._journal.append("contract.approve", source, f"version={version}")
        replayed: list[dict[str, object]] = []
        rejected: list[dict[str, object]] = []
        for held in self._batches.held_for(source, version):
            try:
                records = project_rows(list(held.raw_records), contract)
            except InvalidRequest as exc:
                updated = self._update_batch(
                    held,
                    state=BatchState.REJECTED,
                    detail=f"could not replay after approval: {exc}",
                    mode=ProcessingMode.LEGACY,
                )
                rejected.append(updated.as_dict())
                self._journal.append("contract.batch.reject", source, held.batch_id)
                continue
            updated = self._batches.save(
                BatchRecord(
                    batch_id=held.batch_id,
                    source=source,
                    state=BatchState.APPROVED.value,
                    observed_version=version,
                    contract_version=contract.version,
                    mode=ProcessingMode.REPLAY.value,
                    rows_received=held.rows_received,
                    rows_processed=len(records),
                    changes=held.changes,
                    detail="replayed after destructive-change approval",
                    raw_records=held.raw_records,
                    processed_at=held.processed_at,
                    decided_at=utc_now(),
                )
            )
            replayed.append(updated.as_dict())
            self._journal.append("contract.batch.replay", source, held.batch_id)
        return {"contract": contract.as_dict(), "replayed": replayed, "rejected": rejected}

    def reject(self, source: str, version: int, reason: str = "") -> dict[str, object]:
        contract = self._contracts.reject(source, version, reason)
        self._journal.append("contract.reject", source, f"version={version}")
        rejected = []
        for held in self._batches.held_for(source, version):
            updated = self._update_batch(
                held,
                state=BatchState.REJECTED,
                detail=reason or "destructive change rejected",
                mode=ProcessingMode.LEGACY,
            )
            rejected.append(updated.as_dict())
            self._journal.append("contract.batch.reject", source, held.batch_id)
        return {"contract": contract.as_dict(), "rejected": rejected}

    def _reject_pending(
        self,
        pending: Contract,
        reason: str,
        mark_held: bool,
    ) -> None:
        self._contracts.reject(pending.source, pending.version, reason)
        if mark_held:
            for held in self._batches.held_for(pending.source, pending.version):
                self._update_batch(
                    held,
                    state=BatchState.REJECTED,
                    detail=reason,
                    mode=ProcessingMode.LEGACY,
                )
                self._journal.append("contract.batch.reject", pending.source, held.batch_id)
        self._journal.append(
            "contract.reject",
            pending.source,
            f"version={pending.version};{reason}",
        )

    def _finish_legacy_batch(
        self,
        batch_id: str,
        rows: list[dict[str, Any]],
        active: Contract,
        observed_version: int,
        changes: list[FieldChange],
        at: str,
        pending_action: str,
    ) -> IngestionResult:
        try:
            records = project_rows(rows, active)
        except InvalidRequest as exc:
            batch = self._batches.save(
                BatchRecord(
                    batch_id=batch_id,
                    source=active.source,
                    state=BatchState.HELD.value,
                    observed_version=observed_version,
                    contract_version=active.version,
                    mode=ProcessingMode.LEGACY.value,
                    rows_received=len(rows),
                    rows_processed=0,
                    held_reason=str(exc),
                    changes=tuple(change.as_dict() for change in changes),
                    detail="held until destructive change is confirmed",
                    raw_records=tuple(rows),
                    processed_at=at,
                )
            )
            self._journal.append("contract.held", active.source, batch_id)
            return IngestionResult(batch, active, tuple(changes), ())

        batch = self._batches.save(
            BatchRecord(
                batch_id=batch_id,
                source=active.source,
                state=BatchState.PROCESSED.value,
                observed_version=observed_version,
                contract_version=active.version,
                mode=ProcessingMode.LEGACY.value,
                rows_received=len(rows),
                rows_processed=len(records),
                changes=tuple(change.as_dict() for change in changes),
                detail="processed under previous contract pending confirmation",
                raw_records=tuple(rows),
                processed_at=at,
            )
        )
        self._journal.append(pending_action, active.source, f"batch={batch_id};version={observed_version}")
        return IngestionResult(batch, active, tuple(changes), tuple(records))

    def _update_batch(
        self,
        held: BatchRecord,
        state: BatchState,
        detail: str,
        mode: ProcessingMode,
    ) -> BatchRecord:
        updated = BatchRecord(
            batch_id=held.batch_id,
            source=held.source,
            state=state.value,
            observed_version=held.observed_version,
            contract_version=held.contract_version,
            mode=mode.value,
            rows_received=held.rows_received,
            rows_processed=0,
            held_reason=held.held_reason,
            changes=held.changes,
            detail=detail,
            raw_records=held.raw_records,
            processed_at=held.processed_at,
            decided_at=utc_now(),
        )
        return self._batches.save(updated)


def infer_observed_fields(
    rows: list[dict[str, Any]],
    active: Contract,
) -> tuple[ContractField, ...]:
    if not rows:
        raise InvalidRequest("rows must be a non-empty list")
    names = sorted({name for row in rows for name in row})
    fields: list[ContractField] = []
    for name in names:
        values = [row[name] for row in rows if name in row]
        non_null = [value for value in values if value is not None]
        previous = active.field(name)
        if not non_null:
            if previous is None:
                raise InvalidRequest(f"cannot infer type for field {name!r}")
            fields.append(
                ContractField(
                    name=name,
                    data_type=previous.data_type,
                    required=False,
                    nullable=True,
                )
            )
            continue
        present = [name in row for row in rows]
        required = all(present)
        nullable = (not required) or any(value is None for value in values)
        fields.append(
            ContractField(
                name=name,
                data_type=infer_type(name, non_null).value,
                required=required,
                nullable=nullable,
            )
        )
    fields.extend(field for field in active.fields if field.name not in names and not field.required)
    return tuple(fields)


def infer_type(name: str, values: list[Any]) -> DataType:
    non_null = [value for value in values if value is not None]
    if not non_null:
        raise InvalidRequest(f"cannot infer type for field {name!r}")
    if all(isinstance(value, bool) for value in non_null):
        return DataType.BOOLEAN
    if all(isinstance(value, int) and not isinstance(value, bool) for value in non_null):
        return DataType.INTEGER
    if all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
        and value not in (float("inf"), float("-inf"))
        for value in non_null
    ):
        return DataType.NUMBER
    if _all_match(non_null, value_matches_type, DataType.DATETIME):
        return DataType.DATETIME
    if _all_match(non_null, value_matches_type, DataType.DATE):
        return DataType.DATE
    if all(isinstance(value, str) for value in non_null):
        return DataType.STRING
    raise InvalidRequest(f"cannot infer a consistent type for field {name!r}")


def _all_match(values: list[Any], matcher, data_type: DataType) -> bool:
    return all(matcher(value, data_type) for value in values)


def project_rows(rows: list[dict[str, Any]], contract: Contract) -> list[dict[str, Any]]:
    records = []
    for index, row in enumerate(rows):
        record: dict[str, Any] = {}
        for field in contract.fields:
            if field.name not in row:
                if field.required:
                    raise InvalidRequest(f"row {index} missing required field {field.name!r}")
                record[field.name] = None
                continue
            value = row[field.name]
            if value is None and not field.nullable:
                raise InvalidRequest(f"row {index} field {field.name!r} cannot be null")
            if value is not None and not value_matches_type(value, field.type_enum):
                raise InvalidRequest(
                    f"row {index} field {field.name!r} is not {field.data_type}"
                )
            record[field.name] = value
        records.append(record)
    return records
