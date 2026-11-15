"""Deterministic verification gate for the READ_ONLY pilot contract.

Drives the complete state-transition chain from DECLARED through COMPLETED
using the frozen V-A corpus with live registered closures and the
production verifier runner. Records exact assertion IDs, registrations,
environment captures and receipt identities at every step.
"""
import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import ProductionVerifierRunner, Store
from autobot.collector import Collector
from autobot.models import ConflictError
from autobot.receipts import EVIDENCE_KIND_BY_REQUIREMENT
from autobot.transport import normalize_outcome
from contract_library import content_address


class ReadOnlyVerificationGateTests(unittest.TestCase):
    """Full lifecycle from DECLARED to COMPLETED with exact evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, self.va = support.admitted_runtime(self.dir,
                                                    clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _admit_record(self, vector_behavior="PASS", **kwargs):
        record = support.local_record_payload(
            finalization_class="READ_ONLY",
            evidence_requirements=["COMMAND_GOVERNANCE",
                                   "EXECUTION_LIFECYCLE"],
            vector_behavior=vector_behavior, **kwargs)
        result = self.rt.registry.admit_payload(record,
                                                submitted_by="planner")
        self.assertEqual(result["outcome"], "NEW_RECORD")
        return result, record

    def _drive_to_evidence_complete(self, admitted):
        """Drive using the existing support helper which exercises
        the production Sequencer APIs directly."""
        return support.drive_to_evidence_complete(
            self.rt, admitted, role="builder")

    def _state(self, table, id_col, id_val):
        return self.rt.db.conn.execute(
            "SELECT state FROM %s WHERE %s = ?" % (table, id_col),
            (id_val,)).fetchone()["state"]

    def _journal_rules(self, subject_id=None):
        rows = self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq"
        ).fetchall()
        rules = []
        for row in rows:
            entry = json.loads(row["payload"])
            if subject_id is None or entry.get("subject_id") == subject_id:
                rules.append(entry["rule"])
        return rules

    # ================================================================
    # Positive: complete READ_ONLY lifecycle
    # ================================================================

    def test_full_lifecycle_to_completed(self):
        """DECLARED → READY → EXECUTING → RESERVED → DISPATCHED →
        RUNNING → FINISHED → CANDIDATE_FROZEN → COMPLETE →
        VERIFYING → VERIFIED → ACCEPTED → DEPENDENCIES_RELEASED →
        COMPLETED."""
        admitted, record = self._admit_record()
        sequence_id = admitted["sequence_id"]

        # --- DECLARED → READY ---
        self.assertEqual(self._state("sequences", "sequence_id",
                                      sequence_id), "DECLARED")
        self.seq.mark_ready(sequence_id)
        self.assertEqual(self._state("sequences", "sequence_id",
                                      sequence_id), "READY")

        # --- Drive to EVIDENCE_COMPLETE ---
        info = self._drive_to_evidence_complete(admitted)
        attempt_id = info["attempt_id"]
        fence = info["fence"]
        self.assertEqual(self._state("attempts", "attempt_id",
                                      attempt_id), "EVIDENCE_COMPLETE")
        self.assertEqual(self._state("sequences", "sequence_id",
                                      sequence_id), "VERIFYING")

        # --- Verify with production runner ---
        runner = ProductionVerifierRunner(self.seq, self.rt.store)
        outcome = runner.verify_attempt(attempt_id)
        self.assertFalse(outcome["inconclusive"],
                         "Verification hit INCONCLUSIVE: %s" % outcome)

        # Record assertion results
        rows = {r["assertion_id"]: dict(r) for r in
                self.rt.db.conn.execute(
                    "SELECT * FROM attempt_verifications"
                    " WHERE attempt_id = ?",
                    (attempt_id,)).fetchall()}
        assertion_ids = sorted(rows.keys())
        self.assertIn("a1", assertion_ids)
        self.assertIn("a2", assertion_ids)
        self.assertIn("role-transform/builder", assertion_ids)
        for a_id, row in rows.items():
            self.assertEqual(row["outcome"], "PASS",
                             "Assertion %s: %s" % (a_id, row["outcome"]))

        # Record verification registrations
        registrations = {a_id: row["verifier_id"]
                         for a_id, row in rows.items()
                         if "verifier_id" in row.keys()}

        # Record receipt addresses
        receipt_addresses = {a_id: row["receipt_sha256"]
                             for a_id, row in rows.items()}
        for addr in receipt_addresses.values():
            self.assertTrue(self.rt.store.has(addr),
                            "Receipt %s not in CAS" % addr)

        # --- Conclude → VERIFIED ---
        result = self.seq.conclude_verification(attempt_id, fence)
        self.assertEqual(result, {"attempt": "VERIFIED",
                                  "sequence": "VERIFIED"})
        self.assertEqual(self._state("attempts", "attempt_id",
                                      attempt_id), "VERIFIED")
        self.assertEqual(self._state("sequences", "sequence_id",
                                      sequence_id), "VERIFIED")

        # --- Accept (READ_ONLY t-s14) → ACCEPTED ---
        acceptance = self.seq.accept_verified(sequence_id, fence)
        self.assertIsNotNone(acceptance)
        self.assertEqual(self._state("sequences", "sequence_id",
                                      sequence_id), "ACCEPTED")

        # --- Release dependencies → DEPENDENCIES_RELEASED ---
        release = self.seq.release_dependencies(sequence_id)
        self.assertIsNotNone(release["release_address"])
        self.assertEqual(
            self._state("sequences", "sequence_id", sequence_id),
            "DEPENDENCIES_RELEASED")

        # --- Complete → COMPLETED ---
        self.seq.complete_sequence(sequence_id)
        self.assertEqual(self._state("sequences", "sequence_id",
                                      sequence_id), "COMPLETED")

        # --- Verify the full transition chain in the journal ---
        seq_rules = self._journal_rules(sequence_id)
        attempt_rules = self._journal_rules(attempt_id)
        self.assertIn("t-s10", seq_rules)   # VERIFYING→VERIFIED
        self.assertIn("t-s14", seq_rules)   # VERIFIED→ACCEPTED
        self.assertIn("t-s41", seq_rules)   # ACCEPTED→DEP_RELEASED
        self.assertIn("t-s42", seq_rules)   # DEP_RELEASED→COMPLETED
        self.assertIn("t-a02", attempt_rules)  # dispatch
        self.assertIn("t-a03", attempt_rules)  # running
        self.assertIn("t-a04", attempt_rules)  # finished
        self.assertIn("t-a07", attempt_rules)  # verified

    # ================================================================
    # Exact evidence requirements
    # ================================================================

    def test_evidence_requirements_satisfied(self):
        """The pilot contract requires COMMAND_GOVERNANCE (obj-12) and
        EXECUTION_LIFECYCLE (obj-11). Both are admitted and the
        Collector classifies COMPLETE."""
        admitted, record = self._admit_record()
        info = self._drive_to_evidence_complete(admitted)
        self.assertEqual(self._state("attempts", "attempt_id",
                                      info["attempt_id"]),
                         "EVIDENCE_COMPLETE")
        obj11_rows = self.rt.db.conn.execute(
            "SELECT * FROM attempt_witnesses WHERE attempt_id = ?"
            " AND witness_kind = 'obj-11'",
            (info["attempt_id"],)).fetchall()
        obj12_rows = self.rt.db.conn.execute(
            "SELECT * FROM attempt_witnesses WHERE attempt_id = ?"
            " AND witness_kind = 'obj-12'",
            (info["attempt_id"],)).fetchall()
        self.assertTrue(len(obj11_rows) >= 1)
        self.assertTrue(len(obj12_rows) >= 1)

    # ================================================================
    # Negative: assertion FAIL → not VERIFIED
    # ================================================================

    def test_assertion_fail_prevents_verified(self):
        """When one assertion FAILs, conclude_verification returns
        REJECTED (not VERIFIED). The sequence goes to READY or HALTED
        depending on repair budget — never VERIFIED or ACCEPTED."""
        admitted, record = self._admit_record(
            task_contract_id="tc_" + "fa11" * 8,
            objective="Negative: assertion FAIL",
            vector_behavior="FAIL")
        info = self._drive_to_evidence_complete(admitted)
        attempt_id = info["attempt_id"]
        fence = info["fence"]
        runner = ProductionVerifierRunner(self.seq, self.rt.store)
        outcome = runner.verify_attempt(attempt_id)
        rows = {r["assertion_id"]: dict(r) for r in
                self.rt.db.conn.execute(
                    "SELECT * FROM attempt_verifications"
                    " WHERE attempt_id = ?",
                    (attempt_id,)).fetchall()}
        failed_ids = [a for a, r in rows.items()
                      if r["outcome"] == "FAIL"]
        self.assertTrue(len(failed_ids) > 0,
                        "Expected at least one FAIL assertion")
        result = self.seq.conclude_verification(attempt_id, fence)
        self.assertEqual(result["attempt"], "REJECTED")
        self.assertIn(result["sequence"], ("READY", "HALTED"))
        self.assertNotEqual(result["sequence"], "VERIFIED")

    # ================================================================
    # Negative: missing evidence → INCOMPLETE → cannot verify
    # ================================================================

    def test_missing_evidence_blocks_verification(self):
        """When required evidence is missing (no obj-12 governance),
        the Collector classifies INCOMPLETE. The attempt cannot reach
        EVIDENCE_COMPLETE and verification cannot proceed."""
        admitted, record = self._admit_record(
            task_contract_id="tc_" + "0b12" * 8,
            objective="Negative: missing governance evidence")
        sequence_id = admitted["sequence_id"]
        self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        attempt_id, fence = info["attempt_id"], info["fence"]
        self.seq.reserve_attempt(attempt_id, expected_fence=fence)
        projection = self.rt.store.put(b"projection for missing evidence\n")
        dispatched = self.seq.dispatch_attempt(
            attempt_id, "builder", "fake", projection)
        start = self.seq.admit_witness(
            attempt_id, "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        self.seq.attempt_running(attempt_id, "native-sess-miss",
                                 start["content_address"])
        # Deliberately skip obj-12 (governance) admission
        end = self.seq.admit_witness(
            attempt_id, "obj-13", "agent_clis", b"transcript\n")
        self.seq.finish_attempt(
            attempt_id, "builder", "success", end["content_address"])
        support.mint_and_freeze(self.rt, attempt_id, fence)
        # Collector: not COMPLETE because obj-12 is required but absent
        receipt = support.collect_receipt(self.rt, attempt_id)
        receipt_bytes_data = self.rt.store.get(receipt)
        receipt_payload = json.loads(receipt_bytes_data.decode("utf-8"))
        self.assertNotEqual(receipt_payload["completeness"], "COMPLETE",
                            "Missing required evidence must not classify"
                            " as COMPLETE")
        # complete_evidence should refuse anything other than COMPLETE
        with self.assertRaises(ConflictError):
            self.seq.complete_evidence(attempt_id, fence, receipt)


if __name__ == "__main__":
    unittest.main()
