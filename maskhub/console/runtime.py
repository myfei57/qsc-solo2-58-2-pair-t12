"""Composition root for the gateway."""

from __future__ import annotations

from dataclasses import dataclass

from maskhub.audit.journal import Journal
from maskhub.catalog.dataset import DatasetRegistry
from maskhub.contract.batch import BatchRegistry
from maskhub.contract.registry import ContractRegistry
from maskhub.contract.service import ContractService
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
    contract_versions: ContractRegistry
    contract_batches: BatchRegistry
    contracts_service: ContractService
    rules: RuleRegistry
    policies: PolicyRegistry


def build_runtime(store: RecordStore) -> Runtime:
    journal = Journal(store)
    sources = SourceRegistry(store)
    datasets = DatasetRegistry(store)
    contract_versions = ContractRegistry(store)
    contract_batches = BatchRegistry(store)
    contracts_service = ContractService(
        sources=sources,
        contracts=contract_versions,
        batches=contract_batches,
        journal=journal,
    )
    return Runtime(
        store=store,
        journal=journal,
        sources=sources,
        datasets=datasets,
        contract_versions=contract_versions,
        contract_batches=contract_batches,
        contracts_service=contracts_service,
        rules=RuleRegistry(store),
        policies=PolicyRegistry(store),
    )
