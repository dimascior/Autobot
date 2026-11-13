"""The M4 conformance suite: verification authority and state transitions.

Covers the directive's matrix over the registered rows — t-a07/t-a08 with
t-s10/t-s11/t-s12, t-s07/t-s09 coupling, t-s45's seam, t-a12 staleness —
plus eligibility (SEM-16), INV-53 HALTs, D-52 attestation, environment
binding, budgets, duplicate admissions, stale fences, takeover, and the
journal/recovery pairing for verification rows.
"""

import json
import tempfile
import unittest
from pathlib import Path

import support
from fake_verifier import (
    bound_receipt_bytes,
    environment_bytes,
    transform_receipt_bytes,
)
from autobot import Sequencer, project_state
from autobot.models import (
    AdmissionError,
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    StaleFenceError,
)
from autobot.verification import verifier_eligibility
from contract_library import object_hash

WITNESS_START = b"provider session record\n"
WITNESS_END = b"transcript: verification suite\n"


def human_record_payload():
    """A frozen-contract variant deriving HUMAN_REQUIRED (D-52/SEM-13):
    the live-bound V-A shape plus a HUMAN_ATTESTED assertion bound to the
    service-dependent llm-reviewer registration (the chapter 1 eligibility
    table's HUMAN_ATTESTED-only case) and a non-required reviewer role for
    the t-a12 negative. Live-bound since C4-M2: the role-transform check's
    admission re-executes the bound transform closure, so a record that
    must CONCLUDE verification binds executable registrations (the fixture
    transform has no bytes); the llm registration stays the fixture one —
    a HUMAN_ATTESTED assertion never executes it (SEM-16/D-52)."""
    payload = json.loads(json.dumps(support.load_fixture("va_record.json")))
    graph = support.load_fixture("graph.json")
    llm_hash = object_hash("VerifierRegistration", graph["verifier_llm_v1"])
    payload["task_contract_id"] = "tc_" + "beef" * 8
    payload["acceptance_assertions"]["ids_and_environment_constraints"][0][
        "environment_constraint"] = support.live_environment_constraint()
    payload["acceptance_assertions"]["ids_and_environment_constraints"].append(
        {"assertion_id": "a3", "mode": "HUMAN_ATTESTED",
         "verifier_id": "llm-reviewer"})
    payload["resolved_bindings"]["verifier_registrations"] = sorted((
        support.live_registration_hash("registry-checker", "PASS"),
        support.live_registration_hash("vector-checker", "PASS"),
        llm_hash))
    payload["resolved_bindings"]["transform_registrations"] = [
        support.live_transform_hash("identity-projection")]
    payload["completion_policies"]["reviewer"] = {
        "required": False, "success_outcomes": ["ok"]}
    payload["role_transforms"]["reviewer"] = "identity-projection"
    return payload


class VerificationTestCase(unittest.TestCase):
    """Shared plumbing: a runtime with V-A admitted and helpers that drive
    one attempt to EVIDENCE_COMPLETE (sequence VERIFYING)."""

    extra_seed = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        # The va fixture stays admitted (the admission corpus); the ACTIVE
        # sequence is the LIVE-BOUND V-A shape since C4-M2 — concluding
        # verification now includes the role-transform check, whose
        # admission re-executes the bound transform closure, so the bound
        # registrations must carry executable bytes.
        self.rt, self.va_admitted = support.admitted_runtime(
            self.dir, clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.admitted = self.rt.registry.admit_payload(
            support.local_record_payload(), submitted_by="planner")
        self.assertEqual(self.admitted["outcome"], "NEW_RECORD")
        for name in self.extra_seed:
            self.rt.registry.admit_object(graph[name], submitted_by="you")
        self.seq = self.rt.sequencer
        self.sequence_id = self.admitted["sequence_id"]
        self.projection = self.rt.store.put(b"projection bytes\n")

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    # -- lifecycle helpers -------------------------------------------------

    def _start(self, sequence_id=None):
        sid = sequence_id or self.sequence_id
        self.active_sequence = sid
        self.seq.acquire_lease(ttl_seconds=360000)
        state = self.rt.db.conn.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (sid,)).fetchone()["state"]
        if state == "DECLARED":
            self.seq.mark_ready(sid)
        info = self.seq.start_execution(sid)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        self.attempt_contract_hash = info["attempt_contract_hash"]
        self.seq.reserve_attempt(self.attempt_id, expected_fence=self.fence)
        return info

    def _to_evidence_complete(self, sequence_id=None, role="builder",
                              register_transform=True):
        self._start(sequence_id)
        dispatched = self.seq.dispatch_attempt(self.attempt_id, role,
                                               "fake", self.projection)
        start = self.seq.admit_witness(
            self.attempt_id, "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"],
                                         prompt=support.VA_INPUT))
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"])
        self.seq.admit_witness(self.attempt_id, "obj-12", "helios",
                               b"governance log\n")
        end = self.seq.admit_witness(self.attempt_id, "obj-13", "agent_clis",
                                     WITNESS_END)
        self.seq.finish_attempt(self.attempt_id, role, "success",
                                end["content_address"])
        support.mint_and_freeze(self.rt, self.attempt_id, self.fence)
        # C3-M1: the registered Collector derives-and-admits obj-15.
        receipt = support.collect_receipt(self.rt, self.attempt_id)
        self.seq.complete_evidence(self.attempt_id, self.fence, receipt)
        # C4-M2 (INV-49): the dispatched role declares a transform, so a
        # PASS role-transform check is part of every conclusion; tests that
        # exercise the pre-check seams themselves opt out.
        if register_transform:
            self._register_transform()

    def _register_transform(self, outcome="PASS", **kwargs):
        env = kwargs.pop("environment", None)
        if env is None:
            env = environment_bytes(self._identity())
        receipt = transform_receipt_bytes(self.seq, self.attempt_id, env,
                                          outcome=outcome, **kwargs)
        return self.seq.register_transform_check(
            self.attempt_id, "builder", outcome, "verifier_runner",
            receipt, environment=env)

    def _identity(self):
        return {"sequence_id": self.active_sequence,
                "attempt_id": self.attempt_id,
                "version": 1, "fence": self.fence}

    def _register(self, assertion_id, outcome, environment=None, by=None,
                  **overrides):
        """One bound result through the admission seam: canonical
        obj-19/obj-20 bytes carrying the attempt's REAL durable bindings
        (C4-M1), with the fake obj-18 capture (V-A's a1 constraint wants
        cpython 3.12.4). ``overrides`` forge payload members for the
        binding negatives."""
        env = environment if environment is not None \
            else environment_bytes(self._identity())
        receipt = bound_receipt_bytes(self.seq, self.attempt_id,
                                      assertion_id, outcome, env,
                                      **overrides)
        return self.seq.register_verifier_result(
            self.attempt_id, assertion_id, outcome,
            by or "verifier_runner", receipt, environment=env)

    def _sequence_state(self, sequence_id=None):
        return self.rt.db.conn.execute(
            "SELECT state, repairs_used FROM sequences WHERE sequence_id = ?",
            (sequence_id or self.sequence_id,)).fetchone()

    def _attempt_state(self):
        return self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["state"]

    def _rules(self):
        return [json.loads(r["payload"])["rule"] for r in
                self.rt.db.conn.execute(
                    "SELECT payload FROM transition_journal ORDER BY seq"
                ).fetchall()]

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]


class SequenceArcTests(VerificationTestCase):
    def test_t_s07_and_t_s09_follow_the_attempt(self):
        """freeze_candidate couples t-a05 with t-s07 and complete_evidence
        couples t-a06 with t-s09: the sequence walks EXECUTING →
        CANDIDATE_FROZEN → VERIFYING beside its attempt."""
        self._to_evidence_complete()
        self.assertEqual(self._sequence_state()["state"], "VERIFYING")
        rules = self._rules()
        for rule in ("t-s07", "t-s09"):
            self.assertIn(rule, rules)
        self.assertEqual(self._attempt_state(), "EVIDENCE_COMPLETE")

    def test_t_s09_requires_the_admitted_complete_classification(self):
        """Operator acceptance check 1, carried into C3-M1: the receipt's
        DECLARED completeness still carries no authority — a hand-built
        COMPLETE receipt was never ADMITTED and is refused outright — and
        t-a06/t-s09 now fire only on the collector's admitted COMPLETE
        classification (the classification half of
        execution_receipt_complete, closed)."""
        self._start()
        dispatched = self.seq.dispatch_attempt(self.attempt_id, "builder",
                                               "fake", self.projection)
        start = self.seq.admit_witness(
            self.attempt_id, "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"])
        self.seq.admit_witness(self.attempt_id, "obj-12", "helios",
                               b"governance log\n")
        end = self.seq.admit_witness(self.attempt_id, "obj-13",
                                     "agent_clis", WITNESS_END)
        self.seq.finish_attempt(self.attempt_id, "builder", "success",
                                end["content_address"])
        support.mint_and_freeze(self.rt, self.attempt_id, self.fence)
        declared = self.rt.store.put(json.dumps(
            {"schema_version": "v1", "kind": "ExecutionReceipt",
             "attempt_id": self.attempt_id,
             "attempt_contract_hash": self.attempt_contract_hash,
             "completeness": "COMPLETE",
             "admitted_witnesses": [
                 {"witness_kind": "obj-11", "producer": "autoswarm",
                  "content_sha256": start["content_address"]},
                 {"witness_kind": "obj-13", "producer": "agent_clis",
                  "content_sha256": end["content_address"]},
             ]}, sort_keys=True).encode("utf-8"))
        with self.assertRaises(ConflictError) as ctx:
            self.seq.complete_evidence(self.attempt_id, self.fence,
                                       declared)
        self.assertIn("not admitted", str(ctx.exception))
        receipt = support.collect_receipt(self.rt, self.attempt_id)
        self.seq.complete_evidence(self.attempt_id, self.fence, receipt)
        self.assertEqual(self._attempt_state(), "EVIDENCE_COMPLETE")
        self.assertEqual(self._sequence_state()["state"], "VERIFYING")


class VerificationFlowTests(VerificationTestCase):
    def test_all_pass_concludes_verified(self):
        """t-a07 + t-s10: every declared assertion PASS (a1 under its
        matching obj-18 environment), concluded atomically on both
        machines, with obj-19 references journaled."""
        self._to_evidence_complete()
        env = environment_bytes(self._identity())
        first = self._register("a1", "PASS", environment=env)
        self.assertFalse(first["duplicate"])
        self._register("a2", "PASS")
        result = self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(result, {"attempt": "VERIFIED",
                                  "sequence": "VERIFIED"})
        self.assertEqual(self._attempt_state(), "VERIFIED")
        self.assertEqual(self._sequence_state()["state"], "VERIFIED")
        entries = [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq").fetchall()]
        t_a07 = [e for e in entries if e["rule"] == "t-a07"]
        t_s10 = [e for e in entries if e["rule"] == "t-s10"]
        self.assertEqual((len(t_a07), len(t_s10)), (1, 1))
        self.assertEqual(len(t_a07[0]["facts"]["verification_receipts"]), 2)
        self.assertEqual(t_s10[0]["facts"]["eligibility"], "AUTOMATIC")
        view = project_state(self.rt.db)
        self.assertEqual(view["obligations"]["verified_awaiting_finalization"],
                         [self.sequence_id])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

    def test_fail_repairs_then_budget_exhausts(self):
        """t-a08 with t-s11 while repair budget remains (repairs_used
        counts), then t-a08 with t-s12 budget_exhausted: V-A's
        repair_policy.max_repairs is 1."""
        self._to_evidence_complete()
        self._register("a1", "PASS",
                       environment=environment_bytes(self._identity()))
        self._register("a2", "FAIL")
        result = self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(result, {"attempt": "REJECTED", "sequence": "READY"})
        row = self._sequence_state()
        self.assertEqual((row["state"], row["repairs_used"]), ("READY", 1))
        self.assertIn("t-s11", self._rules())
        # The repair attempt runs the full lifecycle again and fails again:
        # the budget is now exhausted, so t-s12 HALTs the sequence.
        self._to_evidence_complete()
        self._register("a1", "PASS",
                       environment=environment_bytes(self._identity()))
        self._register("a2", "FAIL")
        result = self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(result, {"attempt": "REJECTED",
                                  "sequence": "HALTED"})
        entries = [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq").fetchall()]
        halt = [e for e in entries if e["rule"] == "t-s12"][-1]
        self.assertEqual(halt["facts"]["reason"], "budget_exhausted")
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

    def test_unresolved_then_inconclusive_then_unknown_halt(self):
        """Conclusion needs every declared assertion resolved; an
        INCONCLUSIVE result concludes nothing and routes to t-s12's
        unknown arm, with the attempt holding EVIDENCE_COMPLETE."""
        self._to_evidence_complete()
        with self.assertRaises(ConflictError):
            self.seq.conclude_verification(self.attempt_id, self.fence)
        self._register("a1", "PASS",
                       environment=environment_bytes(self._identity()))
        self._register("a2", "INCONCLUSIVE")
        with self.assertRaises(ConflictError):
            self.seq.conclude_verification(self.attempt_id, self.fence)
        self.seq.halt_verification(self.sequence_id, "unknown")
        self.assertEqual(self._sequence_state()["state"], "HALTED")
        self.assertEqual(self._attempt_state(), "EVIDENCE_COMPLETE")
        view = project_state(self.rt.db)
        self.assertEqual(view["obligations"]["halted_sequences"],
                         [self.sequence_id])
        with self.assertRaises(ConflictError):
            self.seq.halt_verification(self.sequence_id, "unknown")
        with self.assertRaises(ConflictError):
            self.seq.halt_verification(self.sequence_id, "not-a-reason")


class ResultAdmissionTests(VerificationTestCase):
    def test_admission_gates(self):
        """State, producer, vocabulary and declaration gates on the typed
        admission seam — each fires before any payload is even parsed, so
        junk bytes prove the gate, not the schema."""
        self._start()
        dispatched = self.seq.dispatch_attempt(self.attempt_id, "builder",
                                               "fake", self.projection)
        with self.assertRaises(ConflictError):
            # DISPATCHED: not EVIDENCE_COMPLETE
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner", b"junk")
        start = self.seq.admit_witness(
            self.attempt_id, "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"])
        self.seq.admit_witness(self.attempt_id, "obj-12", "helios",
                               b"governance log\n")
        end = self.seq.admit_witness(self.attempt_id, "obj-13", "agent_clis",
                                     WITNESS_END)
        self.seq.finish_attempt(self.attempt_id, "builder", "success",
                                end["content_address"])
        support.mint_and_freeze(self.rt, self.attempt_id, self.fence)
        receipt = support.collect_receipt(self.rt, self.attempt_id)
        self.seq.complete_evidence(self.attempt_id, self.fence, receipt)
        with self.assertRaises(ConflictError):
            # not the obj-19 minter
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "you", b"junk")
        with self.assertRaises(ConflictError):
            # not in the vocabulary
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "MAYBE", "verifier_runner", b"junk")
        with self.assertRaises(ConflictError):
            # not a declared assertion
            self.seq.register_verifier_result(
                self.attempt_id, "a9", "PASS", "verifier_runner", b"junk")

    def test_environment_binding(self):
        """a1's declared constraint requires a bound, matching obj-18:
        missing capture, wrong-attempt scope and mismatching interpreter
        are each refused; the mismatch routes to t-s12 env_mismatch."""
        self._to_evidence_complete(register_transform=False)
        with self.assertRaises(ConflictError) as missing:
            # no capture supplied and none admitted for the attempt
            self.seq.register_verifier_result(
                self.attempt_id, "a1", "PASS", "verifier_runner", b"junk")
        self.assertIn("obj-18", str(missing.exception))
        wrong_scope = environment_bytes(
            {**self._identity(), "attempt_id": "at_" + "0" * 32})
        with self.assertRaises(ConflictError):
            self._register("a1", "PASS", environment=wrong_scope)
        before = self._journal_count()
        mismatch = environment_bytes(self._identity(), version="0.0.1")
        with self.assertRaises(ConflictError) as caught:
            self._register("a1", "PASS", environment=mismatch)
        self.assertIn("env_mismatch", str(caught.exception))
        # The refusal mutated NOTHING: the durable HALTED state exists only
        # once t-s12 itself commits (operator acceptance check 2).
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self._sequence_state()["state"], "VERIFYING")
        self.seq.halt_verification(self.sequence_id, "env_mismatch")
        self.assertEqual(self._sequence_state()["state"], "HALTED")
        self.assertEqual(self._attempt_state(), "EVIDENCE_COMPLETE")

    def test_duplicate_result_admissions(self):
        """An identical re-admission is idempotent and journals nothing; a
        conflicting one never overwrites."""
        self._to_evidence_complete(register_transform=False)
        env = environment_bytes(self._identity())
        receipt = bound_receipt_bytes(self.seq, self.attempt_id, "a2",
                                      "PASS", env)
        first = self.seq.register_verifier_result(
            self.attempt_id, "a2", "PASS", "verifier_runner", receipt,
            environment=env)
        before = self._journal_count()
        again = self.seq.register_verifier_result(
            self.attempt_id, "a2", "PASS", "verifier_runner", receipt,
            environment=env)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["verification_id"], first["verification_id"])
        self.assertEqual(self._journal_count(), before)
        with self.assertRaises(ConflictError):
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "FAIL", "verifier_runner",
                bound_receipt_bytes(self.seq, self.attempt_id, "a2",
                                    "FAIL", env),
                environment=env)
        rows = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_verifications").fetchone()["n"]
        self.assertEqual(rows, 1)

    def test_stale_fence_and_fenced_out_authority(self):
        """conclude under a stale fence is refused; a fenced-out epoch
        admits and concludes nothing (AuthorityLost), leaving the ledger
        and journal untouched."""
        self._to_evidence_complete()
        self._register("a2", "PASS")
        with self.assertRaises(StaleFenceError):
            self.seq.conclude_verification(self.attempt_id, self.fence + 1)
        self.clock.advance(400000)  # the holder's lease expires
        usurper = Sequencer(self.rt.db, self.rt.store, self.clock,
                            holder="takeover")
        usurper.acquire_lease(ttl_seconds=360000)
        before = self._journal_count()
        with self.assertRaises(AuthorityLost):
            self._register("a1", "PASS",
                           environment=environment_bytes(self._identity()))
        with self.assertRaises(AuthorityLost):
            self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self._attempt_state(), "EVIDENCE_COMPLETE")


class EligibilityAndHaltTests(VerificationTestCase):
    def test_sem16_eligibility_from_registered_requirements(self):
        """SEM-16/D-57 evaluated from the registration itself: a
        service-dependent registration is ineligible for a DETERMINISTIC
        assertion; a pinned python closure is eligible; HUMAN_ATTESTED
        assertions are never mechanically satisfiable."""
        graph = support.load_fixture("graph.json")
        det = {"assertion_id": "aX", "mode": "DETERMINISTIC",
               "verifier_id": "llm-reviewer"}
        refusal = verifier_eligibility(det, graph["verifier_llm_v1"])
        self.assertIn("SEM-16", refusal)
        self.assertIsNone(
            verifier_eligibility(det, graph["verifier_checker_v1"]))
        human = {"assertion_id": "aY", "mode": "HUMAN_ATTESTED",
                 "verifier_id": "llm-reviewer"}
        refusal = verifier_eligibility(human, graph["verifier_llm_v1"])
        self.assertIsNotNone(refusal)
        self.assertIn("D-52", refusal)

    def test_inv53_missing_bound_registration_halts(self):
        """INV-53: a bound verifier registration that no longer resolves in
        the ledger HALTs the admission — never substituted, never skipped."""
        self._to_evidence_complete()
        bindings = support.local_record_payload()[
            "resolved_bindings"]["verifier_registrations"]
        with self.rt.db.transaction() as tx:
            for target in bindings:
                tx.execute("DELETE FROM objects WHERE object_hash = ?",
                           (target,))
        before = self._journal_count()
        with self.assertRaises(IntegrityHalt):
            self._register("a2", "PASS")
        # The integrity stop committed no transition (operator acceptance
        # check 2): the sequence is still VERIFYING, the journal untouched;
        # the DURABLE halt is the explicit t-s12 the integration layer then
        # applies with the observed reason.
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self._sequence_state()["state"], "VERIFYING")
        self.seq.halt_verification(self.sequence_id,
                                   "bound_artifact_unavailable")
        self.assertEqual(self._sequence_state()["state"], "HALTED")
        entries = [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq").fetchall()]
        halt = [e for e in entries if e["rule"] == "t-s12"][-1]
        self.assertEqual(halt["facts"]["reason"], "bound_artifact_unavailable")


class AttestationTests(VerificationTestCase):
    """D-52 on a HUMAN_REQUIRED record (derived eligibility, SEM-13)."""

    extra_seed = ("verifier_llm_v1",)

    def setUp(self):
        super().setUp()
        result = self.rt.registry.admit_payload(
            human_record_payload(), submitted_by="planner")
        self.assertEqual(result["outcome"], "NEW_RECORD")
        self.human_sequence = result["sequence_id"]
        row = self.rt.db.conn.execute(
            "SELECT eligibility FROM records WHERE record_hash = ?",
            (result["record_hash"],)).fetchone()
        self.assertEqual(row["eligibility"], "HUMAN_REQUIRED")

    def _attestation(self, assertion_id="a3", **overrides):
        state = self.seq.verification_state(self.attempt_id)
        value = {"schema_version": "v1",
                 "kind": "ManualAttestation.ASSERTION",
                 "attested_by": "you", "assertion_id": assertion_id,
                 "attempt_id": self.attempt_id,
                 "attempt_contract_hash": self.attempt_contract_hash,
                 # D-53 (C4-M2): the attested verdict binds the admitted
                 # obj-16 candidate and the D-47 typed part reference.
                 "candidate_receipt_sha256":
                     state["candidate_receipt_sha256"],
                 "part": {"record": state["record_hash"],
                          "part_kind": "ASSERTION",
                          "part_id": assertion_id},
                 "attests": "PASS"}
        value.update(overrides)
        return value

    def test_attestation_scope_identity_and_ordering(self):
        """The HUMAN_ATTESTED assertion blocks conclusion until your
        scope-bound obj-37 is durable (attestation_before_commit), refuses
        forged identity/scope/verdict, and the DETERMINISTIC/HUMAN mode
        gates hold in both directions."""
        self._to_evidence_complete(sequence_id=self.human_sequence)
        self._register("a1", "PASS",
                       environment=environment_bytes(self._identity()))
        self._register("a2", "PASS")
        with self.assertRaises(ConflictError):
            # a3 unresolved: no commit before the attestation is durable.
            self.seq.conclude_verification(self.attempt_id, self.fence)
        with self.assertRaises(ConflictError):
            # HUMAN assertion via the runner seam (the mode gate fires
            # before any payload is parsed)
            self.seq.register_verifier_result(
                self.attempt_id, "a3", "PASS", "verifier_runner", b"junk")
        with self.assertRaises(ConflictError):
            self.seq.attest_assertion(self.attempt_id, "a2",
                                      self._attestation("a2"))
        for bad in (self._attestation(attested_by="task_force"),
                    self._attestation(attempt_id="at_" + "0" * 32),
                    self._attestation(attempt_contract_hash="sha256:" + "0" * 64),
                    self._attestation(attests="FAIL")):
            with self.assertRaises(AdmissionError):
                self.seq.attest_assertion(self.attempt_id, "a3", bad)
        admitted = self.seq.attest_assertion(self.attempt_id, "a3",
                                             self._attestation())
        self.assertFalse(admitted["duplicate"])
        again = self.seq.attest_assertion(self.attempt_id, "a3",
                                          self._attestation())
        self.assertTrue(again["duplicate"])
        with self.assertRaises(ConflictError):
            # The admitted attestation alone is not yet a RESULT: the row
            # exists only once the runner incorporates it into a canonical
            # obj-19 BY_ATTESTATION (C4-M2 reshape).
            self.seq.conclude_verification(self.attempt_id, self.fence)
        from fake_verifier import attested_receipt_bytes
        self.seq.register_attested_result(
            self.attempt_id, "a3", "verifier_runner",
            attested_receipt_bytes(self.seq, self.attempt_id, "a3",
                                   admitted["attestation_address"]))
        result = self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(result["attempt"], "VERIFIED")
        entries = [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq").fetchall()]
        t_s10 = [e for e in entries if e["rule"] == "t-s10"][0]
        self.assertEqual(t_s10["facts"]["eligibility"], "HUMAN_REQUIRED")
        self.assertEqual(t_s10["facts"]["attested_assertion_ids"], ["a3"])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


class StaleMemberTests(VerificationTestCase):
    """t-a12: lease_expired_required_member_without_result."""

    extra_seed = ("verifier_llm_v1",)

    def test_required_member_lease_expiry_goes_stale_then_retries(self):
        self._start()
        self.seq.dispatch_attempt(self.attempt_id, "builder", "fake",
                                  self.projection)
        start = self.seq.admit_witness(self.attempt_id, "obj-11", "autoswarm",
                                       WITNESS_START)
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"],
                                 member_lease_seconds=600)
        with self.assertRaises(ConflictError):
            self.seq.mark_member_stale(self.attempt_id)  # not yet expired
        self.clock.advance(601)
        self.assertEqual(self.seq.mark_member_stale(self.attempt_id), "STALE")
        self.assertIn("t-a12", self._rules())
        self.assertEqual(self._attempt_state(), "STALE")
        # The registered successor is the pre-existing t-s08 retry.
        self.seq.retry_sequence(self.sequence_id)
        self.assertEqual(self._sequence_state()["state"], "READY")

    def test_staleness_gates(self):
        """A result (FINISHED) forecloses staleness; a non-required member
        and a missing lease are refused; STALE is terminal."""
        result = self.rt.registry.admit_payload(
            human_record_payload(), submitted_by="planner")
        self._to_evidence_complete(sequence_id=result["sequence_id"])
        with self.assertRaises(ConflictError):
            self.seq.mark_member_stale(self.attempt_id)  # not RUNNING
        # A fresh V-A attempt whose member lease was nulled out: staleness
        # is proven by a recorded expiry, never by its absence.
        self._start()
        self.seq.dispatch_attempt(self.attempt_id, "builder", "fake",
                                  self.projection)
        start = self.seq.admit_witness(self.attempt_id, "obj-11", "autoswarm",
                                       WITNESS_START)
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"],
                                 member_lease_seconds=600)
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_calls SET member_lease_expires_utc"
                       " = NULL WHERE attempt_id = ?", (self.attempt_id,))
        self.clock.advance(601)
        with self.assertRaises(ConflictError):
            self.seq.mark_member_stale(self.attempt_id)  # no lease recorded

    def test_non_required_member_never_goes_stale(self):
        result = self.rt.registry.admit_payload(
            human_record_payload(), submitted_by="planner")
        self._start(sequence_id=result["sequence_id"])
        self.seq.dispatch_attempt(self.attempt_id, "reviewer", "fake",
                                  self.projection)
        start = self.seq.admit_witness(self.attempt_id, "obj-11", "autoswarm",
                                       WITNESS_START)
        self.seq.attempt_running(self.attempt_id, "native-sess-9",
                                 start["content_address"],
                                 member_lease_seconds=600)
        self.clock.advance(601)
        with self.assertRaises(ConflictError) as caught:
            self.seq.mark_member_stale(self.attempt_id)
        self.assertIn("required member", str(caught.exception))


class VerificationRecoveryTests(VerificationTestCase):
    def test_journal_row_pairing_both_directions(self):
        """A verification row without its journaled admission HALTs, and a
        journaled admission whose row is gone HALTs (D-68, both ways)."""
        self._to_evidence_complete()
        admitted = self._register("a2", "PASS")
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO attempt_verifications (verification_id,"
                " attempt_id, assertion_id, mode, verifier_id, outcome,"
                " by_principal, receipt_sha256, environment_sha256,"
                " recorded_utc)"
                " SELECT 'vr_' || lower(hex(randomblob(16))), attempt_id,"
                " 'a1', mode, verifier_id, outcome, by_principal,"
                " receipt_sha256, environment_sha256, recorded_utc"
                " FROM attempt_verifications LIMIT 1")
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_verifications"
                       " WHERE assertion_id = 'a1'")
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_verifications"
                       " WHERE verification_id = ?",
                       (admitted["verification_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_verified_without_results_and_forged_repairs_halt(self):
        self._to_evidence_complete()
        self._register("a1", "PASS",
                       environment=environment_bytes(self._identity()))
        self._register("a2", "PASS")
        self.seq.conclude_verification(self.attempt_id, self.fence)
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE sequences SET repairs_used = 5"
                       " WHERE sequence_id = ?", (self.sequence_id,))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE sequences SET repairs_used = 0"
                       " WHERE sequence_id = ?", (self.sequence_id,))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


if __name__ == "__main__":
    unittest.main()
