"""Collection workflow coupling contract checks to the batch ledger."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from maskhub.audit.journal import Journal
from maskhub.contract.batch import (
    HELD,
    PROCESSED,
    REJECTED,
    REPLAYED,
    Batch,
    BatchLedger,
    new_batch_id,
)
from maskhub.contract.column import ContractColumn
from maskhub.contract.registry import ContractRegistry
from maskhub.contract.validation import RecordValidator
from maskhub.contract.version import (
    BREAKING,
    COMPATIBLE,
    columns_match,
    diff_contract,
    severity,
    utc_now,
)
from maskhub.errors import InvalidRequest
from maskhub.ingest.schema import infer_columns


class CollectionService:
    def __init__(
        self,
        contracts: ContractRegistry,
        batches: BatchLedger,
        journal: Journal,
        validator: RecordValidator | None = None,
    ) -> None:
        self._contracts = contracts
        self._batches = batches
        self._journal = journal
        self._validator = validator or RecordValidator()
        self._lock = threading.RLock()

    def collect(
        self,
        source: str,
        records: list[dict[str, Any]],
        explicit_columns: tuple[ContractColumn, ...] | None = None,
        actor: str = "gateway",
        idempotency_key: str | None = None,
    ) -> Batch:
        with self._lock:
            return self._collect_locked(
                source, records, explicit_columns, actor, idempotency_key
            )

    def approve(
        self,
        source: str,
        approved_by: str,
        columns: tuple[ContractColumn, ...] | None = None,
        reason: str = "destructive change approved",
    ) -> dict[str, object]:
        with self._lock:
            return self._approve_locked(source, approved_by, columns, reason)

    def reject(
        self,
        source: str,
        rejected_by: str,
        reason: str = "destructive change rejected",
    ) -> dict[str, object]:
        with self._lock:
            return self._reject_locked(source, rejected_by, reason)

    def replay(self, identifier: str, actor: str = "gateway") -> Batch:
        with self._lock:
            return self._replay_locked(identifier, actor)

    def _collect_locked(
        self,
        source: str,
        records: list[dict[str, Any]],
        explicit_columns: tuple[ContractColumn, ...] | None = None,
        actor: str = "gateway",
        idempotency_key: str | None = None,
    ) -> Batch:
        if not records:
            raise InvalidRequest("records must be a non-empty list")
        if idempotency_key:
            existing = self._batches.find_idempotency_key(source, idempotency_key)
            if existing is not None:
                return existing

        current = self._contracts.current(source)
        current_names = frozenset(column.name for column in current.columns)
        candidate = explicit_columns or infer_columns(records, current_names)
        changes = diff_contract(current.columns, candidate)
        change_severity = severity(changes) if changes else COMPATIBLE
        pending = self._contracts.pending(source)
        change_dicts = tuple(change.as_dict() for change in changes)
        candidate_dicts = tuple(column.as_dict() for column in candidate)

        if change_severity == BREAKING:
            return self._hold(
                source=source,
                records=records,
                current_version=current.version,
                candidate=candidate,
                candidate_dicts=candidate_dicts,
                changes=changes,
                change_dicts=change_dicts,
                pending=pending,
                actor=actor,
                idempotency_key=idempotency_key,
            )

        if pending is not None and changes:
            warnings = (
                f"compatible observation ignored while {pending.proposed_version} awaits confirmation",
            )
            return self._process(
                source=source,
                records=records,
                version=current,
                strict_extra=False,
                candidate_dicts=candidate_dicts,
                change_dicts=change_dicts,
                warnings=warnings,
                idempotency_key=idempotency_key,
            )

        version = current
        if changes:
            version, _, _ = self._contracts.propose(source, candidate, actor, "compatible contract change observed")
            self._journal.append(
                "contract.compatible",
                source,
                f"{current.version} -> {version.version}",
            )

        return self._process(
            source=source,
            records=records,
            version=version,
            strict_extra=True,
            candidate_dicts=candidate_dicts,
            change_dicts=change_dicts,
            warnings=(),
            idempotency_key=idempotency_key,
        )

    def _approve_locked(
        self,
        source: str,
        approved_by: str,
        columns: tuple[ContractColumn, ...] | None = None,
        reason: str = "destructive change approved",
    ) -> dict[str, object]:
        pending = self._contracts.require_pending(source)
        version = self._contracts.approve(source, approved_by, columns, reason)
        self._journal.append("contract.approve", source, f"{pending.base_version} -> {version.version}")
        eligible = [
            batch.identifier
            for batch in self._batches.held(source)
            if batch.proposed_version == pending.proposed_version
        ]
        return {"version": version.as_dict(), "eligible_batch_ids": eligible}

    def _reject_locked(self, source: str, rejected_by: str, reason: str = "destructive change rejected") -> dict[str, object]:
        pending = self._contracts.require_pending(source)
        rejected = self._contracts.reject(source, rejected_by, reason)
        self._journal.append("contract.reject", source, rejected.proposed_version)
        rejected_batches = []
        for batch in self._batches.held(source):
            if batch.proposed_version != pending.proposed_version:
                continue
            updated = replace(
                batch,
                status=REJECTED,
                records=(),
                errors=(reason,),
                resolved_at=utc_now(),
            )
            self._batches.save(updated)
            rejected_batches.append(batch.identifier)
            self._journal.append("batch.rejected", batch.identifier, reason)
        return {"pending": rejected.as_dict(), "rejected_batch_ids": rejected_batches}

    def _replay_locked(self, identifier: str, actor: str = "gateway") -> Batch:
        batch = self._batches.require_held(identifier)
        version = self._contracts.current(batch.source)
        errors: list[str] = []
        accepted = 0
        for index, record in enumerate(batch.records):
            record_errors = self._validator.validate(record, version.columns, strict_extra=True)
            if record_errors:
                errors.append(f"record {index}: " + "; ".join(record_errors))
            else:
                accepted += 1

        replayed_at = utc_now()
        updated = replace(
            batch,
            status=REPLAYED,
            contract_version=version.version,
            proposed_version=None,
            accepted_count=accepted,
            rejected_count=len(batch.records) - accepted,
            records=(),
            errors=tuple(errors),
            replayed_at=replayed_at,
            resolved_at=replayed_at,
        )
        self._batches.save(updated)
        self._journal.append(
            "batch.replayed",
            identifier,
            f"contract={version.version}; accepted={accepted}; rejected={len(batch.records) - accepted}",
        )
        return updated

    def _hold(
        self,
        source: str,
        records: list[dict[str, Any]],
        current_version: str,
        candidate: tuple[ContractColumn, ...],
        candidate_dicts: tuple[dict[str, object], ...],
        changes,
        change_dicts,
        pending,
        actor: str,
        idempotency_key: str | None,
    ) -> Batch:
        if pending is None:
            proposed, _, pending = self._contracts.propose(
                source,
                candidate,
                actor,
                "destructive contract change observed; awaiting confirmation",
            )
            proposed_version = proposed.version
            self._journal.append("contract.pending", source, f"{current_version} -> {proposed_version}")
        else:
            proposed_version = pending.proposed_version
            if not columns_match(pending.columns, candidate):
                self._journal.append(
                    "contract.pending.divergent",
                    source,
                    f"observation differs from pending {proposed_version}",
                )

        batch = Batch(
            identifier=new_batch_id(),
            source=source,
            status=HELD,
            contract_version=current_version,
            received_at=utc_now(),
            record_count=len(records),
            columns=candidate_dicts,
            proposed_version=proposed_version,
            severity=BREAKING,
            changes=change_dicts,
            records=tuple(records),
            warnings=("batch held; current contract remains active",),
            idempotency_key=idempotency_key,
        )
        self._batches.save(batch)
        self._journal.append("batch.held", batch.identifier, f"contract={current_version}; pending={proposed_version}")
        return batch

    def _process(
        self,
        source: str,
        records: list[dict[str, Any]],
        version,
        strict_extra: bool,
        candidate_dicts,
        change_dicts,
        warnings,
        idempotency_key: str | None,
    ) -> Batch:
        errors: list[str] = []
        accepted = 0
        for index, record in enumerate(records):
            record_errors = self._validator.validate(record, version.columns, strict_extra=strict_extra)
            if record_errors:
                errors.append(f"record {index}: " + "; ".join(record_errors))
            else:
                accepted += 1

        batch = Batch(
            identifier=new_batch_id(),
            source=source,
            status=PROCESSED,
            contract_version=version.version,
            received_at=utc_now(),
            record_count=len(records),
            accepted_count=accepted,
            rejected_count=len(records) - accepted,
            columns=candidate_dicts,
            severity=COMPATIBLE if change_dicts else None,
            changes=change_dicts,
            warnings=warnings,
            errors=tuple(errors),
            idempotency_key=idempotency_key,
        )
        self._batches.save(batch)
        self._journal.append(
            "batch.processed",
            batch.identifier,
            f"contract={version.version}; accepted={accepted}; rejected={len(records) - accepted}",
        )
        return batch
