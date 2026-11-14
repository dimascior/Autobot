"""TI-1 WP4: the holes the mutation-matrix enumeration exposed, filled.

Enumerating the existing refusal assertions against the target invariant
list showed which registered refusals were proven by error CLASS only -
no marker pinned on the refusal text, so the test could not tell the
named rule from any other ConflictError or IntegrityHalt at that seam.
Each test here closes one such hole in the chapter-1 negative shape: the
unplanted path passes (the control), a specific defect is planted, and
the refusal is pinned by its marker. The registry rows in
``mutation_matrix.json`` cite these tests; ``test_mutation_matrix``
enforces the pins.
"""

import json
import tempfile
import unittest
from pathlib import Path

import auditor2
import support
from fake_verifier import bound_receipt_bytes
from test_independent_state import admit, base_runtime
from test_provenance import governance_bytes
from autobot import Collector, ProductionVerifierRunner, Sequencer, Store
from autobot import journal
from autobot.git_adapter import build_candidate_payload, capture_tree
from autobot.models import (
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    JournalBroken,
    LeaseUnavailable,
    PermitExhaustedError,
    StaleFenceError,
)
from autobot.verification import environment_satisfies
from autobot.verifier_runner import resolve_closure
from contract_library import canonicalize


class LedgerCoreHoleTests(unittest.TestCase):
    """Chapter 2's registered refusals, each pinned by its marker."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rt, self.result = support.admitted_runtime(
            self.dir, clock=support.manual_clock())
        self.seq = self.rt.sequencer
        self.sequence_id = self.result["sequence_id"]

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _executing(self):
        self.seq.acquire_lease(ttl_seconds=360000)
        self.seq.mark_ready(self.sequence_id)
        return self.seq.start_execution(self.sequence_id)

    def test_stale_fence_names_inv13(self):
        """CH2-stale-fence-rejected (INV-13)."""
        first = self._executing()
        # control: the current fence carries authority
        self.assertEqual(self.seq.reserve_attempt(first["attempt_id"],
                                                  first["fence"]), 1)
        self.seq.fail_attempt(first["attempt_id"], reason="launch_failed")
        self.seq.retry_sequence(self.sequence_id)
        second = self.seq.start_execution(self.sequence_id)
        # plant: the superseded fence presented for the new attempt
        with self.assertRaises(StaleFenceError) as caught:
            self.seq.reserve_attempt(second["attempt_id"],
                                     expected_fence=first["fence"])
        self.assertIn("INV-13", str(caught.exception))
        self.assertEqual(self.seq.reserve_attempt(second["attempt_id"],
                                                  second["fence"]), 2)

    def test_record_bytes_of_another_record_halt_execution(self):
        """CH2-reserve-requires-frozen-record (INV-01, INV-11): bytes that
        re-hash to their address (so D-66 is satisfied) but are not THIS
        record's are caught by the tier recompute at t-s06."""
        other = self.rt.registry.admit_payload(
            support.load_fixture("vector_payloads.json")["V-C"],
            submitted_by="planner")
        self.assertEqual(other["outcome"], "NEW_RECORD")
        # control: an untouched record executes
        self._executing()
        self.seq.mark_ready(other["sequence_id"])
        mine = self.rt.db.conn.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (self.result["record_hash"],)).fetchone()["content_address"]
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE records SET content_address = ? WHERE"
                       " record_hash = ?", (mine, other["record_hash"]))
        with self.assertRaises(IntegrityHalt) as caught:
            self.seq.start_execution(other["sequence_id"])
        self.assertIn("INV-11", str(caught.exception))

    def test_second_live_attempt_names_inv12(self):
        """CH2-single-current-attempt (INV-12): with the state gate forged
        open, the partial unique index is what refuses."""
        self._executing()  # the control: one current attempt exists
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE sequences SET state = 'READY' WHERE"
                       " sequence_id = ?", (self.sequence_id,))
        with self.assertRaises(ConflictError) as caught:
            self.seq.start_execution(self.sequence_id)
        self.assertIn("INV-12", str(caught.exception))
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempts").fetchone()["n"], 1)

    def test_lease_holder_and_fenced_out_writer_are_named(self):
        """CH2-instance-lease (INV-25a): a second holder is refused while
        the lease is valid, and after takeover the old epoch is named as
        lost."""
        self.seq.acquire_lease(ttl_seconds=100)
        self.assertEqual(self.seq.mark_ready(self.sequence_id),
                         "READY")  # the control: the holder mutates
        other = Sequencer(self.rt.db, self.rt.store, self.rt.clock,
                          holder="other-instance")
        with self.assertRaises(LeaseUnavailable) as caught:
            other.acquire_lease(ttl_seconds=100)
        self.assertIn("INV-25a", str(caught.exception))
        self.rt.clock.advance(101)
        self.assertEqual(other.acquire_lease(ttl_seconds=100), 2)
        with self.assertRaises(AuthorityLost) as caught:
            self.seq.start_execution(self.sequence_id)
        self.assertIn("instance lease lost", str(caught.exception))

    def test_tampered_journal_names_the_broken_entry_and_link(self):
        """CH2-failpoint-matrix (INV-10): recovery refuses to resume over
        a journal that does not re-hash or does not chain - JournalBroken,
        never a guess."""
        self._executing()
        cursor = self.rt.db.conn.cursor()
        entries = journal.validate(cursor)  # the control
        self.assertGreater(entries, 10)
        self.seq.recover()
        original = self.rt.db.conn.execute(
            "SELECT seq, payload, previous_hash FROM transition_journal"
            " ORDER BY seq DESC LIMIT 1").fetchone()
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE transition_journal SET payload = ? WHERE seq"
                       " = ?", (original["payload"].replace(
                           '"to_state":"EXECUTING"', '"to_state":"READY"'),
                           original["seq"]))
        with self.assertRaises(JournalBroken) as caught:
            self.seq.recover()
        self.assertIn("entry hash mismatch", str(caught.exception))
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE transition_journal SET payload = ?,"
                       " previous_hash = ? WHERE seq = ?",
                       (original["payload"], "sha256:" + "0" * 64,
                        original["seq"]))
        with self.assertRaises(JournalBroken) as caught:
            self.seq.recover()
        self.assertIn("chain link broken", str(caught.exception))

    def test_second_consume_names_inv42(self):
        """CH2-permit-race-two-reservers-one-use (INV-42)."""
        attempt_id = self._executing()["attempt_id"]
        self.seq.grant_permit(self.sequence_id, permitted_uses=1)
        reservation = self.seq.reserve_use(self.sequence_id, attempt_id)
        self.seq.consume_reservation(reservation, cause="ponr")  # control
        with self.assertRaises(ConflictError) as caught:
            self.seq.consume_reservation(reservation, cause="ponr again")
        self.assertIn("INV-42", str(caught.exception))

    def test_overbooking_names_inv48(self):
        """CH2-permit-counters (INV-48)."""
        attempt_id = self._executing()["attempt_id"]
        self.seq.grant_permit(self.sequence_id, permitted_uses=1)
        self.seq.reserve_use(self.sequence_id, attempt_id)  # control
        with self.assertRaises(PermitExhaustedError) as caught:
            self.seq.reserve_use(self.sequence_id, attempt_id)
        self.assertIn("INV-48", str(caught.exception))

    def test_expired_reservation_lease_grants_no_intent(self):
        """CH2-reservation-lease (INV-60): a reservation's own lease
        window bounds the authority it carries."""
        attempt_id = self._executing()["attempt_id"]
        self.seq.grant_permit(self.sequence_id, permitted_uses=2)
        self.seq.reserve_use(self.sequence_id, attempt_id,
                             lease_seconds=100)
        self.seq.mint_intent(  # control: inside the lease window
            self.sequence_id, attempt_id, "eff-canonical-push",
            target="refs/heads/main", expected_old="sha256:" + "ab" * 32)
        self.rt.clock.advance(101)
        with self.assertRaises(ConflictError) as caught:
            self.seq.mint_intent(
                self.sequence_id, attempt_id, "eff-canonical-push",
                target="refs/heads/main",
                expected_old="sha256:" + "ab" * 32)
        self.assertIn("INV-60", str(caught.exception))

    def test_missing_committed_bytes_name_the_d66_halt(self):
        """INV-72 / D-66: authoritative state is the ledger PLUS its
        content store - a committed reference whose bytes are gone halts
        the production recovery by name, and the independent auditor
        names the same address."""
        self.seq.recover()  # the control, both oracles
        self.assertEqual(auditor2.audit(self.dir)["findings"], [])
        address = self.rt.db.conn.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (self.result["record_hash"],)).fetchone()["content_address"]
        Path(str(self.rt.store._path_for(address))).unlink()
        with self.assertRaises(IntegrityHalt) as caught:
            self.seq.recover()
        self.assertIn("committed reference to missing content",
                      str(caught.exception))
        missing = [finding for finding in
                   auditor2.audit(self.dir)["findings"]
                   if finding["check"] == "content.missing"]
        self.assertTrue(missing)
        self.assertTrue(all(address in finding["detail"]
                            for finding in missing))


class EvidencePlaneHoleTests(unittest.TestCase):
    """Chapter 3's and chapter 4's registered refusals, pinned."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rt, _va = base_runtime(self.dir)
        self.seq = self.rt.sequencer

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _finished(self, objective, governance=(b"governance log\n",),
                  artifact=None):
        """One attempt driven to FINISHED with unique transcript bytes."""
        rt, seq = self.rt, self.seq
        admitted = admit(rt, support.local_record_payload(
            objective=objective))
        seq.mark_ready(admitted["sequence_id"])
        info = seq.start_execution(admitted["sequence_id"])
        attempt_id = info["attempt_id"]
        seq.reserve_attempt(attempt_id, info["fence"])
        dispatched = seq.dispatch_attempt(
            attempt_id, "builder", "fake",
            rt.store.put(("projection: %s\n" % objective).encode("utf-8")))
        start = seq.admit_witness(
            attempt_id, "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        seq.attempt_running(attempt_id, "native-sess-9",
                            start["content_address"])
        for data in governance:
            seq.admit_witness(attempt_id, "obj-12", "helios", data)
        end = seq.admit_witness(
            attempt_id, "obj-13", "agent_clis",
            ("transcript: %s\n" % objective).encode("utf-8"))
        if artifact is not None:
            seq.admit_artifact(attempt_id, "artifact/notes", artifact)
        seq.finish_attempt(attempt_id, "builder", "success",
                           end["content_address"])
        return {"attempt_id": attempt_id, "fence": info["fence"],
                "transcript": end["content_address"]}

    def _frozen(self, objective, **kwargs):
        info = self._finished(objective, **kwargs)
        support.mint_and_freeze(self.rt, info["attempt_id"], info["fence"])
        return info

    def _collect(self, info):
        return Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])

    def test_candidate_artifact_set_is_the_ledgers_not_a_claim(self):
        """CH3-artifact-provenance-single-attempt (INV-03): an obj-16
        whose artifact set omits an admitted provenance event is refused
        by name; the ledger's own set freezes."""
        info = self._finished("TI-1 INV-03 hole",
                              artifact=b"the one admitted artifact\n")
        repo, _sha_old, sha_new = support.ensure_candidate_repo(self.rt)
        state = self.seq.candidate_state(info["attempt_id"])
        honest = build_candidate_payload(state, self.rt.clock,
                                         capture_tree(repo, sha_new))
        self.assertEqual(len(honest["artifacts"]), 1)
        forged = json.loads(json.dumps(honest))
        forged["artifacts"] = []
        with self.assertRaises(ConflictError) as caught:
            self.seq.freeze_candidate(
                info["attempt_id"], info["fence"],
                self.rt.store.put(canonicalize(forged)))
        self.assertIn("INV-03", str(caught.exception))
        # the control: the unplanted candidate is admitted
        self.seq.freeze_candidate(
            info["attempt_id"], info["fence"],
            self.rt.store.put(canonicalize(honest)))

    def test_integrity_failed_evidence_cannot_complete(self):
        """CH3-integrity-classes (INV-68) and CH3-integrity-failure-blocks
        (INV-23): a deny-then-ran contradiction and lost witness bytes
        each derive INTEGRITY_FAILED, and t-a06/t-s09 refuse it by the
        ADMITTED classification's name."""
        control = self._frozen("TI-1 integrity control")
        outcome = self._collect(control)
        self.assertEqual(outcome["completeness"], "COMPLETE")
        self.seq.complete_evidence(control["attempt_id"], control["fence"],
                                   outcome["receipt_address"])
        sha = "ab" * 32
        denied = self._frozen("TI-1 deny then ran", governance=(
            governance_bytes("native-sess-9", sha, decision="DENY"),
            governance_bytes("native-sess-9", sha,
                             hook_event="PostToolUse", success=True)))
        outcome = self._collect(denied)
        self.assertEqual(outcome["completeness"], "INTEGRITY_FAILED")
        payload = json.loads(self.rt.store.get(
            outcome["receipt_address"]).decode("utf-8"))
        self.assertEqual([c["rule"] for c in payload["contradictions"]],
                         ["deny_then_ran"])
        with self.assertRaises(ConflictError) as caught:
            self.seq.complete_evidence(denied["attempt_id"],
                                       denied["fence"],
                                       outcome["receipt_address"])
        self.assertIn("admitted COMPLETE classification",
                      str(caught.exception))
        lost = self._frozen("TI-1 lost witness bytes")
        Path(str(self.rt.store._path_for(lost["transcript"]))).unlink()
        outcome = self._collect(lost)
        self.assertEqual(outcome["completeness"], "INTEGRITY_FAILED")
        with self.assertRaises(ConflictError) as caught:
            self.seq.complete_evidence(lost["attempt_id"], lost["fence"],
                                       outcome["receipt_address"])
        self.assertIn("INTEGRITY_FAILED", str(caught.exception))

    def test_result_admission_names_its_refusals(self):
        """CH4-llm-output-never-pass (INV-06), SEM-16 and INV-53 at the
        result seam: prose dressed as PASS, a service-dependent verifier
        for a DETERMINISTIC assertion, and a bound registration that no
        longer resolves."""
        rt, seq = self.rt, self.seq
        admitted = admit(rt, support.local_record_payload(
            objective="TI-1 result-seam holes"))
        info = support.drive_to_evidence_complete(rt, admitted)
        attempt_id = info["attempt_id"]
        runner = ProductionVerifierRunner(seq, rt.store)
        state = seq.verification_state(attempt_id)
        environment = runner.capture_environment(
            {"sequence_id": state["sequence_id"], "attempt_id": attempt_id,
             "version": state["version"], "fence": state["fence"]})
        seq.admit_environment_capture(attempt_id, environment)
        # the control: an honest, evidence-backed PASS is admitted
        honest = seq.register_verifier_result(
            attempt_id, "a1", "PASS", "verifier_runner",
            bound_receipt_bytes(seq, attempt_id, "a1", "PASS", environment),
            environment=environment)
        self.assertFalse(honest["duplicate"])
        registration = state["registrations"]["vector-checker"]["payload"]
        prose = rt.store.put(b"All tests passed! Everything looks great.\n")
        dressed = bound_receipt_bytes(
            seq, attempt_id, "a2", "PASS", environment,
            execution={"closure_kind": "python",
                       "entrypoint": registration["entrypoint"],
                       "exit_code": 0, "output_sha256": prose})
        with self.assertRaises(ConflictError) as caught:
            seq.register_verifier_result(
                attempt_id, "a2", "PASS", "verifier_runner", dressed,
                environment=environment)
        self.assertIn("claimed PASS is not supported", str(caught.exception))
        service_bound = support.load_fixture("graph.json")["verifier_llm_v1"]
        with self.assertRaises(ConflictError) as caught:
            runner.run_assertion(
                {"assertion_id": "aX", "mode": "DETERMINISTIC",
                 "verifier_id": "llm-reviewer"}, service_bound, {})
        self.assertIn("SEM-16", str(caught.exception))
        unbound = bound_receipt_bytes(seq, attempt_id, "a2", "PASS",
                                      environment)
        with rt.db.transaction() as tx:
            tx.execute("DELETE FROM objects WHERE object_hash = ?",
                       (support.live_registration_hash("vector-checker",
                                                       "PASS"),))
        with self.assertRaises(IntegrityHalt) as caught:
            seq.register_verifier_result(
                attempt_id, "a2", "PASS", "verifier_runner", unbound,
                environment=environment)
        self.assertIn("INV-53", str(caught.exception))
        self.assertEqual(rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_verifications"
        ).fetchone()["n"], 1)  # only the control was ever admitted


class PureSeamHoleTests(unittest.TestCase):
    """Refusals of pure functions: no ledger needed."""

    def test_forged_artifact_root_names_sem15(self):
        """SEM-15 at dispatch: the closure executes only when its
        artifact_root recomputes."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "store")
            store.put(support.LIVE_PASS_SCRIPT)
            store.put(support.LIVE_LOCK)
            registration = support.live_registration("vector-checker",
                                                     "PASS")
            closure = resolve_closure(store, registration)  # the control
            self.assertEqual(closure["kind"], "python")
            forged = json.loads(json.dumps(registration))
            forged["artifact_root"] = "sha256:" + "9" * 64
            with self.assertRaises(IntegrityHalt) as caught:
                resolve_closure(store, forged)
            self.assertIn("SEM-15", str(caught.exception))

    def test_env_vars_is_not_the_frozen_constraint_section(self):
        """F-C4-2: the frozen constraint section is ``env``; the capture
        field's spelling ``env_vars`` is not a constraint section and
        fails closed - a refusal this seam RETURNS."""
        actual = {"interpreter": {"implementation": "cpython",
                                  "version": "3.12.4"},
                  "platform": {"os": "windows", "architecture": "x86_64"},
                  "env_vars": {"CI": "true"}, "tools": {}}
        self.assertIsNone(
            environment_satisfies({"env": {"CI": "true"}}, actual))
        refusal = environment_satisfies({"env_vars": {"CI": "true"}},
                                        actual)
        self.assertIn("not supported", refusal)
        mismatch = environment_satisfies({"env": {"CI": "false"}}, actual)
        self.assertIn("does not equal the declared exact value", mismatch)


if __name__ == "__main__":
    unittest.main()
