"""C3-M1 conformance: the authoritative ExecutionReceipt (obj-15).

The collector seat's derivation, the admission seam's derived-not-declared
byte-equality (D-C3-2), the registry pinning (cap-30 / obj-15 / the
evidence_requirements↔witness-kind mapping), determinism across
re-collection and restart, obj-15's append-only versioning, and the 8f
recovery pairing with forged-row HALTs."""

import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Collector, Database, Sequencer, Store
from autobot import receipts
from autobot.models import ConflictError, IntegrityHalt
from autobot.witnesses import WITNESS_KINDS

GOVLOG = b"governance log\n"


class ReceiptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, self.result = support.admitted_runtime(
            self.dir, clock=self.clock)
        self.seq = self.rt.sequencer
        self.sequence_id = self.result["sequence_id"]

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _to_frozen(self, governance=True):
        self.seq.acquire_lease(ttl_seconds=360000)
        self.seq.mark_ready(self.sequence_id)
        info = self.seq.start_execution(self.sequence_id)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        self.attempt_contract_hash = info["attempt_contract_hash"]
        self.seq.reserve_attempt(self.attempt_id, expected_fence=self.fence)
        projection = self.rt.store.put(b"projection bytes\n")
        dispatched = self.seq.dispatch_attempt(self.attempt_id, "builder",
                                               "fake", projection)
        self.call_id = dispatched["call_id"]
        start = self.seq.admit_witness(
            self.attempt_id, "obj-11", "autoswarm",
            support.session_record_bytes(self.call_id))
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"])
        if governance:
            self.seq.admit_witness(self.attempt_id, "obj-12", "helios",
                                   GOVLOG)
        end = self.seq.admit_witness(self.attempt_id, "obj-13",
                                     "agent_clis", b"transcript\n")
        self.seq.finish_attempt(self.attempt_id, "builder", "success",
                                end["content_address"])
        self.seq.freeze_candidate(self.attempt_id, self.fence,
                                  self.rt.store.put(b"candidate\n"))

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def _rows(self):
        return [dict(r) for r in self.rt.db.conn.execute(
            "SELECT * FROM attempt_receipts ORDER BY rowid").fetchall()]


class PinningTests(ReceiptTestCase):
    def test_mapping_pins_both_registries(self):
        """EVIDENCE_KIND_BY_REQUIREMENT covers exactly the contract
        schema's evidence_requirements enum on one side and exactly the
        registered witness kinds (objects.json via witnesses.py) on the
        other; obj-15's registered minter is the collector."""
        schema_path = (support.REPO / "src" / "autobot" / "schemas"
                       / "task_contract_record.schema.json")
        schema = support.load_json(schema_path)
        enum = schema["properties"]["evidence_requirements"]["items"]["enum"]
        self.assertEqual(sorted(enum),
                         sorted(receipts.EVIDENCE_KIND_BY_REQUIREMENT))
        self.assertEqual(
            sorted(receipts.EVIDENCE_KIND_BY_REQUIREMENT.values()),
            sorted(WITNESS_KINDS))
        entries = support.load_json(
            support.CH0_REGISTRIES / "objects.json")["objects"]
        obj15 = [o for o in entries if o.get("id") == "obj-15"]
        self.assertEqual(len(obj15), 1)
        self.assertEqual(obj15[0]["minter"], receipts.RECEIPT_MINTER)
        self.assertTrue(obj15[0]["attempt_bound"])

    def test_receipt_schema_mirror_is_byte_identical(self):
        """The D-65 discipline extended to the chapter-3 schema: the
        runtime copy equals the chapter-3 provenance copy byte for byte,
        and its witness-kind enum equals the registered kinds."""
        chapter3 = support.REPO / "docs" / "architecture" / "chapter3"
        mirror_dir = support.REPO / "src" / "autobot" / "schemas_chapter3"
        for name in ("execution_receipt.schema.json",
                     "prompt_projection.schema.json"):
            self.assertEqual(
                (chapter3 / "schemas" / name).read_bytes(),
                (mirror_dir / name).read_bytes(),
                "chapter-3 schema mirror drift in %s" % name)
        schema = receipts.receipt_schema()
        self.assertEqual(sorted(schema["$defs"]["witness_kind"]["enum"]),
                         sorted(WITNESS_KINDS))


class CollectorTests(ReceiptTestCase):
    def test_complete_derivation_is_deterministic(self):
        """COMPLETE derivation: required kinds from the frozen record,
        per-role facts, the D-C3-1 bound — and a second collection over
        unchanged facts re-mints byte-identical bytes, admits as a
        duplicate and journals nothing."""
        self._to_frozen()
        anchor = self.rt.db.conn.execute(
            "SELECT updated_utc FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["updated_utc"]
        first = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        self.assertEqual(first["completeness"], "COMPLETE")
        self.assertFalse(first["duplicate"])
        payload = json.loads(self.rt.store.get(
            first["receipt_address"]).decode("utf-8"))
        self.assertEqual(payload["minter"], "collector")
        role = payload["completeness_by_role"]["builder"]
        self.assertEqual(role["required_kinds"], ["obj-11", "obj-12"])
        self.assertEqual(role["present_kinds"],
                         ["obj-11", "obj-12", "obj-13"])
        self.assertEqual(role["missing_kinds"], [])
        self.assertEqual(len(payload["admitted_witnesses"]), 3)
        self.assertEqual(payload["integrity_failures"], [])
        self.assertEqual(payload["derivation"]["anchor_utc"], anchor)
        self.assertEqual(payload["derivation"]["window_seconds"], 3600)
        self.assertEqual(
            payload["evidence_complete_by_utc"],
            receipts.completion_bound(anchor, 3600))
        self.assertEqual(receipts.validate_receipt(payload), [])
        before = self._journal_count()
        second = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["receipt_address"],
                         first["receipt_address"])
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(len(self._rows()), 1)

    def test_incomplete_names_the_missing_kind(self):
        self._to_frozen(governance=False)
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        self.assertEqual(outcome["completeness"], "INCOMPLETE")
        payload = json.loads(self.rt.store.get(
            outcome["receipt_address"]).decode("utf-8"))
        self.assertEqual(
            payload["completeness_by_role"]["builder"]["missing_kinds"],
            ["obj-12"])

    def test_recollection_after_changed_facts_is_a_new_version(self):
        """obj-15 mutability: a different derivation (here, a different
        configured window) mints a NEW admitted version; both rows stand,
        latest by rowid, each with its journaled admission."""
        self._to_frozen()
        first = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        second = Collector(
            self.seq, self.rt.store,
            evidence_window_seconds=7200).collect_and_admit(self.attempt_id)
        self.assertFalse(second["duplicate"])
        self.assertNotEqual(first["receipt_address"],
                            second["receipt_address"])
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["content_sha256"],
                         second["receipt_address"])

    def test_restart_converges_byte_identically(self):
        """Crash between admission and transition: a new runtime over the
        same durable state re-derives the same bytes, admits as a
        duplicate, and the transition proceeds."""
        self._to_frozen()
        first = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        self.rt.db.close()
        db = Database(self.dir / "autobot.db")
        store = Store(self.dir / "store")
        self.rt.db, self.rt.store = db, store
        seq = Sequencer(db, store, self.clock, holder="test-instance")
        self.rt.sequencer = seq
        seq.recover()
        self.assertEqual(seq.acquire_lease(ttl_seconds=360000), 2)
        again = Collector(seq, store).collect_and_admit(self.attempt_id)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["receipt_address"],
                         first["receipt_address"])
        seq.complete_evidence(self.attempt_id, self.fence,
                              again["receipt_address"])
        report = seq.recover()
        self.assertEqual(report["intents_in_doubt"], [])
        self.seq = seq  # tearDown closes the reopened db via self.rt


class AdmissionSeamTests(ReceiptTestCase):
    def _derived(self, window=3600):
        state = self.seq.evidence_state(self.attempt_id)
        return receipts.derive_evidence(self.rt.store, state, window)

    def test_forged_classification_is_refused(self):
        """D-C3-2: tampering any derived field (the classification, the
        bound, the witness set) changes the bytes away from the ledger
        derivation and the admission refuses; nothing is journaled."""
        self._to_frozen(governance=False)  # truly INCOMPLETE
        before = self._journal_count()
        forged = self._derived()
        forged["completeness"] = "COMPLETE"
        forged["completeness_by_role"]["builder"]["missing_kinds"] = []
        with self.assertRaises(ConflictError) as ctx:
            self.seq.admit_execution_receipt(
                self.attempt_id, receipts.receipt_bytes(forged))
        self.assertIn("diverges", str(ctx.exception))
        bound_forged = self._derived()
        bound_forged["evidence_complete_by_utc"] = \
            "2026-11-09T11:00:00.000Z"
        with self.assertRaises(ConflictError):
            self.seq.admit_execution_receipt(
                self.attempt_id, receipts.receipt_bytes(bound_forged))
        subset = self._derived()
        subset["admitted_witnesses"] = subset["admitted_witnesses"][:1]
        with self.assertRaises(ConflictError):
            self.seq.admit_execution_receipt(
                self.attempt_id, receipts.receipt_bytes(subset))
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self._rows(), [])

    def test_shape_and_minter_gates(self):
        """The obj-15 schema gates: a non-collector minter, a zero window
        and non-canonical bytes are each refused structurally."""
        self._to_frozen()
        wrong_minter = self._derived()
        wrong_minter["minter"] = "task_force"
        with self.assertRaises(ConflictError):
            self.seq.admit_execution_receipt(
                self.attempt_id, receipts.receipt_bytes(wrong_minter))
        zero_window = self._derived()
        zero_window["derivation"]["window_seconds"] = 0
        with self.assertRaises(ConflictError):
            self.seq.admit_execution_receipt(
                self.attempt_id, receipts.receipt_bytes(zero_window))
        sloppy = json.dumps(self._derived(), sort_keys=True,
                            indent=2).encode("utf-8")
        with self.assertRaises(ConflictError) as ctx:
            self.seq.admit_execution_receipt(self.attempt_id, sloppy)
        self.assertIn("canonical", str(ctx.exception))

    def test_admission_requires_candidate_frozen(self):
        self.seq.acquire_lease(ttl_seconds=360000)
        self.seq.mark_ready(self.sequence_id)
        info = self.seq.start_execution(self.sequence_id)
        self.seq.reserve_attempt(info["attempt_id"],
                                 expected_fence=info["fence"])
        with self.assertRaises(ConflictError):
            self.seq.evidence_state(info["attempt_id"])
        with self.assertRaises(ConflictError):
            self.seq.admit_execution_receipt(info["attempt_id"], b"{}")

    def test_wrong_call_id_is_refused(self):
        self._to_frozen()
        forged = self._derived()
        forged["call_id"] = "call_" + "0" * 32
        with self.assertRaises(ConflictError) as ctx:
            self.seq.admit_execution_receipt(
                self.attempt_id, receipts.receipt_bytes(forged))
        self.assertIn("call", str(ctx.exception))


class RecoveryTests(ReceiptTestCase):
    def test_recover_halts_on_forged_receipt_rows(self):
        """8f pairing: a tampered completeness column, a row inserted
        without its journal entry, and a deleted row each HALT recovery."""
        self._to_frozen()
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE attempt_receipts SET completeness = 'INCOMPLETE'"
                " WHERE content_sha256 = ?", (outcome["receipt_address"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE attempt_receipts SET completeness = 'COMPLETE'"
                " WHERE content_sha256 = ?", (outcome["receipt_address"],))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        fake_bytes = self.rt.store.put(b'{"forged":true}')
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO attempt_receipts (admission_id, attempt_id,"
                " content_sha256, completeness, evidence_complete_by_utc,"
                " minter, call_id, admitted_utc)"
                " VALUES ('rcp_forged', ?, ?, 'COMPLETE',"
                " '2026-11-09T13:00:00.000Z', 'collector', 'call_forged',"
                " '2026-11-09T12:00:00.000Z')",
                (self.attempt_id, fake_bytes))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_receipts WHERE admission_id ="
                       " 'rcp_forged'")
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        with self.rt.db.transaction() as tx:
            tx.execute(
                "DELETE FROM attempt_receipts WHERE content_sha256 = ?",
                (outcome["receipt_address"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()  # journal names the admission; row is gone


if __name__ == "__main__":
    unittest.main()
