"""Crash, restart and replay (INV-10, D-69), the journal chain (JH-1), and
authoritative-state placement (INV-72). The subprocess matrix is the
operator's milestone gate: crash at every committed boundary, restart, same
Sequence / same Attempt / same fence, no duplicate dispatch, valid chain."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Database, Sequencer, Store, journal
from autobot.models import (
    AuthorityLost,
    ConflictError,
    JournalBroken,
    LeaseUnavailable,
)

CHILD = Path(__file__).resolve().parent / "crash_child.py"


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_in_process_restart_resumes_same_state(self):
        """CH2-failpoint-matrix, clean-shutdown arm: close, reopen, recover;
        the same Sequence, Attempt and fence; no duplicate dispatch."""
        rt, result = support.admitted_runtime(self.dir)
        sequence_id = result["sequence_id"]
        rt.sequencer.acquire_lease(ttl_seconds=3600)
        rt.sequencer.mark_ready(sequence_id)
        info = rt.sequencer.start_execution(sequence_id)
        rt.sequencer.reserve_attempt(info["attempt_id"], info["fence"])
        before = {
            "sequences": [dict(r) for r in rt.db.conn.execute(
                "SELECT * FROM sequences ORDER BY sequence_id").fetchall()],
            "attempts": [dict(r) for r in rt.db.conn.execute(
                "SELECT * FROM attempts ORDER BY attempt_id").fetchall()],
            "journal": rt.db.conn.execute(
                "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"],
        }
        rt.db.close()

        db = Database(self.dir / "autobot.db")
        store = Store(self.dir / "store")
        seq = Sequencer(db, store, support.manual_clock(), holder="test-instance")
        report = seq.recover()
        self.assertEqual(report["journal_entries"], before["journal"])
        after_sequences = [dict(r) for r in db.conn.execute(
            "SELECT * FROM sequences ORDER BY sequence_id").fetchall()]
        after_attempts = [dict(r) for r in db.conn.execute(
            "SELECT * FROM attempts ORDER BY attempt_id").fetchall()]
        self.assertEqual(after_sequences, before["sequences"])
        self.assertEqual(after_attempts, before["attempts"])

        # same-holder restart retakes its own lease WITH an advanced epoch:
        # resumed authority is a new token, never the old one replayed.
        self.assertEqual(seq.acquire_lease(ttl_seconds=3600), 2)
        with self.assertRaises(ConflictError):
            seq.start_execution(sequence_id)  # no duplicate dispatch
        db.close()

    def test_crash_matrix_subprocess(self):
        """CH2-failpoint-matrix (INV-10): hard process death at every
        committed boundary — and mid-transaction — restarts into exactly the
        committed state."""
        for stage in ("admitted", "ready", "executing", "reserved",
                      "mid_transaction"):
            with self.subTest(stage=stage):
                data_dir = self.dir / stage
                proc = subprocess.run(
                    [sys.executable, "-I", str(CHILD), str(data_dir), stage],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(
                    proc.returncode, 137,
                    "child did not die as planned: %s%s" % (proc.stdout, proc.stderr),
                )
                snapshot = json.loads(proc.stdout.strip().splitlines()[-1])

                db = Database(data_dir / "autobot.db")
                store = Store(data_dir / "store")
                seq = Sequencer(db, store, support.manual_clock(),
                                holder="crash-child")
                seq.recover()  # journal chain + every content reference
                row = db.conn.execute(
                    "SELECT state, version, fence, current_attempt_id FROM sequences"
                    " WHERE sequence_id = ?", (snapshot["sequence_id"],),
                ).fetchone()
                self.assertEqual(dict(row), snapshot["sequence"])
                count = db.conn.execute(
                    "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]
                self.assertEqual(count, snapshot["journal_entries"])

                epoch = seq.acquire_lease(ttl_seconds=3600)
                # admitted: the child never leased, so this is the first
                # acquisition; every later stage is a same-holder retake and
                # must advance the child's epoch 1 to 2.
                self.assertEqual(epoch, 1 if stage == "admitted" else 2)
                if stage == "admitted":
                    self.assertEqual(seq.mark_ready(snapshot["sequence_id"]), "READY")
                elif stage == "ready":
                    info = seq.start_execution(snapshot["sequence_id"])
                    self.assertEqual(info["fence"], 1)
                else:
                    with self.assertRaises(ConflictError):
                        seq.start_execution(snapshot["sequence_id"])
                    att = db.conn.execute(
                        "SELECT state, fence FROM attempts WHERE attempt_id = ?",
                        (snapshot["attempt_id"],),
                    ).fetchone()
                    self.assertEqual(att["fence"], snapshot["fence"])
                    if stage == "executing":
                        # the same attempt resumes: reserve it now
                        seq.reserve_attempt(snapshot["attempt_id"], snapshot["fence"])
                    else:
                        self.assertEqual(att["state"], "RESERVED")
                        with self.assertRaises(ConflictError):
                            seq.reserve_attempt(snapshot["attempt_id"],
                                                snapshot["fence"])
                db.close()

    def test_crash_intent_stages(self):
        """D-68's boundary across crashes: a durably committed intent
        survives exactly once (reported in-doubt, resumable to resolution),
        and a torn intent transition vanishes whole."""
        for stage in ("intent", "mid_intent_tx"):
            with self.subTest(stage=stage):
                data_dir = self.dir / stage
                proc = subprocess.run(
                    [sys.executable, "-I", str(CHILD), str(data_dir), stage],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(proc.returncode, 137, proc.stdout + proc.stderr)
                snapshot = json.loads(proc.stdout.strip().splitlines()[-1])

                db = Database(data_dir / "autobot.db")
                store = Store(data_dir / "store")
                seq = Sequencer(db, store, support.manual_clock(),
                                holder="crash-child")
                report = seq.recover()
                self.assertEqual(
                    [d["intent_id"] for d in report["intents_in_doubt"]],
                    [snapshot["intent_id"]])
                row = db.conn.execute(
                    "SELECT state, version, fence FROM effect_intents"
                    " WHERE intent_id = ?", (snapshot["intent_id"],),
                ).fetchone()
                self.assertEqual(dict(row), snapshot["intent"])
                count = db.conn.execute(
                    "SELECT COUNT(*) AS n FROM transition_journal"
                ).fetchone()["n"]
                self.assertEqual(count, snapshot["journal_entries"])

                seq.acquire_lease(ttl_seconds=3600)
                self.assertEqual(
                    seq.resolve_intent(snapshot["intent_id"], "target_equals_new"),
                    "HAPPENED")
                db.close()

    def test_crash_dispatched_stage(self):
        """The M3 criterion at slice level: a restart reconstructs the
        execution relationship (attempt ↔ bound call ↔ projection) from
        durable state alone, and the lifecycle resumes from it."""
        data_dir = self.dir / "dispatched"
        proc = subprocess.run(
            [sys.executable, "-I", str(CHILD), str(data_dir), "dispatched"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 137, proc.stdout + proc.stderr)
        snapshot = json.loads(proc.stdout.strip().splitlines()[-1])

        db = Database(data_dir / "autobot.db")
        store = Store(data_dir / "store")
        seq = Sequencer(db, store, support.manual_clock(), holder="crash-child")
        seq.recover()  # includes the call↔state pairing and projection resolve
        crow = db.conn.execute(
            "SELECT role, provider, call_id, projection_address,"
            " native_session_id FROM attempt_calls WHERE attempt_id = ?",
            (snapshot["attempt_id"],)).fetchone()
        self.assertEqual(dict(crow), snapshot["call"])
        att = db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (snapshot["attempt_id"],)).fetchone()
        self.assertEqual(att["state"], "DISPATCHED")
        count = db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]
        self.assertEqual(count, snapshot["journal_entries"])

        seq.acquire_lease(ttl_seconds=3600)
        witness = store.put(b"post-restart witness\n")
        seq.attempt_running(snapshot["attempt_id"], "native-after-restart",
                            witness)
        db.close()

    def test_takeover_after_expiry_advances_epoch_and_fences_stale_authority(self):
        """The review's fourth property: restart by the same still-valid
        holder (above) is distinct from takeover. A different holder is
        refused while the crashed holder's lease is valid; after expiry it
        takes over with an ADVANCED epoch, the dead process's old token has
        no authority, and the takeover still cannot duplicate dispatch."""
        data_dir = self.dir / "takeover"
        proc = subprocess.run(
            [sys.executable, "-I", str(CHILD), str(data_dir), "reserved"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 137, proc.stdout + proc.stderr)
        snapshot = json.loads(proc.stdout.strip().splitlines()[-1])

        db = Database(data_dir / "autobot.db")
        store = Store(data_dir / "store")
        clock = support.manual_clock()
        other = Sequencer(db, store, clock, holder="takeover-instance")
        other.recover()
        row = db.conn.execute(
            "SELECT holder, epoch FROM instance_lease").fetchone()
        self.assertEqual((row["holder"], row["epoch"]), ("crash-child", 1))

        with self.assertRaises(LeaseUnavailable):
            other.acquire_lease(ttl_seconds=60)  # crashed holder's lease holds
        clock.advance(3601)  # the child's 3600 s window passes
        self.assertEqual(other.acquire_lease(ttl_seconds=60), 2)

        stale = Sequencer(db, store, clock, holder="crash-child")
        stale.epoch = 1  # the dead process's authority token, replayed
        with self.assertRaises(AuthorityLost):
            stale.fail_attempt(snapshot["attempt_id"], reason="stale writer")

        with self.assertRaises(ConflictError):
            other.start_execution(snapshot["sequence_id"])  # no duplicate
        att = db.conn.execute(
            "SELECT state, fence FROM attempts WHERE attempt_id = ?",
            (snapshot["attempt_id"],)).fetchone()
        self.assertEqual((att["state"], att["fence"]),
                         ("RESERVED", snapshot["fence"]))
        db.close()


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def test_chain_recomputes_and_genesis_omits_previous(self):
        """JH-1 (INV-10's chain half): every entry re-hashes from its stored
        canonical payload; the genesis entry has no previous."""
        cur = self.rt.db.conn.cursor()
        count = journal.validate(cur)
        rows = cur.execute(
            "SELECT previous_hash FROM transition_journal ORDER BY seq").fetchall()
        self.assertEqual(count, len(rows))
        self.assertGreater(count, 0)
        self.assertIsNone(rows[0]["previous_hash"])
        for row in rows[1:]:
            self.assertIsNotNone(row["previous_hash"])

    def test_tampered_entry_breaks_the_chain(self):
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE transition_journal SET payload = '{\"forged\": true}'"
                " WHERE seq = (SELECT MIN(seq) FROM transition_journal)")
        with self.assertRaises(JournalBroken):
            journal.validate(self.rt.db.conn.cursor())

    def test_receipt_storage(self):
        """CH2-receipt-storage (INV-72): authoritative state lives only in
        autobot.db and its content store — every committed reference
        resolves, and the runtime wrote nothing anywhere else."""
        rt = self.rt
        rt.sequencer.acquire_lease(ttl_seconds=3600)
        rt.sequencer.mark_ready(self.result["sequence_id"])
        rt.sequencer.start_execution(self.result["sequence_id"])
        cur = rt.db.conn.cursor()
        addresses = [r["content_address"] for r in cur.execute(
            "SELECT content_address FROM objects").fetchall()]
        addresses += [r["content_address"] for r in cur.execute(
            "SELECT content_address FROM records").fetchall()]
        addresses += [r["payload_content_address"] for r in cur.execute(
            "SELECT payload_content_address FROM admissions").fetchall()]
        self.assertEqual(rt.store.verify(addresses), [])
        allowed = {"autobot.db", "autobot.db-wal", "autobot.db-shm", "store"}
        self.assertTrue(set(os.listdir(str(rt.data_dir))) <= allowed)


if __name__ == "__main__":
    unittest.main()
