"""The M5 conformance suite: finalization and real effect execution.

The SL path over a REAL disposable Git repository — t-s15a (reservation +
intent minted in one transaction), the git_executor's authorized CAS,
observation-driven intent outcomes under the frozen ``local`` rule, and
the registered exits: t-s16 (+t-r01 at the PONR), t-s19 (absent, ref
unmoved: re-mint), t-s18 (ref moved: stale candidate, new attempt). Plus
executor authority gates, retry identity (t-i06), stale writers, restart
windows and recovery coherence.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Database, Sequencer, Store, project_state
from autobot.git_executor import (
    execute_local_cas,
    mint_commit_receipt,
    observe_and_resolve,
    observe_ref,
)
from autobot.models import (
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    PermitExhaustedError,
    StaleFenceError,
)

TARGET = "refs/heads/main"


def _git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s %s"
                           % (args, proc.stdout, proc.stderr))
    return proc.stdout.strip()


class FinalizationTestCase(unittest.TestCase):
    """A VERIFIED SL sequence (record L: LOCAL authority) plus a real Git
    repository with main at commit A (expected old) and commit B present
    (the desired new value)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, _ = support.admitted_runtime(self.dir, clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(), submitted_by="planner")
        self.assertEqual(admitted["outcome"], "NEW_RECORD")
        self.sequence_id = admitted["sequence_id"]
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)
        info = support.drive_to_verified(self.rt, admitted)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        self.seq.grant_permit(self.sequence_id, permitted_uses=3)
        self.repo = self.dir / "repo"
        self.sha_old, self.sha_new = support.init_git_repo(self.repo)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _begin(self):
        return self.seq.begin_commit(self.sequence_id, self.attempt_id,
                                     TARGET, self.sha_old, self.sha_new)

    def _sequence_row(self):
        return self.rt.db.conn.execute(
            "SELECT state, fence, current_attempt_id FROM sequences"
            " WHERE sequence_id = ?", (self.sequence_id,)).fetchone()

    def _permit_row(self):
        return self.rt.db.conn.execute(
            "SELECT permitted_uses, consumed_uses, reserved_uses"
            " FROM permit_state WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()

    def _intent_row(self, intent_id):
        return self.rt.db.conn.execute(
            "SELECT * FROM effect_intents WHERE intent_id = ?",
            (intent_id,)).fetchone()

    def _rules(self):
        return [json.loads(r["payload"])["rule"] for r in
                self.rt.db.conn.execute(
                    "SELECT payload FROM transition_journal ORDER BY seq"
                ).fetchall()]

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]


class SlHappyPathTests(FinalizationTestCase):
    def test_verified_to_committed_through_real_git(self):
        """The whole SL arc: t-s15a mints obj-24+obj-25 durably, the
        executor performs the one authorized real CAS, observation yields
        HAPPENED (t-i01), and t-s16 consumes the reservation at the PONR
        (t-r01) and lands COMMITTED — with the obj-26 receipt bytes
        durable and recovery converging."""
        minted = self._begin()
        self.assertEqual(self._sequence_row()["state"], "COMMITTING")
        row = self._intent_row(minted["intent_id"])
        self.assertEqual((row["state"], row["target"], row["expected_old"],
                          row["new_value"]),
                         ("PENDING", TARGET, self.sha_old, self.sha_new))
        report = execute_local_cas(self.seq, minted["intent_id"], self.repo)
        self.assertEqual(report["observation"], "target_equals_new")
        self.assertEqual(report["outcome"], "HAPPENED")
        self.assertEqual(observe_ref(self.repo, TARGET), self.sha_new)
        address = self.seq.commit_committed(self.sequence_id, self.fence,
                                            report["receipt"])
        self.assertEqual(self.rt.store.get(address), report["receipt"])
        self.assertEqual(self._sequence_row()["state"], "COMMITTED")
        permits = self._permit_row()
        self.assertEqual((permits["consumed_uses"], permits["reserved_uses"]),
                         (1, 0))
        reservation = self.rt.db.conn.execute(
            "SELECT state FROM reservations WHERE reservation_id = ?",
            (minted["reservation_id"],)).fetchone()
        self.assertEqual(reservation["state"], "CONSUMED")
        rules = self._rules()
        for rule in ("t-s15a", "t-i01", "t-r01", "t-s16"):
            self.assertIn(rule, rules)
        view = project_state(self.rt.db)
        self.assertEqual(view["obligations"]["committed_awaiting_acceptance"],
                         [self.sequence_id])
        first = self.seq.recover()
        second = self.seq.recover()
        self.assertEqual(first["intents_in_doubt"], [])
        self.assertEqual(second["intents_in_doubt"], [])

    def test_ref_already_at_new_is_happened_without_second_mutation(self):
        """Repeated application: the ref already equals the new value, so
        update-ref's CAS refuses, but the observation is target_equals_new
        and the frozen rule records HAPPENED — one effect identity, no
        second mutation, same COMMITTED exit."""
        minted = self._begin()
        _git(self.repo, "update-ref", TARGET, self.sha_new, self.sha_old)
        report = execute_local_cas(self.seq, minted["intent_id"], self.repo)
        self.assertNotEqual(report["update_ref_exit"], 0)
        self.assertEqual(report["outcome"], "HAPPENED")
        self.seq.commit_committed(self.sequence_id, self.fence,
                                  report["receipt"])
        self.assertEqual(self._sequence_row()["state"], "COMMITTED")
        # Repeated execution after completion: the authority read refuses
        # (the intent is terminal), and the repository is untouched.
        with self.assertRaises(ConflictError):
            execute_local_cas(self.seq, minted["intent_id"], self.repo)
        self.assertEqual(observe_ref(self.repo, TARGET), self.sha_new)


class AbsentArmsTests(FinalizationTestCase):
    def test_fenced_absent_returns_to_verified_and_remints(self):
        """t-s19: the launch never mutated the ref; a fenced observation at
        the expected old value proves absence (frozen local rule), the
        reservation releases (t-r02), the sequence returns to VERIFIED and
        the SAME candidate re-mints and completes."""
        minted = self._begin()
        report = observe_and_resolve(self.seq, minted["intent_id"],
                                     self.repo, executor_fenced=True)
        self.assertEqual(report["outcome"], "ABSENT")
        self.seq.commit_absent(self.sequence_id)
        self.assertEqual(self._sequence_row()["state"], "VERIFIED")
        self.assertEqual(self._permit_row()["reserved_uses"], 0)
        for rule in ("t-i02", "t-r02", "t-s19"):
            self.assertIn(rule, self._rules())
        second = self._begin()
        report = execute_local_cas(self.seq, second["intent_id"], self.repo)
        self.seq.commit_committed(self.sequence_id, self.fence,
                                  report["receipt"])
        self.assertEqual(self._sequence_row()["state"], "COMMITTED")

    def test_moved_ref_is_stale_candidate_t_s18(self):
        """t-s18: the canonical ref moved to a third value — the CAS
        refuses, observation is target_other (ABSENT), the obj-26
        observation receipt binds the intent, the reservation releases and
        the sequence returns to READY for a NEW attempt."""
        minted = self._begin()
        (self.repo / "three.txt").write_text("three\n", encoding="utf-8")
        _git(self.repo, "add", "three.txt")
        _git(self.repo, "commit", "-q", "-m", "commit C")
        sha_c = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "update-ref", TARGET, sha_c)
        report = execute_local_cas(self.seq, minted["intent_id"], self.repo)
        self.assertEqual(report["observation"], "target_other")
        self.assertEqual(report["outcome"], "ABSENT")
        with self.assertRaises(ConflictError):
            self.seq.commit_absent(self.sequence_id)  # a moved ref is t-s18
        receipt = mint_commit_receipt(
            {"intent_id": minted["intent_id"],
             "effect_id": "eff-local-canonical-cas", "target": TARGET,
             "expected_old": self.sha_old, "new_value": self.sha_new},
            report["observed_value"])
        self.seq.commit_stale(self.sequence_id, receipt)
        row = self._sequence_row()
        self.assertEqual((row["state"], row["current_attempt_id"]),
                         ("READY", None))
        self.assertIn("t-s18", self._rules())
        # The stale candidate's successor is a fresh attempt (fence+1).
        info = self.seq.start_execution(self.sequence_id)
        self.assertEqual(info["fence"], self.fence + 1)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


class UncertaintyAndRetryTests(FinalizationTestCase):
    def test_unknown_outcome_expiry_then_t_i06_retry_completes(self):
        """The uncertainty arc: an unfenced observation at the expected old
        value decides nothing while the window is open (PENDING holds),
        expiry moves the intent to UNKNOWN_OUTCOME (t-i03), the t-i06 retry
        mints a successor with the same effect identity binding the
        superseded intent, and the successor completes the SL arc."""
        minted = self._begin()
        report = observe_and_resolve(self.seq, minted["intent_id"],
                                     self.repo, executor_fenced=False)
        self.assertEqual(report["outcome"], "PENDING")
        self.assertEqual(self._intent_row(minted["intent_id"])["state"],
                         "PENDING")
        self.clock.advance(301)  # past the record's 300 s intent TTL
        self.assertEqual(self.seq.expire_intents(), [minted["intent_id"]])
        successor = self.seq.retry_intent(minted["intent_id"])
        srow = self._intent_row(successor)
        self.assertEqual((srow["state"], srow["superseded_intent_id"],
                          srow["target"], srow["expected_old"]),
                         ("PENDING", minted["intent_id"], TARGET,
                          self.sha_old))
        report = execute_local_cas(self.seq, successor, self.repo)
        self.assertEqual(report["outcome"], "HAPPENED")
        self.seq.commit_committed(self.sequence_id, self.fence,
                                  report["receipt"])
        self.assertEqual(self._sequence_row()["state"], "COMMITTED")
        for rule in ("t-i03", "t-i06", "t-i01", "t-r01", "t-s16"):
            self.assertIn(rule, self._rules())
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


class AuthorityGateTests(FinalizationTestCase):
    def test_begin_commit_gates(self):
        """State, attempt-identity, SL-only, and permit gates on t-s15a;
        while COMMITTING, no second effect identity can be minted."""
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM permit_state WHERE sequence_id = ?",
                       (self.sequence_id,))
        with self.assertRaises(PermitExhaustedError):
            self._begin()
        self.seq.grant_permit(self.sequence_id, permitted_uses=1)
        with self.assertRaises(ConflictError):
            self.seq.begin_commit(self.sequence_id, "at_" + "0" * 32,
                                  TARGET, self.sha_old, self.sha_new)
        minted = self._begin()
        with self.assertRaises(ConflictError):
            self._begin()  # COMMITTING: exactly one effect identity governs
        self.assertEqual(self._intent_row(minted["intent_id"])["state"],
                         "PENDING")

    def test_sl_only_boundary_on_remote_record(self):
        """A VERIFIED sequence whose frozen record is REMOTE cannot enter
        the SL path: the remote finalization arcs are deferred rows."""
        remote = self.rt.db.conn.execute(
            "SELECT sequence_id FROM sequences WHERE sequence_id != ?",
            (self.sequence_id,)).fetchone()["sequence_id"]
        info = support.drive_to_verified(
            self.rt, {"sequence_id": remote})
        self.seq.grant_permit(remote, permitted_uses=1)
        with self.assertRaises(ConflictError) as caught:
            self.seq.begin_commit(remote, info["attempt_id"], TARGET,
                                  self.sha_old, self.sha_new)
        self.assertIn("LOCAL", str(caught.exception))

    def test_executor_authority_gates(self):
        """The executor refuses the wrong effect kind, a consumed window,
        and an underspecified authorization; a fenced-out writer admits
        and concludes nothing."""
        minted = self._begin()
        self.clock.advance(241)  # past the record's 240 s hard timeout
        with self.assertRaises(ConflictError):
            execute_local_cas(self.seq, minted["intent_id"], self.repo)
        # The window closed without the effect: expiry then the fenced
        # observation resolves ABSENT and t-s19 re-mints below.
        self.clock.advance(60)
        self.seq.expire_intents()
        report = observe_and_resolve(self.seq, minted["intent_id"],
                                     self.repo, executor_fenced=True)
        self.assertEqual(report["outcome"], "ABSENT")  # t-i05
        self.seq.commit_absent(self.sequence_id)
        second = self._begin()
        other = self.seq.mint_intent(
            self.sequence_id, self.attempt_id, "eff-canonical-push",
            target="refs/heads/main", expected_old="sha256:" + "ab" * 32)
        with self.assertRaises(ConflictError):
            execute_local_cas(self.seq, other, self.repo)  # wrong effect
        report = execute_local_cas(self.seq, second["intent_id"], self.repo)
        self.seq.commit_committed(self.sequence_id, self.fence,
                                  report["receipt"])
        self.assertEqual(self._sequence_row()["state"], "COMMITTED")

    def test_commit_committed_gates(self):
        """t-s16 gates: durable HAPPENED required, receipt identity bound,
        stale fence refused, reservation must be RESERVED (t-r01)."""
        minted = self._begin()
        with self.assertRaises(ConflictError):
            self.seq.commit_committed(self.sequence_id, self.fence,
                                      b'{"kind":"CommitReceipt"}')
        report = execute_local_cas(self.seq, minted["intent_id"], self.repo)
        with self.assertRaises(StaleFenceError):
            self.seq.commit_committed(self.sequence_id, self.fence + 1,
                                      report["receipt"])
        bad = json.loads(report["receipt"].decode("utf-8"))
        bad["intent_id"] = "in_" + "0" * 32
        with self.assertRaises(ConflictError):
            self.seq.commit_committed(
                self.sequence_id, self.fence,
                json.dumps(bad, sort_keys=True).encode("utf-8"))
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE reservations SET state = 'RELEASED'"
                       " WHERE reservation_id = ?",
                       (minted["reservation_id"],))
        with self.assertRaises(ConflictError):
            self.seq.commit_committed(self.sequence_id, self.fence,
                                      report["receipt"])

    def test_fenced_out_writer_finalizes_nothing(self):
        minted = self._begin()
        self.clock.advance(400000)
        usurper = Sequencer(self.rt.db, self.rt.store, self.clock,
                            holder="takeover")
        usurper.acquire_lease(ttl_seconds=360000)
        before = self._journal_count()
        with self.assertRaises(AuthorityLost):
            self.seq.commit_absent(self.sequence_id)
        with self.assertRaises(AuthorityLost):
            self.seq.commit_committed(self.sequence_id, self.fence,
                                      b"{}")
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self._intent_row(minted["intent_id"])["state"],
                         "PENDING")


class FinalizationRestartTests(FinalizationTestCase):
    def _reopen(self):
        self.rt.db.close()
        db = Database(self.dir / "autobot.db")
        store = Store(self.dir / "store")
        self.rt.db, self.rt.store = db, store
        seq = Sequencer(db, store, self.clock, holder="test-instance")
        seq.recover()
        seq.acquire_lease(ttl_seconds=360000)
        self.seq = seq
        return seq

    def test_restart_with_pending_intent_completes_by_observation(self):
        """The pre-effect window: restart after t-s15a finds COMMITTING
        with a durable PENDING intent (recovery 8d), and the executor
        completes the arc from committed_intent() alone."""
        minted = self._begin()
        seq = self._reopen()
        report = execute_local_cas(seq, minted["intent_id"], self.repo)
        self.assertEqual(report["outcome"], "HAPPENED")
        seq.commit_committed(self.sequence_id, self.fence,
                             report["receipt"])
        self.assertEqual(self._sequence_row()["state"], "COMMITTED")

    def test_restart_after_happened_before_t_s16_completes(self):
        """The post-observation window: the intent is durably HAPPENED but
        t-s16 never ran; restart concludes from durable state alone."""
        minted = self._begin()
        report = execute_local_cas(self.seq, minted["intent_id"], self.repo)
        seq = self._reopen()
        seq.commit_committed(self.sequence_id, self.fence,
                             report["receipt"])
        row = self._sequence_row()
        self.assertEqual(row["state"], "COMMITTED")
        self.assertEqual(seq.recover()["intents_in_doubt"], [])

    def test_committing_without_intent_halts_recovery(self):
        """Forged finalization state: a COMMITTING sequence whose intent
        row is gone HALTs recovery (t-s15a mints obj-25 durably)."""
        minted = self._begin()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM effect_intents WHERE intent_id = ?",
                       (minted["intent_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


if __name__ == "__main__":
    unittest.main()
