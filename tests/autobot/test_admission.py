"""Admission over the real ledger: P-2's live-Sequence stub replaced (D-71),
the t-c01..t-c07 machine journaled, tiers equal to the frozen goldens, and
the lifecycle-coupled Sequence (INV-40, INV-54)."""

import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import journal
from autobot.models import AdmissionError


def _patched(record, mutate):
    import copy
    out = copy.deepcopy(record)
    mutate(out)
    return out


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.result = support.admitted_runtime(Path(self.tmp.name))
        self.record = support.load_fixture("va_record.json")

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _journal_rules(self):
        cur = self.rt.db.conn.cursor()
        return [
            json.loads(row["payload"])["rule"]
            for row in cur.execute(
                "SELECT payload FROM transition_journal ORDER BY seq"
            ).fetchall()
        ]

    def test_new_record_matches_frozen_goldens(self):
        golden = support.load_golden()["V-A"]
        self.assertEqual(self.result["outcome"], "NEW_RECORD")
        self.assertEqual(self.result["requirement_digest"], golden["requirement_digest"])
        self.assertEqual(self.result["contract_digest"], golden["contract_digest"])
        self.assertEqual(self.result["record_hash"], golden["record_hash"])

    def test_frozen_record_round_trips_from_the_cas(self):
        cur = self.rt.db.conn.cursor()
        row = cur.execute(
            "SELECT * FROM records WHERE record_hash = ?",
            (self.result["record_hash"],),
        ).fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(self.rt.store.get(row["content_address"]).decode("utf-8"))
        self.assertEqual(payload, self.record)
        self.assertEqual(row["eligibility"], "AUTOMATIC")

    def test_sequence_lifecycle_coupling(self):
        """INV-40: the Sequence is created with the freeze, initial DECLARED,
        fence 0, version 1 — never by a separate transition mint."""
        cur = self.rt.db.conn.cursor()
        row = cur.execute(
            "SELECT * FROM sequences WHERE sequence_id = ?",
            (self.result["sequence_id"],),
        ).fetchone()
        self.assertEqual(row["state"], "DECLARED")
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["fence"], 0)
        self.assertEqual(row["record_hash"], self.result["record_hash"])
        rules = self._journal_rules()
        self.assertIn("t-c01", rules)
        self.assertIn("t-c02", rules)
        self.assertEqual(journal.validate(cur), len(rules))

    def test_duplicate_admission_mints_obj32(self):
        """INV-54/INV-61: a matching ContractDigest against a live Sequence
        creates no record and no Sequence; the Registry records obj-32."""
        second = self.rt.registry.admit_payload(self.record, submitted_by="planner")
        self.assertEqual(second["outcome"], "DUPLICATE")
        self.assertEqual(second["returns_existing_record_hash"],
                         self.result["record_hash"])
        self.assertIn("duplicate_admission_id", second)
        cur = self.rt.db.conn.cursor()
        self.assertEqual(
            cur.execute("SELECT COUNT(*) AS n FROM sequences").fetchone()["n"], 1)
        dup = cur.execute("SELECT * FROM duplicate_admissions").fetchone()
        self.assertEqual(dup["existing_record_hash"], self.result["record_hash"])
        self.assertEqual(dup["payload_contract_digest"], self.result["contract_digest"])
        self.assertTrue(self.rt.store.has(dup["payload_content_address"]))
        self.assertIn("t-c03", self._journal_rules())

    def test_force_new_sequence_creates_an_independent_sequence(self):
        """INV-54's force_new_sequence arm: a second live Sequence for the
        same ContractDigest only under the explicit flag."""
        v_e = support.load_fixture("vector_payloads.json")["V-E"]
        result = self.rt.registry.admit_payload(v_e, submitted_by="planner")
        self.assertEqual(result["outcome"], "NEW_RECORD")
        cur = self.rt.db.conn.cursor()
        self.assertEqual(
            cur.execute("SELECT COUNT(*) AS n FROM sequences").fetchone()["n"], 2)

    def test_rejected_payload_freezes_nothing(self):
        bad = _patched(self.record, lambda r: r["retry_policy"].__setitem__(
            "max_retries", 2.5))
        result = self.rt.registry.admit_payload(bad, submitted_by="planner")
        self.assertEqual(result["outcome"], "REJECTED")
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("ERR_FLOAT_FORBIDDEN", codes)
        cur = self.rt.db.conn.cursor()
        self.assertEqual(
            cur.execute("SELECT COUNT(*) AS n FROM sequences").fetchone()["n"], 1)
        self.assertIn("t-c07", self._journal_rules())
        row = cur.execute(
            "SELECT state FROM admissions ORDER BY admission_id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["state"], "REJECTED")

    def test_unresolved_binding_is_rejected(self):
        """V-F binds verifier_checker_v2, which the seed never admitted: the
        bound hash does not resolve in the ledger."""
        v_f = support.load_fixture("vector_payloads.json")["V-F"]
        result = self.rt.registry.admit_payload(v_f, submitted_by="planner")
        self.assertEqual(result["outcome"], "REJECTED")
        codes = [i["code"] for i in result["issues"]]
        self.assertEqual(codes, ["ERR_UNRESOLVED_BINDING"])

    def test_unresolved_input_is_rejected(self):
        """t-c02's inputs_resolved_exact_bytes: V-H's CRLF input bytes were
        never stored, so the freeze is refused (t-c07)."""
        v_h = support.load_fixture("vector_payloads.json")["V-H"]
        result = self.rt.registry.admit_payload(v_h, submitted_by="planner")
        self.assertEqual(result["outcome"], "REJECTED")
        codes = [i["code"] for i in result["issues"]]
        self.assertEqual(codes, ["ERR_UNRESOLVED_INPUT"])

    def test_expired_delegation_queues_for_you(self):
        """t-d02 + t-c04: a freeze bound to an expired delegation is not
        within an ACTIVE delegation and waits for you."""
        self.rt.clock.advance(60 * 60 * 24 * 420)  # well past 2027-01-01
        v_c = support.load_fixture("vector_payloads.json")["V-C"]
        result = self.rt.registry.admit_payload(v_c, submitted_by="planner")
        self.assertEqual(result["outcome"], "QUEUED_FOR_YOU")
        cur = self.rt.db.conn.cursor()
        row = cur.execute(
            "SELECT state, decided_utc FROM admissions ORDER BY admission_id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["state"], "QUEUED_FOR_YOU")
        self.assertIsNone(row["decided_utc"])
        self.assertIn("t-c04", self._journal_rules())

    def test_unparseable_bytes_never_enter_the_machine(self):
        with self.assertRaises(AdmissionError):
            self.rt.registry.admit_payload(b"\xff{not json", submitted_by="planner")

    def test_sem19_repository_id_uniqueness_binding(self):
        """SEM-19 / D-23: one logical repository per repository_id; a newer
        declaration for the same repository revises the single binding."""
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"], submitted_by="you")
        cur = self.rt.db.conn.cursor()
        rows = cur.execute(
            "SELECT repository_id FROM declarations").fetchall()
        self.assertEqual([r["repository_id"] for r in rows], ["autobot"])


if __name__ == "__main__":
    unittest.main()
