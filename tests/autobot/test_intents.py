"""Milestone 2: the EffectIntent lifecycle (t-i01..t-i07) and the crash-safe
external-effect boundary — no effect may begin before its intent is durably
committed, every outcome enters through the registered observation
vocabulary, retries bind their superseded intent, and recovery reconciles
the whole intent surface (windows, retry chains, in-doubt set)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Database, journal
from autobot.models import (
    AdmissionError,
    ConflictError,
    SchemaTooNew,
)

EXPECTED_OLD = "sha256:" + "ab" * 32
TARGET = "refs/heads/main"


class SchemaEvolutionTests(unittest.TestCase):
    def test_v2_columns_and_newer_db_refusal(self):
        """D-70: schema v2 added the obj-25 retry-identity columns, v3 the
        attempt_calls execution binding, v4 the attempt_witnesses D-21
        admission entries and v5 the M4 verification surface
        (attempt_verifications, the repair counter, the member lease) and
        v6 the M6 released_edges dependency surface, all additively; a
        database newer than the running code is refused."""
        tmp = tempfile.TemporaryDirectory()
        try:
            path = Path(tmp.name) / "autobot.db"
            db = Database(path)
            row = db.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            self.assertEqual(row["value"], "6")
            columns = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(effect_intents)").fetchall()}
            self.assertLessEqual(
                {"target", "expected_old", "new_value"}, columns)
            call_columns = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(attempt_calls)").fetchall()}
            self.assertLessEqual(
                {"attempt_id", "role", "provider", "call_id",
                 "projection_address", "native_session_id"}, call_columns)
            witness_columns = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(attempt_witnesses)").fetchall()}
            self.assertLessEqual(
                {"admission_id", "attempt_id", "witness_kind", "producer",
                 "content_sha256", "call_id", "native_session_id",
                 "admitted_utc"}, witness_columns)
            verification_columns = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(attempt_verifications)").fetchall()}
            self.assertLessEqual(
                {"verification_id", "attempt_id", "assertion_id", "mode",
                 "verifier_id", "outcome", "by_principal", "receipt_sha256",
                 "environment_sha256", "recorded_utc"}, verification_columns)
            sequence_columns = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(sequences)").fetchall()}
            self.assertIn("repairs_used", sequence_columns)
            call_columns_v5 = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(attempt_calls)").fetchall()}
            self.assertIn("member_lease_expires_utc", call_columns_v5)
            edge_columns = {r["name"] for r in db.conn.execute(
                "PRAGMA table_info(released_edges)").fetchall()}
            self.assertLessEqual(
                {"release_id", "predecessor_sequence_id", "edge_kind",
                 "edge_value", "acceptance_sha256", "release_sha256",
                 "released_utc"}, edge_columns)
            db.close()
            raw = sqlite3.connect(str(path))
            raw.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
            raw.commit()
            raw.close()
            with self.assertRaises(SchemaTooNew):
                Database(path)
        finally:
            tmp.cleanup()


class IntentTestCase(unittest.TestCase):
    """Shared pipeline: admitted V-A, leased, executing, permit granted and
    one use reserved — the exact context the attempt_scoped checks need."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.seq = self.rt.sequencer
        self.sequence_id = self.result["sequence_id"]
        self.seq.acquire_lease(ttl_seconds=360000)
        self.seq.mark_ready(self.sequence_id)
        self.attempt_id = self.seq.start_execution(self.sequence_id)["attempt_id"]
        self.seq.grant_permit(self.sequence_id, permitted_uses=3)
        self.reservation_id = self.seq.reserve_use(
            self.sequence_id, self.attempt_id)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _mint(self, **kwargs):
        args = {"effect_id": "eff-canonical-push", "target": TARGET,
                "expected_old": EXPECTED_OLD}
        args.update(kwargs)
        return self.seq.mint_intent(self.sequence_id, self.attempt_id, **args)

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]


class MintTests(IntentTestCase):
    def test_mint_happy_path_and_committed_boundary(self):
        """obj-25 minted under the full attempt_scoped checks; the executor's
        committed_intent read is the effect_intent_committed boundary."""
        intent_id = self._mint()
        row = self.rt.db.conn.execute(
            "SELECT * FROM effect_intents WHERE intent_id = ?",
            (intent_id,)).fetchone()
        self.assertEqual(row["state"], "PENDING")
        self.assertEqual(row["effect_id"], "eff-canonical-push")
        self.assertEqual(row["intent_class"], "attempt_scoped")
        self.assertEqual(row["fence"], 1)
        self.assertEqual(row["reservation_id"], self.reservation_id)
        self.assertEqual(row["target"], TARGET)
        self.assertEqual(row["expected_old"], EXPECTED_OLD)
        # V-A's effect_policy: ttl 300, hard timeout 240 (SEM-12 ordering)
        self.assertLess(row["hard_timeout_utc"], row["expires_utc"])
        facts = self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        self.assertIn("ch2:mint-intent", facts["payload"])
        committed = self.seq.committed_intent(intent_id)
        self.assertEqual(committed["state"], "PENDING")
        self.assertEqual(committed["hard_timeout_utc"], row["hard_timeout_utc"])

    def test_mint_requires_live_reservation(self):
        """reservation_held: a released reservation grants no intent."""
        self.seq.release_reservation(self.reservation_id, cause="test")
        with self.assertRaises(ConflictError):
            self._mint()

    def test_mint_window_rule(self):
        """intent_ttl_gt_hard_timeout (SEM-12, INV-67)."""
        with self.assertRaises(ConflictError):
            self._mint(ttl_seconds=100, hard_timeout_seconds=200)
        with self.assertRaises(ConflictError):
            self._mint(ttl_seconds=100, hard_timeout_seconds=100)
        self._mint(ttl_seconds=200, hard_timeout_seconds=100)

    def test_mint_refuses_bad_effects_and_dead_attempts(self):
        with self.assertRaises(ConflictError):
            self._mint(effect_id="eff-no-such")
        with self.assertRaises(ConflictError):
            self._mint(effect_id="eff-staging-delete")  # standalone class
        self.seq.reserve_attempt(self.attempt_id, expected_fence=1)
        self.seq.fail_attempt(self.attempt_id, reason="launch_failed")
        with self.assertRaises(ConflictError):
            self._mint()  # FAILED attempt has no intent authority

    def test_mint_requires_active_delegation(self):
        """delegation_active_at_intent (t-d02 drives expiry)."""
        self.rt.clock.advance(60 * 60 * 24 * 420)  # past 2027-01-01
        self.seq.acquire_lease(ttl_seconds=360000)  # same-holder retake
        with self.assertRaises(ConflictError):
            self._mint()


class LifecycleTests(IntentTestCase):
    def test_happened_from_pending(self):
        intent_id = self._mint()
        self.assertEqual(
            self.seq.resolve_intent(intent_id, "target_equals_new"), "HAPPENED")
        with self.assertRaises(ConflictError):
            self.seq.resolve_intent(intent_id, "target_equals_new")
        with self.assertRaises(ConflictError):
            self.seq.committed_intent(intent_id)  # terminal: no effect begins

    def test_absent_from_pending(self):
        intent_id = self._mint()
        self.assertEqual(
            self.seq.resolve_intent(intent_id, "target_other"), "ABSENT")

    def test_unknown_requires_expiry_from_pending(self):
        """CORR-49 as registered: the old value is not evidence of absence —
        and before the window expires the effect may still land, so PENDING
        holds (t-i03's expired_and_... precondition)."""
        intent_id = self._mint()
        before = self._journal_count()
        self.assertEqual(
            self.seq.resolve_intent(intent_id, "target_equals_expected_old"),
            "PENDING")
        self.assertEqual(self._journal_count(), before)  # no transition
        self.rt.clock.advance(301)  # past ttl 300
        with self.assertRaises(ConflictError):
            self.seq.committed_intent(intent_id)  # past hard timeout: no start
        self.assertEqual(
            self.seq.resolve_intent(intent_id, "target_equals_expected_old"),
            "UNKNOWN_OUTCOME")
        self.assertEqual(
            self.seq.resolve_intent(intent_id, "target_equals_new"), "HAPPENED")

    def test_absent_from_unknown(self):
        intent_id = self._mint()
        self.rt.clock.advance(301)
        self.seq.expire_intents()
        self.assertEqual(
            self.seq.resolve_intent(intent_id, "target_other"), "ABSENT")

    def test_unknown_observation_vocabulary_is_closed(self):
        intent_id = self._mint()
        with self.assertRaises(ConflictError):
            self.seq.resolve_intent(intent_id, "target_absent")  # not remote_cas

    def test_expiry_sweep_moves_only_expired(self):
        early = self._mint(ttl_seconds=100, hard_timeout_seconds=50)
        late = self._mint(ttl_seconds=400, hard_timeout_seconds=300)
        self.rt.clock.advance(101)
        self.assertEqual(self.seq.expire_intents(), [early])
        self.assertEqual(self.seq.expire_intents(), [])
        self.rt.clock.advance(300)
        self.assertEqual(self.seq.expire_intents(), [late])
        states = {r["intent_id"]: r["state"] for r in self.rt.db.conn.execute(
            "SELECT intent_id, state FROM effect_intents").fetchall()}
        self.assertEqual(states, {early: "UNKNOWN_OUTCOME",
                                  late: "UNKNOWN_OUTCOME"})


class RetryTests(IntentTestCase):
    def test_retry_chain(self):
        """t-i06: the successor binds the superseded intent and carries the
        same effect, target and expected old value (CORR-56, INV-73);
        recovery verifies the chain from durable facts."""
        intent_id = self._mint()
        self.rt.clock.advance(301)
        self.seq.expire_intents()
        successor = self.seq.retry_intent(intent_id)
        rows = {r["intent_id"]: r for r in self.rt.db.conn.execute(
            "SELECT * FROM effect_intents").fetchall()}
        self.assertEqual(rows[intent_id]["state"], "SUPERSEDED_BY_RETRY")
        self.assertEqual(rows[successor]["state"], "PENDING")
        self.assertEqual(rows[successor]["superseded_intent_id"], intent_id)
        self.assertEqual(rows[successor]["target"], TARGET)
        self.assertEqual(rows[successor]["expected_old"], EXPECTED_OLD)
        self.assertEqual(
            self.seq.resolve_intent(successor, "target_equals_new"), "HAPPENED")
        with self.assertRaises(ConflictError):
            self.seq.retry_intent(intent_id)  # terminal now
        report = self.seq.recover()
        self.assertEqual(report["intents_in_doubt"], [])

    def test_retry_requires_live_reservation(self):
        intent_id = self._mint()
        self.rt.clock.advance(301)
        self.seq.expire_intents()
        self.seq.release_reservation(self.reservation_id, cause="pre-ponr exit")
        with self.assertRaises(ConflictError):
            self.seq.retry_intent(intent_id)

    def test_retry_requires_unknown_outcome(self):
        intent_id = self._mint()
        with self.assertRaises(ConflictError):
            self.seq.retry_intent(intent_id)  # still PENDING


class HumanResolutionTests(IntentTestCase):
    def _attestation(self, intent_id, outcome="ABSENT"):
        return {"schema_version": "v1",
                "kind": "ManualAttestation.EFFECT_OUTCOME",
                "intent_id": intent_id, "attested_outcome": outcome,
                "attested_by": "you"}

    def test_t_i07_requires_inactive_authority(self):
        intent_id = self._mint()
        self.rt.clock.advance(301)
        self.seq.expire_intents()
        with self.assertRaises(ConflictError):
            self.seq.resolve_intent_by_attestation(
                intent_id, self._attestation(intent_id))  # delegation active

    def test_t_i07_resolves_with_attestation(self):
        intent_id = self._mint()
        self.rt.clock.advance(301)
        self.seq.expire_intents()
        self.rt.clock.advance(60 * 60 * 24 * 420)  # delegation expires (t-d02)
        self.seq.acquire_lease(ttl_seconds=360000)
        with self.assertRaises(AdmissionError):
            self.seq.resolve_intent_by_attestation(
                intent_id, {"kind": "wrong"})
        address = self.seq.resolve_intent_by_attestation(
            intent_id, self._attestation(intent_id))
        row = self.rt.db.conn.execute(
            "SELECT state FROM effect_intents WHERE intent_id = ?",
            (intent_id,)).fetchone()
        self.assertEqual(row["state"], "HUMAN_RESOLVED")
        # preserved by exact bytes (D-31); obj-37's object hash awaits its
        # owning chapter's domain label (the frozen kind grammar cannot
        # express the underscore in this registry kind)
        self.assertTrue(self.rt.store.has(address))
        last = self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        self.assertIn(address, last["payload"])
        report = self.seq.recover()
        self.assertEqual(report["intents_in_doubt"], [])


class IntentRecoveryGuards(IntentTestCase):
    def test_unjournaled_intent_mutation_halts_recovery(self):
        intent_id = self._mint()
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE effect_intents SET state = 'HAPPENED',"
                " version = version + 1 WHERE intent_id = ?", (intent_id,))
        from autobot.models import IntegrityHalt
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_window_forgery_halts_recovery(self):
        intent_id = self._mint()
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE effect_intents SET hard_timeout_utc = expires_utc"
                " WHERE intent_id = ?", (intent_id,))
        from autobot.models import IntegrityHalt
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_broken_retry_chain_halts_recovery(self):
        intent_id = self._mint()
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE effect_intents SET superseded_intent_id ="
                " 'in_00000000000000000000000000000bad' WHERE intent_id = ?",
                (intent_id,))
        from autobot.models import IntegrityHalt
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


if __name__ == "__main__":
    unittest.main()
