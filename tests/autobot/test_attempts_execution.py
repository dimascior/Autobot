"""The attempt-execution slice (t-a02..t-a06 plus the t-a10/t-a11/t-a13
failure arms): the authoritative lifecycle for one bound specialist
execution, with content-addressed obj-10/11/15/16 references and the
schema-v3 attempt_calls binding."""

import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot.models import ConflictError, IntegrityHalt, StaleFenceError

PROJECTION = b"projection: build the thing (bound to the frozen record)\n"
WITNESS_START = b"witness: session started\n"
WITNESS_END = b"witness: session finished\n"
CANDIDATE = b"candidate: tree stand-in\n"


class ExecutionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.seq = self.rt.sequencer
        self.sequence_id = self.result["sequence_id"]
        self.seq.acquire_lease(ttl_seconds=360000)
        self.seq.mark_ready(self.sequence_id)
        info = self.seq.start_execution(self.sequence_id)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        self.attempt_contract_hash = info["attempt_contract_hash"]
        self.seq.reserve_attempt(self.attempt_id, expected_fence=self.fence)
        self.projection = self.rt.store.put(PROJECTION)
        self.w_start = self.rt.store.put(WITNESS_START)
        self.w_end = self.rt.store.put(WITNESS_END)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _dispatch(self, **kwargs):
        args = {"role": "builder", "provider": "fake", "call_id": "call-1",
                "projection_address": self.projection}
        args.update(kwargs)
        return self.seq.dispatch_attempt(self.attempt_id, **args)

    def _receipt(self, witnesses=None, completeness="COMPLETE", bound=None,
                 attempt_id=None, attempt_contract_hash=None):
        payload = {"schema_version": "v1", "kind": "ExecutionReceipt",
                   "attempt_id": attempt_id if attempt_id is not None
                   else self.attempt_id,
                   "attempt_contract_hash": attempt_contract_hash
                   if attempt_contract_hash is not None
                   else self.attempt_contract_hash,
                   "completeness": completeness,
                   "admitted_witnesses": witnesses if witnesses is not None
                   else [self.w_start, self.w_end]}
        if bound is not None:
            payload["evidence_complete_by_utc"] = bound
        return self.rt.store.put(
            json.dumps(payload, sort_keys=True).encode("utf-8"))

    def _to_candidate_frozen(self):
        self._dispatch()
        self.seq.attempt_running(self.attempt_id, "native-sess-9", self.w_start)
        self.seq.finish_attempt(self.attempt_id, "builder", "success", self.w_end)
        self.seq.freeze_candidate(self.attempt_id, self.fence,
                                  self.rt.store.put(CANDIDATE))

    def _attempt_state(self):
        return self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["state"]

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def _rules(self):
        return [json.loads(r["payload"])["rule"] for r in
                self.rt.db.conn.execute(
                    "SELECT payload FROM transition_journal ORDER BY seq"
                ).fetchall()]


class DispatchTests(ExecutionTestCase):
    def test_full_execution_chain(self):
        """RESERVED→DISPATCHED→RUNNING→FINISHED→CANDIDATE_FROZEN→
        EVIDENCE_COMPLETE, each under its registered rule id."""
        self._dispatch()
        call = self.rt.db.conn.execute(
            "SELECT * FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        self.assertEqual((call["role"], call["provider"], call["call_id"],
                          call["projection_address"]),
                         ("builder", "fake", "call-1", self.projection))
        self.assertIsNone(call["native_session_id"])
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 self.w_start)
        call = self.rt.db.conn.execute(
            "SELECT native_session_id FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        self.assertEqual(call["native_session_id"], "native-sess-9")
        self.assertEqual(
            self.seq.finish_attempt(self.attempt_id, "builder", "success",
                                    self.w_end), "FINISHED")
        candidate = self.rt.store.put(CANDIDATE)
        self.seq.freeze_candidate(self.attempt_id, self.fence, candidate)
        self.seq.complete_evidence(self.attempt_id, self.fence,
                                   self._receipt())
        state = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["state"]
        self.assertEqual(state, "EVIDENCE_COMPLETE")
        rules = self._rules()
        for rule in ("t-a02", "t-a03", "t-a04", "t-a05", "t-a06"):
            self.assertIn(rule, rules)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

    def test_dispatch_requirements(self):
        with self.assertRaises(ConflictError):
            self._dispatch(call_id="")  # call_id_bound
        with self.assertRaises(ConflictError):
            self._dispatch(role="reviewer")  # not in completion_policies
        with self.assertRaises(ConflictError):
            self._dispatch(projection_address="sha256:" + "0" * 64)  # no obj-10
        self._dispatch()
        with self.assertRaises(ConflictError):
            self._dispatch(call_id="call-2")  # already DISPATCHED

    def test_running_requirements(self):
        self._dispatch()
        with self.assertRaises(ConflictError):
            self.seq.attempt_running(self.attempt_id, "", self.w_start)
        with self.assertRaises(ConflictError):
            self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                     "sha256:" + "0" * 64)

    def test_required_role_failure_is_t_a11(self):
        self._dispatch()
        self.seq.attempt_running(self.attempt_id, "native-sess-9", self.w_start)
        with self.assertRaises(ConflictError):
            self.seq.finish_attempt(self.attempt_id, "reviewer", "success",
                                    self.w_end)  # not the dispatched role
        self.assertEqual(
            self.seq.finish_attempt(self.attempt_id, "builder", "crashed",
                                    self.w_end), "FAILED")
        self.assertIn("t-a11", self._rules())

    def test_launch_failure_after_call_id_is_t_a10(self):
        self._dispatch()
        self.seq.fail_dispatched_attempt(self.attempt_id, reason="spawn error")
        self.assertIn("t-a10", self._rules())

    def test_freeze_and_evidence_guards(self):
        self._dispatch()
        self.seq.attempt_running(self.attempt_id, "native-sess-9", self.w_start)
        self.seq.finish_attempt(self.attempt_id, "builder", "success", self.w_end)
        candidate = self.rt.store.put(CANDIDATE)
        with self.assertRaises(StaleFenceError):
            self.seq.freeze_candidate(self.attempt_id, self.fence + 1, candidate)
        with self.assertRaises(ConflictError):
            self.seq.freeze_candidate(self.attempt_id, self.fence,
                                      "sha256:" + "0" * 64)
        self.seq.freeze_candidate(self.attempt_id, self.fence, candidate)
        with self.assertRaises(ConflictError):
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       self._receipt(witnesses=[]))
        with self.assertRaises(ConflictError):
            self.seq.complete_evidence(
                self.attempt_id, self.fence,
                self._receipt(witnesses=["sha256:" + "0" * 64]))
        self.seq.complete_evidence(self.attempt_id, self.fence, self._receipt())

    def test_integrity_failed_candidate_is_partial(self):
        """t-a13 is PARTIAL until Chapter 3's receipt-admission authority
        establishes the completeness classification. Even a correctly
        bound INTEGRITY_FAILED receipt is refused at the authority
        boundary; the attempt state and journal are unchanged."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INTEGRITY_FAILED"),
                reason="witness hash mismatch")
        self.assertIn("PARTIAL", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_stale_fence_changes_nothing(self):
        """Registered fence_matches on t-a13 (operator-identified
        correction): a stale fence is refused and leaves both the attempt
        state and the journal untouched."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(StaleFenceError):
            self.seq.fail_candidate(
                self.attempt_id, self.fence + 1,
                self._receipt(completeness="INTEGRITY_FAILED"),
                reason="stale writer")
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_incomplete_is_partial(self):
        """t-a13 is PARTIAL: an INCOMPLETE receipt with a correctly bound
        attempt and an elapsed deadline is still refused at the authority
        boundary, because the deadline's derivation from D-28 policy
        requires Chapter 3's receipt-admission authority."""
        self._to_candidate_frozen()
        before = self._journal_count()
        past = "2026-11-09T11:00:00.000Z"
        self.rt.clock.advance(3601)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INCOMPLETE", bound=past),
                reason="evidence never completed")
        self.assertIn("PARTIAL", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_terminal_sequence_grants_no_intent_authority(self):
        """Closure-pass review correction: a forged terminal Sequence must
        not mint intents even while its attempt looks live."""
        self.seq.grant_permit(self.sequence_id, permitted_uses=1)
        self.seq.reserve_use(self.sequence_id, self.attempt_id)
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE sequences SET state = 'ABANDONED' WHERE sequence_id = ?",
                (self.sequence_id,))
        with self.assertRaises(ConflictError):
            self.seq.mint_intent(self.sequence_id, self.attempt_id,
                                 "eff-canonical-push", target="refs/heads/main")

    def test_t_a13_rejects_unbound_receipt(self):
        """INV-69: a receipt that does not bind this attempt by attempt_id
        is refused; the attempt state and journal are unchanged."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(ConflictError):
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INTEGRITY_FAILED",
                              attempt_id="at_wrong"),
                reason="forged binding")
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_rejects_wrong_contract_hash(self):
        """INV-68: a receipt declaring the wrong attempt_contract_hash
        is refused; the attempt state and journal are unchanged."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(ConflictError):
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INTEGRITY_FAILED",
                              attempt_contract_hash="sha256:" + "ff" * 32),
                reason="forged contract hash")
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_rejects_wrong_receipt_kind(self):
        """INV-68 identity: a CAS object that is not an ExecutionReceipt
        cannot fail the candidate."""
        self._to_candidate_frozen()
        before = self._journal_count()
        fake = self.rt.store.put(json.dumps(
            {"schema_version": "v1", "kind": "NotAReceipt",
             "attempt_id": self.attempt_id,
             "completeness": "INTEGRITY_FAILED"},
            sort_keys=True).encode("utf-8"))
        with self.assertRaises(ConflictError):
            self.seq.fail_candidate(
                self.attempt_id, self.fence, fake, reason="wrong kind")
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a06_rejects_unbound_receipt(self):
        """The evidence-completion path (t-a06) enforces the same receipt
        authority boundary as t-a13: a receipt with the wrong attempt_id
        is refused."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(ConflictError):
            self.seq.complete_evidence(
                self.attempt_id, self.fence,
                self._receipt(attempt_id="at_wrong"))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_forged_past_deadline_wrong_binding(self):
        """Adversarial: a forged past deadline on an INCOMPLETE receipt
        whose attempt_id is wrong is caught by the binding check before
        the authority boundary is reached."""
        self._to_candidate_frozen()
        before = self._journal_count()
        past = "2026-11-09T11:00:00.000Z"
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INCOMPLETE", bound=past,
                              attempt_id="at_forged"),
                reason="forged deadline + binding")
        self.assertNotIn("PARTIAL", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_correct_binding_forged_classification(self):
        """Adversarial: a receipt with correct attempt binding and
        contract hash but a self-asserted INTEGRITY_FAILED classification
        is stopped at the authority boundary — the classification is not
        treated as authoritative without Chapter 3's receipt-admission."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INTEGRITY_FAILED"),
                reason="forged classification")
        self.assertIn("PARTIAL", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_correct_binding_forged_deadline(self):
        """Adversarial: a receipt with correct binding and a fabricated
        past deadline is stopped at the authority boundary — not at the
        deadline comparison, which it would otherwise pass."""
        self._to_candidate_frozen()
        before = self._journal_count()
        past = "2026-11-09T11:00:00.000Z"
        self.rt.clock.advance(3601)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INCOMPLETE", bound=past),
                reason="forged deadline, correct binding")
        self.assertIn("PARTIAL", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_receipt_requires_contract_hash(self):
        """INV-68: attempt_contract_hash is required in the receipt —
        a receipt that omits it is rejected at the identity boundary."""
        self._to_candidate_frozen()
        before = self._journal_count()
        fake = self.rt.store.put(json.dumps(
            {"schema_version": "v1", "kind": "ExecutionReceipt",
             "attempt_id": self.attempt_id,
             "completeness": "INTEGRITY_FAILED",
             "admitted_witnesses": []},
            sort_keys=True).encode("utf-8"))
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence, fake,
                reason="no contract hash")
        self.assertIn("INV-68", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_recovery_pairs_calls_with_states(self):
        """D-68 pairing for executions: a DISPATCHED attempt without its
        bound call, or a bound call for an undispatched attempt, HALTs."""
        self._dispatch()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_calls WHERE attempt_id = ?",
                       (self.attempt_id,))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


if __name__ == "__main__":
    unittest.main()
