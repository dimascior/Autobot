"""Sequencer authority: the instance lease (INV-25a), execution preconditions
(INV-01), AttemptContract source-state capture (INV-02), one current attempt
(INV-12), fence authority (INV-13) and record immutability/independence
(INV-11)."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Sequencer
from autobot.models import (
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    LeaseUnavailable,
    StaleFenceError,
)
from contract_library import object_hash


class SequencerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.sequence_id = self.result["sequence_id"]

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _leased(self, ttl=3600):
        self.rt.sequencer.acquire_lease(ttl_seconds=ttl)
        return self.rt.sequencer

    def _executing(self):
        seq = self._leased()
        seq.mark_ready(self.sequence_id)
        info = seq.start_execution(self.sequence_id)
        return seq, info

    def _object_payload(self, object_hash_value):
        cur = self.rt.db.conn.cursor()
        row = cur.execute(
            "SELECT content_address FROM objects WHERE object_hash = ?",
            (object_hash_value,),
        ).fetchone()
        return json.loads(self.rt.store.get(row["content_address"]).decode("utf-8"))

    def test_instance_lease(self):
        """CH2-instance-lease (INV-25a): one holder at a time; takeover only
        after expiry; a stale epoch has no authority."""
        first = self._leased(ttl=100)
        other = Sequencer(self.rt.db, self.rt.store, self.rt.clock, holder="other")
        with self.assertRaises(LeaseUnavailable):
            other.acquire_lease(ttl_seconds=100)
        self.rt.clock.advance(101)
        epoch = other.acquire_lease(ttl_seconds=100)
        self.assertEqual(epoch, 2)
        with self.assertRaises(AuthorityLost):
            first.mark_ready(self.sequence_id)

    def test_lease_renewal_and_expiry(self):
        seq = self._leased(ttl=100)
        self.rt.clock.advance(50)
        seq.renew_lease(ttl_seconds=100)
        self.rt.clock.advance(99)
        seq.renew_lease(ttl_seconds=100)  # still inside the renewed window
        self.rt.clock.advance(101)
        with self.assertRaises(AuthorityLost):
            seq.renew_lease(ttl_seconds=100)

    def test_mutation_requires_a_lease(self):
        with self.assertRaises(AuthorityLost):
            self.rt.sequencer.mark_ready(self.sequence_id)

    def test_reserve_requires_frozen_record(self):
        """CH2-reserve-requires-frozen-record (INV-01): execution refuses a
        record whose committed bytes no longer recompute (t-s06's
        cas_state_version_record_hash)."""
        seq = self._leased()
        seq.mark_ready(self.sequence_id)
        cur = self.rt.db.conn.cursor()
        row = cur.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (self.result["record_hash"],),
        ).fetchone()
        path = self.rt.store._path_for(row["content_address"])
        path.write_bytes(b"tampered")
        with self.assertRaises(IntegrityHalt):
            seq.start_execution(self.sequence_id)

    def test_attempt_contract_captures_source_state(self):
        """CH2-attempt-contract-captures-source-state (INV-02): obj-09 binds
        the frozen record and source domain as digests, plus the attempt
        identity quadruple; its object hash recomputes."""
        _, info = self._executing()
        payload = self._object_payload(info["attempt_contract_hash"])
        record = support.load_fixture("va_record.json")
        self.assertEqual(payload["kind"], "AttemptContract")
        self.assertEqual(payload["record_hash"], self.result["record_hash"])
        self.assertEqual(payload["requirement_digest"], self.result["requirement_digest"])
        self.assertEqual(payload["contract_digest"], self.result["contract_digest"])
        self.assertEqual(payload["attempt"]["sequence_id"], self.sequence_id)
        self.assertEqual(payload["attempt"]["attempt_id"], info["attempt_id"])
        self.assertEqual(payload["attempt"]["fence"], 1)
        self.assertEqual(payload["source_state"]["record_hash"], self.result["record_hash"])
        self.assertEqual(
            payload["source_state"]["repository_authority_declaration"],
            record["resolved_bindings"]["repository_authority_declaration"])
        self.assertEqual(payload["source_state"]["repository_id"], "autobot")
        self.assertEqual(payload["source_state"]["target_ref"], "refs/heads/main")
        self.assertEqual(
            object_hash("AttemptContract", payload), info["attempt_contract_hash"])

    def test_single_current_attempt(self):
        """CH2-single-current-attempt (INV-12): the state machine refuses a
        second execution, and the partial unique index refuses a second live
        attempt row even if code were bypassed."""
        seq, info = self._executing()
        with self.assertRaises(ConflictError):
            seq.start_execution(self.sequence_id)
        cur = self.rt.db.conn.cursor()
        contract_hash = cur.execute(
            "SELECT attempt_contract_hash FROM attempts WHERE attempt_id = ?",
            (info["attempt_id"],),
        ).fetchone()["attempt_contract_hash"]
        with self.assertRaises(sqlite3.IntegrityError):
            with self.rt.db.transaction() as tx:
                tx.execute(
                    "INSERT INTO attempts (attempt_id, sequence_id, state, version,"
                    " fence, attempt_contract_hash, created_utc, updated_utc)"
                    " VALUES ('at_00000000000000000000000000000bad', ?, 'DECLARED',"
                    " 1, 2, ?, 'x', 'x')",
                    (self.sequence_id, contract_hash),
                )

    def test_stale_fence_rejected(self):
        """CH2-stale-fence-rejected (INV-13): after the fence advances, an
        action presenting the old fence has no authority."""
        seq, first = self._executing()
        seq.reserve_attempt(first["attempt_id"], expected_fence=1)
        seq.fail_attempt(first["attempt_id"], reason="launch_failed")
        seq.retry_sequence(self.sequence_id)
        second = seq.start_execution(self.sequence_id)
        self.assertEqual(second["fence"], 2)
        with self.assertRaises(StaleFenceError):
            seq.reserve_attempt(second["attempt_id"], expected_fence=1)
        with self.assertRaises(ConflictError):
            seq.reserve_attempt(first["attempt_id"], expected_fence=2)
        self.assertEqual(
            seq.reserve_attempt(second["attempt_id"], expected_fence=2), 2)
        cur = self.rt.db.conn.cursor()
        last = cur.execute(
            "SELECT payload FROM transition_journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        facts = json.loads(last["payload"])["facts"]
        for key in ("sequence_id", "attempt_id", "version", "fence"):
            self.assertIn(key, facts)  # INV-13's receipt quadruple

    def test_retry_budget_bounds_attempts(self):
        """t-s06/t-s08 effective_budget_available: max_retries 2 means at
        most three attempts ever."""
        seq, info = self._executing()
        for expected_fence in (2, 3):
            seq.reserve_attempt(info["attempt_id"], expected_fence=expected_fence - 1)
            seq.fail_attempt(info["attempt_id"], reason="launch_failed")
            seq.retry_sequence(self.sequence_id)
            info = seq.start_execution(self.sequence_id)
            self.assertEqual(info["fence"], expected_fence)
        seq.reserve_attempt(info["attempt_id"], expected_fence=3)
        seq.fail_attempt(info["attempt_id"], reason="launch_failed")
        with self.assertRaises(ConflictError):
            seq.retry_sequence(self.sequence_id)

    def test_record_immutable_and_sequences_independent(self):
        """CH2-record-immutable-in-sequence (INV-11): the frozen record's row
        never changes while its Sequence advances, and a second Sequence with
        the same RequirementDigest is independent."""
        cur = self.rt.db.conn.cursor()
        before = dict(cur.execute(
            "SELECT * FROM records WHERE record_hash = ?",
            (self.result["record_hash"],),
        ).fetchone())
        v_c = support.load_fixture("vector_payloads.json")["V-C"]
        other = self.rt.registry.admit_payload(v_c, submitted_by="planner")
        self.assertEqual(other["outcome"], "NEW_RECORD")
        self.assertEqual(other["requirement_digest"], self.result["requirement_digest"])
        self.assertNotEqual(other["contract_digest"], self.result["contract_digest"])

        seq, _ = self._executing()
        after = dict(cur.execute(
            "SELECT * FROM records WHERE record_hash = ?",
            (self.result["record_hash"],),
        ).fetchone())
        self.assertEqual(before, after)
        other_row = cur.execute(
            "SELECT state, version, fence FROM sequences WHERE sequence_id = ?",
            (other["sequence_id"],),
        ).fetchone()
        self.assertEqual(
            (other_row["state"], other_row["version"], other_row["fence"]),
            ("DECLARED", 1, 0))


if __name__ == "__main__":
    unittest.main()
