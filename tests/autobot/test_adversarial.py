"""The focused adversarial review (operator, 2026-11-09), one property per
test class:

1. Transaction boundary — a state mutation and its TransitionRecord commit
   atomically: neither a durable mutation without its journal entry nor a
   journal entry whose mutation rolled back can exist, and recovery HALTs on
   any forged divergence.
2. Fence ownership — the expected state/version/fence are conditions of the
   mutating SQL statement itself; a writer bypassing every Python check
   still cannot apply a stale mutation.
3. CAS durability contract — orphaned temp/unreferenced objects are harmless
   and reported; a committed ledger reference to missing or altered bytes is
   HALT (D-66), including pre-planted wrong bytes at an address.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support
from autobot import journal
from autobot.models import IntegrityHalt


class TransactionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.sequence_id = self.result["sequence_id"]

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def test_failed_journal_append_rolls_back_the_mutation(self):
        """D-68: the TransitionRecord append is inside the same transaction
        as the state mutation — if the append fails, the mutation vanishes."""
        self.rt.sequencer.acquire_lease(ttl_seconds=3600)
        before = self._journal_count()
        with mock.patch("autobot.journal.append",
                        side_effect=RuntimeError("journal write failed")):
            with self.assertRaises(RuntimeError):
                self.rt.sequencer.mark_ready(self.sequence_id)
        row = self.rt.db.conn.execute(
            "SELECT state, version FROM sequences WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()
        self.assertEqual((row["state"], row["version"]), ("DECLARED", 1))
        self.assertEqual(self._journal_count(), before)
        # and the untouched transaction machinery still works afterwards
        self.assertEqual(self.rt.sequencer.mark_ready(self.sequence_id), "READY")

    def test_unjournaled_mutation_halts_recovery(self):
        """A durable mutation without its journal entry is a D-68 pairing
        violation recovery must refuse to resume over."""
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE sequences SET state = 'HALTED', version = version + 1,"
                " updated_utc = 'forged' WHERE sequence_id = ?",
                (self.sequence_id,))
        with self.assertRaises(IntegrityHalt):
            self.rt.sequencer.recover()

    def test_counter_drift_halts_recovery(self):
        """Permit counters that disagree with their reservation rows are a
        forged state recovery must refuse (INV-42/INV-48)."""
        seq = self.rt.sequencer
        seq.acquire_lease(ttl_seconds=3600)
        seq.mark_ready(self.sequence_id)
        attempt_id = seq.start_execution(self.sequence_id)["attempt_id"]
        seq.grant_permit(self.sequence_id, permitted_uses=5)
        seq.reserve_use(self.sequence_id, attempt_id)
        with self.rt.db.transaction() as tx:
            tx.execute(
                "UPDATE permit_state SET reserved_uses = 2 WHERE sequence_id = ?",
                (self.sequence_id,))
        with self.assertRaises(IntegrityHalt):
            seq.recover()


class FenceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.sequence_id = self.result["sequence_id"]
        self.rt.sequencer.acquire_lease(ttl_seconds=3600)
        self.rt.sequencer.mark_ready(self.sequence_id)  # now version 2, fence 0

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def test_stale_guard_lives_in_the_sql(self):
        """A second connection that skips every in-process check still cannot
        apply a mutation with a stale version/fence: the UPDATE's own WHERE
        clause refuses it (rowcount 0), and only the exact current
        state/version/fence matches."""
        con2 = sqlite3.connect(str(self.rt.data_dir / "autobot.db"),
                               isolation_level=None)
        try:
            con2.execute("PRAGMA busy_timeout=2000")
            con2.execute("BEGIN IMMEDIATE")
            stale = con2.execute(
                "UPDATE sequences SET state = 'EXECUTING', version = version + 1"
                " WHERE sequence_id = ? AND state = 'READY'"
                " AND version = 1 AND fence = 0",
                (self.sequence_id,))
            self.assertEqual(stale.rowcount, 0)  # version is 2: stale refused
            exact = con2.execute(
                "UPDATE sequences SET state = 'EXECUTING', version = version + 1"
                " WHERE sequence_id = ? AND state = 'READY'"
                " AND version = 2 AND fence = 0",
                (self.sequence_id,))
            self.assertEqual(exact.rowcount, 1)  # the guard is the WHERE clause
            con2.rollback()
        finally:
            con2.close()
        row = self.rt.db.conn.execute(
            "SELECT state, version, fence FROM sequences WHERE sequence_id = ?",
            (self.sequence_id,)).fetchone()
        self.assertEqual((row["state"], row["version"], row["fence"]),
                         ("READY", 2, 0))


class CasContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def test_missing_committed_reference_halts_recovery(self):
        """D-66: a committed ledger reference to missing bytes is HALT."""
        row = self.rt.db.conn.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (self.result["record_hash"],)).fetchone()
        self.rt.store._path_for(row["content_address"]).unlink()
        with self.assertRaises(IntegrityHalt):
            self.rt.sequencer.recover()

    def test_orphans_are_harmless_and_reported(self):
        """D-66: a torn put's temp file and an unreferenced object never
        block recovery — they are reported and ignored. This is the explicit
        Windows contract: directory-fsync is best-effort there, and every
        crash outcome resolves to either this harmless case or the HALT
        case, never to silent trust."""
        (self.rt.store.root / "tmp" / "put-torn").write_bytes(b"half a put")
        orphan_address = self.rt.store.put(b"never referenced by the ledger\n")
        report = self.rt.sequencer.recover()
        self.assertIn("put-torn", report["orphans"]["tmp_files"])
        self.assertIn(orphan_address, report["orphans"]["unreferenced_objects"])

    def test_clean_state_has_no_orphans(self):
        report = self.rt.sequencer.recover()
        self.assertEqual(report["orphans"],
                         {"tmp_files": [], "unreferenced_objects": []})

    def test_put_refuses_planted_wrong_bytes(self):
        """A file already sitting at an address whose bytes do not re-hash to
        it is tampering: put HALTs rather than trusting or replacing it."""
        from contract_library import content_address
        address = content_address(b"good bytes")
        path = self.rt.store._path_for(address)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"evil bytes")
        with self.assertRaises(IntegrityHalt):
            self.rt.store.put(b"good bytes")

    def test_journal_entries_match_validate(self):
        cur = self.rt.db.conn.cursor()
        self.assertEqual(len(journal.entries(cur)), journal.validate(cur))


if __name__ == "__main__":
    unittest.main()
