from __future__ import annotations

import tempfile
import unittest

from maskhub.audit.journal import Journal
from maskhub.contract.batch import HELD, PROCESSED, REPLAYED
from maskhub.contract.column import ContractColumn
from maskhub.contract.registry import ContractRegistry
from maskhub.contract.batch import BatchLedger
from maskhub.ingest.service import CollectionService
from maskhub.store.kvstore import RecordStore


class ContractCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = RecordStore(self.temp.name)
        journal = Journal(store)
        self.contracts = ContractRegistry(store)
        self.batches = BatchLedger(store)
        self.service = CollectionService(self.contracts, self.batches, journal)
        self.contracts.register(
            "users",
            (
                ContractColumn("name", "string"),
                ContractColumn("age", "integer"),
            ),
            "tester",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_initial_batch_records_contract_version(self) -> None:
        batch = self.service.collect("users", [{"name": "Ada", "age": 36}])
        self.assertEqual(batch.status, PROCESSED)
        self.assertEqual(batch.contract_version, "1.0.0")
        self.assertEqual(batch.accepted_count, 1)

    def test_compatible_change_is_auto_versioned_and_recorded(self) -> None:
        batch = self.service.collect(
            "users",
            [
                {"name": "Ada", "age": 36, "nickname": "A"},
                {"name": "Grace", "age": 42, "nickname": None},
            ],
        )
        self.assertEqual(batch.status, PROCESSED)
        self.assertEqual(batch.contract_version, "1.1.0")
        self.assertEqual(self.contracts.current("users").version, "1.1.0")
        self.assertEqual([item.version for item in self.contracts.history("users")], ["1.0.0", "1.1.0"])
        self.assertEqual(batch.changes[0]["kind"], "column_added")
        self.assertTrue(batch.changes[0]["compatible"])

    def test_breaking_change_is_held_without_activating_contract(self) -> None:
        breaking = self.service.collect("users", [{"name": "Ada"}])
        self.assertEqual(breaking.status, HELD)
        self.assertEqual(breaking.contract_version, "1.0.0")
        self.assertEqual(breaking.proposed_version, "2.0.0")
        stored = self.batches.get(breaking.identifier)
        self.assertEqual(len(stored.records), 1)
        self.assertEqual(self.contracts.current("users").version, "1.0.0")

        old_shape = self.service.collect("users", [{"name": "Grace", "age": 42}])
        self.assertEqual(old_shape.status, PROCESSED)
        self.assertEqual(old_shape.contract_version, "1.0.0")

    def test_approval_then_replay_uses_confirmed_version(self) -> None:
        held = self.service.collect("users", [{"name": "Ada"}])
        result = self.service.approve("users", "owner")
        self.assertEqual(result["version"]["version"], "2.0.0")
        replayed = self.service.replay(held.identifier, "owner")
        self.assertEqual(replayed.status, REPLAYED)
        self.assertEqual(replayed.contract_version, "2.0.0")
        self.assertEqual(replayed.records, ())

    def test_rejection_retains_old_contract_for_new_batches(self) -> None:
        held = self.service.collect("users", [{"name": "Ada"}])
        result = self.service.reject("users", "owner")
        self.assertIn(held.identifier, result["rejected_batch_ids"])
        self.assertEqual(self.contracts.current("users").version, "1.0.0")
        self.assertIsNone(self.contracts.pending("users"))
        self.assertEqual(self.batches.get(held.identifier).status, "rejected")

        batch = self.service.collect("users", [{"name": "Grace", "age": 42}])
        self.assertEqual(batch.status, PROCESSED)
        self.assertEqual(batch.contract_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
