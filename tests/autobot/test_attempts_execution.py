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
        args = {"role": "builder", "provider": "fake",
                "projection_address": self.projection}
        args.update(kwargs)
        result = self.seq.dispatch_attempt(self.attempt_id, **args)
        self.call_id = result["call_id"]
        return result

    def _admit(self, witness_kind, producer, data):
        return self.seq.admit_witness(self.attempt_id, witness_kind,
                                      producer, data)

    @staticmethod
    def _entry(admitted, witness_kind, producer):
        """The D-21 structured admission entry a receipt carries."""
        return {"witness_kind": witness_kind, "producer": producer,
                "content_sha256": admitted["content_address"]}

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
                   else [self.entry_start, self.entry_end]}
        if bound is not None:
            payload["evidence_complete_by_utc"] = bound
        return self.rt.store.put(
            json.dumps(payload, sort_keys=True).encode("utf-8"))

    def _run_and_finish(self, outcome="success", governance=True):
        """dispatch → admit the lifecycle witness → t-a03 → admit the
        governance and transcript witnesses → t-a04/t-a11 per the role
        policy. ``governance=False`` leaves the record-required obj-12
        unadmitted — the INCOMPLETE arm's setup."""
        self._dispatch()
        start = self._admit("obj-11", "autoswarm", WITNESS_START)
        self.entry_start = self._entry(start, "obj-11", "autoswarm")
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"])
        if governance:
            self._admit("obj-12", "helios", b"governance log\n")
        end = self._admit("obj-13", "agent_clis", WITNESS_END)
        self.entry_end = self._entry(end, "obj-13", "agent_clis")
        return self.seq.finish_attempt(self.attempt_id, "builder", outcome,
                                       end["content_address"])

    def _to_candidate_frozen(self, governance=True):
        self._run_and_finish(governance=governance)
        self.seq.freeze_candidate(self.attempt_id, self.fence,
                                  self.rt.store.put(CANDIDATE))

    def _collect(self, window_seconds=None):
        """Derive-and-admit the obj-15 through the registered Collector."""
        kwargs = {}
        if window_seconds is not None:
            kwargs["evidence_window_seconds"] = window_seconds
        from autobot import Collector
        return Collector(self.seq, self.rt.store,
                         **kwargs).collect_and_admit(self.attempt_id)

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
        EVIDENCE_COMPLETE, each under its registered rule id, with the
        Sequencer-minted call identity and the admitted-witness join."""
        self._dispatch()
        call = self.rt.db.conn.execute(
            "SELECT * FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        self.assertEqual((call["role"], call["provider"],
                          call["projection_address"]),
                         ("builder", "fake", self.projection))
        self.assertEqual(call["call_id"], self.call_id)
        self.assertRegex(call["call_id"], r"^call_[0-9a-f]{32}$")  # D-22
        self.assertIsNone(call["native_session_id"])
        start = self._admit("obj-11", "autoswarm", WITNESS_START)
        self.entry_start = self._entry(start, "obj-11", "autoswarm")
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"])
        call = self.rt.db.conn.execute(
            "SELECT native_session_id FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        self.assertEqual(call["native_session_id"], "native-sess-9")
        self._admit("obj-12", "helios", b"governance log\n")
        end = self._admit("obj-13", "agent_clis", WITNESS_END)
        self.entry_end = self._entry(end, "obj-13", "agent_clis")
        self.assertEqual(
            self.seq.finish_attempt(self.attempt_id, "builder", "success",
                                    end["content_address"]), "FINISHED")
        candidate = self.rt.store.put(CANDIDATE)
        self.seq.freeze_candidate(self.attempt_id, self.fence, candidate)
        outcome = self._collect()
        self.assertEqual(outcome["completeness"], "COMPLETE")
        self.seq.complete_evidence(self.attempt_id, self.fence,
                                   outcome["receipt_address"])
        state = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["state"]
        self.assertEqual(state, "EVIDENCE_COMPLETE")
        rules = self._rules()
        for rule in ("t-a02", "t-a03", "t-a04", "t-a05", "t-a06",
                     "ch2:admit-witness", "ch3:admit-execution-receipt"):
            self.assertIn(rule, rules)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

    def test_dispatch_requirements(self):
        with self.assertRaises(ConflictError):
            self._dispatch(role="reviewer")  # not in completion_policies
        with self.assertRaises(ConflictError):
            self._dispatch(projection_address="sha256:" + "0" * 64)  # no obj-10
        first = self._dispatch()
        with self.assertRaises(ConflictError):
            self._dispatch()  # already DISPATCHED
        # the one committed call identity is the first mint, untouched
        call = self.rt.db.conn.execute(
            "SELECT call_id FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        self.assertEqual(call["call_id"], first["call_id"])

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
        self._run_and_finish()
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
            # a bare address is not a D-21 structured admission entry
            self.seq.complete_evidence(
                self.attempt_id, self.fence,
                self._receipt(witnesses=["sha256:" + "0" * 64]))
        with self.assertRaises(ConflictError):
            # structurally valid, but this evidence was never admitted
            self.seq.complete_evidence(
                self.attempt_id, self.fence,
                self._receipt(witnesses=[
                    {"witness_kind": "obj-11", "producer": "autoswarm",
                     "content_sha256": "sha256:" + "0" * 64}]))
        with self.assertRaises(ConflictError) as ctx:
            # a hand-built COMPLETE receipt joining real admitted rows is
            # still refused: it was never ADMITTED (cap-30, D-C3-2)
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       self._receipt())
        self.assertIn("not admitted", str(ctx.exception))
        self.seq.complete_evidence(self.attempt_id, self.fence,
                                   self._collect()["receipt_address"])

    def test_ch3_integrity_failure_blocks(self):
        """INV-23's registered chapter-3 test (CH3-integrity-failure-blocks),
        live since C3-M1: admitted evidence whose bytes are lost classifies
        the receipt INTEGRITY_FAILED (the non-raising D-66 probe, D-C3-3);
        t-a13 fails the candidate at once and t-s45 HALTs the sequence
        beside it, durably blocking acceptance before verification — and
        the next recover() independently halts at the D-66 witness
        re-hash, the deliberate interplay D-C3-3 records."""
        self._to_candidate_frozen()
        # lose the transcript's bytes after admission
        path = self.rt.store._path_for(self.entry_end["content_sha256"])
        Path(str(path)).unlink()
        outcome = self._collect()
        self.assertEqual(outcome["completeness"], "INTEGRITY_FAILED")
        with self.assertRaises(ConflictError):
            # INTEGRITY_FAILED can never complete the evidence
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       outcome["receipt_address"])
        self.seq.fail_candidate(
            self.attempt_id, self.fence, outcome["receipt_address"],
            reason="witness bytes lost")
        self.assertEqual(self._attempt_state(), "FAILED")
        seq_state = self.rt.db.conn.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()["state"]
        self.assertEqual(seq_state, "HALTED")
        rules = self._rules()
        self.assertIn("t-a13", rules)
        self.assertIn("t-s45", rules)
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()  # D-66: the lost bytes halt recovery too

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

    def test_t_a13_incomplete_fails_only_beyond_bound(self):
        """The INCOMPLETE arm, live since C3-M1: a required evidence kind
        (obj-12, COMMAND_GOVERNANCE) never admitted classifies the receipt
        INCOMPLETE with the derived D-C3-1 bound (freeze + window); inside
        the window t-a13 refuses as premature and nothing changes; once
        the bound elapses the SAME admitted receipt fails the candidate
        and t-s45 halts the sequence."""
        self._to_candidate_frozen(governance=False)
        outcome = self._collect()
        self.assertEqual(outcome["completeness"], "INCOMPLETE")
        with self.assertRaises(ConflictError):
            # the classification half of t-s09 refuses completion too
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       outcome["receipt_address"])
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence, outcome["receipt_address"],
                reason="too eager")
        self.assertIn("premature", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)
        self.rt.clock.advance(3601)  # past freeze + 3600s (D-C3-1 default)
        self.seq.fail_candidate(
            self.attempt_id, self.fence, outcome["receipt_address"],
            reason="evidence never completed")
        self.assertEqual(self._attempt_state(), "FAILED")
        rules = self._rules()
        self.assertIn("t-a13", rules)
        self.assertIn("t-s45", rules)
        report = self.seq.recover()
        self.assertEqual(report["intents_in_doubt"], [])

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
        whose attempt_id is wrong is caught by the binding check (INV-69)
        before the admitted-classification seam is even consulted."""
        self._to_candidate_frozen()
        before = self._journal_count()
        past = "2026-11-09T11:00:00.000Z"
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INCOMPLETE", bound=past,
                              attempt_id="at_forged"),
                reason="forged deadline + binding")
        self.assertIn("INV-69", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_declared_facts_carry_no_authority(self):
        """Adversarial (D-C3-2): well-formed hand-built receipts that were
        never ADMITTED grant nothing — an INCOMPLETE one with an elapsed
        bound cannot fail the candidate, and a COMPLETE one cannot
        complete the evidence; state and journal stay unchanged."""
        self._to_candidate_frozen()
        before = self._journal_count()
        past = "2026-11-09T11:00:00.000Z"
        self.rt.clock.advance(7200)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INCOMPLETE", bound=past),
                reason="declared, never admitted")
        self.assertIn("not admitted", str(ctx.exception))
        with self.assertRaises(ConflictError) as ctx:
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       self._receipt())
        self.assertIn("not admitted", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_correct_binding_forged_classification(self):
        """Adversarial: a receipt with correct attempt binding and
        contract hash but a self-asserted INTEGRITY_FAILED classification
        is stopped at the authority boundary — it was never ADMITTED, and
        declared classifications carry no authority (cap-30, D-C3-2)."""
        self._to_candidate_frozen()
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INTEGRITY_FAILED"),
                reason="forged classification")
        self.assertIn("not admitted", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.assertEqual(self._journal_count(), before)

    def test_t_a13_correct_binding_forged_deadline(self):
        """Adversarial: a receipt with correct binding and a fabricated
        past deadline is stopped at the authority boundary (never
        admitted) — not at the deadline comparison, which it would
        otherwise pass."""
        self._to_candidate_frozen()
        before = self._journal_count()
        past = "2026-11-09T11:00:00.000Z"
        self.rt.clock.advance(3601)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                self._receipt(completeness="INCOMPLETE", bound=past),
                reason="forged deadline, correct binding")
        self.assertIn("not admitted", str(ctx.exception))
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


class WitnessAdmissionTests(ExecutionTestCase):
    """The M3 admission seam: D-21 structured entries, ledger-derived
    correlation, idempotency, state gating, and recovery pairing."""

    def test_admission_requires_bound_unfrozen_execution(self):
        with self.assertRaises(ConflictError):
            # RESERVED: no bound call yet, nothing to correlate with
            self._admit("obj-11", "autoswarm", WITNESS_START)
        self._to_candidate_frozen()
        with self.assertRaises(ConflictError):
            # after t-a05 the candidate is frozen: late evidence is refused
            self._admit("obj-12", "helios", b"late governance witness\n")

    def test_admission_vocabulary_and_producer_binding(self):
        self._dispatch()
        with self.assertRaises(ConflictError):
            self._admit("obj-99", "autoswarm", WITNESS_START)  # unknown kind
        with self.assertRaises(ConflictError):
            # helios is not the registered minter of execution-lifecycle
            self._admit("obj-11", "helios", WITNESS_START)
        with self.assertRaises(ConflictError):
            self._admit("obj-11", "nobody", WITNESS_START)

    def test_admission_entry_carries_ledger_correlation(self):
        """The D-21 correlation comes from attempt_calls, never the caller:
        before t-a03 the native id is absent (D-37); after it, present."""
        self._dispatch()
        first = self._admit("obj-11", "autoswarm", WITNESS_START)
        row = self.rt.db.conn.execute(
            "SELECT * FROM attempt_witnesses WHERE admission_id = ?",
            (first["admission_id"],)).fetchone()
        self.assertEqual(row["call_id"], self.call_id)
        self.assertIsNone(row["native_session_id"])
        self.assertEqual(row["producer"], "autoswarm")
        # byte identity through the seam (D-38)
        self.assertEqual(self.rt.store.get(row["content_sha256"]),
                         WITNESS_START)
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 first["content_address"])
        second = self._admit("obj-13", "agent_clis", WITNESS_END)
        row = self.rt.db.conn.execute(
            "SELECT * FROM attempt_witnesses WHERE admission_id = ?",
            (second["admission_id"],)).fetchone()
        self.assertEqual(row["native_session_id"], "native-sess-9")

    def test_admission_is_idempotent_on_identity(self):
        self._dispatch()
        first = self._admit("obj-11", "autoswarm", WITNESS_START)
        self.assertFalse(first["duplicate"])
        before = self._journal_count()
        again = self._admit("obj-11", "autoswarm", WITNESS_START)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["admission_id"], first["admission_id"])
        self.assertEqual(self._journal_count(), before)  # nothing claimed
        rows = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_witnesses").fetchone()["n"]
        self.assertEqual(rows, 1)

    def test_recovery_pairs_witness_rows_and_journal(self):
        """D-68 pairing both ways: a journaled admission without its row
        HALTs, and a forged row without its journal entry HALTs."""
        self._dispatch()
        admitted = self._admit("obj-11", "autoswarm", WITNESS_START)
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_witnesses WHERE admission_id = ?",
                       (admitted["admission_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_recovery_rejects_forged_witness_row(self):
        self._dispatch()
        self._admit("obj-11", "autoswarm", WITNESS_START)
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO attempt_witnesses (admission_id, attempt_id,"
                " witness_kind, producer, content_sha256, call_id,"
                " native_session_id, admitted_utc)"
                " SELECT 'wit_' || lower(hex(randomblob(16))), attempt_id,"
                " 'obj-12', 'helios', content_sha256, call_id,"
                " native_session_id, admitted_utc FROM attempt_witnesses"
                " LIMIT 1")
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


if __name__ == "__main__":
    unittest.main()
