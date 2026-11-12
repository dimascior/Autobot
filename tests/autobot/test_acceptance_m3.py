"""The M3 acceptance scenario (M3_SPEC.md): ONE frozen TaskContract through
ONE authorized specialist execution over the deterministic fake AutoSwarm
transport, preserving identity, authority and evidence throughout — the
eleven published criteria in one integration test, plus the adversarial
arms (uncertain launch outcome, hidden session, stale authority, duplicate
dispatch, missing evidence, the fail-closed t-a13 boundary)."""

import json
import tempfile
import unittest
from pathlib import Path

import support
from fake_transport import FakeTransport
from autobot import (
    Collector,
    Database,
    Sequencer,
    Store,
    project_prompt,
    project_state,
    reconcile_dispatched,
)
from autobot.models import AuthorityLost, ConflictError

GUIDANCE = ("Produce the chapter 1 golden vectors exactly as the frozen"
            " contract states; stay inside the allowed scope.")
TRANSCRIPT = b"transcript: builder produced the vectors\nsecond line\n"


class AcceptanceBase(unittest.TestCase):
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

    def _to_reserved(self, ttl_seconds=360000):
        self.seq.acquire_lease(ttl_seconds=ttl_seconds)
        self.seq.mark_ready(self.sequence_id)
        info = self.seq.start_execution(self.sequence_id)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        self.attempt_contract_hash = info["attempt_contract_hash"]
        self.seq.reserve_attempt(self.attempt_id, expected_fence=self.fence)
        return info

    def _projection(self, store=None):
        rec = self.rt.db.conn.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (self.result["record_hash"],)).fetchone()
        payload = json.loads(
            (store or self.rt.store).get(rec["content_address"]).decode("utf-8"))
        identity = {"sequence_id": self.sequence_id,
                    "attempt_id": self.attempt_id, "fence": self.fence,
                    "attempt_contract_hash": self.attempt_contract_hash}
        return project_prompt(store or self.rt.store, payload, "builder",
                              identity, {"author": "planner",
                                         "guidance": GUIDANCE})

    def _attempt_state(self, db=None):
        return (db or self.rt.db).conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["state"]

    def _journal_count(self, db=None):
        return (db or self.rt.db).conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]


class AcceptanceScenario(AcceptanceBase):
    def test_the_eleven_criteria(self):
        golden = support.load_golden()["V-A"]

        # 1. a valid objective is a frozen TaskContractRecord (V-A).
        self.assertEqual(self.result["outcome"], "NEW_RECORD")
        rec = self.rt.db.conn.execute(
            "SELECT * FROM records WHERE record_hash = ?",
            (self.result["record_hash"],)).fetchone()
        self.assertIsNotNone(rec)

        # 2. Registry admission froze it and coupled its Sequence; the
        #    identity tiers equal the goldens.
        self.assertEqual(
            (rec["requirement_digest"], rec["contract_digest"],
             rec["record_hash"]),
            (golden["requirement_digest"], golden["contract_digest"],
             golden["record_hash"]))
        self.assertEqual(rec["eligibility"], golden["eligibility"])
        srow = self.rt.db.conn.execute(
            "SELECT state, fence FROM sequences WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()
        self.assertEqual(srow["state"], "DECLARED")

        # 3. orchestration requests an eligible Attempt.
        self._to_reserved()

        # 4. the Attempt's authority and lifecycle state are durable.
        att = self.rt.db.conn.execute(
            "SELECT state, fence FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        seq_fence = self.rt.db.conn.execute(
            "SELECT fence FROM sequences WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()["fence"]
        self.assertEqual((att["state"], att["fence"], seq_fence),
                         ("RESERVED", self.fence, self.fence))
        lease = self.rt.db.conn.execute(
            "SELECT holder, epoch FROM instance_lease").fetchone()
        self.assertEqual((lease["holder"], lease["epoch"]),
                         ("test-instance", 1))

        # 5. PromptProjection: content-addressed, bound to the exact record +
        #    role + attempt identity, and reproducible.
        data, address = self._projection()
        data2, address2 = self._projection()
        self.assertEqual((data, address), (data2, address2))
        artifact = json.loads(data.decode("utf-8"))
        self.assertEqual(artifact["authority_effect"], "none")
        self.assertEqual(artifact["record"]["record_hash"],
                         golden["record_hash"])
        self.assertEqual(artifact["attempt"]["attempt_id"], self.attempt_id)
        self.assertEqual(artifact["role"], "builder")
        self.assertEqual(artifact["engineering_context"],
                         {"provenance": "chief-engineer-guidance",
                          "author": "planner", "guidance": GUIDANCE})

        # 6. the fake AutoSwarm receives the authorized request: dispatch
        #    commits the minted call identity BEFORE the launch, and the
        #    launch is idempotent on it (duplicate-dispatch arm).
        call_id = self.seq.dispatch_attempt(
            self.attempt_id, "builder", "autoswarm", address)["call_id"]
        transport = FakeTransport()
        native = transport.launch(address, "autoswarm", "builder", call_id)
        self.assertEqual(
            transport.launch(address, "autoswarm", "builder", call_id),
            native)
        self.assertEqual(len(transport.sessions), 1)

        # 10a. a process restart reconstructs the execution relationship
        #      from durable state alone (the provider side survives).
        self.rt.db.close()
        db = Database(self.dir / "autobot.db")
        store = Store(self.dir / "store")
        self.rt.db, self.rt.store = db, store  # tearDown closes the reopened db
        seq = Sequencer(db, store, self.clock, holder="test-instance")
        seq.recover()
        self.assertEqual(self._attempt_state(db), "DISPATCHED")
        crow = db.conn.execute(
            "SELECT call_id, native_session_id FROM attempt_calls"
            " WHERE attempt_id = ?", (self.attempt_id,)).fetchone()
        self.assertEqual(crow["call_id"], call_id)
        self.assertIsNone(crow["native_session_id"])
        self.assertEqual(seq.acquire_lease(ttl_seconds=360000), 2)

        # 7. the session correlates to the exact Attempt: adoption through
        #    find_session(call_id), evidence admitted, t-a03 binds the id.
        report = reconcile_dispatched(seq, transport)
        self.assertEqual(report, [{
            "attempt_id": self.attempt_id, "call_id": call_id,
            "action": "adopted", "native_session_id": native}])
        crow = db.conn.execute(
            "SELECT native_session_id FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()
        self.assertEqual(crow["native_session_id"], native)
        self.assertEqual(self._attempt_state(db), "RUNNING")

        # 8. execution lifecycle evidence is preserved byte-identically
        #    through the admission seam, with ledger-derived correlation.
        transport.scripted_collect[call_id] = {
            "witness_files": [("obj-12", "helios",
                               b"command governance log\n"),
                              ("obj-13", "agent_clis", TRANSCRIPT)],
            "outcome": "success"}
        collected = transport.collect(call_id)
        receipt_entries = []
        last_address = None
        for kind, producer, data_bytes in collected["witness_files"]:
            admitted = seq.admit_witness(self.attempt_id, kind, producer,
                                         data_bytes)
            self.assertEqual(store.get(admitted["content_address"]),
                             data_bytes)
            receipt_entries.append(
                {"witness_kind": kind, "producer": producer,
                 "content_sha256": admitted["content_address"]})
            last_address = admitted["content_address"]
        wrows = [dict(r) for r in db.conn.execute(
            "SELECT witness_kind, producer, content_sha256, call_id,"
            " native_session_id FROM attempt_witnesses WHERE attempt_id = ?"
            " ORDER BY rowid", (self.attempt_id,)).fetchall()]
        self.assertEqual(len(wrows), 3)  # adoption + governance + transcript
        by_kind = {row["witness_kind"]: row for row in wrows}
        for row in wrows:
            self.assertEqual(row["call_id"], call_id)
        # the adoption witness was admitted at DISPATCHED (no native id yet,
        # D-37); the collected kinds after t-a03 carry the adopted session.
        self.assertIsNone(by_kind["obj-11"]["native_session_id"])
        self.assertEqual(by_kind["obj-12"]["native_session_id"], native)
        self.assertEqual(by_kind["obj-13"]["native_session_id"], native)

        # per-role completion, then freeze and the real evidence join.
        self.assertEqual(
            seq.finish_attempt(self.attempt_id, "builder",
                               collected["outcome"], last_address),
            "FINISHED")
        candidate = store.put(b"candidate: tree stand-in\n")
        seq.freeze_candidate(self.attempt_id, self.fence, candidate)
        # C3-M1: the registered Collector derives the obj-15 classification
        # from the frozen record's evidence_requirements over the admitted
        # rows (COMPLETE here: obj-11 + obj-12 present), mints the
        # canonical receipt and admits it; completion consumes the
        # ADMITTED classification.
        outcome = Collector(seq, store).collect_and_admit(self.attempt_id)
        self.assertEqual(outcome["completeness"], "COMPLETE")
        receipt = outcome["receipt_address"]
        seq.complete_evidence(self.attempt_id, self.fence, receipt)
        self.assertEqual(self._attempt_state(db), "EVIDENCE_COMPLETE")

        # 9. the Attempt's transitions match the frozen machine, in order,
        #    nothing else interleaved for this subject.
        subject_rules = [
            json.loads(r["payload"])["rule"] for r in db.conn.execute(
                "SELECT payload FROM transition_journal ORDER BY seq").fetchall()
            if json.loads(r["payload"])["machine"] == "attempt"
            and json.loads(r["payload"])["subject_id"] == self.attempt_id]
        self.assertEqual(subject_rules, [
            "t-a01", "t-a02", "t-a03", "t-a04", "t-a05", "t-a06"])

        # 10b. recovery after the full flow still validates everything.
        report = seq.recover()
        self.assertEqual(report["intents_in_doubt"], [])

        # 11. the Chief Engineer's ProjectState reflects the execution and
        #     its next obligations: verification is the named next gate.
        view = project_state(db)
        # M4: the sequence walks beside its attempt (t-s07 then t-s09), so
        # evidence completion leaves it VERIFYING, awaiting the M4 gates.
        self.assertEqual(view["sequences_by_state"],
                         {"VERIFYING": [self.sequence_id]})
        entry = [a for a in view["attempts"]
                 if a["attempt_id"] == self.attempt_id][0]
        self.assertEqual(entry["state"], "EVIDENCE_COMPLETE")
        self.assertEqual(entry["witnesses"], 3)
        self.assertEqual(entry["call"]["call_id"], call_id)
        self.assertEqual(entry["call"]["native_session_id"], native)
        self.assertEqual(view["obligations"]["awaiting_verification"],
                         [{"attempt_id": self.attempt_id,
                           "next_gate": "verification"}])
        self.assertEqual(view["work_eligibility"]["dispatch_attempt"], [])
        self.assertEqual(view["journal"]["entries"],
                         self._journal_count(db))


class AcceptanceFailureArms(AcceptanceBase):
    def test_uncertain_outcome_holds_then_positive_failure_is_t_a10(self):
        """Launch-failure arm with the uncertainty rule: no session and no
        positive determination → the attempt HOLDS in DISPATCHED (held);
        t-a10 fires only once the transport positively establishes the
        failure."""
        self._to_reserved()
        _, address = self._projection()
        call_id = self.seq.dispatch_attempt(
            self.attempt_id, "builder", "autoswarm", address)["call_id"]
        transport = FakeTransport()  # launch never happened: no session
        before = self._journal_count()
        report = reconcile_dispatched(self.seq, transport)
        self.assertEqual(report[0]["action"], "held")
        self.assertEqual(self._attempt_state(), "DISPATCHED")
        self.assertEqual(self._journal_count(), before)  # no transition
        transport.failures[call_id] = "provider error record: spawn failed"
        report = reconcile_dispatched(self.seq, transport)
        self.assertEqual(report[0]["action"], "failed")
        self.assertEqual(self._attempt_state(), "FAILED")
        rules = [json.loads(r["payload"])["rule"]
                 for r in self.rt.db.conn.execute(
                     "SELECT payload FROM transition_journal ORDER BY seq"
                 ).fetchall()]
        self.assertIn("t-a10", rules)

    def test_hidden_session_stays_uncertain_then_adopts(self):
        """find_session(call_id) → None is NOT failure proof: a session
        that exists but is unobservable holds the attempt in DISPATCHED;
        once observable it is adopted, never duplicated."""
        self._to_reserved()
        _, address = self._projection()
        call_id = self.seq.dispatch_attempt(
            self.attempt_id, "builder", "autoswarm", address)["call_id"]
        transport = FakeTransport()
        native = transport.launch(address, "autoswarm", "builder", call_id)
        transport.hidden.add(call_id)
        report = reconcile_dispatched(self.seq, transport)
        self.assertEqual(report[0]["action"], "held")
        self.assertEqual(self._attempt_state(), "DISPATCHED")
        transport.hidden.discard(call_id)
        report = reconcile_dispatched(self.seq, transport)
        self.assertEqual(report[0]["action"], "adopted")
        self.assertEqual(report[0]["native_session_id"], native)
        self.assertEqual(self._attempt_state(), "RUNNING")
        self.assertEqual(len(transport.sessions), 1)

    def test_stale_authority_dispatches_nothing(self):
        """Stale-authority arm: a Sequencer holding a fenced-out epoch
        cannot dispatch — the ledger and journal are untouched."""
        self._to_reserved(ttl_seconds=60)
        _, address = self._projection()
        self.clock.advance(61)  # the holder's lease expires
        usurper = Sequencer(self.rt.db, self.rt.store, self.clock,
                            holder="takeover-instance")
        self.assertEqual(usurper.acquire_lease(ttl_seconds=360000), 2)
        before = self._journal_count()
        with self.assertRaises(AuthorityLost):
            self.seq.dispatch_attempt(
                self.attempt_id, "builder", "autoswarm", address)
        self.assertEqual(self._attempt_state(), "RESERVED")
        self.assertEqual(self._journal_count(), before)
        self.assertIsNone(self.rt.db.conn.execute(
            "SELECT 1 FROM attempt_calls WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone())

    def test_missing_evidence_blocks_completion_and_fails_beyond_bound(self):
        """Missing-evidence arm, live since C3-M1: a receipt naming
        evidence the admission seam never saw cannot complete t-a06; a
        hand-built classification carries no authority (not admitted); the
        collector's INCOMPLETE classification refuses completion (the
        t-s09 classification half) and refuses t-a13 inside its window —
        and once the derived bound elapses, t-a13 fails the candidate with
        t-s45 halting the sequence beside it."""
        self._to_reserved()
        _, address = self._projection()
        self.seq.dispatch_attempt(
            self.attempt_id, "builder", "autoswarm", address)
        started = self.seq.admit_witness(
            self.attempt_id, "obj-11", "autoswarm", b"session started\n")
        self.seq.attempt_running(self.attempt_id, "native-sess-1",
                                 started["content_address"])
        # COMMAND_GOVERNANCE (obj-12) is required by the frozen record but
        # never admitted here — the missing-evidence arm.
        finished = self.seq.admit_witness(
            self.attempt_id, "obj-13", "agent_clis", TRANSCRIPT)
        self.seq.finish_attempt(self.attempt_id, "builder", "success",
                                finished["content_address"])
        self.seq.freeze_candidate(self.attempt_id, self.fence,
                                  self.rt.store.put(b"candidate\n"))
        good_entry = {"witness_kind": "obj-13", "producer": "agent_clis",
                      "content_sha256": finished["content_address"]}
        phantom = {"witness_kind": "obj-12", "producer": "helios",
                   "content_sha256": "sha256:" + "0" * 64}

        def receipt(entries, completeness="COMPLETE"):
            return self.rt.store.put(json.dumps(
                {"schema_version": "v1", "kind": "ExecutionReceipt",
                 "attempt_id": self.attempt_id,
                 "attempt_contract_hash": self.attempt_contract_hash,
                 "completeness": completeness,
                 "admitted_witnesses": entries},
                sort_keys=True).encode("utf-8"))

        with self.assertRaises(ConflictError):
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       receipt([good_entry, phantom]))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        with self.assertRaises(ConflictError) as ctx:
            self.seq.fail_candidate(
                self.attempt_id, self.fence,
                receipt([good_entry], completeness="INTEGRITY_FAILED"),
                reason="forged classification in the integrated flow")
        self.assertIn("not admitted", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            self.attempt_id)
        self.assertEqual(outcome["completeness"], "INCOMPLETE")
        with self.assertRaises(ConflictError):
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       outcome["receipt_address"])
        with self.assertRaises(ConflictError):
            self.seq.fail_candidate(
                self.attempt_id, self.fence, outcome["receipt_address"],
                reason="premature")  # the bound has not elapsed
        self.assertEqual(self._attempt_state(), "CANDIDATE_FROZEN")
        self.clock.advance(3601)  # past the derived D-C3-1 bound
        self.seq.fail_candidate(
            self.attempt_id, self.fence, outcome["receipt_address"],
            reason="evidence never completed")
        self.assertEqual(self._attempt_state(), "FAILED")
        seq_state = self.rt.db.conn.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()["state"]
        self.assertEqual(seq_state, "HALTED")


if __name__ == "__main__":
    unittest.main()
