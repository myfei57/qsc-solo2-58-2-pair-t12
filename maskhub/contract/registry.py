"""Persistent registry of immutable contract versions."""

from __future__ import annotations

from datetime import datetime, timezone

from maskhub.contract.contract import Contract, ContractState
from maskhub.contract.schema import ContractField
from maskhub.errors import Conflict, NotFound
from maskhub.store.kvstore import RecordStore

PREFIX = "contract."


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def contract_key(source: str) -> str:
    return PREFIX + source


class ContractRegistry:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def register(
        self,
        source: str,
        fields: tuple[ContractField, ...],
        at: str | None = None,
    ) -> Contract:
        key = contract_key(source)
        if self._store.has(key):
            raise Conflict(f"contract for source {source} already registered")
        contract = Contract(
            source=source,
            version=1,
            fields=fields,
            state=ContractState.ACTIVE.value,
            registered_at=at or utc_now(),
        )
        self._store.put_json(key, _document(contract))
        return contract

    def current(self, source: str) -> Contract:
        document = self._document_or_raise(source)
        return Contract.from_dict(document["versions"][document["current_version"] - 1])  # type: ignore[return-value]

    def find(self, source: str) -> Contract | None:
        document = self._store.get_json(contract_key(source))
        if not _valid_document(document):
            return None
        return Contract.from_dict(document["versions"][document["current_version"] - 1])

    def pending(self, source: str) -> Contract | None:
        for contract in self.history(source):
            if contract.state_enum is ContractState.PENDING:
                return contract
        return None

    def history(self, source: str) -> list[Contract]:
        document = self._document_or_raise(source)
        contracts = [Contract.from_dict(item) for item in document["versions"]]
        if any(item is None for item in contracts):
            raise ValueError(f"invalid persisted contract for source {source}")
        return list(contracts)  # type: ignore[arg-type]

    def version(self, source: str, version: int) -> Contract:
        for contract in self.history(source):
            if contract.version == version:
                return contract
        raise NotFound(f"unknown contract version {source}@{version}")

    def activate_compatible(
        self,
        source: str,
        fields: tuple[ContractField, ...],
        reason: str,
        at: str | None = None,
    ) -> Contract:
        def mutate(document: object) -> tuple[object, Contract]:
            aggregate = _require_document(document)
            if _pending(aggregate) is not None:
                raise Conflict(f"source {source} has a pending destructive contract")
            previous = _current_contract(aggregate)
            new_contract = Contract(
                source=source,
                version=len(aggregate["versions"]) + 1,
                fields=fields,
                state=ContractState.ACTIVE.value,
                registered_at=at or utc_now(),
                reason=reason,
                based_on_version=previous.version,
            )
            aggregate["versions"][previous.version - 1]["state"] = ContractState.SUPERSEDED.value
            aggregate["versions"].append(new_contract.as_dict())
            aggregate["current_version"] = new_contract.version
            return aggregate, new_contract

        return self._store.update_json(contract_key(source), mutate)

    def propose_breaking(
        self,
        source: str,
        fields: tuple[ContractField, ...],
        reason: str,
        at: str | None = None,
    ) -> Contract:
        existing_pending = self.pending(source)
        if existing_pending is not None:
            if existing_pending.fields == fields:
                return existing_pending
            self._replace_pending_state(source, existing_pending.version, ContractState.REJECTED, "superseded by a newer observation")

        def mutate(document: object) -> tuple[object, Contract]:
            aggregate = _require_document(document)
            current = _current_contract(aggregate)
            pending = _pending(aggregate)
            if pending is not None:
                raise Conflict(f"source {source} has a pending destructive contract")
            new_contract = Contract(
                source=source,
                version=len(aggregate["versions"]) + 1,
                fields=fields,
                state=ContractState.PENDING.value,
                registered_at=at or utc_now(),
                reason=reason,
                based_on_version=current.version,
            )
            aggregate["versions"].append(new_contract.as_dict())
            return aggregate, new_contract

        return self._store.update_json(contract_key(source), mutate)

    def approve(self, source: str, version: int, reason: str = "", at: str | None = None) -> Contract:
        def mutate(document: object) -> tuple[object, Contract]:
            aggregate = _require_document(document)
            candidate = _version(aggregate, version)
            if candidate["state"] != ContractState.PENDING.value:
                raise Conflict(f"contract {source}@{version} is not pending")
            current = _current_contract(aggregate)
            decided_at = at or utc_now()
            aggregate["versions"][current.version - 1]["state"] = ContractState.SUPERSEDED.value
            aggregate["versions"][version - 1]["state"] = ContractState.APPROVED.value
            aggregate["versions"][version - 1]["decided_at"] = decided_at
            aggregate["versions"][version - 1]["reason"] = reason or candidate.get("reason", "")
            aggregate["current_version"] = version
            contract = Contract.from_dict(aggregate["versions"][version - 1])
            assert contract is not None
            return aggregate, contract

        return self._store.update_json(contract_key(source), mutate)

    def reject(self, source: str, version: int, reason: str = "", at: str | None = None) -> Contract:
        def mutate(document: object) -> tuple[object, Contract]:
            aggregate = _require_document(document)
            candidate = _version(aggregate, version)
            if candidate["state"] != ContractState.PENDING.value:
                raise Conflict(f"contract {source}@{version} is not pending")
            decided_at = at or utc_now()
            aggregate["versions"][version - 1]["state"] = ContractState.REJECTED.value
            aggregate["versions"][version - 1]["decided_at"] = decided_at
            aggregate["versions"][version - 1]["reason"] = reason or candidate.get("reason", "")
            contract = Contract.from_dict(aggregate["versions"][version - 1])
            assert contract is not None
            return aggregate, contract

        return self._store.update_json(contract_key(source), mutate)

    def _replace_pending_state(
        self,
        source: str,
        version: int,
        state: ContractState,
        reason: str,
    ) -> None:
        def mutate(document: object) -> tuple[object, None]:
            aggregate = _require_document(document)
            candidate = _version(aggregate, version)
            candidate["state"] = state.value
            candidate["decided_at"] = utc_now()
            candidate["reason"] = reason
            return aggregate, None

        self._store.update_json(contract_key(source), mutate)

    def _document_or_raise(self, source: str) -> dict[str, object]:
        document = self._store.get_json(contract_key(source))
        if not _valid_document(document):
            raise NotFound(f"unknown contract for source {source}")
        return document


def _document(contract: Contract) -> dict[str, object]:
    return {
        "source": contract.source,
        "current_version": contract.version,
        "versions": [contract.as_dict()],
    }


def _require_document(document: object) -> dict[str, object]:
    if not _valid_document(document):
        raise NotFound("unknown contract")
    return document


def _valid_document(document: object) -> bool:
    if not isinstance(document, dict):
        return False
    source = document.get("source")
    current = document.get("current_version")
    versions = document.get("versions")
    if not isinstance(source, str) or not isinstance(current, int) or isinstance(current, bool):
        return False
    if not isinstance(versions, list) or not versions:
        return False
    if current < 1 or current > len(versions):
        return False
    return all(isinstance(item, dict) and Contract.from_dict(item) is not None for item in versions)


def _current_contract(document: dict[str, object]) -> Contract:
    current_version = int(document["current_version"])  # type: ignore[arg-type]
    contract = Contract.from_dict(document["versions"][current_version - 1])  # type: ignore[index]
    assert contract is not None
    return contract


def _pending(document: dict[str, object]) -> dict[str, object] | None:
    for item in document["versions"]:  # type: ignore[union-attr]
        if item.get("state") == ContractState.PENDING.value:
            return item
    return None


def _version(document: dict[str, object], version: int) -> dict[str, object]:
    try:
        item = document["versions"][version - 1]  # type: ignore[index]
    except IndexError as exc:
        raise NotFound(f"unknown contract version @{version}") from exc
    if not isinstance(item, dict):
        raise ValueError("invalid contract version")
    return item
