"""Composition root for the gateway."""

from __future__ import annotations

from dataclasses import dataclass

from maskhub.audit.journal import Journal
from maskhub.catalog.dataset import DatasetRegistry
from maskhub.contract.batch import BatchLedger
from maskhub.contract.registry import ContractRegistry
from maskhub.ingest.service import CollectionService
from maskhub.policy.registry import PolicyRegistry
from maskhub.rule.registry import RuleRegistry
from maskhub.source.registry import SourceRegistry
from maskhub.store.kvstore import RecordStore


@dataclass
class Runtime:
    store: RecordStore
    journal: Journal
    sources: SourceRegistry
    datasets: DatasetRegistry
    contracts: ContractRegistry
    batches: BatchLedger
    collection: CollectionService
    rules: RuleRegistry
    policies: PolicyRegistry


def build_runtime(store: RecordStore) -> Runtime:
    journal = Journal(store)
    sources = SourceRegistry(store)
    datasets = DatasetRegistry(store)
    contracts = ContractRegistry(store)
    batches = BatchLedger(store)
    return Runtime(
        store=store,
        journal=journal,
        sources=sources,
        datasets=datasets,
        contracts=contracts,
        batches=batches,
        collection=CollectionService(contracts, batches, journal),
        rules=RuleRegistry(store),
        policies=PolicyRegistry(store),
    )
