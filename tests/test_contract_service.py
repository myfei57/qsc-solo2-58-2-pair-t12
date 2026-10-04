import tempfile
import unittest

from maskhub.audit.journal import Journal
from maskhub.contract.batch import BatchRegistry, BatchState, ProcessingMode
from maskhub.contract.registry import ContractRegistry
from maskhub.contract.schema import ContractField
from maskhub.contract.service import ContractService
from maskhub.errors import Conflict, InvalidRequest
from maskhub.source.registry import Source, SourceRegistry, SourceStatus
from maskhub.store.kvstore import RecordStore


class ContractServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RecordStore(self.temp.name)
        self.sources = SourceRegistry(self.store)
        self.contracts = ContractRegistry(self.store)
        self.batches = BatchRegistry(self.store)
        self.journal = Journal(self.store)
        self.service = ContractService(
            self.sources,
            self.contracts,
            self.batches,
            self.journal,
        )
        self.sources.register(Source("s1", "api", "http://example"))
        self.sources.set_status("s1", SourceStatus.ACTIVE)
        self.service.register(
            "s1",
            (
                ContractField("id", "integer"),
                ContractField("name", "string"),
                ContractField("score", "integer", required=False, nullable=True),
            ),
            at="2026-01-01T00:00:00+00:00",
        )

    def test_exact_batch_records_active_contract_version(self) -> None:
        result = self.service.ingest(
            "s1", "b1", [{"id": 1, "name": "a", "score": None}], at="2026-01-02T00:00:00+00:00"
        )

        self.assertEqual(BatchState.PROCESSED.value, result.batch.state)
        self.assertEqual(ProcessingMode.EXACT.value, result.batch.mode)
        self.assertEqual(1, result.batch.contract_version)
        self.assertEqual((), result.changes)

    def test_compatible_change_is_activated_and_audited(self) -> None:
        result = self.service.ingest(
            "s1",
            "b2",
            [
                {"id": 1, "name": "a", "nickname": "a", "score": 1.5},
                {"id": 2, "name": "b"},
            ],
            at="2026-01-03T00:00:00+00:00",
        )

        self.assertEqual(2, self.contracts.current("s1").version)
        self.assertEqual(BatchState.PROCESSED.value, result.batch.state)
        self.assertEqual(ProcessingMode.COMPATIBLE.value, result.batch.mode)
        self.assertEqual(2, result.batch.contract_version)
        actions = [entry.action for entry in self.journal.entries()]
        self.assertIn("contract.compatible", actions)

    def test_breaking_change_runs_old_contract_and_holds_non_conforming_batch(self) -> None:
        result = self.service.ingest(
            "s1",
            "b3",
            [{"id": "one", "label": "a"}],
            at="2026-01-04T00:00:00+00:00",
        )

        self.assertEqual(BatchState.HELD.value, result.batch.state)
        self.assertEqual(1, result.batch.contract_version)
        self.assertEqual(2, result.batch.observed_version)
        self.assertEqual(ProcessingMode.LEGACY.value, result.batch.mode)
        self.assertEqual(1, self.contracts.current("s1").version)
        self.assertEqual(2, self.contracts.pending("s1").version)

    def test_breaking_change_can_still_process_rows_matching_old_contract(self) -> None:
        result = self.service.ingest(
            "s1",
            "b4",
            [{"id": 1, "name": "a", "score": 1}],
            at="2026-01-05T00:00:00+00:00",
        )

        self.assertEqual(BatchState.PROCESSED.value, result.batch.state)
        self.assertEqual(ProcessingMode.LEGACY.value, result.batch.mode)
        self.assertEqual(1, result.batch.contract_version)
        self.assertEqual(({"id": 1, "name": "a", "score": 1},), result.records)

    def test_approval_replays_held_batch_with_new_contract(self) -> None:
        self.service.ingest(
            "s1",
            "b5",
            [{"id": "one", "label": "a"}],
            at="2026-01-06T00:00:00+00:00",
        )
        decision = self.service.approve("s1", 2, "confirmed rename and type change")
        batch = self.batches.get("b5")

        self.assertEqual(BatchState.APPROVED.value, batch.state)
        self.assertEqual(2, batch.contract_version)
        self.assertEqual(ProcessingMode.REPLAY.value, batch.mode)
        self.assertEqual(1, batch.rows_processed)
        self.assertEqual(2, self.contracts.current("s1").version)
        self.assertEqual(1, len(decision["replayed"]))

    def test_rejection_keeps_active_contract_and_retains_held_raw_batch(self) -> None:
        self.service.ingest(
            "s1",
            "b6",
            [{"id": "one", "label": "a"}],
            at="2026-01-07T00:00:00+00:00",
        )
        self.service.reject("s1", 2, "upstream bug")
        batch = self.batches.get("b6")

        self.assertEqual(BatchState.REJECTED.value, batch.state)
        self.assertEqual(1, self.contracts.current("s1").version)
        self.assertEqual(({"id": "one", "label": "a"},), batch.raw_records)
        self.assertIsNone(self.contracts.pending("s1"))

    def test_batch_id_is_idempotent(self) -> None:
        rows = [{"id": 1, "name": "a"}]
        self.service.ingest("s1", "duplicate", rows)
        with self.assertRaises(Conflict):
            self.service.ingest("s1", "duplicate", rows)

    def test_invalid_batch_shape_is_rejected_before_contract_change(self) -> None:
        with self.assertRaises(InvalidRequest):
            self.service.ingest("s1", "bad", [{"id": 1, "name": object()}])
        self.assertEqual(1, self.contracts.current("s1").version)
        self.assertIsNone(self.contracts.pending("s1"))


if __name__ == "__main__":
    unittest.main()
