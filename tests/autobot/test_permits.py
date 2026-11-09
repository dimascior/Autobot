"""Standing-permit arithmetic and reservation rows: INV-42 (reservation rows
move from RESERVED exactly once), INV-48 (counter inequality, Sequencer-only
writes), INV-60 (each reservation's own renewable lease)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import support
from autobot.models import ConflictError, PermitExhaustedError


class PermitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.sequence_id = self.result["sequence_id"]
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=36000)
        self.seq.mark_ready(self.sequence_id)
        self.attempt_id = self.seq.start_execution(self.sequence_id)["attempt_id"]

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _counters(self):
        row = self.rt.db.conn.execute(
            "SELECT permitted_uses, consumed_uses, reserved_uses FROM permit_state"
            " WHERE sequence_id = ?", (self.sequence_id,),
        ).fetchone()
        return (row["permitted_uses"], row["consumed_uses"], row["reserved_uses"])

    def test_permit_race_two_reservers_one_use(self):
        """CH2-permit-race-two-reservers-one-use (INV-42): with one use
        available exactly one reserver wins, and consume moves the row from
        RESERVED exactly once."""
        self.seq.grant_permit(self.sequence_id, permitted_uses=1)
        rv1 = self.seq.reserve_use(self.sequence_id, self.attempt_id)
        with self.assertRaises(PermitExhaustedError):
            self.seq.reserve_use(self.sequence_id, self.attempt_id)
        self.seq.consume_reservation(rv1, cause="test-ponr")
        with self.assertRaises(ConflictError):
            self.seq.consume_reservation(rv1, cause="test-ponr")
        self.assertFalse(self.seq.release_reservation(rv1, cause="late"))
        self.assertEqual(self._counters(), (1, 1, 0))

    def test_permit_counters(self):
        """CH2-permit-counters (INV-48): consumed + reserved <= permitted at
        every step, enforced by logic and by the table CHECK."""
        self.seq.grant_permit(self.sequence_id, permitted_uses=2)
        rv1 = self.seq.reserve_use(self.sequence_id, self.attempt_id)
        rv2 = self.seq.reserve_use(self.sequence_id, self.attempt_id)
        self.assertEqual(self._counters(), (2, 0, 2))
        with self.assertRaises(PermitExhaustedError):
            self.seq.reserve_use(self.sequence_id, self.attempt_id)
        self.seq.consume_reservation(rv1, cause="test-ponr")
        self.assertEqual(self._counters(), (2, 1, 1))
        self.assertTrue(self.seq.release_reservation(rv2, cause="pre-ponr exit"))
        self.assertEqual(self._counters(), (2, 1, 0))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.rt.db.transaction() as tx:
                tx.execute(
                    "UPDATE permit_state SET reserved_uses = permitted_uses + 1"
                    " WHERE sequence_id = ?", (self.sequence_id,),
                )

    def test_permitted_uses_domain(self):
        from autobot.models import INT_MAX
        with self.assertRaises(ConflictError):
            self.seq.grant_permit(self.sequence_id, permitted_uses=INT_MAX + 1)

    def test_reservation_lease(self):
        """CH2-reservation-lease (INV-60): every reservation has its own
        renewable lease; expiry releases exactly the expired rows (t-r02)."""
        self.seq.grant_permit(self.sequence_id, permitted_uses=2)
        rv1 = self.seq.reserve_use(self.sequence_id, self.attempt_id,
                                   lease_seconds=100)
        rv2 = self.seq.reserve_use(self.sequence_id, self.attempt_id,
                                   lease_seconds=300)
        self.seq.renew_reservation(rv2, lease_seconds=7200)
        self.rt.clock.advance(150)
        self.assertEqual(self.seq.release_expired_reservations(), [rv1])
        self.assertEqual(self._counters(), (2, 0, 1))
        self.assertEqual(self.seq.release_expired_reservations(), [])
        self.rt.clock.advance(7200)
        self.assertEqual(self.seq.release_expired_reservations(), [rv2])
        self.assertEqual(self._counters(), (2, 0, 0))
        with self.assertRaises(ConflictError):
            self.seq.renew_reservation(rv1)
        rows = self.rt.db.conn.execute(
            "SELECT reservation_id, state FROM reservations").fetchall()
        self.assertEqual(
            {r["reservation_id"]: r["state"] for r in rows},
            {rv1: "RELEASED", rv2: "RELEASED"})


if __name__ == "__main__":
    unittest.main()
